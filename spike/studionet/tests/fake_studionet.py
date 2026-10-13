"""
In-memory stand-in for Studionet (chain 61999) at the JSON-RPC level.

The real genlayer-py 0.18.0 client runs against it unchanged: it builds,
signs and sends real transactions, polls real-looking receipts and decodes
real gen_call results. Shapes follow Studio v0.121
(TransactionsProcessor._parse_transaction_data, get_transaction_receipt,
get_gas_price, get_gas_estimate) and the live observations: records carry
sim_config.signed_rollup_transaction, sim_getConsensusContract reports the
zero address with the 5-input ABI.

The contract is a small Python model of GoodFaithLayer's state machine, not
the contract itself; contract logic is covered separately against the real
GenVM v0.2.16 standard library (tests/test_port.py).
"""

import base64
import json
import sys
from pathlib import Path

import rlp
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_utils import keccak, to_checksum_address

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
sys.path.insert(0, str(KIT))
import studionet_checks as sn  # noqa: E402
from genlayer_py.abi import calldata as gl_calldata  # noqa: E402

ZERO = "0x" + "00" * 20
NEW_TX_TOPIC = "0x" + keccak(text="NewTransaction(bytes32,address,address)").hex()


def key_account(n):
    return Account.from_key((n).to_bytes(32, "big"))


def wallet_envelope(acct, contract, method, args, *, nonce, chain_id=sn.CHAIN_ID, to=sn.CONSENSUS_ADDRESS,
                    flag=b"\x00", value=0, gl_sender=None):
    """A transaction shaped exactly like genlayer-js 1.1.8 asks the wallet to sign (v5, inner flag 0x00)."""
    cd = gl_calldata.encode({"method": method, "args": args} if args else {"method": method})
    inner = rlp.encode([cd, flag])
    data = bytes.fromhex(sn.V5_SELECTOR) + abi_encode(
        ["address", "address", "uint256", "uint256", "bytes"],
        [gl_sender or acct.address, to_checksum_address(contract), 5, 3, inner])
    raw = bytes(acct.sign_transaction({"to": to_checksum_address(to), "data": data, "value": value,
                                       "gas": 500000, "gasPrice": 0, "nonce": nonce,
                                       "chainId": chain_id}).raw_transaction)
    return "0x" + raw.hex()


class GFLModel:
    def __init__(self, owner, flag_authority):
        self.owner, self.flag = owner.lower(), flag_authority.lower()
        self.attesters, self.policies, self.payments = set(), {}, {}
        self.pool, self.balances = 0, {}

    def call(self, sender, method, args):
        s = sender.lower()
        if method == "register_attester":
            assert s == self.owner, "owner only"
            self.attesters.add(args[0].lower())
        elif method == "fund_protection_pool":
            assert s == self.owner, "owner only"
            self.pool += int(args[0])
        elif method == "register_policy":
            assert s == self.owner, "owner only"
            pid, text, checks, ttl = args
            assert pid not in self.policies, "policies are immutable"
            self.policies[pid] = {"text": text, "checks": checks, "ttl": ttl}
        elif method == "register_payment":
            pid, recipient, amount, policy_id, terms = args
            assert pid not in self.payments and policy_id in self.policies
            self.payments[pid] = {"payer": s, "recipient": recipient.lower(), "amount": amount, "policy": policy_id,
                                  "status": "REGISTERED", "attested": False, "fresh": False, "verdict": "", "paid": 0}
        elif method == "attest":
            assert s in self.attesters, "not an attester"
            p = self.payments[args[0]]
            assert p["status"] == "REGISTERED" and not p["attested"]
            p["attested"] = True
        elif method == "accept_payment":
            p = self.payments[args[0]]
            assert s == p["recipient"], "only the recipient may accept"
            assert p["status"] == "REGISTERED" and args[1] == p["policy"]
            p.update(status="ACCEPTED", fresh=p["attested"])
        elif method == "flag_payment":
            assert s == self.flag, "only the flag authority"
            p = self.payments[args[0]]
            assert p["status"] == "ACCEPTED"
            p["status"] = "FLAGGED"
        elif method == "open_claim":
            p = self.payments[args[0]]
            assert s == p["recipient"] and p["status"] == "FLAGGED"
            ok = p["fresh"]
            p.update(status="RESOLVED", verdict="PROTECTED" if ok else "REVIEW_REQUIRED")
            if ok:
                paid = min(self.pool, p["amount"])
                self.pool -= paid
                p["paid"] = paid
                self.balances[p["recipient"]] = self.balances.get(p["recipient"], 0) + paid
        else:
            raise AssertionError(f"unknown method {method}")

    def read(self, method, args):
        if method == "get_status":
            return self.payments[args[0]]["status"]
        if method == "has_attestation":
            return self.payments[args[0]]["attested"]
        if method == "is_attestation_fresh":
            return self.payments[args[0]]["fresh"]
        if method == "get_protection_pool":
            return self.pool
        if method == "get_max_attestation_age":
            return self.policies[args[0]]["ttl"]
        if method == "get_policy":
            return self.policies[args[0]]["text"]
        if method == "get_required_check_ids":
            return json.dumps([c["check_id"] for c in json.loads(self.policies[args[0]]["checks"])],
                              separators=(",", ":"))
        if method == "is_attester":
            return args[0].lower() in self.attesters
        if method == "get_verdict":
            return self.payments[args[0]]["verdict"]
        if method == "get_paid_out":
            return self.payments[args[0]]["paid"]
        if method == "get_claim_balance":
            return self.balances.get(args[0].lower(), 0)
        if method == "get_reasoning":
            return "model"
        raise AssertionError(f"unknown read {method}")


class FakeStudionet:
    def __init__(self):
        self.chain_id = sn.CHAIN_ID
        self.gas_price = "0x0"
        self.records = {}
        self.nonces = {}
        self.contracts = {}            # checksum address -> {"code_b64":..., "model": GFLModel}
        self.calls = []                # every JSON-RPC method received
        self.sent_raw = []
        self.schema = json.loads((KIT / "contract" / "schema.expected.json").read_text())
        self.status_on_send = "FINALIZED"
        self.votes = None              # default 5 agree
        self.fail_gen_call = 0         # next N gen_calls answer with a JSON-RPC error
        self.mutate_record = None      # fn(record) applied when a record is created
        self.next_contract = to_checksum_address("0x" + "c0" * 20)

    # transport entry point: same signature as operator_studionet._timed_request
    def send(self, url, method, params):
        self.calls.append(method)
        fn = getattr(self, "rpc_" + method, None)
        if fn is None:
            return self._err(-32601, f"Method not found: {method}")
        try:
            return {"jsonrpc": "2.0", "id": 1, "result": fn(*params)}
        except _RpcErr as e:
            import operator_studionet as op
            raise op.RemoteRPCError(f"{method} failed (code={e.code}): {e.msg}")

    def _err(self, code, msg):
        import operator_studionet as op
        raise op.RemoteRPCError(f"fake failed (code={code}): {msg}")

    # ---- simple reads ----------------------------------------------------
    def rpc_eth_chainId(self):
        return hex(self.chain_id)

    def rpc_eth_gasPrice(self):
        return self.gas_price

    def rpc_eth_estimateGas(self, tx, *a):
        return hex(0x7A120)

    def rpc_eth_getTransactionCount(self, address, block="latest"):
        return hex(self.nonces.get(address.lower(), 0))

    def rpc_eth_getBalance(self, address, block="latest"):
        return "0x0"

    def rpc_eth_blockNumber(self):
        return "0x1"

    def rpc_sim_getConsensusContract(self, name):
        return {"address": ZERO, "abi": sn.load_v5_abi()}

    def rpc_sim_getFinalityWindowTime(self):
        return 30

    def rpc_gen_getContractCode(self, address):
        c = self.contracts.get(address)          # exact-string lookup, like Studio
        if c is None:
            raise _RpcErr(-32000, f"Contract {address} not found")
        return c["code_b64"]

    def rpc_gen_getContractSchemaForCode(self, code_hex):
        code = bytes.fromhex(code_hex[2:])
        if sn.sc.sha256(code) != sn.PORT_SOURCE_SHA256:
            raise _RpcErr(-32000, "schema generation failed: No module named 'genlayer.storage'")
        return self.schema

    def rpc_eth_getTransactionByHash(self, h, *a):
        rec = self.records.get(h.lower())
        if rec is None:
            raise _RpcErr(-32000, f"Transaction {h} not found")
        return json.loads(json.dumps(rec))

    def rpc_eth_getTransactionReceipt(self, h):
        rec = self.records.get(h.lower())
        if rec is None:
            return None
        to_addr, from_addr = rec["to_address"], rec["from_address"]
        return {
            "transactionHash": h, "transactionIndex": "0x0", "blockHash": h, "blockNumber": "0x0",
            "from": from_addr, "to": to_addr, "cumulativeGasUsed": hex(8000000), "gasUsed": hex(8000000),
            "effectiveGasPrice": "0x0", "type": "0x0", "contractAddress": None,
            "logs": [{"address": to_addr, "topics": [NEW_TX_TOPIC, h,
                                                     "0x" + "00" * 12 + to_addr[2:].lower(),
                                                     "0x" + "00" * 12 + from_addr[2:].lower()],
                      "data": "0x", "blockNumber": "0x0", "transactionHash": h, "transactionIndex": "0x0",
                      "blockHash": h, "logIndex": "0x0", "removed": False}],
            "logsBloom": "0x" + "00" * 256, "status": "0x1",
        }

    def rpc_gen_call(self, p):
        if self.fail_gen_call:
            self.fail_gen_call -= 1
            raise _RpcErr(-32603, "simulated read failure")
        assert p.get("type") == "read" and p.get("from")
        c = self.contracts.get(p["to"])
        if c is None:
            raise _RpcErr(-32000, f"Contract {p['to']} not found")
        cd, flag = rlp.decode(bytes.fromhex(p["data"][2:]))
        call = gl_calldata.decode(cd)
        assert "method" in call and "" not in call, call
        value = c["model"].read(call["method"], list(call.get("args", [])))
        return gl_calldata.encode(value).hex()

    # ---- the write path ----------------------------------------------------
    def rpc_eth_sendRawTransaction(self, raw_hex, *rest):
        assert not rest, "sim_config must never be sent"
        from eth_abi import decode as abi_decode
        self.sent_raw.append(raw_hex)
        tx = sn.sc._decode_raw_evm_tx(bytes.fromhex(raw_hex[2:]))       # signature recovered, like the server
        signer = to_checksum_address(tx["signer"])
        self.nonces[signer.lower()] = self.nonces.get(signer.lower(), 0) + 1
        h = tx["hash"]
        rec = {"hash": h, "from_address": signer, "value": tx["value"], "status": self.status_on_send,
               "num_of_initial_validators": 5, "created_at": "2026-10-14T10:00:00+00:00",
               "sim_config": {"signed_rollup_transaction": raw_hex},
               "consensus_data": {"votes": dict(self.votes or {f"0xv{i}": "agree" for i in range(5)}),
                                  "leader_receipt": [{"execution_result": "SUCCESS", "genvm_result": {}}]},
               "consensus_history": {"consensus_results": [{"consensus_round": "Accepted"}]}}
        if tx["data"][:4].hex() != sn.V5_SELECTOR:
            # Studio v0.121 decodes with its v5 ABI only; anything else is a plain transfer.
            rec.update(type=0, to_address=to_checksum_address(tx["to"]), data={}, leader_only=False,
                       execution_mode="NORMAL")
            self.records[h] = rec
            return h
        gl_sender, gl_recipient, _, _, inner = abi_decode(
            ["address", "address", "uint256", "uint256", "bytes"], tx["data"][4:])
        items = rlp.decode(inner)
        leader_only = items[-1] == b"\x01"
        rec.update(leader_only=leader_only, execution_mode="LEADER_ONLY" if leader_only else "NORMAL")
        if gl_recipient.lower() == ZERO:
            code, cd = items[0], items[1]
            addr = self.next_contract
            ctor = gl_calldata.decode(cd)
            self.contracts[addr] = {"code_b64": base64.b64encode(code).decode(),
                                    "model": GFLModel(signer, ctor["args"][0])}
            rec.update(type=1, to_address=addr, data={"contract_address": addr,
                                                      "contract_code": base64.b64encode(code).decode(),
                                                      "calldata": base64.b64encode(cd).decode()})
        else:
            cd = items[0]
            target = to_checksum_address(gl_recipient)
            rec.update(type=2, to_address=target, data={"calldata": base64.b64encode(cd).decode()})
            call = gl_calldata.decode(cd)
            try:
                self.contracts[target]["model"].call(signer, call["method"], list(call.get("args", [])))
            except Exception:
                rec["consensus_data"]["leader_receipt"] = [{"execution_result": "ERROR"}]
        if self.mutate_record:
            self.mutate_record(rec)
        self.records[h] = rec
        return h


def _inner(env):
    from eth_abi import decode as abi_decode
    return abi_decode(["address", "address", "uint256", "uint256", "bytes"], env["data"][4:])[4]


class _RpcErr(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg

"""
Good Faith Layer - Studionet external-wallet kit (operator side), chain 61999.

Runs the privileged roles (owner/payer, attester, flag authority) against a
SEPARATE deployment of the reviewed Studionet port of the contract
(spike/studionet/contract/good_faith_layer.studionet.py). accept_payment and
open_claim are signed by the user's external wallet in the page served by
spike/studionet/serve_studionet.py.

Needs its own Python 3.12 environment with genlayer-py 0.18.0 exactly
(requirements-studionet.txt). It refuses to run on any other version.

Keys: GFL_SN_OWNER_KEY, GFL_SN_ATTESTER_KEY, GFL_SN_FLAG_KEY, or a hidden
prompt if unset. Never printed, logged or written. spike/studionet/run/ holds
only public addresses, hashes, timings, decoded checks and read-backs. It is
separate from spike/run/ (the 61997 case), which this script never touches.

Commands (repository root, in order):
    python spike/studionet/operator_studionet.py schema                      (read-only, no keys)
    python spike/studionet/operator_studionet.py preflight --wallet 0xWALLET (read-only)
    python spike/studionet/operator_studionet.py setup --wallet 0xWALLET --confirm-allowance N
    python spike/studionet/operator_studionet.py check-state [--attempts N]  (read-only)
    python spike/studionet/operator_studionet.py attest
    python spike/studionet/operator_studionet.py flag --accept-hash 0xACCEPT_TX
    python spike/studionet/operator_studionet.py verify --claim-hash 0xCLAIM_TX (read-only)
    python spike/studionet/operator_studionet.py recover --wallet 0xWALLET   (read-only; from run/progress.json)

Every transaction goes through one boundary (Transport): it refuses any RPC
method not on its allowlist, decodes and checks each signed transaction
before it leaves this machine (chain 61999, consensus target, v5 encoding,
signer, zero value, NORMAL mode, exact method/arguments or exact reviewed
code), journals keccak(raw) before sending, sends it once, and refuses a
second send for the same step. Nothing is retried.

Exit codes: 0 passed, 1 verification failed, 2 blocked.
"""

import argparse
import copy
import getpass
import json
import math
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import studionet_checks as sn  # noqa: E402

ROOT = HERE.parents[1]
RUN = HERE / "run"
CONTRACT = HERE / "contract" / "good_faith_layer.studionet.py"
EXPECTED_SCHEMA = HERE / "contract" / "schema.expected.json"
POLICY = ROOT / "policies" / "gfl-standard-v1.txt"
CHECKS = ROOT / "policies" / "gfl-standard-v1-checks.json"
ASSERTIONS = ROOT / "scenarios" / "01_protected" / "assertions.json"
ATTESTED = ROOT / "scenarios" / "01_protected" / "attested.json"
RPC_URL = "https://studio.genlayer.com/api"
SDK_VERSION = "0.18.0"

POLL_SECONDS = 3                  # equal to POLL_MS in public/sn-core.js
WAIT_SECONDS = 20 * 60            # bounded wait per transaction
RPC_TIMEOUT_SECONDS = 30
PAYMENT_AMOUNT = 1000
MAX_READ_ATTEMPTS = 20
EXIT_OK, EXIT_FAILED, EXIT_BLOCKED = 0, 1, 2

SLEEP = time.sleep
CLOCK = time.time

KEY_VARS = ("GFL_SN_OWNER_KEY", "GFL_SN_ATTESTER_KEY", "GFL_SN_FLAG_KEY")


class Blocked(Exception):
    pass


class Refused(Blocked):
    """The transport boundary refused a request; nothing was sent."""


class RemoteRPCError(Exception):
    """The RPC endpoint answered with a JSON-RPC error."""


class TransportError(Exception):
    """No JSON-RPC answer (network, timeout, HTTP, bad JSON)."""


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------
def now_iso():
    return datetime.now(timezone.utc).isoformat()


def text_exact(p: Path) -> str:
    """File text with line endings preserved (the page sends the same bytes)."""
    return p.read_bytes().decode("utf-8")


def load(name):
    p = RUN / name
    if not p.exists():
        raise Blocked(f"{p} missing; run the earlier command first")
    return json.loads(p.read_text(encoding="utf-8"))


def save(name, data):
    """Atomic write (os.replace also works on Windows)."""
    RUN.mkdir(parents=True, exist_ok=True)
    target = RUN / name
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=2, default=str))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)


class Journal:
    """
    run/progress.json, rewritten before every send, after every send and after
    every result. A crash at any point keeps every hash. Evidence only:
    nothing is ever re-sent from it.
    """

    def __init__(self, **case):
        self.data = {"started": now_iso(), "complete": False, **case, "transactions": []}
        save("progress.json", self.data)

    def set(self, **kw):
        self.data.update(kw)
        save("progress.json", self.data)

    def sending(self, function, tx_hash, sender):
        self.data["transactions"].append({"function": function, "hash": tx_hash, "sender": sender,
                                          "sending_at": now_iso(), "result": "sending"})
        save("progress.json", self.data)

    def update(self, tx_hash, **kw):
        for t in self.data["transactions"]:
            if t["hash"] == tx_hash:
                t.update(kw)
        save("progress.json", self.data)


# --------------------------------------------------------------------------
# transport boundary
# --------------------------------------------------------------------------
def _timed_request(url, method, params):
    import requests
    payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000), "method": method, "params": params}
    try:
        r = requests.post(url, json=payload, timeout=RPC_TIMEOUT_SECONDS,
                          headers={"Content-Type": "application/json", "User-Agent": "genlayer-py"})
    except requests.exceptions.Timeout as e:
        raise TransportError(f"{method}: no response within {RPC_TIMEOUT_SECONDS}s ({e})")
    except requests.exceptions.RequestException as e:
        raise TransportError(f"{method}: request to {url} failed: {e}")
    try:
        body = r.json()
    except ValueError:
        raise TransportError(f"{method}: HTTP {r.status_code}, response is not JSON: {r.text[:200]!r}")
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        raise TransportError(f"{method}: HTTP {r.status_code}, not a JSON-RPC 2.0 response")
    if body.get("error"):
        err = body["error"] if isinstance(body["error"], dict) else {"message": str(body["error"])}
        raise RemoteRPCError(f"{method} failed (code={err.get('code', 'unknown')}): {err.get('message', 'unknown error')}")
    if "result" not in body:
        raise TransportError(f"{method}: HTTP {r.status_code}, JSON-RPC response has no result")
    return body


READ_METHODS = frozenset({
    "eth_chainId", "eth_gasPrice", "eth_getTransactionCount", "eth_estimateGas", "eth_blockNumber",
    "eth_getBlockByNumber", "eth_getTransactionReceipt", "eth_getTransactionByHash", "eth_getBalance",
    "gen_call", "gen_getContractCode", "gen_getContractSchemaForCode", "sim_getConsensusContract",
    "sim_getFinalityWindowTime",
})


class Transport:
    """Single path from the SDK to the RPC. See the module docstring."""

    def __init__(self, url=RPC_URL, send=None):
        self.url = url
        self._send = send or _timed_request
        self.slot = None
        self.trace = []

    def arm(self, slot):
        if self.slot is not None:
            raise Blocked("another write is already armed")
        slot.setdefault("sent", 0)
        self.slot = slot

    def disarm(self):
        self.slot = None

    def make_request(self, method, params):
        entry = {"method": method}
        self.trace.append(entry)
        if "sim_config" in json.dumps(params, default=str):
            entry["refused"] = "sim_config"
            raise Refused(f"{method}: sim_config is never sent")
        if method == "eth_sendRawTransaction":
            return self._send_raw(params, entry)
        if method not in READ_METHODS:
            entry["refused"] = "not allowlisted"
            raise Refused(f"{method} is not permitted by this kit")
        return self._forward(method, params, entry)

    def _forward(self, method, params, entry):
        try:
            resp = self._send(self.url, method, params)
        except RemoteRPCError as e:
            entry["remote_error"] = str(e)
            raise
        except TransportError as e:
            entry["transport_error"] = str(e)
            raise
        entry["ok"] = True
        return resp

    def _send_raw(self, params, entry):
        s = self.slot
        if s is None:
            entry["refused"] = "no armed write"
            raise Refused("eth_sendRawTransaction outside an armed write; nothing sent")
        if s["sent"]:
            entry["refused"] = "second send"
            s["rejection"] = "the SDK attempted a second transaction for one step; refused"
            raise Refused(s["rejection"])
        if not isinstance(params, list) or len(params) != 1 or not isinstance(params[0], str):
            entry["refused"] = "params"
            raise Refused("eth_sendRawTransaction must carry exactly one raw transaction")
        raw = params[0]
        fails, env = sn.precheck_raw(raw, **s["expect"])
        if fails:
            entry["refused"] = "precheck"
            s["rejection"] = "; ".join(fails)
            raise Refused("signed transaction failed the pre-send check; nothing sent: " + s["rejection"])
        h = env["hash"]
        s["sent"] = 1
        s["hash"] = h
        if s.get("journal"):
            s["journal"].sending(s["function"], h, s["sender"])
        s["sent_at"] = CLOCK()
        resp = self._forward("eth_sendRawTransaction", params, entry)
        returned = sn._hash(resp.get("result") if isinstance(resp, dict) else None)
        if returned != h:
            s["hash_mismatch"] = returned
        if s.get("journal"):
            s["journal"].update(h, result="submitted", returned_hash=returned)
        if returned != h:            # do not let the SDK wait on a hash we cannot bind
            raise Blocked(f"RPC returned hash {returned}, keccak(raw) is {h}")
        return resp


# --------------------------------------------------------------------------
# sdk (genlayer-py 0.18.0) - patched in tests only through Transport.send
# --------------------------------------------------------------------------
def sdk_version():
    from importlib.metadata import version
    return version("genlayer-py")


def require_sdk():
    v = sdk_version()
    if v != SDK_VERSION:
        raise Blocked(f"genlayer-py {v} is installed; this kit needs exactly {SDK_VERSION} "
                      "(use the separate environment from requirements-studionet.txt)")


def chain_config():
    """
    studionet from genlayer-py 0.18.0, with the consensus contract pinned to the
    address genlayer-js 1.1.8 uses and the v5 ABI Studionet parses. 0.18.0's own
    static ABI has the 6-input addTransaction, and its runtime lookup would
    replace the address with the hosted zero address; both are overridden here
    and the pre-send check enforces the result.
    """
    from genlayer_py.chains import studionet
    chain = copy.deepcopy(studionet)
    chain.rpc_urls["default"]["http"] = [RPC_URL]
    chain.consensus_main_contract = {"address": sn.CONSENSUS_ADDRESS, "abi": sn.load_v5_abi(), "bytecode": ""}
    return chain


def make_client(account, transport):
    from genlayer_py.client.genlayer_client import GenLayerClient
    client = GenLayerClient(chain_config(), account)
    client.provider.make_request = transport.make_request
    # write_contract/deploy_contract call this before every send; keep the pinned config.
    client.initialize_consensus_smart_contract = lambda *a, **k: None
    return client


def key(var):
    v = os.environ.get(var) or getpass.getpass(f"{var} (hidden): ")
    if not v:
        raise Blocked(f"{var} not provided")
    from genlayer_py import create_account
    return create_account(v.strip())


def operators():
    owner, attester, flag = (key(v) for v in KEY_VARS)
    if len({owner.address.lower(), attester.address.lower(), flag.address.lower()}) != 3:
        raise Blocked("owner, attester and flag authority must be three distinct accounts")
    return owner, attester, flag


def op_dict(ops):
    return {"owner_payer": ops[0].address, "attester": ops[1].address, "flag_authority": ops[2].address}


class Ctx:
    """A client plus its transport. Read contexts use a fresh in-memory account that never signs."""

    def __init__(self, account=None, transport=None):
        require_sdk()
        self.transport = transport or Transport()
        if account is None:
            from genlayer_py import create_account
            account = create_account()          # random, memory only; gen_call needs a 'from'
            self.read_only = True
        else:
            self.read_only = False
        self.account = account
        self.client = make_client(account, self.transport)

    def rpc(self, method, params):
        resp = self.transport.make_request(method, params)
        return resp.get("result") if isinstance(resp, dict) else resp


TRANSPORT_FACTORY = Transport           # tests replace this to route to an in-memory Studionet


def ctx(account=None):
    return Ctx(account, TRANSPORT_FACTORY())


def read(c, address, fn, args=None):
    return c.client.read_contract(address=sn.checksum(address), function_name=fn, args=args or [])


def tx_record(c, tx_hash):
    try:
        return c.rpc("eth_getTransactionByHash", [tx_hash])
    except (RemoteRPCError, TransportError) as e:
        raise Blocked(f"transaction record for {tx_hash} unavailable: {e}")


TERMINAL_BAD = {"UNDETERMINED", "LEADER_TIMEOUT", "VALIDATORS_TIMEOUT", "CANCELED"}


def wait_record(c, tx_hash, finalized=False, seconds=None):
    """Bounded polling of the record. Read failures while polling are tolerated; nothing is re-sent."""
    want = {"FINALIZED"} if finalized else sn.DECIDED
    deadline = CLOCK() + (WAIT_SECONDS if seconds is None else seconds)
    last = None
    while True:
        try:
            rec = c.rpc("eth_getTransactionByHash", [tx_hash])
            last = rec.get("status") if isinstance(rec, dict) else None
            if last in want or last in TERMINAL_BAD:
                return rec
        except (RemoteRPCError, TransportError) as e:
            last = f"read error: {str(e)[:120]}"
        if CLOCK() >= deadline:
            raise Blocked(f"{tx_hash} did not reach {'FINALIZED' if finalized else 'a decided state'} "
                          f"within the bound; last observed: {last}")
        SLEEP(POLL_SECONDS)


# --------------------------------------------------------------------------
# live identity
# --------------------------------------------------------------------------
def require_chain(c):
    chain = int(c.rpc("eth_chainId", []), 16)
    if chain != sn.CHAIN_ID:
        raise Blocked(f"RPC reports chain {chain}, expected {sn.CHAIN_ID}")
    return chain


def require_gasless(c):
    gp = c.rpc("eth_gasPrice", [])
    if int(gp, 16) != 0:
        raise Blocked(f"eth_gasPrice is {gp}; this kit assumes gasless Studionet writes (0x0). Stopping.")
    return gp


def require_same_operators(state, ops):
    for k, v in op_dict(ops).items():
        if sn._addr(state["operators"][k]) != sn._addr(v):
            raise Blocked(f"{k} key now resolves to {v}, the case was set up with {state['operators'][k]}")


# --------------------------------------------------------------------------
# one guarded write
# --------------------------------------------------------------------------
def guarded_send(c, function, expect, call, journal=None):
    """
    Arm the boundary for exactly one transaction, run the SDK call, and return
    (hash, sent_at, sdk_seconds, sdk_error). sent_at is the transport's own
    timestamp taken immediately before eth_sendRawTransaction left this machine;
    latency is measured from it, not from when the SDK call returns (the SDK
    waits for an EVM receipt inside the call).
    """
    require_chain(c)
    slot = {"function": function, "sender": c.account.address, "expect": expect, "journal": journal}
    c.transport.arm(slot)
    sdk_error = None
    t0 = CLOCK()
    try:
        call()
    except Exception as e:           # the SDK may fail after the send (receipt/event parsing)
        sdk_error = e
    finally:
        c.transport.disarm()
    h = slot.get("hash")
    if not h:
        why = slot.get("rejection") or (f"{type(sdk_error).__name__}: {sdk_error}" if sdk_error else "no transaction sent")
        raise Blocked(f"{function} NOT SUBMITTED: {why}")
    if slot.get("rejection"):        # a second send was attempted after the first went out
        print(f"note: {slot['rejection']}")
    if slot.get("hash_mismatch"):
        raise Blocked(f"{function}: RPC returned hash {slot['hash_mismatch']} but keccak(raw) is {h}; "
                      "the journal keeps both. Stopping.")
    return h, slot["sent_at"], round(CLOCK() - t0, 2), sdk_error


def operator_write(c, contract, fn, args, log, journal=None):
    expect = {"sender": c.account.address, "recipient": contract, "method": fn, "args": args}
    h, sent_at, sdk_seconds, sdk_error = guarded_send(c, fn, expect, lambda: c.client.write_contract(
        address=sn.checksum(contract), function_name=fn, account=c.account, args=args, leader_only=False), journal)
    raw = wait_record(c, h)
    decided_at = CLOCK()
    fails = sn.check_call_binding(raw, fn, tx_hash=h, contract=contract, sender=c.account.address,
                                  method=fn, args=args) + sn.check_consensus(raw, fn)
    entry = {"function": fn, "sender": c.account.address, "hash": h,
             "submit_to_decided_seconds": round(decided_at - sent_at, 2),
             "sdk_call_seconds": sdk_seconds,
             "created_unix": sn.record_created(raw), "failures": fails,
             "sdk_post_send_error": str(sdk_error)[:300] if sdk_error else None}
    log.append(entry)
    if journal:
        journal.update(h, result="verified" if not fails else "failed", failures=fails)
    print(f"{fn:<22} {h}  decided in {entry['submit_to_decided_seconds']}s  {'OK' if not fails else 'FAILED'}")
    if fails:
        save("operator_log.json", log)
        raise Blocked(f"{fn} did not verify: " + "; ".join(fails))
    return entry


# --------------------------------------------------------------------------
# schema (read-only)
# --------------------------------------------------------------------------
def schema_problems(live, expected):
    p = []
    if not isinstance(live, dict) or not isinstance(live.get("methods"), dict):
        return ["schema response has no methods"]
    if (live.get("ctor") or {}).get("params") != expected["ctor"]["params"]:
        p.append(f"constructor {live.get('ctor')} != {expected['ctor']}")
    if set(live["methods"]) != set(expected["methods"]):
        p.append(f"methods differ: missing {sorted(set(expected['methods']) - set(live['methods']))}, "
                 f"extra {sorted(set(live['methods']) - set(expected['methods']))}")
    for name, m in expected["methods"].items():
        lm = live["methods"].get(name)
        if lm is None:
            continue
        for k in ("params", "readonly", "payable"):
            if lm.get(k) != m.get(k):
                p.append(f"{name}.{k}: {lm.get(k)!r} != {m.get(k)!r}")
    return p


def check_schema(c, payload):
    live = c.client.get_contract_schema_for_code(payload)
    expected = json.loads(EXPECTED_SCHEMA.read_text(encoding="utf-8"))
    return live, schema_problems(live, expected)


def cmd_schema(a):
    payload, info = sn.canonical_source_payload(CONTRACT.read_bytes())
    c = ctx()
    require_chain(c)
    live, problems = check_schema(c, payload)
    out = {"at": now_iso(), "rpc": RPC_URL, "source": info, "methods": len(live.get("methods", {})),
           "ctor": live.get("ctor"), "problems": problems}
    save("schema.json", out)
    print(json.dumps(out, indent=2, default=str))
    return EXIT_OK if not problems else EXIT_FAILED


# --------------------------------------------------------------------------
# preflight / setup
# --------------------------------------------------------------------------
def cmd_preflight(a):
    c = ctx()
    ops = operators()
    wallet = sn.checksum(a.wallet)
    if sn._addr(wallet) in {sn._addr(x.address) for x in ops}:
        raise Blocked("wallet must not be one of the operator accounts")
    payload, info = sn.canonical_source_payload(CONTRACT.read_bytes())
    out = {"at": now_iso(), "rpc": RPC_URL, "sdk": f"genlayer-py {sdk_version()}",
           "chain_id": require_chain(c), "gas_price": require_gasless(c),
           "consensus_target": sn.CONSENSUS_ADDRESS,
           "hosted_consensus_main": (c.rpc("sim_getConsensusContract", ["ConsensusMain"]) or {}).get("address"),
           "operators": op_dict(ops), "wallet": wallet, "source": info}
    try:
        out["finality_window_seconds"] = c.rpc("sim_getFinalityWindowTime", [])
    except (RemoteRPCError, TransportError) as e:
        out["finality_window_seconds"] = f"unavailable: {e}"
    _, problems = check_schema(c, payload)
    out["schema_problems"] = problems
    save("preflight.json", out)
    print(json.dumps(out, indent=2, default=str))
    if problems:
        raise Blocked("live schema does not match the reviewed port: " + "; ".join(problems))
    return EXIT_OK


def cmd_setup(a):
    prior = RUN / "progress.json"
    if prior.exists() and not a.new_case:
        p = json.loads(prior.read_text(encoding="utf-8"))
        if not p.get("complete"):
            raise Blocked("spike/studionet/run/progress.json records an unfinished case "
                          f"(contract {p.get('contract')}). Recover it with `recover`, "
                          "or pass --new-case to deliberately start another deployment.")
    ops = operators()
    owner, attester, flag = ops
    wallet = sn.checksum(a.wallet)
    pre = load("preflight.json")
    if pre.get("operators") != op_dict(ops) or sn._addr(pre.get("wallet")) != sn._addr(wallet):
        raise Blocked("operators or wallet differ from preflight; rerun preflight")
    if pre.get("schema_problems"):
        raise Blocked("preflight recorded schema problems; not deploying")
    c = ctx(owner)
    require_chain(c)
    require_gasless(c)
    payload, src_info = sn.canonical_source_payload(CONTRACT.read_bytes())
    _, problems = check_schema(c, payload)                 # again, immediately before deploying
    if problems:
        raise Blocked("live schema does not match the reviewed port: " + "; ".join(problems))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    policy_id, payment_id = f"gfl-sn-{stamp}", f"sn-{stamp}"
    journal = Journal(rpc=RPC_URL, chain_id=sn.CHAIN_ID, wallet=wallet, operators=op_dict(ops),
                      consensus_target=sn.CONSENSUS_ADDRESS, policy_id=policy_id, payment_id=payment_id,
                      amount=PAYMENT_AMOUNT, confirm_allowance_seconds=a.confirm_allowance, source=src_info)
    log = []

    flag_arg = sn.checksum(flag.address)
    expect = {"sender": owner.address, "recipient": None, "deploy_code": payload, "deploy_args": [flag_arg]}
    dep_hash, dep_sent_at, dep_sdk_seconds, sdk_error = guarded_send(c, "deploy", expect, lambda: c.client.deploy_contract(
        code=payload, account=owner, args=[flag_arg], leader_only=False), journal)
    journal.set(deploy_hash=dep_hash)
    dep_raw = wait_record(c, dep_hash)
    dep_decided_at = CLOCK()
    address = sn.deployed_address(dep_raw)
    if not address:
        journal.update(dep_hash, result="failed", failures=["no contract address in the record"])
        raise Blocked("deployment record names no single contract address")
    journal.set(contract=address)
    dep_fail = sn.check_deploy(dep_raw, dep_hash, owner.address, flag_arg) + sn.check_consensus(dep_raw, "deploy")
    code_fail = sn.check_deployed_code(c.rpc("gen_getContractCode", [address]))
    journal.update(dep_hash, result="verified" if not dep_fail + code_fail else "failed",
                   failures=dep_fail + code_fail)
    log.append({"function": "deploy", "hash": dep_hash, "sender": owner.address,
                "submit_to_decided_seconds": round(dep_decided_at - dep_sent_at, 2),
                "sdk_call_seconds": dep_sdk_seconds, "failures": dep_fail + code_fail,
                "sdk_post_send_error": str(sdk_error)[:300] if sdk_error else None})
    print(f"{'deploy':<22} {dep_hash}  -> {address}  {'OK' if not dep_fail + code_fail else 'FAILED'}")
    if dep_fail or code_fail:
        save("operator_log.json", log)
        raise Blocked("deployment identity: " + "; ".join(dep_fail + code_fail))

    operator_write(c, address, "register_attester", [sn.checksum(attester.address)], log, journal)
    operator_write(c, address, "fund_protection_pool", [PAYMENT_AMOUNT], log, journal)

    observed = [e["submit_to_decided_seconds"] for e in log if e["function"] != "deploy"]
    L = max(observed)
    ttl = math.ceil(2 * L + 2 * POLL_SECONDS + a.confirm_allowance)
    journal.set(ttl_seconds=ttl, ttl_inputs={"observed_submit_to_decided": observed, "poll_seconds": POLL_SECONDS,
                                             "confirm_allowance_seconds": a.confirm_allowance})
    print(f"observed submit->decided {observed}, max {L}s -> TTL {ttl}s "
          f"(includes {a.confirm_allowance}s stated confirmation allowance)")

    operator_write(c, address, "register_policy", [policy_id, text_exact(POLICY), text_exact(CHECKS), ttl],
                   log, journal)
    operator_write(c, address, "register_payment",
                   [payment_id, wallet, PAYMENT_AMOUNT, policy_id, "Brand identity package"], log, journal)
    save("operator_log.json", log)
    state = {
        "created": now_iso(), "rpc": RPC_URL, "chain_id": sn.CHAIN_ID, "contract": address,
        "deploy_hash": dep_hash, "source": src_info, "policy_id": policy_id, "payment_id": payment_id,
        "amount": PAYMENT_AMOUNT, "ttl_seconds": ttl, "ttl_inputs": journal.data["ttl_inputs"],
        "wallet": wallet, "operators": op_dict(ops), "consensus_target": sn.CONSENSUS_ADDRESS,
        "setup_transactions": {t["function"]: t["hash"] for t in journal.data["transactions"]},
        "live_state_verified": False,
    }
    save("state.json", state)
    journal.set(complete=True, state_file="state.json")
    return _verify_live_state(ctx(), state)


# --------------------------------------------------------------------------
# live state
# --------------------------------------------------------------------------
def _read_live_state(c, st):
    pid, addr = st["payment_id"], st["contract"]
    return {
        "status": read(c, addr, "get_status", [pid]),
        "has_attestation": read(c, addr, "has_attestation", [pid]),
        "protection_pool": read(c, addr, "get_protection_pool"),
        "max_attestation_age": read(c, addr, "get_max_attestation_age", [st["policy_id"]]),
        "required_check_ids": read(c, addr, "get_required_check_ids", [st["policy_id"]]),
        "is_attester": read(c, addr, "is_attester", [st["operators"]["attester"]]),
        "policy_text": read(c, addr, "get_policy", [st["policy_id"]]),
    }


def _expected_check_ids():
    return json.dumps([x["check_id"] for x in json.loads(text_exact(CHECKS))], separators=(",", ":"))


def _same(read_value, expected):
    if isinstance(expected, bool) or isinstance(read_value, bool):
        return read_value is expected
    if isinstance(expected, int):
        return isinstance(read_value, int) and read_value == expected
    return read_value == expected


def _verify_live_state(c, st):
    """Contract reads are the only thing that can mark a case usable."""
    try:
        values = _read_live_state(c, st)
    except Exception as e:
        st["live_state_verified"] = False
        st["live_state_error"] = {"at": now_iso(), "error": str(e)[:500]}
        save("state.json", st)
        raise Blocked("the transactions verified, but the contract state could not be read "
                      f"({str(e)[:200]}). The case is saved with live_state_verified=false and wallet "
                      "writes stay disabled. Run `check-state` once reads work.")
    expected = {"status": "REGISTERED", "has_attestation": False, "protection_pool": st["amount"],
                "max_attestation_age": st["ttl_seconds"], "required_check_ids": _expected_check_ids(),
                "is_attester": True, "policy_text": text_exact(POLICY)}
    mismatch = {k: {"read": values[k], "expected": v} for k, v in expected.items() if not _same(values[k], v)}
    st["live_state"] = {"at": now_iso(), "values": {k: v for k, v in values.items() if k != "policy_text"},
                        "policy_text_matches": "policy_text" not in mismatch, "mismatch": mismatch}
    st["live_state_verified"] = not mismatch
    st.pop("live_state_error", None)
    save("state.json", st)
    print(json.dumps(st["live_state"], indent=2, default=str))
    if mismatch:
        print("FAILED: live contract state does not match the case; wallet writes stay disabled.")
        return EXIT_FAILED
    print("Live state verified. NEXT: in the page, Load case -> Arm accept; then run: attest")
    return EXIT_OK


def cmd_check_state(a):
    st = load("state.json")
    attempts = max(1, min(int(a.attempts or 1), MAX_READ_ATTEMPTS))
    history = []
    for i in range(1, attempts + 1):
        c = ctx()
        try:
            require_chain(c)
            _read_live_state(c, st)
        except (RemoteRPCError, TransportError) as e:
            history.append({"attempt": i, "at": now_iso(), "error": str(e)[:300]})
            st["read_attempts"] = history
            save("state.json", st)
            print(f"attempt {i}/{attempts}: read failed: {str(e)[:160]}")
            if i < attempts:
                SLEEP(max(0.0, float(a.spacing)))
            continue
        history.append({"attempt": i, "at": now_iso(), "ok": True})
        st["read_attempts"] = history
        return _verify_live_state(c, st)
    return _verify_live_state(ctx(), st)


# --------------------------------------------------------------------------
# attest / flag
# --------------------------------------------------------------------------
def _require_verified(st):
    if st.get("live_state_verified") is not True:
        raise Blocked("live contract state is not verified for this case; run check-state first")


def cmd_attest(a):
    st = load("state.json")
    _require_verified(st)
    log = load("operator_log.json") if (RUN / "operator_log.json").exists() else []
    ops = operators()
    require_same_operators(st, ops)
    c = ctx(ops[1])
    require_chain(c)
    if read(c, st["contract"], "get_status", [st["payment_id"]]) != "REGISTERED":
        raise Blocked("payment is not REGISTERED")
    if read(c, st["contract"], "has_attestation", [st["payment_id"]]) is not False:
        raise Blocked("payment already carries an attestation")
    input("Confirm the page shows 'armed', then press Enter to attest... ")
    e = operator_write(c, st["contract"], "attest", [st["payment_id"], text_exact(ATTESTED)], log)
    if read(c, st["contract"], "has_attestation", [st["payment_id"]]) is not True:
        raise Blocked("attestation not visible on read-back")
    st["attest_hash"], st["attest_created_unix"] = e["hash"], e["created_unix"]
    save("state.json", st)
    save("operator_log.json", log)
    print("Attestation readable. The page now requests the accept_payment signature.")
    return EXIT_OK


def _age_seconds(t_from, t_to):
    if t_from is None or t_to is None:
        return None
    return round(float(t_to) - float(t_from), 2)


def cmd_flag(a):
    st = load("state.json")
    _require_verified(st)
    log = load("operator_log.json") if (RUN / "operator_log.json").exists() else []
    ops = operators()
    require_same_operators(st, ops)
    c = ctx(ops[2])
    require_chain(c)
    accept_hash = sn._hash(a.accept_hash)
    if accept_hash is None:
        raise Blocked("--accept-hash is not a 32-byte 0x hash")
    raw = wait_record(c, accept_hash)
    fails = sn.check_call_binding(raw, "accept", tx_hash=accept_hash, contract=st["contract"], sender=st["wallet"],
                                  method="accept_payment",
                                  args=[st["payment_id"], st["policy_id"], text_exact(ASSERTIONS)]) + \
        sn.check_consensus(raw, "accept")
    status = read(c, st["contract"], "get_status", [st["payment_id"]])
    fresh = read(c, st["contract"], "is_attestation_fresh", [st["payment_id"]])
    st["accept"] = {"hash": accept_hash, "failures": fails, "status_readback": status,
                    "attestation_fresh_readback": fresh,
                    "observed_age_seconds": _age_seconds(st.get("attest_created_unix"), sn.record_created(raw))}
    save("state.json", st)
    if fails:
        raise Blocked("accept_payment did not verify; not flagging: " + "; ".join(fails))
    if status != "ACCEPTED":
        raise Blocked(f"read-back status {status}, not ACCEPTED; not flagging")
    if fresh is not True:
        raise Blocked(f"attestation stale at acceptance (age {st['accept']['observed_age_seconds']}s, "
                      f"TTL {st['ttl_seconds']}s); the clean case cannot reach PROTECTED; not flagging")
    operator_write(c, st["contract"], "flag_payment", [st["payment_id"], "upstream exposure identified"], log)
    st["status_after_flag"] = read(c, st["contract"], "get_status", [st["payment_id"]])
    save("state.json", st)
    save("operator_log.json", log)
    if st["status_after_flag"] != "FLAGGED":
        raise Blocked(f"status after flag is {st['status_after_flag']}")
    print("FLAGGED. The page can now request the open_claim signature.")
    return EXIT_OK


# --------------------------------------------------------------------------
# verify (read-only)
# --------------------------------------------------------------------------
def collect_evidence(c, st, claim_hash):
    """Everything is fetched fresh; only identifiers come from the case file."""
    accept_hash = (st.get("accept") or {}).get("hash")
    ev = {"state": st, "accept_hash": accept_hash, "claim_hash": claim_hash,
          "assertions_text": text_exact(ASSERTIONS)}
    ev["chain_id"] = int(c.rpc("eth_chainId", []), 16)
    ev["deploy_raw"] = tx_record(c, st["deploy_hash"])
    ev["deployed_code_b64"] = c.rpc("gen_getContractCode", [sn.checksum(st["contract"])])
    waits = {}
    for name, h in (("accept_raw", accept_hash), ("claim_raw", claim_hash)):
        if not h:
            ev[name] = None
            continue
        try:
            ev[name] = wait_record(c, h, finalized=True)
        except Blocked as e:
            waits[name] = str(e)
            ev[name] = tx_record(c, h)
    ev["finality_waits"] = waits
    pid, addr = st["payment_id"], st["contract"]
    try:
        ev["final"] = {
            "status": read(c, addr, "get_status", [pid]),
            "verdict": read(c, addr, "get_verdict", [pid]),
            "paid_out": read(c, addr, "get_paid_out", [pid]),
            "claim_balance_wallet": read(c, addr, "get_claim_balance", [st["wallet"]]),
            "attestation_fresh": read(c, addr, "is_attestation_fresh", [pid]),
            "protection_pool": read(c, addr, "get_protection_pool"),
            "reasoning": read(c, addr, "get_reasoning", [pid]),
        }
    except Exception as e:                   # no read, no pass
        ev["final"] = {}
        ev["final_read_error"] = str(e)[:300]
    return ev


def cmd_verify(a):
    report = {"at": now_iso(), "result": "BLOCKED", "claim_hash": a.claim_hash}
    try:
        st = load("state.json")
        report["state"] = st
        claim = sn._hash(a.claim_hash)
        if claim is None:
            raise Blocked("--claim-hash is not a 32-byte 0x hash")
        ev = collect_evidence(ctx(), st, claim)
        checks, ok = sn.evaluate(ev)
        report.update({
            "checks": {k: {"passed": not v, "failures": v} for k, v in checks.items()},
            "failed_checks": [k for k, v in checks.items() if v],
            "final_state": ev["final"], "final_read_error": ev.get("final_read_error"),
            "finality_waits": ev.get("finality_waits"), "result": "PASSED" if ok else "FAILED",
        })
        code = EXIT_OK if ok else EXIT_FAILED
    except (Blocked, sn.EvidenceError, sn.SourceIdentityError) as e:
        report["blocked_reason"] = str(e)
        code = EXIT_BLOCKED
    except Exception as e:
        report["blocked_reason"] = f"unexpected error: {e}"
        report["traceback"] = traceback.format_exc()
        code = EXIT_BLOCKED
    report["exit_code"] = code
    save("report.json", report)
    print(json.dumps({k: report.get(k) for k in ("result", "failed_checks", "blocked_reason", "final_state")},
                     indent=2, default=str))
    print(f"\nSTUDIONET KIT {report['result']} (exit {code}) - full diagnostics: spike/studionet/run/report.json")
    return code


# --------------------------------------------------------------------------
# recover (read-only): rebuild state.json from run/progress.json
# --------------------------------------------------------------------------
SETUP_ORDER = ["deploy", "register_attester", "fund_protection_pool", "register_policy", "register_payment"]


def cmd_recover(a):
    p = load("progress.json")
    by = {}
    for t in p.get("transactions", []):
        if t["function"] in by:
            raise Blocked(f"progress.json lists {t['function']} more than once")
        by[t["function"]] = sn._hash(t["hash"])
    missing = [f for f in SETUP_ORDER if not by.get(f)]
    if missing:
        raise Blocked(f"progress.json has no hash for: {', '.join(missing)} (setup did not get that far)")
    wallet = sn.checksum(a.wallet)
    if sn._addr(p.get("wallet")) != sn._addr(wallet):
        raise Blocked(f"--wallet {wallet} is not the journal's wallet {p.get('wallet')}")
    ops, ttl = p.get("operators") or {}, p.get("ttl_seconds")
    if not isinstance(ttl, int) or ttl <= 0:
        raise Blocked("progress.json has no TTL; the case cannot be reconstructed")
    c = ctx()
    require_chain(c)
    recs = {f: tx_record(c, by[f]) for f in SETUP_ORDER}
    contract = sn.deployed_address(recs["deploy"])
    fails = []
    if not contract:
        fails.append("deploy record names no single contract address")
    elif p.get("contract") and sn._addr(p["contract"]) != sn._addr(contract):
        fails.append(f"journal contract {p['contract']} != deployed {contract}")
    owner, att, flag = ops.get("owner_payer"), ops.get("attester"), ops.get("flag_authority")
    fails += sn.check_deploy(recs["deploy"], by["deploy"], owner, flag) + sn.check_consensus(recs["deploy"], "deploy")
    if contract:
        fails += sn.check_deployed_code(c.rpc("gen_getContractCode", [contract]))
        expected = {
            "register_attester": [sn.checksum(att)],
            "fund_protection_pool": [PAYMENT_AMOUNT],
            "register_policy": [p["policy_id"], text_exact(POLICY), text_exact(CHECKS), ttl],
            "register_payment": [p["payment_id"], wallet, PAYMENT_AMOUNT, p["policy_id"], "Brand identity package"],
        }
        for f, args in expected.items():
            fails += sn.check_call_binding(recs[f], f, tx_hash=by[f], contract=contract, sender=owner,
                                           method=f, args=args) + sn.check_consensus(recs[f], f)
    save("recovery_evidence.json", {"at": now_iso(), "hashes": by, "records": recs, "failures": fails})
    if fails:
        raise Blocked("recovery refused: " + "; ".join(fails[:8]))
    if (RUN / "state.json").exists():
        old = json.loads((RUN / "state.json").read_text(encoding="utf-8"))
        if sn._addr(old.get("contract")) != sn._addr(contract) or old.get("payment_id") != p["payment_id"]:
            raise Blocked("spike/studionet/run/state.json describes a different case; move it aside first")
    _, src_info = sn.canonical_source_payload(CONTRACT.read_bytes())
    state = {"created": now_iso(), "recovered": True, "rpc": RPC_URL, "chain_id": sn.CHAIN_ID,
             "contract": contract, "deploy_hash": by["deploy"], "source": src_info,
             "policy_id": p["policy_id"], "payment_id": p["payment_id"], "amount": PAYMENT_AMOUNT,
             "ttl_seconds": ttl, "wallet": wallet, "operators": ops, "consensus_target": sn.CONSENSUS_ADDRESS,
             "setup_transactions": by, "live_state_verified": False}
    save("state.json", state)
    p["complete"] = True
    save("progress.json", p)
    print("Recovered from 5 verified transactions; evidence: spike/studionet/run/recovery_evidence.json")
    return _verify_live_state(c, state)


COMMANDS = {"schema": cmd_schema, "preflight": cmd_preflight, "setup": cmd_setup, "check-state": cmd_check_state,
            "attest": cmd_attest, "flag": cmd_flag, "verify": cmd_verify, "recover": cmd_recover}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("schema", help="check the reviewed port against Studionet's schema generator (no writes, no keys)")
    s = sub.add_parser("preflight"); s.add_argument("--wallet", required=True)
    s = sub.add_parser("setup"); s.add_argument("--wallet", required=True)
    s.add_argument("--confirm-allowance", type=int, required=True,
                   help="seconds you allow to click Confirm in the wallet; reported separately")
    s.add_argument("--new-case", action="store_true",
                   help="start a new deployment although progress.json records an unfinished one")
    s = sub.add_parser("check-state", help="read the contract state; enable the case only if it matches (no writes)")
    s.add_argument("--attempts", type=int, default=1, help=f"retry failed reads up to N times (max {MAX_READ_ATTEMPTS})")
    s.add_argument("--spacing", type=float, default=2.0)
    sub.add_parser("attest")
    s = sub.add_parser("flag"); s.add_argument("--accept-hash", required=True)
    s = sub.add_parser("verify"); s.add_argument("--claim-hash", required=True)
    s = sub.add_parser("recover", help="rebuild state.json from run/progress.json (no writes)")
    s.add_argument("--wallet", required=True)
    a = p.parse_args(argv)
    try:
        return COMMANDS[a.cmd](a)
    except (Blocked, sn.SourceIdentityError, sn.EvidenceError) as e:
        print("BLOCKED:", e)
        save(f"{a.cmd}_blocked.json", {"at": now_iso(), "command": a.cmd, "reason": str(e)})
        return EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())

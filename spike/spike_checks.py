"""
Pure verification logic for the external-wallet spike.

No network access and no keys. operator_spike.py feeds it raw Studio records
(gen_getStudioTransactionByHash(hash, true)) and contract reads; the offline
tests feed it fixtures. Every check returns a list of failure strings; an
empty list means it passed. Missing or unrecognised evidence is a failure.

Record schema
-------------
normalize_record() is the only place that reads raw record field names.
  * Observed on studio-dev (2026-10-13): tx_id, sender, recipient, status,
    txExecutionResultName, execution_mode, leader_only, an electorate of 5,
    and sim_config = {"signed_rollup_transaction": ...}.
  * genlayer-studio v0.123.0-rc.5 names (legacy aliases): hash, from_address,
    to_address, created_at, num_of_initial_validators.
  If a record carries two names for the same field with different values,
  it is rejected.

Signed envelope
---------------
sim_config.signed_rollup_transaction is the raw EVM transaction the sender
signed. It is the strongest available binding: Studio's transaction hash is
keccak(raw) (rc.5 consensus_service.generate_transaction_hash), the signer is
recoverable, and the calldata names the GenLayer sender, recipient, method,
arguments and execution mode. Simulation overrides in sim_config
(genvm_datetime, genvm_executor_selector, validators) are rejected when set;
any other key is rejected as unsupported.
"""

import base64
import hashlib
from datetime import datetime

EXPECTED_SOURCE_SHA256 = (
    "2e93a95d5cb084965c31e343677d15d8f6885a4a68590a10e36d6609c52054d2"
)
EXPECTED_CHAIN_ID = 61997
TX_TYPE_DEPLOY = 1
TX_TYPE_CALL = 2
DECIDED_ACCEPTED_STATUSES = {"ACCEPTED", "FINALIZED"}
ZERO_ADDRESS = "0x" + "00" * 20

SIM_EVIDENCE_KEYS = {"signed_rollup_transaction"}
SIM_OVERRIDE_KEYS = {"genvm_datetime", "genvm_executor_selector", "validators"}

# canonical name -> raw names, observed live schema first
RECORD_ALIASES = {
    "tx_hash": ("tx_id", "hash"),
    "sender": ("sender", "from_address"),
    "recipient": ("recipient", "to_address"),
    "electorate": ("num_of_initial_validators", "electorate"),
    "created": ("created_timestamp", "created_at"),
}
RECORD_PLAIN = ("type", "status", "txExecutionResultName", "execution_mode", "leader_only",
                "sim_config", "data", "consensus_data", "consensus_history")


class SourceIdentityError(Exception):
    pass


class EvidenceError(Exception):
    """Evidence is missing or cannot be interpreted. Blocks verification."""


# --------------------------------------------------------------------------
# Source identity
# --------------------------------------------------------------------------
def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def canonical_source_payload(raw: bytes, expected_sha=EXPECTED_SOURCE_SHA256):
    """
    Return (payload_bytes, info). payload_bytes is exactly what gets deployed
    and always hashes to expected_sha. A CRLF checkout is accepted only if
    CRLF->LF yields the reviewed bytes exactly; anything else is refused.
    """
    info = {"raw_sha256": sha256(raw), "line_endings_normalized": False}
    if sha256(raw) == expected_sha:
        payload = raw
    else:
        if b"\r\n" not in raw:
            raise SourceIdentityError(
                f"contract source sha256 {info['raw_sha256']} != reviewed {expected_sha}")
        normalized = raw.replace(b"\r\n", b"\n")
        if b"\r" in normalized:
            raise SourceIdentityError("contract source contains a lone CR; refusing")
        if sha256(normalized) != expected_sha:
            raise SourceIdentityError(
                "contract source differs from the reviewed commit even after CRLF->LF")
        payload = normalized
        info["line_endings_normalized"] = True
    try:
        payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as e:
        raise SourceIdentityError(f"contract source is not valid UTF-8: {e}")
    info["payload_sha256"] = sha256(payload)
    assert info["payload_sha256"] == expected_sha
    return payload, info


def b64_sha256(value):
    if not isinstance(value, str) or not value:
        raise EvidenceError("expected a non-empty base64 string")
    try:
        return sha256(base64.b64decode(value, validate=True))
    except Exception as e:
        raise EvidenceError(f"not valid base64: {e}")


# --------------------------------------------------------------------------
# Record adapter
# --------------------------------------------------------------------------
def _addr(a):
    if not isinstance(a, str):
        return None
    a = a.strip().lower()
    return a if a.startswith("0x") and len(a) == 42 else None


def checksum(a):
    """
    EIP-55 form of an address. Studio looks contracts up by exact string
    (ContractSnapshot: CurrentState.id == address), so every address sent to
    the RPC must be the checksum form; a lowercase address is "not found".
    """
    low = _addr(a)
    if low is None:
        raise EvidenceError(f"not a 20-byte 0x address: {a!r}")
    from eth_utils import to_checksum_address
    return to_checksum_address(low)


def _hash(h):
    if isinstance(h, (bytes, bytearray)):
        return "0x" + bytes(h).hex()
    if not isinstance(h, str):
        return None
    h = h.strip().lower()
    return h if h.startswith("0x") and len(h) == 66 else None


def _unix(v):
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip()
    if s.isdigit():
        return float(s)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise EvidenceError(f"unrecognised timestamp {v!r}")


def _int(v):
    if isinstance(v, bool):
        raise EvidenceError(f"expected an integer, got {v!r}")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise EvidenceError(f"expected an integer, got {v!r}")


_NORMALIZERS = {"tx_hash": _hash, "sender": _addr, "recipient": _addr,
                "electorate": _int, "created": _unix}


def normalize_record(raw):
    """Map a raw Studio record onto canonical names. Conflicting aliases raise."""
    if not isinstance(raw, dict):
        raise EvidenceError("no transaction record")
    out = {}
    for canon, names in RECORD_ALIASES.items():
        seen = {}
        for n in names:
            val = raw.get(n)
            if val is None or val == "":
                continue                      # absent; a deploy has no recipient
            v = _NORMALIZERS[canon](val)
            if v is None:
                raise EvidenceError(f"field {n!r} is not a valid value: {val!r}")
            seen[n] = v
        values = set(seen.values())
        if canon == "created" and len(seen) > 1:
            vs = list(seen.values())
            if max(vs) - min(vs) > 1.0:
                raise EvidenceError(f"conflicting aliases for {canon}: {seen}")
            values = {vs[0]}
        if len(values) > 1:
            raise EvidenceError(f"conflicting aliases for {canon}: {seen}")
        out[canon] = next(iter(values)) if values else None
    for k in RECORD_PLAIN:
        out[k] = raw.get(k)
    out["_aliases_used"] = {c: [n for n in names if n in raw] for c, names in RECORD_ALIASES.items()}
    return out


# --------------------------------------------------------------------------
# GenVM calldata and the signed envelope
# --------------------------------------------------------------------------
def _decoder():
    from genlayer_py.abi.calldata.decoder import decode
    return decode


def decode_genvm_calldata(raw: bytes, decode=None):
    try:
        obj = (decode or _decoder())(raw)
    except Exception as e:
        raise EvidenceError(f"calldata is not decodable GenVM calldata: {e}")
    if not isinstance(obj, dict):
        raise EvidenceError("decoded calldata is not an object")
    return obj


def decode_calldata(b64: str, decode=None):
    if not isinstance(b64, str) or not b64:
        raise EvidenceError("transaction has no calldata")
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as e:
        raise EvidenceError(f"calldata is not base64: {e}")
    return decode_genvm_calldata(raw, decode)


def _keccak(b):
    from eth_utils import keccak
    return keccak(b)


_FEES = "(uint256,uint256,uint256,uint256,uint256,uint256,uint256[],uint256,uint256,uint256)"
_ALLOC = "(uint8,bool,uint256,address,bytes32,uint256,bytes)"
_PARAMS = f"(address,address,uint256,uint256,uint256,uint256,uint256,{_FEES},bytes,{_ALLOC}[])"
CONSENSUS_SIGNATURES = {
    # name -> (kind, abi types); selectors computed at import
    f"addTransaction({_PARAMS})": ("fees", [_PARAMS]),
    f"deploySalted({_PARAMS})": ("fees_deploy", [_PARAMS]),
    "addTransaction(address,address,uint256,uint256,bytes,uint256)":
        ("v6", ["address", "address", "uint256", "uint256", "bytes", "uint256"]),
    "addTransaction(address,address,uint256,uint256,bytes)":
        ("v5", ["address", "address", "uint256", "uint256", "bytes"]),
}
SELECTORS = {_keccak(sig.encode())[:4].hex(): (sig, kind, types)
             for sig, (kind, types) in CONSENSUS_SIGNATURES.items()}


def _decode_raw_evm_tx(raw: bytes):
    try:
        return _decode_raw_evm_tx_unchecked(raw)
    except EvidenceError:
        raise
    except Exception as e:
        raise EvidenceError(f"signed envelope is not a decodable EVM transaction: {e}")


def _decode_raw_evm_tx_unchecked(raw: bytes):
    import rlp
    from eth_account import Account
    if not raw:
        raise EvidenceError("empty signed envelope")
    if raw[0] >= 0xC0:
        f = rlp.decode(raw)
        if len(f) != 9:
            raise EvidenceError("legacy envelope has unexpected field count")
        to, value, data, v = f[3], f[4], f[5], int.from_bytes(f[6], "big")
        if v in (27, 28):
            raise EvidenceError("envelope is not EIP-155 replay protected (no chain id)")
        chain_id = (v - 35) // 2
        kind = "legacy"
    elif raw[0] in (1, 2):
        f = rlp.decode(raw[1:])
        if raw[0] == 2 and len(f) == 12:
            chain_id, to, value, data = int.from_bytes(f[0], "big"), f[5], f[6], f[7]
        elif raw[0] == 1 and len(f) == 11:
            chain_id, to, value, data = int.from_bytes(f[0], "big"), f[4], f[5], f[6]
        else:
            raise EvidenceError("typed envelope has unexpected field count")
        kind = f"type{raw[0]}"
    else:
        raise EvidenceError(f"unsupported envelope type byte {raw[0]}")
    try:
        signer = Account.recover_transaction(raw)
    except Exception as e:
        raise EvidenceError(f"envelope signature does not recover: {e}")
    return {"kind": kind, "chain_id": chain_id, "to": "0x" + to.hex() if to else None,
            "value": int.from_bytes(value, "big"), "data": data,
            "signer": signer.lower(), "hash": "0x" + _keccak(raw).hex()}


def _decode_inner(tx_data: bytes, deploy: bool):
    """rlp([calldata, mode]) for calls, rlp([code, calldata, mode]) for deploys."""
    import rlp
    try:
        items = rlp.decode(tx_data)
    except Exception as e:
        raise EvidenceError(f"inner transaction data is not RLP: {e}")
    want = 3 if deploy else 2
    if not isinstance(items, list) or len(items) != want or not all(isinstance(i, bytes) for i in items):
        raise EvidenceError(f"inner transaction data is not a {want}-item RLP list")
    mode = items[-1]
    if mode not in (b"", b"\x00"):
        raise EvidenceError(f"inner execution mode/leader_only is {mode.hex() or '0'}, not normal")
    return {"code": items[0] if deploy else None, "calldata": items[-2]}


def decode_envelope(raw_hex):
    """Fully decode a signed rollup transaction into what it commits to."""
    from eth_abi import decode as abi_decode
    if not isinstance(raw_hex, str) or not raw_hex.startswith("0x"):
        raise EvidenceError("signed envelope missing or not 0x-hex")
    try:
        raw = bytes.fromhex(raw_hex[2:])
    except ValueError:
        raise EvidenceError("signed envelope is not hex")
    env = _decode_raw_evm_tx(raw)
    data = env["data"]
    sel = data[:4].hex()
    if sel not in SELECTORS:
        raise EvidenceError(f"envelope calls unrecognised consensus selector 0x{sel}")
    sig, kind, types = SELECTORS[sel]
    try:
        args = abi_decode(types, data[4:])
    except Exception as e:
        raise EvidenceError(f"envelope calldata does not ABI-decode as {sig}: {e}")
    if kind in ("fees", "fees_deploy"):
        p = args[0]
        gl_sender, gl_recipient, user_value, tx_data = p[0], p[1], p[6], p[8]
    else:
        gl_sender, gl_recipient, tx_data, user_value = args[0], args[1], args[4], 0
    is_deploy = kind == "fees_deploy" or _addr(gl_recipient) == ZERO_ADDRESS
    inner = _decode_inner(tx_data, is_deploy)
    env.update({"function": sig, "gl_sender": gl_sender.lower(), "gl_recipient": gl_recipient.lower(),
                "user_value": user_value, "is_deploy": is_deploy, **inner})
    return env


def check_sim_config(sim_config, label):
    """Returns (failures, envelope_hex_or_None)."""
    if sim_config in (None, {}):
        return [], None
    if not isinstance(sim_config, dict):
        return [f"{label}: sim_config is not an object: {sim_config!r}"], None
    f = []
    for k, v in sim_config.items():
        if k in SIM_EVIDENCE_KEYS:
            continue
        if k in SIM_OVERRIDE_KEYS:
            if v not in (None, [], {}, ""):
                f.append(f"{label}: simulation override sim_config.{k} = {str(v)[:80]!r}")
        else:
            f.append(f"{label}: unsupported sim_config key {k!r}")
    return f, sim_config.get("signed_rollup_transaction")


def check_envelope(rec, label, *, tx_hash, sender, consensus, chain_id=EXPECTED_CHAIN_ID):
    """Validate the signed envelope against the record. Returns (failures, env)."""
    f, raw_hex = check_sim_config(rec.get("sim_config"), label)
    if raw_hex is None:
        return f + [f"{label}: signed envelope (sim_config.signed_rollup_transaction) missing"], None
    try:
        env = decode_envelope(raw_hex)
    except EvidenceError as e:
        return f + [f"{label}: {e}"], None
    if env["hash"] != _hash(tx_hash):
        f.append(f"{label}: keccak(envelope) {env['hash']} != transaction hash {tx_hash}")
    if env["chain_id"] != chain_id:
        f.append(f"{label}: envelope chain id {env['chain_id']} != {chain_id}")
    if _addr(env["to"]) != _addr(consensus):
        f.append(f"{label}: envelope target {env['to']} is not the consensus contract {consensus}")
    if sender is not None and env["signer"] != _addr(sender):
        f.append(f"{label}: envelope signed by {env['signer']}, expected {sender}")
    if env["gl_sender"] != env["signer"]:
        f.append(f"{label}: envelope names GenLayer sender {env['gl_sender']} but was signed by {env['signer']}")
    if rec.get("sender") != env["signer"]:
        f.append(f"{label}: record sender {rec.get('sender')} != envelope signer {env['signer']}")
    if env["user_value"] != 0:
        f.append(f"{label}: envelope transfers user value {env['user_value']}")
    return f, env


# --------------------------------------------------------------------------
# Consensus
# --------------------------------------------------------------------------
def consensus_outcome(votes, electorate=None):
    """Reimplements Studio's determine_consensus_from_votes (rc.5)."""
    vs = [str(v).lower() for v in votes]
    total = len(vs) if electorate is None else max(len(vs), int(electorate))
    majority = total / 2
    agree, disagree = vs.count("agree"), vs.count("disagree")
    timeout = vs.count("timeout") + vs.count("idle")
    dv = vs.count("deterministic_violation")
    if agree > majority:
        return "MAJORITY_AGREE"
    if disagree > majority:
        return "MAJORITY_DISAGREE"
    if timeout > majority:
        return "TIMEOUT"
    if dv > majority:
        return "DETERMINISTIC_VIOLATION"
    return "NO_MAJORITY"


def check_consensus(raw, label, require_finalized=False):
    """Outcome, quorum, execution and mode. sim_config is checked by check_envelope."""
    try:
        rec = normalize_record(raw)
    except EvidenceError as e:
        return [f"{label}: {e}"]
    f = []
    ok_status = {"FINALIZED"} if require_finalized else DECIDED_ACCEPTED_STATUSES
    if rec["status"] not in ok_status:
        f.append(f"{label}: status {rec['status']!r} not in {sorted(ok_status)}")
    if rec["leader_only"] is not False:
        f.append(f"{label}: leader_only is {rec['leader_only']!r}, expected False")
    if rec["execution_mode"] != "NORMAL":
        f.append(f"{label}: execution_mode {rec['execution_mode']!r}, expected 'NORMAL'")
    if rec["txExecutionResultName"] != "FINISHED_WITH_RETURN":
        f.append(f"{label}: txExecutionResultName {rec['txExecutionResultName']!r}")
    cd = rec["consensus_data"]
    if not isinstance(cd, dict):
        return f + [f"{label}: consensus_data missing"]
    votes = cd.get("votes")
    if not isinstance(votes, dict) or not votes:
        return f + [f"{label}: no validator votes recorded"]
    if rec["electorate"] is None:
        f.append(f"{label}: electorate size missing")
    outcome = consensus_outcome(votes.values(), rec["electorate"])
    if outcome != "MAJORITY_AGREE":
        f.append(f"{label}: recomputed consensus {outcome} from votes {votes} (electorate {rec['electorate']})")
    leader = cd.get("leader_receipt")
    lr = leader[0] if isinstance(leader, list) and leader else leader
    if not isinstance(lr, dict):
        f.append(f"{label}: leader receipt missing")
    elif str(lr.get("execution_result", "")).upper() != "SUCCESS":
        f.append(f"{label}: leader execution_result {lr.get('execution_result')!r}")
    hist = rec["consensus_history"]
    rounds = hist.get("consensus_results") if isinstance(hist, dict) else None
    if not isinstance(rounds, list) or not rounds:
        f.append(f"{label}: consensus history missing")
    elif rounds[-1].get("consensus_round") != "Accepted":
        f.append(f"{label}: last consensus round {rounds[-1].get('consensus_round')!r}, expected 'Accepted'")
    return f


# --------------------------------------------------------------------------
# Binding a supplied hash to the expected call
# --------------------------------------------------------------------------
def check_call_binding(raw, label, *, tx_hash, contract, sender, method, args, consensus,
                       decode=None):
    try:
        rec = normalize_record(raw)
    except EvidenceError as e:
        return [f"{label}: {e}"]
    f = []
    if rec["tx_hash"] != _hash(tx_hash):
        f.append(f"{label}: record hash {rec['tx_hash']!r} != supplied {tx_hash!r}")
    if rec["type"] is not None and rec["type"] != TX_TYPE_CALL:
        f.append(f"{label}: transaction type {rec['type']!r}, expected contract call ({TX_TYPE_CALL})")
    if rec["recipient"] != _addr(contract):
        f.append(f"{label}: target {rec['recipient']!r} is not the spike contract {contract}")
    if rec["sender"] != _addr(sender):
        f.append(f"{label}: sender {rec['sender']!r} is not {sender}")

    env_f, env = check_envelope(rec, label, tx_hash=tx_hash, sender=sender, consensus=consensus)
    f += env_f
    if env is None:
        return f
    if env["is_deploy"]:
        return f + [f"{label}: envelope is a deployment, expected a contract call"]
    if env["gl_recipient"] != _addr(contract):
        f.append(f"{label}: envelope recipient {env['gl_recipient']} is not the spike contract {contract}")
    try:
        call = decode_genvm_calldata(env["calldata"], decode)
    except EvidenceError as e:
        return f + [f"{label}: envelope {e}"]

    # The record's own decoded calldata, if present, must agree with the envelope.
    data = rec["data"]
    stored = data.get("calldata") if isinstance(data, dict) else None
    if stored is not None:
        try:
            if base64.b64decode(stored, validate=True) != env["calldata"]:
                f.append(f"{label}: record calldata differs from the signed envelope")
        except Exception:
            f.append(f"{label}: record calldata is not base64")

    if call.get("") != method:
        f.append(f"{label}: method {call.get('')!r}, expected {method!r}")
    if call.get("kwargs"):
        f.append(f"{label}: unexpected kwargs {call.get('kwargs')!r}")
    got = call.get("args", [])
    if list(got) != list(args):
        f.append(f"{label}: arguments do not match the case "
                 f"(got {len(got)} arg(s); first {str(got[:1])[:120]})")
    return f


def check_deploy(raw, deploy_hash, sender, consensus, expected_sha=EXPECTED_SOURCE_SHA256):
    try:
        rec = normalize_record(raw)
    except EvidenceError as e:
        return [f"deploy: {e}"]
    f = []
    if rec["tx_hash"] != _hash(deploy_hash):
        f.append("deploy: record hash does not match")
    if rec["type"] is not None and rec["type"] != TX_TYPE_DEPLOY:
        f.append(f"deploy: transaction type {rec['type']!r}, expected deploy")
    if rec["sender"] != _addr(sender):
        f.append(f"deploy: sender {rec['sender']!r} is not owner {sender}")
    data = rec["data"] if isinstance(rec["data"], dict) else {}
    if data.get("contract_code") is not None:
        try:
            if b64_sha256(data.get("contract_code")) != expected_sha:
                f.append("deploy: record contract_code does not hash to the reviewed source")
        except EvidenceError as e:
            f.append(f"deploy: record contract_code {e}")
    env_f, env = check_envelope(rec, "deploy", tx_hash=deploy_hash, sender=sender, consensus=consensus)
    f += env_f
    if env is not None:
        if not env["is_deploy"]:
            f.append("deploy: envelope is not a deployment")
        elif sha256(env["code"]) != expected_sha:
            f.append(f"deploy: signed envelope code sha256 {sha256(env['code'])} != reviewed {expected_sha}")
    return f


def check_deployed_code(code_b64, expected_sha=EXPECTED_SOURCE_SHA256):
    try:
        got = b64_sha256(code_b64)
    except EvidenceError as e:
        return [f"deployed code: {e}"]
    return [] if got == expected_sha else [f"deployed code sha256 {got} != reviewed {expected_sha}"]


def inspect_transaction(raw, label, *, tx_hash, consensus, decode=None):
    """
    Derive what a recorded transaction actually committed to, from its signed
    envelope, without assuming any expected values. Used by recovery. Returns
    (failures, info). Every structural check that does not depend on an
    expected value is applied here: record/alias consistency, envelope hash,
    chain, consensus target, signature, signer == GenLayer sender == record
    sender, normal mode, no simulation overrides, consensus outcome.
    """
    try:
        rec = normalize_record(raw)
    except EvidenceError as e:
        return [f"{label}: {e}"], None
    f = []
    if rec["tx_hash"] != _hash(tx_hash):
        f.append(f"{label}: record hash {rec['tx_hash']!r} != supplied {tx_hash!r}")
    env_f, env = check_envelope(rec, label, tx_hash=tx_hash, sender=None, consensus=consensus)
    f += env_f + check_consensus(raw, label)
    if env is None:
        return f, None
    info = {"hash": _hash(tx_hash), "signer": env["signer"], "is_deploy": env["is_deploy"],
            "record_recipient": rec["recipient"], "created": rec["created"]}
    try:
        call = decode_genvm_calldata(env["calldata"], decode)
    except EvidenceError as e:
        return f + [f"{label}: envelope {e}"], info
    if call.get("kwargs"):
        f.append(f"{label}: unexpected kwargs")
    info["args"] = list(call.get("args", []))
    if env["is_deploy"]:
        info["code_sha256"] = sha256(env["code"])
        if call.get(""):
            f.append(f"{label}: deployment calldata names a method {call.get('')!r}")
    else:
        info["method"] = call.get("")
        info["contract"] = env["gl_recipient"]
        if rec["recipient"] != env["gl_recipient"]:
            f.append(f"{label}: record recipient {rec['recipient']} != envelope recipient {env['gl_recipient']}")
    return f, info


def record_created(raw):
    try:
        return normalize_record(raw)["created"]
    except EvidenceError:
        return None


# --------------------------------------------------------------------------
# Final evaluation
# --------------------------------------------------------------------------
def evaluate(ev, decode=None):
    st = ev["state"]
    c, w, pid = st["contract"], st["wallet"], st["payment_id"]
    consensus = st.get("hosted_consensus_main")
    checks = {}
    checks["chain"] = [] if ev.get("chain_id") == EXPECTED_CHAIN_ID else [
        f"chain id {ev.get('chain_id')!r} != {EXPECTED_CHAIN_ID}"]
    checks["consensus_contract"] = [] if _addr(ev.get("hosted_consensus_main")) == _addr(consensus) and _addr(consensus) else [
        f"hosted consensus contract {ev.get('hosted_consensus_main')!r} != case {consensus!r}"]
    checks["deploy_payload"] = check_deploy(ev.get("deploy_raw"), st.get("deploy_hash"),
                                            st["operators"]["owner_payer"], consensus)
    checks["deployed_code_identity"] = check_deployed_code(ev.get("deployed_code_b64"))
    distinct = []
    if not ev.get("accept_hash") or not ev.get("claim_hash"):
        distinct.append("accept and claim hashes are both required")
    elif _hash(ev["accept_hash"]) == _hash(ev["claim_hash"]):
        distinct.append("accept and claim hashes are the same transaction")
    checks["distinct_hashes"] = distinct
    checks["accept_binding"] = check_call_binding(
        ev.get("accept_raw"), "accept", tx_hash=ev.get("accept_hash"), contract=c, sender=w,
        method="accept_payment", args=[pid, st["policy_id"], ev.get("assertions_text")],
        consensus=consensus, decode=decode)
    checks["accept_consensus"] = check_consensus(ev.get("accept_raw"), "accept")
    checks["claim_binding"] = check_call_binding(
        ev.get("claim_raw"), "claim", tx_hash=ev.get("claim_hash"), contract=c, sender=w,
        method="open_claim", args=[pid], consensus=consensus, decode=decode)
    checks["claim_consensus"] = check_consensus(ev.get("claim_raw"), "claim", require_finalized=True)

    fin = ev.get("final") or {}
    amt = st.get("amount")
    s = []
    if fin.get("status") != "RESOLVED":
        s.append(f"status {fin.get('status')!r} != 'RESOLVED'")
    if fin.get("verdict") != "PROTECTED":
        s.append(f"verdict {fin.get('verdict')!r} != 'PROTECTED'")
    for k in ("paid_out", "claim_balance_wallet"):
        try:
            if int(fin.get(k)) != int(amt):
                s.append(f"{k} {fin.get(k)!r} != {amt}")
        except (TypeError, ValueError):
            s.append(f"{k} missing or not an integer: {fin.get(k)!r}")
    if fin.get("attestation_fresh") is not True:
        s.append(f"attestation_fresh {fin.get('attestation_fresh')!r}")
    checks["final_state"] = s
    return checks, all(not v for v in checks.values())

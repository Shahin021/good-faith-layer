"""
Studionet (chain 61999) verification profile for the GFL wallet kit.

No network, no keys. Every check returns a list of failure strings; an empty
list means it passed; missing or unrecognised evidence is a failure.

Neutral helpers come from the 61997 kit's spike/spike_checks.py, imported
unchanged (record normalisation, envelope decoding, signature recovery,
GenVM calldata decoding, vote counting). Everything that depends on the
deployment is defined here instead, and is at least as strict as the 61997
checks:

  chain             EIP-155 chain id 61999 inside the signed envelope. Studio
                    v0.121 does not check it server-side, so this is the only
                    chain binding.
  consensus target  the envelope's `to` must be CONSENSUS_ADDRESS, the static
                    studionet address in genlayer-js 1.1.8 (and genlayer-py
                    0.18.0). Studionet's own sim_getConsensusContract reports
                    the zero address; that endpoint is deprecated in favour of
                    the SDK chain config, so the SDK address is the pin.
  encoding          v5 addTransaction(address,address,uint256,uint256,bytes)
                    only; any other selector is refused.
  value             EVM value 0 (v5 has no GenLayer value field; Studio v0.121
                    takes value from the EVM envelope) and GenLayer value 0.
  mode              inner RLP flag empty or 0x00 (NORMAL); record
                    leader_only False and execution_mode NORMAL.
  calldata          GenVM v0.2.16 dispatch key "method"; the keys must be
                    exactly {"method", "args"} (args omitted when empty);
                    a "" key, kwargs or any other key is refused; method and
                    args must equal the expected call; the record's stored
                    calldata must be present and byte-identical.
  execution         Studionet records carry no txExecutionResultName. It is
                    derived with Studio rc.7's own rule from the leader
                    receipt (SUCCESS -> FINISHED_WITH_RETURN, timeouts ->
                    TIMEOUT). If a record does carry the field it must agree.
  majority          strict AGREE majority over max(cast votes, electorate);
                    electorate required. Stricter than v0.121's own rule.
  finality          FINALIZED where required (claim and, at verify time,
                    accept); decided (ACCEPTED/FINALIZED) for setup steps.
  source identity   deployed code (in the signed envelope, in the record if
                    present, and gen_getContractCode) hashes to
                    PORT_SOURCE_SHA256, the reviewed port of the original.
"""

import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import spike_checks as sc  # noqa: E402  (the 61997 kit's checks, unchanged)

EvidenceError = sc.EvidenceError
SourceIdentityError = sc.SourceIdentityError

CHAIN_ID = 61999
CONSENSUS_ADDRESS = "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575"
V5_SIGNATURE = "addTransaction(address,address,uint256,uint256,bytes)"
V5_SELECTOR = "27241a99"
ORIGINAL_SOURCE_SHA256 = sc.EXPECTED_SOURCE_SHA256
PORT_SOURCE_SHA256 = "b6d7bb600370c8b8ab91bc4339206283ffb46e0006cd92a96ae0942ad9631d79"
DECIDED = {"ACCEPTED", "FINALIZED"}
TX_TYPE_DEPLOY, TX_TYPE_CALL = 1, 2

checksum = sc.checksum
_addr = sc._addr
_hash = sc._hash
normalize_record = sc.normalize_record
record_created = sc.record_created


def canonical_source_payload(raw: bytes):
    """Exact bytes to deploy; refuses anything but the reviewed port (CRLF checkouts normalised)."""
    return sc.canonical_source_payload(raw, expected_sha=PORT_SOURCE_SHA256)


def decode_envelope(raw_hex):
    env = sc.decode_envelope(raw_hex)
    if env["function"] != V5_SIGNATURE:
        raise EvidenceError(f"envelope uses {env['function']}, expected v5 {V5_SIGNATURE}")
    return env


# --------------------------------------------------------------------------
# Envelope
# --------------------------------------------------------------------------
def precheck_raw(raw_hex, *, sender, recipient, method=None, args=None, deploy_code=None, deploy_args=None):
    """
    Check a signed transaction BEFORE it is sent (operator boundary). Same rules
    as the recorded-envelope checks, minus the record. Returns (failures, env).
    """
    try:
        env = decode_envelope(raw_hex)
    except EvidenceError as e:
        return [str(e)], None
    f = _envelope_rules(env, "pre-send", sender)
    if deploy_code is not None:
        if not env["is_deploy"]:
            f.append("pre-send: not a deployment")
        else:
            if sc.sha256(env["code"]) != PORT_SOURCE_SHA256:
                f.append("pre-send: deployment code is not the reviewed port")
            f += _constructor_rules(env["calldata"], deploy_args, "pre-send")
    else:
        if env["is_deploy"]:
            f.append("pre-send: unexpected deployment")
        else:
            if env["gl_recipient"] != _addr(recipient):
                f.append(f"pre-send: GenLayer recipient {env['gl_recipient']} is not {recipient}")
            f += _call_rules(env["calldata"], method, args, "pre-send")
    return f, env


def _envelope_rules(env, label, sender):
    f = []
    if env["chain_id"] != CHAIN_ID:
        f.append(f"{label}: envelope chain id {env['chain_id']} != {CHAIN_ID}")
    if _addr(env["to"]) != _addr(CONSENSUS_ADDRESS):
        f.append(f"{label}: envelope target {env['to']} is not the consensus contract {CONSENSUS_ADDRESS}")
    if sender is not None and env["signer"] != _addr(sender):
        f.append(f"{label}: envelope signed by {env['signer']}, expected {sender}")
    if env["gl_sender"] != env["signer"]:
        f.append(f"{label}: envelope names GenLayer sender {env['gl_sender']} but was signed by {env['signer']}")
    if env["value"] != 0:
        f.append(f"{label}: envelope moves EVM value {env['value']}")
    if env["user_value"] != 0:
        f.append(f"{label}: envelope transfers GenLayer value {env['user_value']}")
    return f


def check_envelope(rec, label, *, tx_hash, sender):
    """rec is a normalized record. Returns (failures, env)."""
    f, raw_hex = sc.check_sim_config(rec.get("sim_config"), label)
    if raw_hex is None:
        return f + [f"{label}: signed envelope (sim_config.signed_rollup_transaction) missing"], None
    try:
        env = decode_envelope(raw_hex)
    except EvidenceError as e:
        return f + [f"{label}: {e}"], None
    if env["hash"] != _hash(tx_hash):
        f.append(f"{label}: keccak(envelope) {env['hash']} != transaction hash {tx_hash}")
    f += _envelope_rules(env, label, sender)
    if rec.get("sender") != env["signer"]:
        f.append(f"{label}: record sender {rec.get('sender')} != envelope signer {env['signer']}")
    return f, env


# --------------------------------------------------------------------------
# GenVM calldata (v0.2.16 convention)
# --------------------------------------------------------------------------
def _call_rules(calldata, method, args, label, decode=None):
    try:
        call = sc.decode_genvm_calldata(calldata, decode)
    except EvidenceError as e:
        return [f"{label}: {e}"]
    f = []
    keys = set(call)
    if "" in keys:
        f.append(f"{label}: calldata uses the v0.6 '' method key")
    extra = keys - {"method", "args"}
    if extra:
        f.append(f"{label}: unexpected calldata keys {sorted(extra)}")
    if call.get("method") != method:
        f.append(f"{label}: method {call.get('method')!r}, expected {method!r}")
    got = list(call.get("args", []))
    if got != list(args or []):
        f.append(f"{label}: arguments do not match the case (got {len(got)} arg(s); first {str(got[:1])[:120]})")
    return f


def _constructor_rules(calldata, args, label, decode=None):
    try:
        call = sc.decode_genvm_calldata(calldata, decode)
    except EvidenceError as e:
        return [f"{label}: constructor {e}"]
    f = []
    if set(call) != {"args"}:
        f.append(f"{label}: constructor calldata keys {sorted(call)} != ['args']")
    if list(call.get("args", [])) != list(args or []):
        f.append(f"{label}: constructor arguments {call.get('args')!r} != {args!r}")
    return f


# --------------------------------------------------------------------------
# Consensus / execution / finality
# --------------------------------------------------------------------------
def execution_result_name(consensus_data):
    """Studio rc.7 TransactionsProcessor._execution_result_fields, applied to a v0.121 record."""
    lr = (consensus_data or {}).get("leader_receipt") if isinstance(consensus_data, dict) else None
    lr = lr[0] if isinstance(lr, list) and lr else lr
    if not isinstance(lr, dict):
        return "NOT_VOTED"
    gr = lr.get("genvm_result")
    if isinstance(gr, dict) and str(gr.get("error_code") or "") in {
            "CONSENSUS_LEADER_EXEC_TIMEOUT", "CONSENSUS_VALIDATOR_EXEC_TIMEOUT"}:
        return "TIMEOUT"
    v = str(lr.get("execution_result") or "").upper()
    if v == "SUCCESS":
        return "FINISHED_WITH_RETURN"
    if v in {"ERROR", "FAILURE", "FINISHEDWITHERROR", "FINISHED_WITH_ERROR"}:
        return "FINISHED_WITH_ERROR"
    return "NOT_VOTED"


def check_consensus(raw, label, require_finalized=False):
    try:
        rec = normalize_record(raw)
    except EvidenceError as e:
        return [f"{label}: {e}"]
    f = []
    ok = {"FINALIZED"} if require_finalized else DECIDED
    if rec["status"] not in ok:
        f.append(f"{label}: status {rec['status']!r} not in {sorted(ok)}")
    if rec["leader_only"] is not False:
        f.append(f"{label}: leader_only is {rec['leader_only']!r}, expected False")
    if rec["execution_mode"] != "NORMAL":
        f.append(f"{label}: execution_mode {rec['execution_mode']!r}, expected 'NORMAL'")
    cd = rec["consensus_data"]
    derived = execution_result_name(cd)
    if derived != "FINISHED_WITH_RETURN":
        f.append(f"{label}: execution {derived} (from the leader receipt)")
    stated = rec.get("txExecutionResultName")
    if stated is not None and stated != derived:
        f.append(f"{label}: record says {stated!r} but the leader receipt gives {derived!r}")
    if not isinstance(cd, dict):
        return f + [f"{label}: consensus_data missing"]
    votes = cd.get("votes")
    if not isinstance(votes, dict) or not votes:
        return f + [f"{label}: no validator votes recorded"]
    if rec["electorate"] is None:
        f.append(f"{label}: electorate size missing")
    outcome = sc.consensus_outcome(votes.values(), rec["electorate"])
    if outcome != "MAJORITY_AGREE":
        f.append(f"{label}: recomputed consensus {outcome} from votes {votes} (electorate {rec['electorate']})")
    hist = rec["consensus_history"]
    rounds = hist.get("consensus_results") if isinstance(hist, dict) else None
    if not isinstance(rounds, list) or not rounds:
        f.append(f"{label}: consensus history missing")
    elif rounds[-1].get("consensus_round") != "Accepted":
        f.append(f"{label}: last consensus round {rounds[-1].get('consensus_round')!r}, expected 'Accepted'")
    return f


# --------------------------------------------------------------------------
# Binding a hash to the expected call / deployment
# --------------------------------------------------------------------------
def check_call_binding(raw, label, *, tx_hash, contract, sender, method, args, decode=None):
    try:
        rec = normalize_record(raw)
    except EvidenceError as e:
        return [f"{label}: {e}"]
    f = []
    if rec["tx_hash"] != _hash(tx_hash):
        f.append(f"{label}: record hash {rec['tx_hash']!r} != supplied {tx_hash!r}")
    if rec["type"] != TX_TYPE_CALL:
        f.append(f"{label}: transaction type {rec['type']!r}, expected contract call ({TX_TYPE_CALL})")
    if rec["recipient"] != _addr(contract):
        f.append(f"{label}: target {rec['recipient']!r} is not the case contract {contract}")
    if rec["sender"] != _addr(sender):
        f.append(f"{label}: sender {rec['sender']!r} is not {sender}")
    env_f, env = check_envelope(rec, label, tx_hash=tx_hash, sender=sender)
    f += env_f
    if env is None:
        return f
    if env["is_deploy"]:
        return f + [f"{label}: envelope is a deployment, expected a contract call"]
    if env["gl_recipient"] != _addr(contract):
        f.append(f"{label}: envelope recipient {env['gl_recipient']} is not the case contract {contract}")
    f += _call_rules(env["calldata"], method, args, label, decode)
    data = rec["data"]
    stored = data.get("calldata") if isinstance(data, dict) else None
    if stored is None:
        f.append(f"{label}: record has no stored calldata to compare with the envelope")
    else:
        try:
            if base64.b64decode(stored, validate=True) != env["calldata"]:
                f.append(f"{label}: record calldata differs from the signed envelope")
        except Exception:
            f.append(f"{label}: record calldata is not base64")
    return f


def _code_sha(value):
    """Record contract_code as base64 (rc line) or as source text; either must hash to the port."""
    if not isinstance(value, str) or not value:
        raise EvidenceError("record contract_code is empty or not a string")
    try:
        return sc.sha256(base64.b64decode(value, validate=True))
    except Exception:
        return sc.sha256(value.encode("utf-8"))


def check_deploy(raw, deploy_hash, owner, flag_authority, decode=None):
    try:
        rec = normalize_record(raw)
    except EvidenceError as e:
        return [f"deploy: {e}"]
    f = []
    if rec["tx_hash"] != _hash(deploy_hash):
        f.append("deploy: record hash does not match")
    if rec["type"] != TX_TYPE_DEPLOY:
        f.append(f"deploy: transaction type {rec['type']!r}, expected deploy ({TX_TYPE_DEPLOY})")
    if rec["sender"] != _addr(owner):
        f.append(f"deploy: sender {rec['sender']!r} is not owner {owner}")
    data = rec["data"] if isinstance(rec["data"], dict) else {}
    if data.get("contract_code") is not None:
        try:
            if _code_sha(data["contract_code"]) != PORT_SOURCE_SHA256:
                f.append("deploy: record contract_code does not hash to the reviewed port")
        except EvidenceError as e:
            f.append(f"deploy: {e}")
    env_f, env = check_envelope(rec, "deploy", tx_hash=deploy_hash, sender=owner)
    f += env_f
    if env is not None:
        if not env["is_deploy"]:
            f.append("deploy: envelope is not a deployment")
        else:
            if sc.sha256(env["code"]) != PORT_SOURCE_SHA256:
                f.append(f"deploy: signed code sha256 {sc.sha256(env['code'])} != reviewed port {PORT_SOURCE_SHA256}")
            f += _constructor_rules(env["calldata"], [checksum(flag_authority)] if flag_authority else None,
                                    "deploy", decode)
    return f


def check_deployed_code(code_b64):
    try:
        got = sc.b64_sha256(code_b64)
    except EvidenceError as e:
        return [f"deployed code: {e}"]
    return [] if got == PORT_SOURCE_SHA256 else [f"deployed code sha256 {got} != reviewed port {PORT_SOURCE_SHA256}"]


def deployed_address(raw):
    """Contract address Studio assigned to a deployment (record to_address, or data.contract_address)."""
    try:
        rec = normalize_record(raw)
    except EvidenceError:
        return None
    data = rec["data"] if isinstance(rec["data"], dict) else {}
    cands = {a for a in (_addr(data.get("contract_address")), rec["recipient"]) if a}
    if len(cands) != 1:
        return None
    return checksum(next(iter(cands)))


# --------------------------------------------------------------------------
# Final evaluation
# --------------------------------------------------------------------------
def evaluate(ev, decode=None):
    st = ev["state"]
    c, w, pid = st["contract"], st["wallet"], st["payment_id"]
    ops = st["operators"]
    checks = {}
    checks["chain"] = [] if ev.get("chain_id") == CHAIN_ID else [f"chain id {ev.get('chain_id')!r} != {CHAIN_ID}"]
    checks["deploy_payload"] = check_deploy(ev.get("deploy_raw"), st.get("deploy_hash"), ops["owner_payer"],
                                            ops["flag_authority"], decode) + \
        check_consensus(ev.get("deploy_raw"), "deploy")
    checks["deployed_code_identity"] = check_deployed_code(ev.get("deployed_code_b64"))
    distinct = []
    if not ev.get("accept_hash") or not ev.get("claim_hash"):
        distinct.append("accept and claim hashes are both required")
    elif _hash(ev["accept_hash"]) == _hash(ev["claim_hash"]):
        distinct.append("accept and claim hashes are the same transaction")
    checks["distinct_hashes"] = distinct
    checks["accept_binding"] = check_call_binding(
        ev.get("accept_raw"), "accept", tx_hash=ev.get("accept_hash"), contract=c, sender=w,
        method="accept_payment", args=[pid, st["policy_id"], ev.get("assertions_text")], decode=decode)
    checks["accept_consensus"] = check_consensus(ev.get("accept_raw"), "accept", require_finalized=True)
    checks["claim_binding"] = check_call_binding(
        ev.get("claim_raw"), "claim", tx_hash=ev.get("claim_hash"), contract=c, sender=w,
        method="open_claim", args=[pid], decode=decode)
    checks["claim_consensus"] = check_consensus(ev.get("claim_raw"), "claim", require_finalized=True)

    fin = ev.get("final") or {}
    amt = st.get("amount")
    s = []
    if ev.get("final_read_error"):
        s.append(f"final contract state could not be read: {ev['final_read_error']}")
    if fin.get("status") != "RESOLVED":
        s.append(f"status {fin.get('status')!r} != 'RESOLVED'")
    if fin.get("verdict") != "PROTECTED":
        s.append(f"verdict {fin.get('verdict')!r} != 'PROTECTED'")
    for k in ("paid_out", "claim_balance_wallet"):
        v = fin.get(k)
        if isinstance(v, bool) or not isinstance(v, int) or v != int(amt):
            s.append(f"{k} {v!r} != {amt}")
    if fin.get("attestation_fresh") is not True:
        s.append(f"attestation_fresh {fin.get('attestation_fresh')!r}")
    checks["final_state"] = s
    return checks, all(not v for v in checks.values())


def load_v5_abi():
    return json.loads((HERE / "consensus_main_v5_abi.json").read_text(encoding="utf-8"))

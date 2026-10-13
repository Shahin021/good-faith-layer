"""
Read-only feasibility probe for stable Studionet (chain 61999).

    python spike/studionet-gate/probe_studionet.py [--tx HASH]... [--address ADDR]... [--max-tx 40]

It answers the Phase 1 questions that need the live deployment:
  * Do recorded NORMAL contract calls carry a signed envelope we can bind
    (keccak == hash, EIP-155 chain 61999, signer == sender, target, v5
    selector, inner mode NORMAL, EVM value 0, exact GenVM calldata)?
  * Do they carry validator votes that recompute to a majority AGREE over
    max(votes, electorate), a SUCCESS leader receipt, an Accepted last round
    and FINALIZED status?
  * Gas price and finality window (funding/fee behaviour).
  * Which runner the sampled live contract pins (for the port's Depends line).

Safety: every JSON-RPC method must be in READ_ONLY_METHODS; params may never
contain sim_config; no keys, no writes, no funding, no admin or validator
calls. <= MAX_REQUESTS requests, TIMEOUT_S each, responses capped at
MAX_BYTES, no retries.

Writes spike/studionet-gate/run/probe_studionet.json (requests, trimmed
responses, per-transaction analysis) and prints a summary. Exit 0 when the
probe ran (whatever it found), 2 when it could not run.
"""
import argparse
import base64
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import spike_checks as sc  # noqa: E402

RUN = HERE / "run"
DEFAULT_RPC = "https://studio.genlayer.com/api"
CHAIN_ID = 61999
SDK_CONSENSUS = "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575"  # genlayer-js 1.1.8 / genlayer-py 0.18.0 studionet
SEED_TX = "0xee647f1cf6e0c2b1eff565e50c7b07fbfe48f8c631860c8708f26f61c8d1635c"
SEED_CONTRACT = "0xBD64275533268F61D02C348Ab39Fe9eCC2F32A65"
V5_SIG = "addTransaction(address,address,uint256,uint256,bytes)"

READ_ONLY_METHODS = frozenset({
    "eth_chainId", "eth_gasPrice", "eth_blockNumber", "eth_getBlockByNumber",
    "eth_getTransactionByHash", "sim_getTransactionsForAddress", "sim_getFinalityWindowTime",
    "sim_countValidators", "sim_getConsensusContract", "gen_getContractCode",
})
MAX_REQUESTS, TIMEOUT_S, MAX_BYTES, MAX_TX_CAP = 80, 20, 16 * 1024 * 1024, 120


class Refused(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class Prober:
    def __init__(self, rpc_url, session=None):
        import requests
        self.rpc_url = rpc_url
        self.http = session or requests.Session()
        self.log = []
        self.next_id = 0

    def rpc(self, method, params):
        if method not in READ_ONLY_METHODS:
            raise Refused(f"{method} is not on the read-only allowlist")
        if "sim_config" in json.dumps(params):
            raise Refused("sim_config is refused in params")
        if len(self.log) >= MAX_REQUESTS:
            raise Refused(f"request budget of {MAX_REQUESTS} exhausted")
        self.next_id += 1
        body = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        entry = {"at": now(), "request": body}
        t0 = time.time()
        result = None
        try:
            r = self.http.post(self.rpc_url, json=body, timeout=TIMEOUT_S, stream=True,
                               headers={"Content-Type": "application/json"})
            entry["http_status"] = r.status_code
            buf = bytearray()
            for chunk in r.iter_content(65536):
                buf += chunk
                if len(buf) > MAX_BYTES:
                    r.close()
                    raise Refused(f"response exceeds {MAX_BYTES} bytes")
            entry["bytes"] = len(buf)
            j = json.loads(buf.decode("utf-8"))
            if isinstance(j, dict) and j.get("error"):
                entry["outcome"], entry["error"] = "remote_error", j["error"]
            elif isinstance(j, dict) and "result" in j:
                entry["outcome"] = "ok"
                result = j["result"]
                s = json.dumps(result, default=str)
                entry["result"] = result if len(s) < 2000 else f"<{len(s)} chars; analysed below>"
            else:
                entry["outcome"] = "malformed"
        except Refused as e:
            entry.update(outcome="refused", error=str(e))
        except ValueError as e:
            entry.update(outcome="not_json", error=str(e)[:200])
        except Exception as e:
            entry.update(outcome="transport_error", error=str(e)[:300])
        entry["seconds"] = round(time.time() - t0, 3)
        self.log.append(entry)
        return entry["outcome"], result


# ---------------------------------------------------------------------------
# Analysis of one record under the Studionet profile (no network)
# ---------------------------------------------------------------------------
def execution_result_name(consensus_data):
    """Same rule as Studio rc.7 TransactionsProcessor._execution_result_fields."""
    lr = (consensus_data or {}).get("leader_receipt")
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


def analyse(rec):
    """Every check the existing verifier makes, with Studionet's constants and
    calldata key, plus EVM value 0 for v5. Returns a dict; 'binding' and
    'consensus' are lists of failures (empty = pass)."""
    out = {"hash": rec.get("hash"), "type": rec.get("type"), "status": rec.get("status"),
           "leader_only": rec.get("leader_only"), "execution_mode": rec.get("execution_mode"),
           "from": rec.get("from_address"), "to": rec.get("to_address")}
    sim = rec.get("sim_config")
    out["sim_config_keys"] = sorted(sim) if isinstance(sim, dict) else sim
    out["has_envelope"] = isinstance(sim, dict) and isinstance(sim.get("signed_rollup_transaction"), str)
    out["normal_call"] = (rec.get("type") == 2 and rec.get("leader_only") is False
                          and rec.get("execution_mode") in (None, "NORMAL"))

    b = []
    sim_f, raw_hex = sc.check_sim_config(sim, "sim_config")
    b += sim_f
    env = None
    if raw_hex is None:
        b.append("no signed envelope (sim_config.signed_rollup_transaction)")
    else:
        try:
            env = sc.decode_envelope(raw_hex)
        except sc.EvidenceError as e:
            b.append(str(e))
    if env:
        out["envelope"] = {"to": env["to"], "chain_id": env["chain_id"], "kind": env["kind"],
                           "function": env["function"], "signer": env["signer"], "evm_value": env["value"]}
        if env["hash"] != str(rec.get("hash", "")).lower():
            b.append("keccak(envelope) != transaction hash")
        if env["chain_id"] != CHAIN_ID:
            b.append(f"envelope chain {env['chain_id']} != {CHAIN_ID}")
        if env["function"] != V5_SIG:
            b.append(f"envelope function {env['function']} is not v5")
        if env["signer"] != str(rec.get("from_address", "")).lower():
            b.append("envelope signer != record sender")
        if env["gl_sender"] != env["signer"]:
            b.append("GenLayer sender in envelope != signer")
        if env["gl_recipient"] != str(rec.get("to_address", "")).lower():
            b.append("envelope recipient != record recipient")
        if env["value"] != 0 or env["user_value"] != 0:
            b.append(f"envelope moves value (EVM {env['value']}, GenLayer {env['user_value']})")
        if env["is_deploy"]:
            b.append("envelope is a deployment")
        else:
            try:
                call = sc.decode_genvm_calldata(env["calldata"])
                out["calldata_keys"] = sorted(call)
                out["method"] = call.get("method")
                if "" in call:
                    b.append("calldata uses the v0.6 '' method key")
                if not isinstance(call.get("method"), str):
                    b.append("calldata has no 'method'")
                if set(call) - {"method", "args", "kwargs"}:
                    b.append(f"unexpected calldata keys {sorted(set(call) - {'method', 'args', 'kwargs'})}")
            except sc.EvidenceError as e:
                b.append(str(e))
            data = rec.get("data")
            stored = data.get("calldata") if isinstance(data, dict) else None
            if stored is None:
                b.append("record has no calldata to compare")
            else:
                try:
                    if base64.b64decode(stored, validate=True) != env["calldata"]:
                        b.append("record calldata differs from the signed envelope")
                except Exception:
                    b.append("record calldata is not base64")
        out["envelope_target_is_sdk_consensus"] = sc._addr(env["to"]) == sc._addr(SDK_CONSENSUS)
        if not out["envelope_target_is_sdk_consensus"]:   # mandatory: part of the binding
            b.append(f"envelope target {env['to']} is not the SDK consensus contract {SDK_CONSENSUS}")
    out["binding"] = b

    c = []
    if rec.get("status") != "FINALIZED":
        c.append(f"status {rec.get('status')!r}, not FINALIZED")
    if not out["normal_call"]:
        c.append("not a NORMAL contract call")
    cd = rec.get("consensus_data")
    votes = cd.get("votes") if isinstance(cd, dict) else None
    electorate = rec.get("num_of_initial_validators")
    out["votes"] = votes
    out["electorate"] = electorate
    if not isinstance(votes, dict) or not votes:
        c.append("no validator votes recorded")
    else:
        if electorate is None:
            c.append("electorate size missing")
        outcome = sc.consensus_outcome(votes.values(), electorate)
        out["recomputed"] = outcome
        if outcome != "MAJORITY_AGREE":
            c.append(f"recomputed {outcome}")
    name = execution_result_name(cd if isinstance(cd, dict) else None)
    out["execution_result_name"] = name
    if name != "FINISHED_WITH_RETURN":
        c.append(f"execution {name}")
    hist = rec.get("consensus_history")
    rounds = hist.get("consensus_results") if isinstance(hist, dict) else None
    if not isinstance(rounds, list) or not rounds:
        c.append("consensus history missing")
    else:
        out["last_round"] = rounds[-1].get("consensus_round")
        if out["last_round"] != "Accepted":
            c.append(f"last round {out['last_round']!r}")
    out["consensus"] = c
    out["fully_verifiable"] = not b and not c
    return out


def summarise(analyses):
    normal = [a for a in analyses if a["normal_call"]]
    return {
        "records": len(analyses),
        "normal_calls": len(normal),
        "normal_with_envelope": sum(a["has_envelope"] for a in normal),
        "normal_binding_pass": sum(not a["binding"] for a in normal),
        "normal_consensus_pass": sum(not a["consensus"] for a in normal),
        "fully_verifiable": [a["hash"] for a in normal if a["fully_verifiable"]],
        "envelope_targets": sorted({(a.get("envelope") or {}).get("to") or "-" for a in analyses}),
        "sim_config_key_sets": sorted({json.dumps(a["sim_config_keys"]) for a in analyses}),
        "leader_only_or_other": len(analyses) - len(normal),
    }


def trim(rec):
    r = dict(rec)
    r.pop("contract_snapshot", None)
    s = json.dumps(r, default=str)
    if len(s) > 20000:
        for k in ("consensus_data", "consensus_history"):
            if k in r:
                r[k] = f"<{len(json.dumps(r[k], default=str))} chars; see analysis>"
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rpc", default=DEFAULT_RPC)
    ap.add_argument("--tx", action="append", default=[], help="extra transaction hash (repeatable)")
    ap.add_argument("--address", action="append", default=[], help="extra address to list (repeatable)")
    ap.add_argument("--max-tx", type=int, default=40)
    ap.add_argument("--out", default=str(RUN / "probe_studionet.json"))
    a = ap.parse_args(argv)
    if not 1 <= a.max_tx <= MAX_TX_CAP:
        print(f"--max-tx must be 1..{MAX_TX_CAP}", file=sys.stderr)
        return 2

    p = Prober(a.rpc)
    facts = {}
    for key, method, params in [
        ("chain_id", "eth_chainId", []), ("gas_price", "eth_gasPrice", []),
        ("block_number", "eth_blockNumber", []), ("finality_window", "sim_getFinalityWindowTime", []),
        ("validators", "sim_countValidators", []),
        ("consensus_main", "sim_getConsensusContract", ["ConsensusMain"]),
    ]:
        outcome, result = p.rpc(method, params)
        facts[key] = result if outcome == "ok" else {"outcome": outcome, "error": p.log[-1].get("error")}
    if facts["chain_id"] != hex(CHAIN_ID):
        print(f"RPC reports chain {facts['chain_id']!r}, expected {hex(CHAIN_ID)}; stopping", file=sys.stderr)
        _write(a.out, p, facts, [], {})
        return 2
    if isinstance(facts["consensus_main"], dict):
        cm = facts["consensus_main"]
        facts["consensus_main"] = {"address": cm.get("address"),
                                   "addTransaction_inputs": [len(e.get("inputs", [])) for e in cm.get("abi", [])
                                                             if e.get("name") == "addTransaction"]}

    outcome, code = p.rpc("gen_getContractCode", [SEED_CONTRACT])
    if outcome == "ok" and isinstance(code, str):
        try:
            text = base64.b64decode(code).decode("utf-8", "replace") if not code.lstrip().startswith("#") else code
        except Exception:
            text = code
        facts["seed_contract_depends"] = [ln.strip() for ln in text.splitlines()[:5] if "Depends" in ln]

    records = {}

    def add(rec):
        if isinstance(rec, dict) and isinstance(rec.get("hash"), str):
            records.setdefault(rec["hash"].lower(), rec)

    outcome, blk = p.rpc("eth_getBlockByNumber", ["latest", True])
    if outcome == "ok" and isinstance(blk, dict):
        for t in blk.get("transactions") or []:
            add(t)
    for h in [SEED_TX, *a.tx]:
        outcome, rec = p.rpc("eth_getTransactionByHash", [h])
        add(rec)
    addrs = [SEED_CONTRACT, *a.address]
    seed = records.get(SEED_TX)
    if seed and seed.get("from_address"):
        addrs.append(seed["from_address"])
    for addr in dict.fromkeys(sc.checksum(x) for x in addrs):
        if len(records) >= a.max_tx:
            break
        outcome, recs = p.rpc("sim_getTransactionsForAddress", [addr])
        if outcome == "ok" and isinstance(recs, list):
            facts.setdefault("listed", {})[addr] = len(recs)
            # newest first if the server gives timestamps
            recs = sorted(recs, key=lambda r: str(r.get("created_at", "")), reverse=True)
            for r in recs:
                if len(records) >= a.max_tx:
                    break
                add(r)

    analyses = [analyse(r) for r in records.values()]
    summary = summarise(analyses)
    _write(a.out, p, facts, analyses, summary, records)
    print(json.dumps({"facts": facts, "summary": summary}, indent=1, default=str))
    print(verdict(facts, summary))
    return 0


def verdict(facts, s):
    lines = []
    lines.append(f"gas price {facts.get('gas_price')!r}: "
                 + ("writes are gasless; a zero-balance MetaMask account can sign"
                    if facts.get("gas_price") == "0x0" else "NOT zero; funding behaviour must be re-examined"))
    if s.get("fully_verifiable"):
        lines.append(f"ITEM 3 EVIDENCE PRESENT: {len(s['fully_verifiable'])} NORMAL call(s) pass every binding "
                     f"and consensus check (e.g. {s['fully_verifiable'][0]}).")
    elif s.get("normal_calls", 0) == 0:
        lines.append("ITEM 3 NOT SETTLED: no NORMAL contract call found in the sample. Supply --tx/--address.")
    else:
        lines.append(f"ITEM 3 BLOCKER: {s['normal_calls']} NORMAL call(s) sampled, none fully verifiable "
                     f"({s['normal_with_envelope']} with an envelope, {s['normal_binding_pass']} binding-clean, "
                     f"{s['normal_consensus_pass']} consensus-clean). See per-transaction failures.")
    return "\n".join(lines)


def _write(path, p, facts, analyses, summary, records=None):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {"created": now(), "facts": facts, "summary": summary, "analyses": analyses,
           "records": [trim(r) for r in (records or {}).values()], "requests": p.log}
    out.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(f"evidence: {out}", file=sys.stderr)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        sys.exit(2)

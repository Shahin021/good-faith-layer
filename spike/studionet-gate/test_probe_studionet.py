"""Offline tests for probe_studionet.py: safety rails and the per-record analysis.

Records are synthetic but carry real envelopes produced by genlayer-js 1.1.8
(sdk/envelopes.json); the record layout follows Studio v0.121
TransactionsProcessor._parse_transaction_data.
"""
import base64
import copy
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import probe_studionet as ps  # noqa: E402
import spike_checks as sc  # noqa: E402

ENVELOPES = HERE / "sdk" / "envelopes.json"
needs_env = pytest.mark.skipif(not ENVELOPES.exists(), reason="run `node --test` in sdk/ first")


def envs():
    return json.loads(ENVELOPES.read_text())


def record(e, **over):
    try:
        calldata = sc.decode_envelope(e["raw"])["calldata"]
    except sc.EvidenceError:  # leader-only: the verifier refuses to decode further
        calldata = b""
    r = {
        "hash": e["hash"], "type": 2, "status": "FINALIZED",
        "from_address": sc.checksum(e["signer"]), "to_address": sc.checksum(e["contract"]),
        "data": {"calldata": base64.b64encode(calldata).decode()},
        "leader_only": e["leaderOnly"], "execution_mode": "LEADER_ONLY" if e["leaderOnly"] else "NORMAL",
        "num_of_initial_validators": 5,
        "consensus_data": {"votes": {f"0x{i:040x}": "agree" for i in range(5)},
                           "leader_receipt": [{"execution_result": "SUCCESS", "genvm_result": {}}]},
        "consensus_history": {"consensus_results": [{"consensus_round": "Accepted"}]},
        "sim_config": {"signed_rollup_transaction": e["raw"]},
    }
    r.update(over)
    return r


@needs_env
def test_clean_normal_call_is_fully_verifiable():
    a = ps.analyse(record(envs()[0]))
    assert a["binding"] == [] and a["consensus"] == [] and a["fully_verifiable"]
    assert a["envelope_target_is_sdk_consensus"] and a["method"] == "accept_payment"


@needs_env
def test_missing_envelope_blocks():
    a = ps.analyse(record(envs()[0], sim_config=None))
    assert "no signed envelope" in " ".join(a["binding"]) and not a["fully_verifiable"]


@needs_env
def test_leader_only_blocks():
    a = ps.analyse(record(envs()[2]))
    assert not a["normal_call"] and not a["fully_verifiable"]


@needs_env
@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r.update(status="ACCEPTED"), "not FINALIZED"),
    (lambda r: r["consensus_data"].update(votes={"a": "agree", "b": "agree", "c": "disagree"}), "recomputed"),
    (lambda r: r.update(num_of_initial_validators=None), "electorate size missing"),
    (lambda r: r["consensus_data"].update(leader_receipt=[{"execution_result": "ERROR"}]), "FINISHED_WITH_ERROR"),
    (lambda r: r["consensus_data"].update(leader_receipt=[{"execution_result": "SUCCESS", "genvm_result": {
        "error_code": "CONSENSUS_LEADER_EXEC_TIMEOUT"}}]), "TIMEOUT"),
    (lambda r: r.update(consensus_history={"consensus_results": [{"consensus_round": "Undetermined"}]}),
     "last round"),
])
def test_consensus_failures(mutate, needle):
    r = record(envs()[0])
    mutate(r)
    a = ps.analyse(r)
    assert any(needle in x for x in a["consensus"]), a["consensus"]


@needs_env
def test_three_of_five_votes_out_of_five_still_passes_but_two_of_five_do_not():
    r = record(envs()[0])
    r["consensus_data"]["votes"] = {"a": "agree", "b": "agree", "c": "agree"}
    assert ps.analyse(r)["consensus"] == []
    r["consensus_data"]["votes"] = {"a": "agree", "b": "agree"}  # electorate 5: not a majority
    assert any("recomputed" in x for x in ps.analyse(r)["consensus"])


@needs_env
@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r.update(hash="0x" + "11" * 32), "keccak"),
    (lambda r: r.update(from_address=sc.checksum("0x" + "22" * 20)), "signer != record sender"),
    (lambda r: r.update(to_address=sc.checksum("0x" + "33" * 20)), "recipient"),
    (lambda r: r.update(data={"calldata": base64.b64encode(b"x").decode()}), "differs"),
    (lambda r: r.update(data={}), "no calldata"),
    (lambda r: r["sim_config"].update(validators=[{"provider": "x"}]), "override"),
    (lambda r: r["sim_config"].update(provider="x"), "unsupported sim_config key"),
])
def test_binding_failures(mutate, needle):
    r = record(envs()[0])
    mutate(r)
    a = ps.analyse(r)
    assert any(needle in x for x in a["binding"]), a["binding"]


@needs_env
def test_wrong_chain_and_evm_value_fail_binding():
    wc = [x for x in envs() if x.get("wrongChain")][0]
    assert any("chain 61997" in x for x in ps.analyse(record(wc))["binding"])
    ev = [x for x in envs() if x.get("evmValue")][0]
    assert any("moves value" in x for x in ps.analyse(record(ev, hash=ev["hash"]))["binding"])


def test_execution_result_name_rule():
    assert ps.execution_result_name(None) == "NOT_VOTED"
    assert ps.execution_result_name({"leader_receipt": {"execution_result": "SUCCESS"}}) == "FINISHED_WITH_RETURN"


class _Resp:
    def __init__(self, obj, status=200):
        self._b = json.dumps(obj).encode()
        self.status_code = status

    def iter_content(self, n):
        for i in range(0, len(self._b), n):
            yield self._b[i:i + n]

    def close(self):
        pass


class _Session:
    def __init__(self, handler):
        self.handler, self.sent = handler, []

    def post(self, url, json=None, **kw):
        self.sent.append(json)
        return _Resp({"jsonrpc": "2.0", "id": json["id"], **self.handler(json["method"], json["params"])})


def test_refuses_non_allowlisted_and_sim_config():
    p = ps.Prober("http://x", session=_Session(lambda m, a: {"result": 1}))
    for m in ("eth_sendRawTransaction", "sim_fundAccount", "gen_call", "sim_createValidator",
              "sim_restoreSnapshot", "sim_clearDbTables", "sim_updateProvider"):
        with pytest.raises(ps.Refused):
            p.rpc(m, [])
    with pytest.raises(ps.Refused):
        p.rpc("eth_getTransactionByHash", ["0x1", {"sim_config": {}}])
    assert p.log == []


def test_budget_and_size_cap(monkeypatch):
    p = ps.Prober("http://x", session=_Session(lambda m, a: {"result": "0x0"}))
    monkeypatch.setattr(ps, "MAX_REQUESTS", 2)
    p.rpc("eth_chainId", [])
    p.rpc("eth_chainId", [])
    with pytest.raises(ps.Refused):
        p.rpc("eth_chainId", [])
    monkeypatch.setattr(ps, "MAX_BYTES", 10)
    q = ps.Prober("http://x", session=_Session(lambda m, a: {"result": "y" * 100}))
    outcome, _ = q.rpc("eth_chainId", [])
    assert outcome == "refused"


@needs_env
def test_main_end_to_end_offline(tmp_path, monkeypatch):
    rec = record(envs()[0])
    lo = record(envs()[2])

    def handler(m, a):
        return {
            "eth_chainId": {"result": "0xf22f"}, "eth_gasPrice": {"result": "0x0"},
            "eth_blockNumber": {"result": "0x1"}, "sim_getFinalityWindowTime": {"result": 10},
            "sim_countValidators": {"result": 20},
            "sim_getConsensusContract": {"result": {"address": "0x" + "00" * 20, "abi": [
                {"name": "addTransaction", "inputs": [{}] * 5}]}},
            "gen_getContractCode": {"result": base64.b64encode(
                b'# { "Depends": "py-genlayer:abc" }\nfrom genlayer import *\n').decode()},
            "eth_getBlockByNumber": {"result": {"transactions": [lo]}},
            "eth_getTransactionByHash": {"error": {"code": -32000, "message": "not found"}},
            "sim_getTransactionsForAddress": {"result": [rec]},
        }[m]

    sess = _Session(handler)
    monkeypatch.setattr(ps, "Prober", lambda url: ps.__dict__["_RealProber"](url, session=sess))
    out = tmp_path / "e.json"
    assert ps.main(["--out", str(out), "--max-tx", "5"]) == 0
    doc = json.loads(out.read_text())
    assert doc["summary"]["normal_calls"] == 1 and doc["summary"]["fully_verifiable"] == [rec["hash"]]
    assert doc["facts"]["seed_contract_depends"] == ['# { "Depends": "py-genlayer:abc" }']
    assert all(r["method"] in ps.READ_ONLY_METHODS for r in sess.sent)
    assert "sim_config" not in json.dumps([r["params"] for r in sess.sent])


ps._RealProber = ps.Prober


@needs_env
@pytest.mark.parametrize("decoded,needles", [
    ({"": "accept_payment", "args": []}, ["'' method key", "no 'method'"]),
    ({"method": "accept_payment", "args": [], "extra": 1}, ["unexpected calldata keys"]),
])
def test_calldata_key_profile(monkeypatch, decoded, needles):
    monkeypatch.setattr(ps.sc, "decode_genvm_calldata", lambda raw, decode=None: decoded)
    a = ps.analyse(record(envs()[0]))
    for n in needles:
        assert any(n in x for x in a["binding"]), a["binding"]


@needs_env
def test_correctly_signed_envelope_to_wrong_target_is_not_verifiable():
    """Regression: a valid signature over a transaction to another address must fail the binding."""
    from eth_account import Account
    from eth_utils import keccak
    e = envs()[0]
    good = sc.decode_envelope(e["raw"])
    acct = Account.create()                       # throwaway, in memory only
    from eth_abi import encode as abi_encode
    data = bytes.fromhex("27241a99") + abi_encode(
        ["address", "address", "uint256", "uint256", "bytes"],
        [acct.address, sc.checksum(e["contract"]), 5, 3, _inner_from(e["raw"])])
    for target in ("0x" + "00" * 20, "0x" + "ab" * 20):
        raw = bytes(acct.sign_transaction({"to": sc.checksum(target), "data": data, "value": 0, "gas": 500000,
                                           "gasPrice": 0, "nonce": 0, "chainId": 61999}).raw_transaction)
        rec = record({**e, "raw": "0x" + raw.hex(), "hash": "0x" + keccak(raw).hex(), "signer": acct.address})
        a = ps.analyse(rec)
        assert a["envelope_target_is_sdk_consensus"] is False
        assert any("is not the SDK consensus contract" in x for x in a["binding"]), a["binding"]
        assert a["fully_verifiable"] is False
        # everything else about it is clean, so the target is what fails it
        assert len(a["binding"]) == 1 and a["consensus"] == []
    assert good["to"] == ps.SDK_CONSENSUS.lower()


def _inner_from(raw_hex):
    from eth_abi import decode as abi_decode
    env = sc.decode_envelope(raw_hex)
    return abi_decode(["address", "address", "uint256", "uint256", "bytes"], env["data"][4:])[4]

"""
Offline tests for the Studionet kit. Run in the separate Python 3.12
environment with genlayer-py 0.18.0 (requirements-studionet.txt):

    python -m pytest spike/studionet/tests -q

The operator runs with the real genlayer-py 0.18.0 client end to end; only the
RPC is replaced (tests/fake_studionet.py). Keys are throwaway test keys.
"""
import base64
import copy
import difflib
import hashlib
import json
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
ROOT = KIT.parents[1]
sys.path.insert(0, str(KIT))
sys.path.insert(0, str(HERE))

import studionet_checks as sn  # noqa: E402
import operator_studionet as op  # noqa: E402
import fake_studionet as fk  # noqa: E402

OWNER, ATTESTER, FLAG, WALLET = (fk.key_account(n) for n in (11, 12, 13, 20))
ASSERTIONS_TEXT = (ROOT / "scenarios" / "01_protected" / "assertions.json").read_bytes().decode()


# ===========================================================================
# fixtures
# ===========================================================================
@pytest.fixture
def chain(tmp_path, monkeypatch):
    f = fk.FakeStudionet()
    monkeypatch.setattr(op, "RUN", tmp_path / "run")
    monkeypatch.setattr(op, "TRANSPORT_FACTORY", lambda: op.Transport(send=f.send))
    monkeypatch.setattr(op, "SLEEP", lambda s: None)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    for var, acct in zip(op.KEY_VARS, (OWNER, ATTESTER, FLAG)):
        monkeypatch.setenv(var, "0x" + bytes(acct.key).hex())
    return f


def run(*argv):
    return op.main(list(argv))


def state():
    return json.loads((op.RUN / "state.json").read_text())


def wallet_send(f, method, args):
    raw = fk.wallet_envelope(WALLET, state()["contract"], method, args,
                             nonce=int(f.rpc_eth_getTransactionCount(WALLET.address), 16))
    return f.rpc_eth_sendRawTransaction(raw)


def full_case(f):
    assert run("preflight", "--wallet", WALLET.address) == 0
    assert run("setup", "--wallet", WALLET.address, "--confirm-allowance", "60") == 0
    st = state()
    assert run("attest") == 0
    accept = wallet_send(f, "accept_payment", [st["payment_id"], st["policy_id"], ASSERTIONS_TEXT])
    assert run("flag", "--accept-hash", accept) == 0
    claim = wallet_send(f, "open_claim", [st["payment_id"]])
    return accept, claim


def report():
    return json.loads((op.RUN / "report.json").read_text())


# ===========================================================================
# port identity
# ===========================================================================
def test_port_copy_is_the_reviewed_bytes_and_the_original_is_unchanged():
    """Both files are compared after CRLF->LF only (a Windows checkout may be CRLF); any other change fails."""
    orig, _ = sn.sc.canonical_source_payload((ROOT / "contracts" / "good_faith_layer.py").read_bytes(),
                                             expected_sha=sn.ORIGINAL_SOURCE_SHA256)
    port, _ = sn.canonical_source_payload((KIT / "contract" / "good_faith_layer.studionet.py").read_bytes())
    assert hashlib.sha256(orig).hexdigest() == sn.ORIGINAL_SOURCE_SHA256
    assert hashlib.sha256(port).hexdigest() == sn.PORT_SOURCE_SHA256


def test_port_diff_is_exactly_three_hunks_and_reproduces_the_copy():
    orig = (ROOT / "contracts" / "good_faith_layer.py").read_text()
    port = (KIT / "contract" / "good_faith_layer.studionet.py").read_text()
    diff = "".join(difflib.unified_diff(orig.splitlines(True), port.splitlines(True),
                                       "a/contracts/good_faith_layer.py",
                                       "b/spike/studionet/contract/good_faith_layer.studionet.py"))
    assert diff == (KIT / "contract" / "port.diff").read_text()
    removed = [l for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    added = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    assert removed == ['-# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }',
                       "-import genlayer as gl", "-from genlayer.storage import allow as allow_storage",
                       "-class GoodFaithLayer(gl.contract.Contract):"]
    assert added == ['+# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }',
                     "+class GoodFaithLayer(gl.Contract):"]


def test_source_identity_accepts_a_crlf_checkout_and_refuses_any_edit():
    port = (KIT / "contract" / "good_faith_layer.studionet.py").read_bytes().replace(b"\r\n", b"\n")
    payload, info = sn.canonical_source_payload(port.replace(b"\n", b"\r\n"))
    assert payload == port and info["line_endings_normalized"]
    with pytest.raises(sn.SourceIdentityError):
        sn.canonical_source_payload(port + b"\n# edit\n")
    with pytest.raises(sn.SourceIdentityError):
        sn.canonical_source_payload((ROOT / "contracts" / "good_faith_layer.py").read_bytes())


def test_v5_abi_file_has_the_v5_selector_and_events():
    from eth_utils import keccak
    abi = sn.load_v5_abi()
    add = [e for e in abi if e.get("name") == "addTransaction"]
    assert len(add) == 1 and [i["type"] for i in add[0]["inputs"]] == ["address", "address", "uint256", "uint256", "bytes"]
    assert keccak(text=sn.V5_SIGNATURE)[:4].hex() == sn.V5_SELECTOR
    assert {e["name"] for e in abi if e["type"] == "event"} == {"NewTransaction", "CreatedTransaction"}


def test_sdk_is_pinned_to_0_18_0(monkeypatch):
    assert op.sdk_version() == "0.18.0"
    monkeypatch.setattr(op, "sdk_version", lambda: "0.19.0rc2")
    with pytest.raises(op.Blocked, match="exactly 0.18.0"):
        op.require_sdk()


# ===========================================================================
# end to end against the fake, real genlayer-py 0.18.0
# ===========================================================================
def test_full_case_passes_and_every_request_is_read_only_or_a_checked_send(chain):
    accept, claim = full_case(chain)
    assert run("verify", "--claim-hash", claim) == 0
    r = report()
    assert r["result"] == "PASSED" and r["failed_checks"] == []
    assert set(chain.calls) <= op.READ_METHODS | {"eth_sendRawTransaction"}
    assert "sim_fundAccount" not in chain.calls
    # deploy + 4 setup + attest + flag by operators, accept + claim by the wallet
    assert len(chain.sent_raw) == 9
    for raw in chain.sent_raw:
        env = sn.decode_envelope(raw)
        assert env["to"] == sn.CONSENSUS_ADDRESS.lower() and env["chain_id"] == 61999 and env["value"] == 0


def test_setup_journal_records_each_hash_before_it_is_sent(chain):
    order = []
    real_send = chain.send

    def spy(url, method, params):
        if method == "eth_sendRawTransaction":
            prog = json.loads((op.RUN / "progress.json").read_text())
            from eth_utils import keccak
            h = "0x" + keccak(bytes.fromhex(params[0][2:])).hex()
            order.append(any(t["hash"] == h for t in prog["transactions"]))
        return real_send(url, method, params)
    chain.send = spy
    assert run("preflight", "--wallet", WALLET.address) == 0
    assert run("setup", "--wallet", WALLET.address, "--confirm-allowance", "60") == 0
    assert order == [True] * 5


def test_preflight_blocks_when_gas_price_is_not_zero(chain):
    chain.gas_price = "0x1"
    assert run("preflight", "--wallet", WALLET.address) == op.EXIT_BLOCKED


def test_preflight_blocks_on_schema_mismatch(chain):
    del chain.schema["methods"]["open_claim"]
    assert run("preflight", "--wallet", WALLET.address) == op.EXIT_BLOCKED
    assert "open_claim" in json.loads((op.RUN / "preflight.json").read_text())["schema_problems"][0]


def test_schema_command_is_read_only_and_matches(chain):
    assert run("schema") == 0
    assert chain.calls == ["eth_chainId", "gen_getContractSchemaForCode"]


def test_wallet_equal_to_an_operator_is_refused(chain):
    assert run("preflight", "--wallet", OWNER.address) == op.EXIT_BLOCKED


def test_setup_refuses_to_start_over_an_unfinished_case(chain):
    assert run("preflight", "--wallet", WALLET.address) == 0
    chain.fail_gen_call = 10 ** 6                       # reads fail -> case saved unverified
    assert run("setup", "--wallet", WALLET.address, "--confirm-allowance", "60") == op.EXIT_BLOCKED
    assert state()["live_state_verified"] is False
    (op.RUN / "progress.json").write_text(json.dumps({**json.loads((op.RUN / "progress.json").read_text()),
                                                      "complete": False}))
    sent = len(chain.sent_raw)
    assert run("setup", "--wallet", WALLET.address, "--confirm-allowance", "60") == op.EXIT_BLOCKED
    assert len(chain.sent_raw) == sent


def test_live_state_is_only_verified_by_successful_matching_reads(chain):
    assert run("preflight", "--wallet", WALLET.address) == 0
    chain.fail_gen_call = 10 ** 6
    assert run("setup", "--wallet", WALLET.address, "--confirm-allowance", "60") == op.EXIT_BLOCKED
    assert state()["live_state_verified"] is False
    assert run("attest") == op.EXIT_BLOCKED             # writes refused while unverified
    chain.fail_gen_call = 3
    assert run("check-state", "--attempts", "5") == 0
    assert state()["live_state_verified"] is True


def test_live_state_mismatch_never_verifies(chain):
    assert run("preflight", "--wallet", WALLET.address) == 0
    assert run("setup", "--wallet", WALLET.address, "--confirm-allowance", "60") == 0
    st = state()
    chain.contracts[st["contract"]]["model"].pool = 999
    assert run("check-state") == op.EXIT_FAILED
    assert state()["live_state_verified"] is False


def test_verify_requires_claim_finality(chain):
    accept, claim = full_case(chain)
    chain.records[claim]["status"] = "ACCEPTED"
    op.WAIT_SECONDS = 0
    try:
        assert run("verify", "--claim-hash", claim) == op.EXIT_FAILED
    finally:
        op.WAIT_SECONDS = 20 * 60
    assert "claim_consensus" in report()["failed_checks"]


def test_verify_requires_accept_finality(chain):
    accept, claim = full_case(chain)
    chain.records[accept]["status"] = "ACCEPTED"
    op.WAIT_SECONDS = 0
    try:
        assert run("verify", "--claim-hash", claim) == op.EXIT_FAILED
    finally:
        op.WAIT_SECONDS = 20 * 60
    assert "accept_consensus" in report()["failed_checks"]


def test_verify_fails_without_a_final_state_read(chain):
    accept, claim = full_case(chain)
    chain.fail_gen_call = 10 ** 6
    assert run("verify", "--claim-hash", claim) == op.EXIT_FAILED
    assert "final_state" in report()["failed_checks"]
    assert any("could not be read" in x for x in report()["checks"]["final_state"]["failures"])


@pytest.mark.parametrize("mutate,check", [
    (lambda r: r["consensus_data"].update(votes={"a": "agree", "b": "agree", "c": "idle", "d": "idle", "e": "idle"}),
     "claim_consensus"),
    (lambda r: r.update(leader_only=True, execution_mode="LEADER_ONLY"), "claim_consensus"),
    (lambda r: r["consensus_data"].update(leader_receipt=[{"execution_result": "ERROR"}]), "claim_consensus"),
    (lambda r: r.update(sim_config=None), "claim_binding"),
    (lambda r: r.update(data={}), "claim_binding"),
    (lambda r: r["sim_config"].update(validators=[{"provider": "x"}]), "claim_binding"),
])
def test_verify_fails_on_a_tampered_claim_record(chain, mutate, check):
    accept, claim = full_case(chain)
    mutate(chain.records[claim])
    assert run("verify", "--claim-hash", claim) == op.EXIT_FAILED
    assert check in report()["failed_checks"]


def test_three_agree_two_idle_out_of_five_passes(chain):
    chain.votes = {"a": "agree", "b": "agree", "c": "agree", "d": "idle", "e": "idle"}
    accept, claim = full_case(chain)
    assert run("verify", "--claim-hash", claim) == 0


def test_flag_refuses_an_accept_signed_for_the_wrong_target(chain):
    assert run("preflight", "--wallet", WALLET.address) == 0
    assert run("setup", "--wallet", WALLET.address, "--confirm-allowance", "60") == 0
    st = state()
    assert run("attest") == 0
    raw = fk.wallet_envelope(WALLET, st["contract"], "accept_payment",
                             [st["payment_id"], st["policy_id"], ASSERTIONS_TEXT], nonce=0, to="0x" + "ab" * 20)
    h = chain.rpc_eth_sendRawTransaction(raw)
    assert run("flag", "--accept-hash", h) == op.EXIT_BLOCKED
    assert any("consensus contract" in x for x in state()["accept"]["failures"])


def test_recover_rebuilds_the_case_from_the_journal(chain):
    assert run("preflight", "--wallet", WALLET.address) == 0
    assert run("setup", "--wallet", WALLET.address, "--confirm-allowance", "60") == 0
    st = state()
    (op.RUN / "state.json").unlink()
    sent = len(chain.sent_raw)
    assert run("recover", "--wallet", WALLET.address) == 0
    new = state()
    assert len(chain.sent_raw) == sent
    for k in ("contract", "payment_id", "policy_id", "ttl_seconds", "operators", "deploy_hash"):
        assert new[k] == st[k]
    assert new["live_state_verified"] is True


def test_recover_refuses_a_tampered_journal(chain):
    assert run("preflight", "--wallet", WALLET.address) == 0
    assert run("setup", "--wallet", WALLET.address, "--confirm-allowance", "60") == 0
    (op.RUN / "state.json").unlink()
    p = json.loads((op.RUN / "progress.json").read_text())
    p["ttl_seconds"] += 1
    (op.RUN / "progress.json").write_text(json.dumps(p))
    assert run("recover", "--wallet", WALLET.address) == op.EXIT_BLOCKED
    assert not (op.RUN / "state.json").exists()


def test_kit_never_writes_into_the_61997_run_directory(chain):
    before = sorted(p.name for p in (ROOT / "spike" / "run").glob("*")) if (ROOT / "spike" / "run").exists() else None
    full_case(chain)
    after = sorted(p.name for p in (ROOT / "spike" / "run").glob("*")) if (ROOT / "spike" / "run").exists() else None
    assert before == after
    assert op.RUN != ROOT / "spike" / "run"


# ===========================================================================
# the transport boundary
# ===========================================================================
def armed_transport(f, **expect):
    t = op.Transport(send=f.send)
    t.arm({"function": "x", "sender": WALLET.address, "expect": {"sender": WALLET.address, "recipient": fk.ZERO,
                                                               "method": "open_claim", "args": ["p"], **expect}})
    return t


def test_boundary_refuses_unarmed_sends_sim_config_and_unlisted_methods(chain):
    t = op.Transport(send=chain.send)
    raw = fk.wallet_envelope(WALLET, "0x" + "c0" * 20, "open_claim", ["p"], nonce=0)
    with pytest.raises(op.Refused, match="outside an armed write"):
        t.make_request("eth_sendRawTransaction", [raw])
    with pytest.raises(op.Refused, match="sim_config"):
        t.make_request("gen_call", [{"type": "read", "sim_config": {}}])
    for m in ("sim_fundAccount", "sim_createValidator", "sim_clearDbTables", "sim_updateProvider", "eth_sendTransaction",
              "sim_restoreSnapshot", "sim_upgradeContractCode", "gen_getStudioTransactionByHash"):
        with pytest.raises(op.Refused):
            t.make_request(m, [])
    assert chain.calls == []


@pytest.mark.parametrize("kw,needle", [
    ({"to": "0x" + "00" * 20}, "consensus contract"),
    ({"to": "0x" + "ab" * 20}, "consensus contract"),
    ({"chain_id": 61997}, "chain id"),
    ({"value": 1}, "EVM value"),
    ({"flag": b"\x01"}, "not normal"),
    ({"gl_sender": "0x" + "99" * 20}, "GenLayer sender"),
])
def test_boundary_precheck_refuses_before_anything_is_sent(chain, kw, needle):
    contract = "0x" + "c0" * 20
    t = armed_transport(chain, recipient=contract)
    raw = fk.wallet_envelope(WALLET, contract, "open_claim", ["p"], nonce=0, **kw)
    with pytest.raises(op.Refused, match=needle):
        t.make_request("eth_sendRawTransaction", [raw])
    assert chain.sent_raw == []


def test_boundary_refuses_wrong_method_args_and_second_send(chain):
    contract = "0x" + "c0" * 20
    t = armed_transport(chain, recipient=contract)
    with pytest.raises(op.Refused, match="arguments"):
        t.make_request("eth_sendRawTransaction",
                       [fk.wallet_envelope(WALLET, contract, "open_claim", ["other"], nonce=0)])
    with pytest.raises(op.Refused, match="method"):
        t.make_request("eth_sendRawTransaction",
                       [fk.wallet_envelope(WALLET, contract, "attest", ["p"], nonce=0)])
    ok = fk.wallet_envelope(WALLET, contract, "open_claim", ["p"], nonce=0)
    chain.contracts[sn.checksum(contract)] = {"code_b64": "", "model": fk.GFLModel(OWNER.address, FLAG.address)}
    t.make_request("eth_sendRawTransaction", [ok])
    with pytest.raises(op.Refused, match="second"):
        t.make_request("eth_sendRawTransaction", [ok])
    assert chain.sent_raw == [ok]


def test_stock_genlayer_py_0_18_config_would_be_refused_by_the_boundary(chain):
    """Why the kit pins the consensus config: unmodified 0.18.0 cannot produce an acceptable transaction."""
    from genlayer_py.chains import studionet
    from genlayer_py.client.genlayer_client import GenLayerClient
    contract = "0x" + "c0" * 20
    # (a) runtime lookup: Studionet answers with the zero address -> transactions go to 0x0
    t = armed_transport(chain, recipient=contract)
    t.slot["expect"]["sender"] = OWNER.address
    c = GenLayerClient(copy.deepcopy(studionet), OWNER)
    c.provider.make_request = t.make_request
    c.initialize_consensus_smart_contract()
    assert c.chain.consensus_main_contract["address"] == fk.ZERO
    with pytest.raises(op.Refused, match="consensus contract"):
        c.write_contract(address=sn.checksum(contract), function_name="open_claim", account=OWNER, args=["p"])
    # (b) static config: the 6-input addTransaction, which Studionet's v0.121 parser does not decode
    t2 = armed_transport(chain, recipient=contract)
    t2.slot["expect"]["sender"] = OWNER.address
    c2 = GenLayerClient(copy.deepcopy(studionet), OWNER)
    c2.provider.make_request = t2.make_request
    c2.initialize_consensus_smart_contract = lambda *a, **k: None
    with pytest.raises(op.Refused, match="v5"):
        c2.write_contract(address=sn.checksum(contract), function_name="open_claim", account=OWNER, args=["p"])
    assert chain.sent_raw == []


def test_kit_client_produces_exactly_the_genlayer_js_wallet_shape(chain):
    """Operator (genlayer-py 0.18.0 + pinned config) and wallet (genlayer-js 1.1.8) envelopes decode identically."""
    contract = "0x" + "c0" * 20
    t = armed_transport(chain, recipient=contract)
    t.slot["expect"]["sender"] = OWNER.address
    chain.contracts[sn.checksum(contract)] = {"code_b64": "", "model": fk.GFLModel(OWNER.address, FLAG.address)}
    c = op.make_client(OWNER, t)
    try:
        c.write_contract(address=sn.checksum(contract), function_name="open_claim", account=OWNER, args=["p"])
    except Exception:
        pass                                    # post-send receipt handling is irrelevant here
    py = sn.decode_envelope(chain.sent_raw[0])
    js = sn.decode_envelope(fk.wallet_envelope(OWNER, contract, "open_claim", ["p"], nonce=0))
    for k in ("to", "chain_id", "function", "gl_sender", "gl_recipient", "calldata", "value", "user_value", "is_deploy"):
        assert py[k] == js[k], k


# ===========================================================================
# studionet_checks on real envelopes
# ===========================================================================
class _Accepting:
    def call(self, *a):
        pass


def call_record(raw, f=None):
    f = f or fk.FakeStudionet()
    contract = sn.checksum("0x" + "c0" * 20)
    f.contracts[contract] = {"code_b64": "", "model": _Accepting()}
    h = f.rpc_eth_sendRawTransaction(raw)
    return h, f.records[h]


def binding(rec, h, **kw):
    a = dict(tx_hash=h, contract="0x" + "c0" * 20, sender=WALLET.address, method="open_claim", args=["p"])
    a.update(kw)
    return sn.check_call_binding(rec, "t", **a)


def test_checks_pass_a_clean_wallet_call():
    h, rec = call_record(fk.wallet_envelope(WALLET, "0x" + "c0" * 20, "open_claim", ["p"], nonce=0))
    assert binding(rec, h) == [] and sn.check_consensus(rec, "t", require_finalized=True) == []


@pytest.mark.parametrize("kw,needle", [
    ({"to": "0x" + "00" * 20}, "consensus contract"),
    ({"to": "0x" + "ab" * 20}, "consensus contract"),
    ({"chain_id": 1}, "chain id"),
    ({"value": 7}, "EVM value"),
    ({"gl_sender": "0x" + "99" * 20}, "GenLayer sender"),
])
def test_checks_fail_correctly_signed_but_wrong_envelopes(kw, needle):
    h, rec = call_record(fk.wallet_envelope(WALLET, "0x" + "c0" * 20, "open_claim", ["p"], nonce=0, **kw))
    assert any(needle in x for x in binding(rec, h)), binding(rec, h)


def test_checks_fail_leader_only():
    h, rec = call_record(fk.wallet_envelope(WALLET, "0x" + "c0" * 20, "open_claim", ["p"], nonce=0, flag=b"\x01"))
    assert any("not normal" in x for x in binding(rec, h))
    assert any("leader_only" in x for x in sn.check_consensus(rec, "t"))


@pytest.mark.parametrize("kw,needle", [
    ({"method": "accept_payment"}, "method"),
    ({"args": ["q"]}, "arguments"),
    ({"sender": OWNER.address}, "sender"),
    ({"contract": "0x" + "c1" * 20}, "contract"),
    ({"tx_hash": "0x" + "11" * 32}, "hash"),
])
def test_checks_fail_on_every_binding_field(kw, needle):
    h, rec = call_record(fk.wallet_envelope(WALLET, "0x" + "c0" * 20, "open_claim", ["p"], nonce=0))
    assert any(needle in x for x in binding(rec, h, **kw)), binding(rec, h, **kw)


def test_checks_refuse_the_v06_method_key_and_extra_keys(monkeypatch):
    h, rec = call_record(fk.wallet_envelope(WALLET, "0x" + "c0" * 20, "open_claim", ["p"], nonce=0))
    monkeypatch.setattr(sn.sc, "decode_genvm_calldata", lambda raw, d=None: {"": "open_claim", "args": ["p"]})
    assert any("'' method key" in x for x in binding(rec, h))
    monkeypatch.setattr(sn.sc, "decode_genvm_calldata", lambda raw, d=None: {"method": "open_claim", "args": ["p"], "kwargs": {}})
    assert any("unexpected calldata keys" in x for x in binding(rec, h))


@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r.update(status="ACCEPTED"), "not in ['FINALIZED']"),
    (lambda r: r.update(num_of_initial_validators=None), "electorate"),
    (lambda r: r["consensus_data"].update(votes={"a": "agree", "b": "agree"}), "recomputed"),
    (lambda r: r["consensus_data"].update(votes={}), "no validator votes"),
    (lambda r: r["consensus_data"].update(leader_receipt=[{"execution_result": "ERROR"}]), "FINISHED_WITH_ERROR"),
    (lambda r: r["consensus_data"].update(leader_receipt=[{"execution_result": "SUCCESS", "genvm_result": {
        "error_code": "CONSENSUS_VALIDATOR_EXEC_TIMEOUT"}}]), "TIMEOUT"),
    (lambda r: r.update(txExecutionResultName="FINISHED_WITH_ERROR"), "record says"),
    (lambda r: r.update(consensus_history={"consensus_results": [{"consensus_round": "Undetermined"}]}), "last consensus"),
    (lambda r: r.update(consensus_history=None), "history missing"),
])
def test_consensus_failures(mutate, needle):
    h, rec = call_record(fk.wallet_envelope(WALLET, "0x" + "c0" * 20, "open_claim", ["p"], nonce=0))
    mutate(rec)
    f = sn.check_consensus(rec, "t", require_finalized=True)
    assert any(needle in x for x in f), f


def test_record_calldata_must_be_present_and_identical():
    h, rec = call_record(fk.wallet_envelope(WALLET, "0x" + "c0" * 20, "open_claim", ["p"], nonce=0))
    rec2 = copy.deepcopy(rec); rec2["data"] = {}
    assert any("no stored calldata" in x for x in binding(rec2, h))
    rec3 = copy.deepcopy(rec); rec3["data"] = {"calldata": base64.b64encode(b"x").decode()}
    assert any("differs" in x for x in binding(rec3, h))


def test_deploy_checks_code_constructor_and_target(chain):
    full_case(chain)
    st = state()
    dep = chain.records[st["deploy_hash"]]
    ok = sn.check_deploy(dep, st["deploy_hash"], st["operators"]["owner_payer"], st["operators"]["flag_authority"])
    assert ok == []
    assert any("constructor arguments" in x for x in
               sn.check_deploy(dep, st["deploy_hash"], st["operators"]["owner_payer"], OWNER.address))
    bad = copy.deepcopy(dep); bad["data"]["contract_code"] = base64.b64encode(b"other").decode()
    assert any("reviewed port" in x for x in
               sn.check_deploy(bad, st["deploy_hash"], st["operators"]["owner_payer"], st["operators"]["flag_authority"]))
    assert sn.check_deployed_code(base64.b64encode(b"x").decode())


# ===========================================================================
# loopback server
# ===========================================================================
def test_server_serves_only_the_allowlist_on_loopback(tmp_path, monkeypatch):
    import serve_studionet as srv
    s = srv.make_server(0)
    port = s.server_address[1]
    th = threading.Thread(target=s.serve_forever, daemon=True)
    th.start()
    try:
        def get(path, host=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": host or f"127.0.0.1:{port}"})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, b""
        assert get("/wallet.html")[0] == 200
        status, body = get("/genlayer-js-1.1.8.js")
        assert status == 200 and hashlib.sha256(body).hexdigest() == VENDOR_SHA256
        for p in ("/../run/state.json", "/.env", "/spike-core.js", "/genlayer-js.js", "/operator_studionet.py",
                  "/vendor/genlayer-js-1.1.8.js"):
            assert get(p)[0] == 404, p
        assert get("/wallet.html", host="evil.example")[0] == 421
        assert srv.ROUTES["/state.json"][0] == KIT / "run" / "state.json"
        assert s.server_address[0] == "127.0.0.1"
    finally:
        s.shutdown()


VENDOR_SHA256 = "a7af4598e4aa5ef3108c5721b03860aeef6f1ba77e2ea1a69cb37486d113bef6"


def test_vendored_bundle_hash_is_pinned():
    assert hashlib.sha256((KIT / "public" / "vendor" / "genlayer-js-1.1.8.js").read_bytes()).hexdigest() == VENDOR_SHA256
    lock = json.loads((KIT / "sdk" / "package-lock.json").read_text())
    assert lock["packages"]["node_modules/genlayer-js"]["version"] == "1.1.8"


# ===========================================================================
# the original deterministic checks, run against the port
# ===========================================================================
DETERMINISTIC = sorted(p.name for p in ROOT.glob("test_*.py") if "rc5_runtime" not in p.name)


def _run_scripts(contract_bytes, workdir):
    (workdir / "contracts").mkdir(parents=True)
    (workdir / "contracts" / "good_faith_layer.py").write_bytes(contract_bytes)
    for d in ("policies", "scenarios"):
        subprocess.run([sys.executable, "-c", "import shutil,sys; shutil.copytree(sys.argv[1], sys.argv[2])",
                        str(ROOT / d), str(workdir / d)], check=True)
    out = {}
    for name in DETERMINISTIC:
        (workdir / name).write_bytes((ROOT / name).read_bytes())
        p = subprocess.run([sys.executable, "-B", name], cwd=workdir, capture_output=True, timeout=300)
        out[name] = (p.returncode, p.stdout + p.stderr)
    return out


def test_original_deterministic_checks_pass_identically_on_the_port(tmp_path):
    assert len(DETERMINISTIC) == 10, DETERMINISTIC
    o_bytes, _ = sn.sc.canonical_source_payload((ROOT / "contracts" / "good_faith_layer.py").read_bytes(),
                                                expected_sha=sn.ORIGINAL_SOURCE_SHA256)
    p_bytes, _ = sn.canonical_source_payload((KIT / "contract" / "good_faith_layer.studionet.py").read_bytes())
    orig = _run_scripts(o_bytes, tmp_path / "o")
    port = _run_scripts(p_bytes, tmp_path / "p")
    for name in DETERMINISTIC:
        assert orig[name][0] == 0, (name, orig[name][1][-400:])
        assert port[name] == orig[name], name


@pytest.mark.skipif(not os.environ.get("GFL_GENVM_STD"), reason="set GFL_GENVM_STD to genvm v0.2.16 runners/genlayer-py-std/src")
def test_port_on_the_real_genvm_v0216_std():
    script = ROOT / "spike" / "studionet-gate" / "genvm" / "assess_port.py"
    p = subprocess.run([sys.executable, str(script), "--std", os.environ["GFL_GENVM_STD"]], capture_output=True,
                       text=True, timeout=600)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "37/37 passed" in p.stdout and sn.PORT_SOURCE_SHA256 in p.stdout


def test_deploy_check_reads_the_code_from_the_signed_envelope_not_only_the_record():
    import rlp
    from eth_abi import encode as abi_encode
    from genlayer_py.abi import calldata as glc
    other = b"# not the reviewed port\n"
    inner = rlp.encode([other, glc.encode({"args": [sn.checksum(FLAG.address)]}), b"\x00"])
    data = bytes.fromhex(sn.V5_SELECTOR) + abi_encode(["address", "address", "uint256", "uint256", "bytes"],
                                                      [OWNER.address, fk.ZERO, 5, 3, inner])
    raw = "0x" + bytes(OWNER.sign_transaction({"to": sn.CONSENSUS_ADDRESS, "data": data, "value": 0, "gas": 500000,
                                               "gasPrice": 0, "nonce": 0, "chainId": 61999}).raw_transaction).hex()
    f = fk.FakeStudionet()
    h = f.rpc_eth_sendRawTransaction(raw)
    rec = f.records[h]
    port, _ = sn.canonical_source_payload((KIT / "contract" / "good_faith_layer.studionet.py").read_bytes())
    rec["data"]["contract_code"] = base64.b64encode(port).decode()      # record claims the port
    fails = sn.check_deploy(rec, h, OWNER.address, FLAG.address)
    assert any("signed code sha256" in x for x in fails), fails


def test_setup_stops_if_the_rpc_returns_a_different_hash_than_keccak_raw(chain):
    assert run("preflight", "--wallet", WALLET.address) == 0
    real = chain.rpc_eth_sendRawTransaction
    chain.rpc_eth_sendRawTransaction = lambda raw, *r: (real(raw), "0x" + "ee" * 32)[1]
    assert run("setup", "--wallet", WALLET.address, "--confirm-allowance", "60") == op.EXIT_BLOCKED
    prog = json.loads((op.RUN / "progress.json").read_text())
    t = prog["transactions"][0]
    assert t["returned_hash"] == "0x" + "ee" * 32 and t["hash"] != t["returned_hash"]
    assert len(chain.sent_raw) == 1
    assert "keccak(raw)" in json.loads((op.RUN / "setup_blocked.json").read_text())["reason"]


def test_setup_refuses_a_preflight_that_recorded_schema_problems(chain):
    assert run("preflight", "--wallet", WALLET.address) == 0
    pre = json.loads((op.RUN / "preflight.json").read_text())
    pre["schema_problems"] = ["x"]
    (op.RUN / "preflight.json").write_text(json.dumps(pre))
    assert run("setup", "--wallet", WALLET.address, "--confirm-allowance", "60") == op.EXIT_BLOCKED
    assert chain.sent_raw == []


def test_latency_runs_from_the_actual_send_to_decided_including_the_sdk_receipt_wait(chain, monkeypatch):
    """Regression: the SDK waits for an EVM receipt inside write_contract/deploy_contract.

    Simulated: 5 s of pre-send work (nonce lookup), 15 s inside the SDK's receipt
    wait, 2 s inside wait_record. Submission-to-decided must be 15 + 2 = 17 s for
    every step, the pre-send 5 s must not count, and the TTL must use 17 s.
    """
    t = [1_000_000.0]
    monkeypatch.setattr(op, "CLOCK", lambda: t[0])
    advance = {"eth_getTransactionCount": 5.0, "eth_getTransactionReceipt": 15.0, "eth_getTransactionByHash": 2.0}
    real_send = chain.send
    in_setup = {"on": False}

    def timed(url, method, params):
        if in_setup["on"]:
            t[0] += advance.get(method, 0.0)
        return real_send(url, method, params)
    chain.send = timed
    assert run("preflight", "--wallet", WALLET.address) == 0
    in_setup["on"] = True
    assert run("setup", "--wallet", WALLET.address, "--confirm-allowance", "60") == 0
    log = json.loads((op.RUN / "operator_log.json").read_text())
    assert [e["function"] for e in log] == ["deploy", "register_attester", "fund_protection_pool",
                                            "register_policy", "register_payment"]
    for e in log:
        assert e["submit_to_decided_seconds"] == 17.0, e
        assert e["sdk_call_seconds"] >= 20.0, e          # includes the pre-send and receipt time
    st = state()
    assert st["ttl_inputs"]["observed_submit_to_decided"] == [17.0, 17.0]
    assert st["ttl_seconds"] == 2 * 17 + 2 * op.POLL_SECONDS + 60


def test_reviewed_original_hash_is_checked_on_canonical_lf_bytes():
    """Regression: a CRLF Windows checkout of the unchanged original must pass; any real change must not."""
    orig = (ROOT / "contracts" / "good_faith_layer.py").read_bytes()
    lf = orig.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    mixed = lf.replace(b"\n", b"\r\n", 1)          # only line endings differ: same canonical bytes
    for variant in (lf, crlf, mixed):
        payload, info = sn.sc.canonical_source_payload(variant, expected_sha=sn.ORIGINAL_SOURCE_SHA256)
        assert hashlib.sha256(payload).hexdigest() == sn.ORIGINAL_SOURCE_SHA256
    for bad in (lf + b"\n", lf.replace(b"PROTECTED", b"PROTECTEd", 1), crlf.replace(b"\r\n", b"\r", 1),
                crlf.replace(b"owner only", b"owner-only", 1), crlf + b"\r\n", b"\xef\xbb\xbf" + lf):
        with pytest.raises(sn.SourceIdentityError):
            sn.sc.canonical_source_payload(bad, expected_sha=sn.ORIGINAL_SOURCE_SHA256)

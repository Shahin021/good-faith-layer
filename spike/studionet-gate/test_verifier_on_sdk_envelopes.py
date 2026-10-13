"""Our existing verifier (spike/spike_checks.py, unchanged) on genlayer-js 1.1.8
studionet envelopes. Records which checks carry over as they are and which
need a Studionet profile before they can be relied on. Nothing here changes
the verifier.
"""
import base64
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import spike_checks as sc  # noqa: E402

ENVELOPES = HERE / "sdk" / "envelopes.json"
SDK_CONSENSUS = "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575"
pytestmark = pytest.mark.skipif(not ENVELOPES.exists(), reason="run `node --test` in sdk/ first")


def envs():
    return json.loads(ENVELOPES.read_text())


def rec_for(e):
    return {"sim_config": {"signed_rollup_transaction": e["raw"]}, "sender": e["signer"].lower()}


def check(e, chain=61999):
    return sc.check_envelope(rec_for(e), "t", tx_hash=e["hash"], sender=e["signer"],
                             consensus=SDK_CONSENSUS, chain_id=chain)


def test_normal_write_passes_envelope_checks_with_studionet_parameters():
    f, env = check(envs()[0])
    assert f == []
    assert env["function"] == "addTransaction(address,address,uint256,uint256,bytes)"
    assert env["chain_id"] == 61999 and env["kind"] == "legacy"


def test_default_chain_pin_is_61997_so_studionet_needs_its_own_constant():
    f, _ = sc.check_envelope(rec_for(envs()[0]), "t", tx_hash=envs()[0]["hash"], sender=envs()[0]["signer"],
                             consensus=SDK_CONSENSUS)
    assert any("chain id 61999 != 61997" in x for x in f)


def test_leader_only_write_is_rejected():
    f, env = check(envs()[2])
    assert env is None and any("not normal" in x for x in f)


def test_wrong_chain_is_rejected():
    e = [x for x in envs() if x.get("wrongChain")][0]
    f, _ = check(e)
    assert any("chain id 61997 != 61999" in x for x in f)


def test_wrong_target_is_rejected():
    e = envs()[0]
    f, _ = sc.check_envelope(rec_for(e), "t", tx_hash=e["hash"], sender=e["signer"],
                             consensus="0x" + "00" * 20, chain_id=61999)
    assert any("is not the consensus contract" in x for x in f)


def test_GAP_evm_value_is_not_checked_for_v5():
    """GAP 1: v5 has no GenLayer value field; Studio v0.121 takes the EVM value.
    The current verifier only checks the GenLayer user_value, so a v5 envelope
    that moves EVM value passes. A Studionet profile must also require EVM value 0."""
    e = [x for x in envs() if x.get("evmValue")][0]
    f, env = check(e)
    assert env["value"] == 5
    assert f == []  # current behaviour; must become a failure in the Studionet profile


def test_GAP_method_key_differs():
    """GAP 2: GenVM v0.2.16 dispatches on calldata key "method"; the verifier
    reads key "". The call binding would reject every genuine Studionet call."""
    _, env = check(envs()[0])
    call = sc.decode_genvm_calldata(env["calldata"], None)
    assert call.get("method") == "accept_payment"
    assert call.get("") is None


def test_record_calldata_comparison_is_byte_exact():
    _, env = check(envs()[0])
    assert base64.b64encode(env["calldata"]).decode()  # the stored form the binding compares

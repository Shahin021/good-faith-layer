"""Run Studio v0.121's real transaction parser on envelopes made by the real
genlayer-js 1.1.8 (sdk/envelopes.json, written by `node --test` in sdk/).

Source: genlayer-studio branch v0.121 at 2004fc4. Point GFL_STUDIO_V0121 at a
checkout (the directory that contains backend/). Only the parser, its type
modules and the built-in default ConsensusMain ABI are imported; the database,
consensus and GenVM modules are replaced by inert stubs.

The deployed Studionet build is not known; this shows what the inspected
public source does with these envelopes, nothing more.

Needs the parser's pinned deps: web3==7.14.1 eth-account==0.13.7 eth-utils==5.3.1.
"""
import enum
import json
import os
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SRC = Path(os.environ.get("GFL_STUDIO_V0121", "/tmp/claude-0/v121"))
ENVELOPES = HERE / "sdk" / "envelopes.json"
SDK_CONSENSUS = "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575"

pytestmark = pytest.mark.skipif(
    not (SRC / "backend/protocol_rpc/transactions_parser.py").exists() or not ENVELOPES.exists(),
    reason="needs a v0.121 checkout (GFL_STUDIO_V0121) and sdk/envelopes.json",
)


def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


@pytest.fixture(scope="module")
def parser():
    sys.path.insert(0, str(SRC))

    class TransactionStatus(enum.Enum):
        PENDING = "PENDING"

    _stub("backend.database_handler.models", TransactionStatus=TransactionStatus)
    _stub("backend.database_handler.types", ConsensusData=object)
    _stub("backend.database_handler.contract_snapshot", ContractSnapshot=object)
    _stub("backend.rollup.consensus_service", ConsensusService=object)
    from web3 import Web3
    from backend.rollup.default_contracts.consensus_main import get_default_consensus_main_contract
    from backend.protocol_rpc import transactions_parser as tp

    class FakeConsensus:
        web3 = Web3()

        def load_contract(self, name):
            assert name == "ConsensusMain"
            return get_default_consensus_main_contract()

    return tp, tp.TransactionParser(FakeConsensus())


def _envelopes():
    return json.loads(ENVELOPES.read_text()) if ENVELOPES.exists() else []


def test_default_consensus_address_is_zero(parser):
    from backend.rollup.default_contracts.consensus_main import DEFAULT_CONSENSUS_MAIN_ADDRESS
    assert DEFAULT_CONSENSUS_MAIN_ADDRESS == "0x0000000000000000000000000000000000000000"


@pytest.mark.parametrize("i", range(3))
def test_server_decodes_sdk_write(parser, i):
    tp, p = parser
    e = _envelopes()[i]
    d = p.decode_signed_transaction(e["raw"])
    assert d is not None
    assert d.from_address == e["signer"]
    assert p.transaction_has_valid_signature(e["raw"], d)
    # The server takes `to` as given; it is the SDK's address, not the server's zero default.
    assert d.to_address == SDK_CONSENSUS
    assert d.data.function_name == "addTransaction"
    assert d.data.args.sender == e["signer"]
    assert d.data.args.recipient.lower() == e["contract"]
    g = p.get_genlayer_transaction(d)
    assert g.data.leader_only is e["leaderOnly"]
    assert g.data.execution_mode == ("LEADER_ONLY" if e["leaderOnly"] else "NORMAL")


def test_server_does_not_reject_wrong_chain(parser):
    """The parser decodes and signature-checks a chain-61997 envelope without complaint.

    Chain binding therefore has to come from the verifier, as ours does."""
    tp, p = parser
    e = [x for x in _envelopes() if x.get("wrongChain")][0]
    d = p.decode_signed_transaction(e["raw"])
    assert d is not None and p.transaction_has_valid_signature(e["raw"], d)


def test_server_reads_evm_value_for_v5(parser):
    """v5 carries value only in the EVM envelope, and the server uses it."""
    tp, p = parser
    e = [x for x in _envelopes() if x.get("evmValue")][0]
    d = p.decode_signed_transaction(e["raw"])
    assert d.value == e["evmValue"]

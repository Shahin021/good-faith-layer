from pathlib import Path
import time

import pytest
from gltest import get_contract_factory
from gltest.accounts import get_accounts
from gltest.clients import get_gl_client
from gltest.assertions import tx_execution_succeeded


FEE_ESTIMATE_OPTIONS = {
    "leaderTimeunitsAllocation": 100,
    "validatorTimeunitsAllocation": 200,
    "rotations": [1],
}


def transaction_fee_preset():
    estimate = get_gl_client().estimate_transaction_fees(
        FEE_ESTIMATE_OPTIONS
    )
    return {
        "distribution": estimate["distribution"],
        "feeValue": estimate["feeValue"],
    }


def tx(method, args):
    receipt = method(args=args).transact(
        fees=transaction_fee_preset(),
        wait_until="finalized",
    )

    print(
        "WRITE:",
        receipt.get("txExecutionResultName"),
        receipt.get("hash"),
    )

    assert tx_execution_succeeded(receipt), (
        "WRITE FAILED: "
        + str(receipt.get("hash"))
        + " / "
        + str(receipt.get("txExecutionResultName"))
    )

    return receipt


@pytest.mark.integration
def test_scenario06_rc5_runtime():
    accounts = get_accounts()
    assert len(accounts) >= 4

    owner = accounts[0]
    attester = accounts[1]
    recipient = accounts[2]
    flag_authority = accounts[3]

    print("OWNER:", owner.address)
    print("ATTESTER:", attester.address)
    print("RECIPIENT:", recipient.address)
    print("FLAG AUTHORITY:", flag_authority.address)

    policy_text = Path(
        "policies/gfl-standard-v1.txt"
    ).read_text(encoding="utf-8")

    required_checks = Path(
        "policies/gfl-standard-v1-checks.json"
    ).read_text(encoding="utf-8")

    attested = Path(
        "scenarios/06_stale_attestation/attested.json"
    ).read_text(encoding="utf-8")

    assertions = Path(
        "scenarios/06_stale_attestation/assertions.json"
    ).read_text(encoding="utf-8")

    factory = get_contract_factory("GoodFaithLayer")

    contract = factory.deploy(
        args=[flag_authority.address],
        account=owner,
        fees=transaction_fee_preset(),
        wait_until="finalized",
    )

    print("CONTRACT:", contract.address)

    owner_contract = contract.connect(owner)
    attester_contract = contract.connect(attester)
    recipient_contract = contract.connect(recipient)
    flag_contract = contract.connect(flag_authority)

    tx(
        owner_contract.register_policy,
        [
            "gfl-stale-demo-v1",
            policy_text,
            required_checks,
            60,
        ],
    )

    tx(
        owner_contract.register_attester,
        [attester.address],
    )

    tx(
        owner_contract.fund_protection_pool,
        [5000],
    )

    tx(
        owner_contract.register_payment,
        [
            "scenario-06-rc5",
            recipient.address,
            1000,
            "gfl-stale-demo-v1",
            "Backend API integration, delivered and deployed",
        ],
    )

    tx(
        attester_contract.attest,
        [
            "scenario-06-rc5",
            attested,
        ],
    )

    print("WAITING 65 SECONDS SO ATTESTATION BECOMES STALE...")
    time.sleep(65)

    tx(
        recipient_contract.accept_payment,
        [
            "scenario-06-rc5",
            "gfl-stale-demo-v1",
            assertions,
        ],
    )

    tx(
        flag_contract.flag_payment,
        [
            "scenario-06-rc5",
            "upstream exposure identified",
        ],
    )

    claim_receipt = tx(
        recipient_contract.open_claim,
        ["scenario-06-rc5"],
    )

    print("OPEN_CLAIM_RECEIPT:", claim_receipt)

    verdict = recipient_contract.get_verdict(
        args=["scenario-06-rc5"]
    ).call()

    paid_out = recipient_contract.get_paid_out(
        args=["scenario-06-rc5"]
    ).call()

    print("VERDICT:", verdict)
    print("PAID OUT:", paid_out)

    assert verdict == "REVIEW_REQUIRED"
    assert int(paid_out) == 0

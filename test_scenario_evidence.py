"""Static checks for structured attestation fixtures."""

import json
from pathlib import Path

ROOT = Path(__file__).parent / "scenarios"

REQUIRED_CHECK_IDS = {
    "provider_risk_screen",
    "counterparty_history_or_identity",
}


def load(name):
    return json.loads((ROOT / name / "attested.json").read_text())


def derive_checks(a):
    seen = {}
    unknown_id = False

    for c in a.get("checks", []):
        check_id = c.get("check_id")
        status = c.get("status")

        if check_id not in REQUIRED_CHECK_IDS:
            unknown_id = True
            continue

        seen.setdefault(check_id, []).append(status)

    for check_id in REQUIRED_CHECK_IDS:
        if "not_performed" in seen.get(check_id, []):
            return "no"

    if unknown_id:
        return "unclear"

    for check_id in REQUIRED_CHECK_IDS:
        statuses = seen.get(check_id, [])
        if not statuses:
            return "unclear"
        if any(s != "performed" for s in statuses):
            return "unclear"

    return "yes"


def derive_related_party(a):
    rp = a.get("related_party_check")

    if not isinstance(rp, dict):
        return "unclear"

    if rp.get("status") != "performed":
        return "unclear"

    result = rp.get("result")

    if result == "indicators_found":
        return "yes"
    if result == "no_indicators":
        return "no"

    return "unclear"


def main():
    failures = []
    tests = []

    def run(name, fn):
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            failures.append(name)
            print(f"  FAIL  {name}: {e}")
        tests.append(name)

    def clean(name):
        a = load(name)
        assert a["schema_version"] == "gfl-attestation-v1"
        assert a.get("warning_displayed") is False
        assert derive_checks(a) == "yes"
        assert derive_related_party(a) == "no"
        assert "delivery_evidence" in a

    run(
        "protected fixture maps deterministically to clean structured facts",
        lambda: clean("01_protected"),
    )

    run(
        "stale fixture remains substantively clean",
        lambda: clean("06_stale_attestation"),
    )

    def notice_case():
        a = load("02_notice_at_acceptance")
        assert a.get("warning_displayed") is True
        assert derive_checks(a) == "unclear"
        assert derive_related_party(a) == "unclear"

        risk = next(
            c for c in a["checks"]
            if c["check_id"] == "provider_risk_screen"
        )
        assert risk["status"] == "performed"

    run(
        "adverse screening result still counts as a performed check",
        notice_case,
    )

    def ambiguous_case():
        a = load("03_ambiguous")
        assert derive_checks(a) == "unclear"

        identity = next(
            c for c in a["checks"]
            if c["check_id"] == "counterparty_history_or_identity"
        )
        assert identity["status"] == "incomplete"

    run(
        "incomplete required check stays unclear rather than becoming no",
        ambiguous_case,
    )

    def injection_case():
        a = load("04_injection")
        assert derive_checks(a) == "unclear"
        assert derive_related_party(a) == "unclear"

    run(
        "partial injection fixture does not gain clean deterministic facts",
        injection_case,
    )

    run(
        "unattested fixture still has no attested file",
        lambda: (_ for _ in ()).throw(
            AssertionError("attested.json exists")
        )
        if (ROOT / "05_unattested" / "attested.json").exists()
        else None,
    )

    print(f"\n{len(tests)-len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

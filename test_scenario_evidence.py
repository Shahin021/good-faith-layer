"""Static checks for the demo evidence fixtures.

These catch a class of bug the pure verdict tests cannot see: a synthetic
`PROTECTED` finding can pass even when the actual happy-path fixture does not
contain enough attested facts for an LLM to reach those findings faithfully.
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent / "scenarios"


def load(name, kind):
    return json.loads((ROOT / name / kind).read_text())


def check_clean_attested(name):
    a = load(name, "attested.json")
    assert a["risk_snapshot"]["warning_displayed"] is False
    checks = a.get("checks", [])
    assert any(c.get("outcome") == "pass" and "risk list" in c.get("check", "") for c in checks)
    assert any(c.get("outcome") == "pass" and ("prior settled history" in c.get("check", "") or "verified identity" in c.get("check", "")) for c in checks)
    assert a.get("delivery_evidence", {}).get("outcome") == "pass"
    assert a.get("related_party_check", {}).get("outcome") == "pass"


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

    run("protected fixture has attested support for all paying findings",
        lambda: check_clean_attested("01_protected"))
    run("stale fixture is substantively clean apart from freshness",
        lambda: check_clean_attested("06_stale_attestation"))
    run("unattested fixture really has no attested file",
        lambda: (_ for _ in ()).throw(AssertionError("attested.json exists"))
        if (ROOT / "05_unattested" / "attested.json").exists() else None)

    print(f"\n{len(tests)-len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

import ast
import json
from pathlib import Path

CONTRACT = Path("contracts/good_faith_layer.py")
SCENARIOS = Path("scenarios")
REQUIRED_CHECKS = Path("policies/gfl-standard-v1-checks.json").read_text()

TARGETS = {
    "_canonicalize_attested_evidence",
    "_derive_agreed_checks_performed",
    "_derive_related_party_indicators",
    "_derive_structured_notice",
    "_derive_structured_findings",
}

tree = ast.parse(CONTRACT.read_text())

functions = [
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name in TARGETS
]

found = {node.name for node in functions}
missing = TARGETS - found

if missing:
    raise RuntimeError(f"contract helper(s) missing: {sorted(missing)}")

module = ast.Module(body=functions, type_ignores=[])
ast.fix_missing_locations(module)

ns = {"json": json}

exec(
    compile(module, str(CONTRACT), "exec"),
    ns,
)

canonicalize = ns["_canonicalize_attested_evidence"]
derive_checks = ns["_derive_agreed_checks_performed"]
derive_related = ns["_derive_related_party_indicators"]
derive_notice = ns["_derive_structured_notice"]
derive_all = ns["_derive_structured_findings"]


def load(name):
    return json.loads(
        (SCENARIOS / name / "attested.json").read_text()
    )


def canonical_string(obj):
    return canonicalize(
        json.dumps(obj, ensure_ascii=False)
    )


def canonical_obj(obj):
    return json.loads(canonical_string(obj))


def expect_error(fn):
    try:
        fn()
    except Exception:
        return
    raise AssertionError("expected exception")


def main():
    tests = []
    failures = []

    def run(name, fn):
        tests.append(name)

        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:
            failures.append(name)
            print(f"  FAIL  {name}: {exc}")

    def protected():
        raw = load("01_protected")
        canonical = canonical_string(raw)

        findings = derive_all(
            canonical,
            REQUIRED_CHECKS,
        )

        assert findings == {
            "agreed_checks_performed": "yes",
            "related_party_indicators": "no",
            "notice_at_acceptance": "no",
        }

    run(
        "protected fixture derives clean structured findings",
        protected,
    )

    def notice_case():
        raw = load("02_notice_at_acceptance")
        canonical = canonical_string(raw)

        findings = derive_all(
            canonical,
            REQUIRED_CHECKS,
        )

        assert findings["agreed_checks_performed"] == "unclear"
        assert findings["related_party_indicators"] == "unclear"
        assert findings["notice_at_acceptance"] == "yes"

    run(
        "scenario 02 warning deterministically establishes notice",
        notice_case,
    )

    def ambiguous_case():
        raw = load("03_ambiguous")
        canonical = canonical_string(raw)

        findings = derive_all(
            canonical,
            REQUIRED_CHECKS,
        )

        assert findings["agreed_checks_performed"] == "unclear"
        assert findings["notice_at_acceptance"] == "unclear"

    run(
        "incomplete check remains unclear",
        ambiguous_case,
    )

    def explicit_nonperformance():
        obj = {
            "schema_version": "gfl-attestation-v1",
            "checks": [
                {
                    "check_id": "provider_risk_screen",
                    "status": "performed",
                },
                {
                    "check_id": "counterparty_history_or_identity",
                    "status": "not_performed",
                },
            ],
        }

        a = canonical_obj(obj)

        assert derive_checks(a, REQUIRED_CHECKS) == "no"

    run(
        "explicit not_performed derives no",
        explicit_nonperformance,
    )

    def conflicting_duplicate():
        obj = {
            "schema_version": "gfl-attestation-v1",
            "checks": [
                {
                    "check_id": "provider_risk_screen",
                    "status": "performed",
                },
                {
                    "check_id": "provider_risk_screen",
                    "status": "not_performed",
                },
                {
                    "check_id": "counterparty_history_or_identity",
                    "status": "performed",
                },
            ],
        }

        a = canonical_obj(obj)

        assert derive_checks(a, REQUIRED_CHECKS) == "no"

    run(
        "not_performed wins conflicting duplicate evidence",
        conflicting_duplicate,
    )

    def unknown_check():
        obj = {
            "schema_version": "gfl-attestation-v1",
            "checks": [
                {
                    "check_id": "provider_risk_screen",
                    "status": "performed",
                },
                {
                    "check_id": "counterparty_history_or_identity",
                    "status": "performed",
                },
                {
                    "check_id": "typo_or_unknown_check",
                    "status": "performed",
                },
            ],
        }

        a = canonical_obj(obj)

        assert derive_checks(a, REQUIRED_CHECKS) == "unclear"

    run(
        "unknown check id forces unclear",
        unknown_check,
    )

    def related_found():
        obj = {
            "schema_version": "gfl-attestation-v1",
            "checks": [],
            "related_party_check": {
                "status": "performed",
                "result": "indicators_found",
            },
        }

        a = canonical_obj(obj)

        assert derive_related(a) == "yes"

    run(
        "related-party indicators_found derives yes",
        related_found,
    )

    def warning_wins():
        obj = {
            "schema_version": "gfl-attestation-v1",
            "checks": [],
            "warning_displayed": True,
            "notice_check": {
                "status": "performed",
                "result": "no_notice_found",
            },
        }

        a = canonical_obj(obj)

        assert derive_notice(a) == "yes"

    run(
        "explicit displayed warning overrides no_notice_found",
        warning_wins,
    )

    def false_warning_not_negative_proof():
        obj = {
            "schema_version": "gfl-attestation-v1",
            "checks": [],
            "warning_displayed": False,
        }

        a = canonical_obj(obj)

        assert derive_notice(a) == "unclear"

    run(
        "warning false alone does not establish no notice",
        false_warning_not_negative_proof,
    )

    def no_notice_check():
        obj = {
            "schema_version": "gfl-attestation-v1",
            "checks": [],
            "notice_check": {
                "status": "performed",
                "result": "no_notice_found",
            },
        }

        a = canonical_obj(obj)

        assert derive_notice(a) == "no"

    run(
        "performed no_notice_found derives no",
        no_notice_check,
    )

    run(
        "invalid JSON rejected",
        lambda: expect_error(
            lambda: canonicalize("{not-json")
        ),
    )

    run(
        "unsupported schema version rejected",
        lambda: expect_error(
            lambda: canonical_string({
                "schema_version": "gfl-attestation-v999",
                "checks": [],
            })
        ),
    )

    run(
        "unknown top-level field rejected",
        lambda: expect_error(
            lambda: canonical_string({
                "schema_version": "gfl-attestation-v1",
                "checks": [],
                "made_up_field": True,
            })
        ),
    )

    run(
        "performed notice check requires result",
        lambda: expect_error(
            lambda: canonical_string({
                "schema_version": "gfl-attestation-v1",
                "checks": [],
                "notice_check": {
                    "status": "performed",
                },
            })
        ),
    )

    def empty_attestation():
        assert derive_all("", REQUIRED_CHECKS) == {
            "agreed_checks_performed": "unclear",
            "related_party_indicators": "unclear",
            "notice_at_acceptance": "unclear",
        }

    run(
        "missing attestation derives only unclear facts",
        empty_attestation,
    )

    def canonical_stability():
        raw = (SCENARIOS / "01_protected" / "attested.json").read_text()

        once = canonicalize(raw)
        twice = canonicalize(once)

        assert once == twice

    run(
        "canonicalization is stable",
        canonical_stability,
    )

    print(
        f"\n{len(tests) - len(failures)}/{len(tests)} passed"
    )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

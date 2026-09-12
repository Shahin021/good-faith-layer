import ast
from pathlib import Path

CONTRACT = Path("contracts/good_faith_layer.py")
source = CONTRACT.read_text()
tree = ast.parse(source)

TARGETS = {
    "_merge_findings",
    "decide",
}

nodes = [
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name in TARGETS
]

found = {node.name for node in nodes}

if found != TARGETS:
    raise RuntimeError(
        f"missing helpers: {sorted(TARGETS - found)}"
    )

module = ast.Module(
    body=nodes,
    type_ignores=[],
)

ast.fix_missing_locations(module)

ns = {}

exec(
    compile(module, str(CONTRACT), "exec"),
    ns,
)

merge = ns["_merge_findings"]
decide = ns["decide"]


def semantic(
    value="yes",
    notice="unclear",
    injection=False,
    parse_ok=True,
):
    return {
        "value_exchanged": value,
        "semantic_notice_found": notice,
        "prompt_injection_detected": injection,
        "reasoning": "test",
        "parse_ok": parse_ok,
    }


def verdict(s, structured):
    findings = merge(s, structured)

    return decide(
        findings,
        True,
        True,
    )[0]


def main():
    failures = []
    tests = []

    def run(name, fn):
        tests.append(name)

        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:
            failures.append(name)
            print(f"  FAIL  {name}: {exc}")

    def scenario02_value_disagreement():
        structured = {
            "agreed_checks_performed": "unclear",
            "related_party_indicators": "unclear",
            "notice_at_acceptance": "yes",
        }

        leader = verdict(
            semantic(value="yes"),
            structured,
        )

        validator = verdict(
            semantic(value="no"),
            structured,
        )

        assert leader == "RECIPIENT_BEARS"
        assert validator == "RECIPIENT_BEARS"
        assert leader == validator

    run(
        "Scenario 02 value disagreement still agrees on verdict",
        scenario02_value_disagreement,
    )

    def meaningful_value_disagreement():
        structured = {
            "agreed_checks_performed": "yes",
            "related_party_indicators": "no",
            "notice_at_acceptance": "no",
        }

        leader = verdict(
            semantic(value="yes"),
            structured,
        )

        validator = verdict(
            semantic(value="unclear"),
            structured,
        )

        assert leader == "PROTECTED"
        assert validator == "REVIEW_REQUIRED"
        assert leader != validator

    run(
        "material value disagreement still breaks consensus",
        meaningful_value_disagreement,
    )

    def injection_disagreement():
        structured = {
            "agreed_checks_performed": "unclear",
            "related_party_indicators": "unclear",
            "notice_at_acceptance": "yes",
        }

        leader = verdict(
            semantic(injection=False),
            structured,
        )

        validator = verdict(
            semantic(injection=True),
            structured,
        )

        assert leader == "RECIPIENT_BEARS"
        assert validator == "REVIEW_REQUIRED"
        assert leader != validator

    run(
        "prompt injection disagreement remains material",
        injection_disagreement,
    )

    def parse_failure_disagreement():
        structured = {
            "agreed_checks_performed": "yes",
            "related_party_indicators": "no",
            "notice_at_acceptance": "no",
        }

        leader = verdict(
            semantic(parse_ok=True),
            structured,
        )

        validator = verdict(
            semantic(parse_ok=False),
            structured,
        )

        assert leader == "PROTECTED"
        assert validator == "REVIEW_REQUIRED"
        assert leader != validator

    run(
        "parse failure cannot agree with payout",
        parse_failure_disagreement,
    )

    print(
        f"\n{len(tests) - len(failures)}/{len(tests)} passed"
    )

    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()

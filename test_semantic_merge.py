import ast
import json
from pathlib import Path

CONTRACT = Path("contracts/good_faith_layer.py")

TARGETS = {
    "parse_semantic_findings",
    "_merge_findings",
}

tree = ast.parse(CONTRACT.read_text())

nodes = []

for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in TARGETS:
        nodes.append(node)
        continue

    if isinstance(node, ast.Assign):
        names = {
            target.id
            for target in node.targets
            if isinstance(target, ast.Name)
        }

        if names & {
            "SEMANTIC_REQUIRED_KEYS",
            "SEMANTIC_DECISION_KEYS",
        }:
            nodes.append(node)

found = {
    node.name
    for node in nodes
    if isinstance(node, ast.FunctionDef)
}

if found != TARGETS:
    raise RuntimeError(
        f"missing helpers: {sorted(TARGETS - found)}"
    )

module = ast.Module(
    body=nodes,
    type_ignores=[],
)

ast.fix_missing_locations(module)

ns = {"json": json}

exec(
    compile(module, str(CONTRACT), "exec"),
    ns,
)

parse = ns["parse_semantic_findings"]
merge = ns["_merge_findings"]


def S(
    value="yes",
    notice="unclear",
    injection=False,
):
    return {
        "value_exchanged": value,
        "semantic_notice_found": notice,
        "prompt_injection_detected": injection,
        "reasoning": "test",
    }


def structured(
    checks="yes",
    related="no",
    notice="no",
):
    return {
        "agreed_checks_performed": checks,
        "related_party_indicators": related,
        "notice_at_acceptance": notice,
    }


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

    def clean_merge():
        semantic = parse(S())
        out = merge(
            semantic,
            structured(),
        )

        assert out["value_exchanged"] == "yes"
        assert out["notice_at_acceptance"] == "no"
        assert out["agreed_checks_performed"] == "yes"
        assert out["related_party_indicators"] == "no"
        assert out["parse_ok"] is True

    run(
        "clean structured facts survive semantic merge",
        clean_merge,
    )

    def semantic_notice_upgrade():
        semantic = parse(
            S(notice="yes")
        )

        out = merge(
            semantic,
            structured(notice="no"),
        )

        assert out["notice_at_acceptance"] == "yes"

    run(
        "semantic notice can upgrade structured no to yes",
        semantic_notice_upgrade,
    )

    def structured_yes_survives():
        semantic = parse(
            S(notice="unclear")
        )

        out = merge(
            semantic,
            structured(notice="yes"),
        )

        assert out["notice_at_acceptance"] == "yes"

    run(
        "structured notice yes cannot be downgraded",
        structured_yes_survives,
    )

    def unclear_stays_unclear():
        semantic = parse(
            S(notice="unclear")
        )

        out = merge(
            semantic,
            structured(notice="unclear"),
        )

        assert out["notice_at_acceptance"] == "unclear"

    run(
        "two unclear notice sources remain unclear",
        unclear_stays_unclear,
    )

    def checks_are_not_model_owned():
        semantic = parse(S())

        out = merge(
            semantic,
            structured(
                checks="no",
                related="yes",
            ),
        )

        assert out["agreed_checks_performed"] == "no"
        assert out["related_party_indicators"] == "yes"

    run(
        "semantic layer cannot rewrite checks or related party",
        checks_are_not_model_owned,
    )

    def no_notice_output_rejected():
        result = parse(
            S(notice="no")
        )

        assert result["parse_ok"] is False

    run(
        "semantic model cannot output notice no",
        no_notice_output_rejected,
    )

    def malformed_schema():
        result = parse({
            "value_exchanged": "yes",
            "semantic_notice_found": "unclear",
            "prompt_injection_detected": False,
        })

        assert result["parse_ok"] is False

    run(
        "missing reasoning fails semantic parser",
        malformed_schema,
    )

    def extra_key():
        raw = S()
        raw["agreed_checks_performed"] = "yes"

        result = parse(raw)

        assert result["parse_ok"] is False

    run(
        "model cannot smuggle deterministic check finding",
        extra_key,
    )

    def related_key_rejected():
        raw = S()
        raw["related_party_indicators"] = "no"

        result = parse(raw)

        assert result["parse_ok"] is False

    run(
        "model cannot smuggle related-party finding",
        related_key_rejected,
    )

    def injection_preserved():
        semantic = parse(
            S(injection=True)
        )

        out = merge(
            semantic,
            structured(),
        )

        assert out["prompt_injection_detected"] is True

    run(
        "prompt injection flag survives merge",
        injection_preserved,
    )

    print(
        f"\n{len(tests)-len(failures)}/{len(tests)} passed"
    )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

import ast
from pathlib import Path

CONTRACT = Path("contracts/good_faith_layer.py")
SOURCE = CONTRACT.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

TARGETS = {
    "_has_obvious_assessor_instruction",
    "_merge_findings",
    "decide",
}

nodes = [
    node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef)
    and node.name in TARGETS
]

found = {node.name for node in nodes}

if found != TARGETS:
    raise RuntimeError(f"missing helpers: {sorted(TARGETS - found)}")

module = ast.Module(body=nodes, type_ignores=[])
ast.fix_missing_locations(module)

ns = {}
exec(compile(module, str(CONTRACT), "exec"), ns)

detect = ns["_has_obvious_assessor_instruction"]
merge = ns["_merge_findings"]
decide = ns["decide"]

failures = []

def run(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
    except Exception as exc:
        failures.append(name)
        print(f"  FAIL  {name}: {exc}")

def scenario_04_detected():
    assertions = Path(
        "scenarios/04_injection/assertions.json"
    ).read_text(encoding="utf-8")

    assert detect("", assertions) is True

run(
    "scenario 04 assertions trigger deterministic guard",
    scenario_04_detected,
)

def benign_not_flagged():
    assertions = '''
    {
      "deliverable": {
        "description": "Ignore the old export. The instruction manual is included."
      },
      "recipient_note": "The protected output directory is documented for the client."
    }
    '''

    assert detect("", assertions) is False

run(
    "ordinary recipient assertions do not trigger guard",
    benign_not_flagged,
)

def model_miss_cannot_bypass_guard():
    semantic = {
        "value_exchanged": "yes",
        "semantic_notice_found": "unclear",
        "prompt_injection_detected": False,
        "reasoning": "model missed injection",
        "parse_ok": True,
    }

    structured = {
        "notice_at_acceptance": "no",
        "agreed_checks_performed": "yes",
        "related_party_indicators": "no",
    }

    findings = merge(
        semantic,
        structured,
        True,
    )

    assert findings["prompt_injection_detected"] is True

    verdict, reasoning = decide(
        findings,
        True,
        True,
    )

    assert verdict == "REVIEW_REQUIRED"
    assert reasoning == "The input contained text addressed to the assessor."

run(
    "deterministic guard overrides model false negative",
    model_miss_cannot_bypass_guard,
)

def clean_path_unchanged():
    semantic = {
        "value_exchanged": "yes",
        "semantic_notice_found": "unclear",
        "prompt_injection_detected": False,
        "reasoning": "clean",
        "parse_ok": True,
    }

    structured = {
        "notice_at_acceptance": "no",
        "agreed_checks_performed": "yes",
        "related_party_indicators": "no",
    }

    findings = merge(
        semantic,
        structured,
        False,
    )

    verdict, _ = decide(
        findings,
        True,
        True,
    )

    assert verdict == "PROTECTED"

run(
    "clean protected path remains unchanged",
    clean_path_unchanged,
)

body_start = SOURCE.index("    def open_claim(")
body = SOURCE[body_start:]

run(
    "open_claim wires deterministic guard into consensus path",
    lambda: (
        None
        if (
            "_has_obvious_assessor_instruction(" in body
            and body.count("deterministic_injection,") >= 3
        )
        else (_ for _ in ()).throw(
            AssertionError("guard is not wired through all merge paths")
        )
    ),
)

print(f"\n{5-len(failures)}/5 passed")
raise SystemExit(1 if failures else 0)

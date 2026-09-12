import ast
from pathlib import Path

path = Path("contracts/good_faith_layer.py")
source = path.read_text()
tree = ast.parse(source)

target = None

for node in ast.walk(tree):
    if (
        isinstance(node, ast.FunctionDef)
        and node.name == "open_claim"
    ):
        target = node
        break

if target is None:
    raise RuntimeError("open_claim not found")

body = ast.get_source_segment(source, target)

tests = {
    "structured findings derived before model":
        "_derive_structured_findings(" in body,

    "policy required checks feed deterministic derivation":
        "required_checks_json" in body,

    "semantic parser replaces old parser":
        "parse_semantic_findings(response)" in body
        and "parse_findings(response)" not in body,

    "semantic and structured findings are merged":
        "_merge_findings(" in body,

    "old raw DECISION_KEYS comparison removed":
        "DECISION_KEYS" not in body,

    "model no longer outputs agreed check finding":
        '"agreed_checks_performed"' not in body,

    "model no longer outputs related-party finding":
        '"related_party_indicators"' not in body,

    "semantic notice cannot output no":
        '"semantic_notice_found": "yes" | "unclear"' in body,

    "validator compares final verdict":
        "mine_verdict == theirs_verdict" in body,

    "raw canonical attestation is not pasted directly into prompt":
        "(attested if has_attestation" not in body,
}

failed = []

for name, ok in tests.items():
    if ok:
        print(f"  PASS  {name}")
    else:
        failed.append(name)
        print(f"  FAIL  {name}")

print(
    f"\n{len(tests) - len(failed)}/{len(tests)} passed"
)

raise SystemExit(1 if failed else 0)

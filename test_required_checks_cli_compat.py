import ast
import json
from pathlib import Path

CONTRACT = Path("contracts/good_faith_layer.py")

tree = ast.parse(
    CONTRACT.read_text(encoding="utf-8")
)

target = None

for node in tree.body:
    if (
        isinstance(node, ast.FunctionDef)
        and node.name == "_canonicalize_required_checks"
    ):
        target = node
        break

if target is None:
    raise RuntimeError(
        "_canonicalize_required_checks not found"
    )

module = ast.Module(
    body=[target],
    type_ignores=[],
)
ast.fix_missing_locations(module)

ns = {
    "json": json,
}

exec(
    compile(module, str(CONTRACT), "exec"),
    ns,
)

canonicalize = ns[
    "_canonicalize_required_checks"
]

checks = [
    {
        "check_id": "provider_risk_screen",
        "description":
            "Screen the sending address against the provider risk list.",
    },
    {
        "check_id": "counterparty_history_or_identity",
        "description":
            "Confirm the counterparty has prior settled history or verified identity.",
    },
]

expected = json.dumps(
    checks,
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
)

as_string = canonicalize(
    json.dumps(checks, ensure_ascii=False)
)

as_list = canonicalize(checks)

assert as_string == expected
assert as_list == expected
assert as_string == as_list

try:
    canonicalize(
        {
            "check_id": "not_a_list"
        }
    )
except Exception:
    pass
else:
    raise AssertionError(
        "non-list structured input should be rejected"
    )

print("PASS: JSON string canonicalizes correctly")
print("PASS: CLI-decoded list canonicalizes identically")
print("PASS: unsupported structured input is rejected")
print("3/3 passed")

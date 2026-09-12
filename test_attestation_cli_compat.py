import ast
import json
from pathlib import Path

CONTRACT = Path("contracts/good_faith_layer.py")
FIXTURE = Path(
    "scenarios/02_notice_at_acceptance/attested.json"
)

tree = ast.parse(
    CONTRACT.read_text(encoding="utf-8")
)

target = None

for node in tree.body:
    if (
        isinstance(node, ast.FunctionDef)
        and node.name == "_canonicalize_attested_evidence"
    ):
        target = node
        break

if target is None:
    raise RuntimeError(
        "_canonicalize_attested_evidence not found"
    )

module = ast.Module(
    body=[target],
    type_ignores=[],
)
ast.fix_missing_locations(module)

ns = {"json": json}

exec(
    compile(module, str(CONTRACT), "exec"),
    ns,
)

canonicalize = ns["_canonicalize_attested_evidence"]

fixture = json.loads(
    FIXTURE.read_text(encoding="utf-8")
)

as_string = canonicalize(
    json.dumps(
        fixture,
        ensure_ascii=False,
    )
)

as_object = canonicalize(fixture)

assert as_object == as_string

print(
    "PASS: CLI-decoded attestation object "
    "canonicalizes identically"
)

try:
    canonicalize([])
except Exception:
    print(
        "PASS: unsupported structured input "
        "is rejected"
    )
else:
    raise AssertionError(
        "list input should have been rejected"
    )

print("2/2 passed")

"""
Phase 1 item 6: what GFL needs in order to load on GenVM v0.2.16 (Studionet).

    python spike/studionet-gate/genvm/assess_port.py --std PATH/TO/genvm/runners/genlayer-py-std/src

PATH is a checkout of github.com/genlayerlabs/genvm at tag v0.2.16 (Studio
v0.121 builds that tag: docker Dockerfile.backend ARG GENVM_TAG=v0.2.16).

Offline. It reads contracts/good_faith_layer.py (never writes it), applies
the minimal candidate edits in memory, and runs both files through the real
v0.2.16 Python standard library with a stubbed host:
  1. module load + get_schema (what Studio's schema endpoint does first);
  2. an in-memory lifecycle on the std library's own storage manager.
The candidate copy is written only to a temporary directory and printed as a
diff with its hash. This is an assessment, not the port kit.

What it cannot show: GenVM's WASM sandbox, the real runner hash resolution,
real LLM calls, or validator consensus. The Depends hash below is the one
Studio v0.121's examples pin; the live probe reads the hash a live contract
uses so the two can be compared.
"""
import argparse
import difflib
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ORIGINAL = REPO / "contracts" / "good_faith_layer.py"
ORIGINAL_SHA256 = "2e93a95d5cb084965c31e343677d15d8f6885a4a68590a10e36d6609c52054d2"
V0121_EXAMPLES_DEPENDS = "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6"

EDITS = [
    # 1. runner: the original pins a GenVM v0.6-line runner
    ('# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }\n',
     f'# {{ "Depends": "{V0121_EXAMPLES_DEPENDS}" }}\n'),
    # 2. imports: v0.2.16 has no genlayer.storage (it is genlayer.py.storage) and
    #    `from genlayer import *` already binds gl (= genlayer.gl) and allow_storage
    ("import genlayer as gl\nfrom genlayer import *\nfrom genlayer.storage import allow as allow_storage\n",
     "from genlayer import *\n"),
    # 3. base class: v0.2.16 exposes gl.Contract, not gl.contract.Contract
    ("class GoodFaithLayer(gl.contract.Contract):", "class GoodFaithLayer(gl.Contract):"),
]


def candidate(src: str) -> str:
    for old, new in EDITS:
        if src.count(old) != 1:
            raise SystemExit(f"edit anchor not found exactly once: {old[:60]!r}")
        src = src.replace(old, new)
    return src


def message_bytes(std: str) -> bytes:
    code = (
        "import sys; sys.path.insert(0, sys.argv[1])\n"
        "from genlayer.py import calldata\nfrom genlayer.py.types import Address\n"
        "a = Address(b'\\x01' * 20)\n"
        "sys.stdout.buffer.write(calldata.encode({'contract_address': a, 'sender_address': a,"
        " 'origin_address': a, 'stack': [], 'value': 0, 'datetime': '2026-10-13T00:00:00Z',"
        " 'is_init': False, 'chain_id': 61999, 'entry_kind': 0, 'entry_data': b'',"
        " 'entry_stage_data': None}))\n"
    )
    return subprocess.run([sys.executable, "-I", "-c", code, std], capture_output=True, check=True).stdout


def run(std, contract, schema_out, msg):
    p = subprocess.run([sys.executable, "-I", str(HERE / "harness_v0216.py"), std, str(contract), str(REPO),
                        str(schema_out)], input=msg, capture_output=True, timeout=120)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--std", required=True, help="genvm v0.2.16 runners/genlayer-py-std/src")
    a = ap.parse_args(argv)
    if sys.version_info < (3, 12):
        raise SystemExit("v0.2.16's std library needs Python 3.12+")
    raw = ORIGINAL.read_bytes()
    # A Windows checkout may be CRLF: compare and edit the canonical LF bytes,
    # and refuse anything else that differs from the reviewed original.
    lf = raw.replace(b"\r\n", b"\n")
    sha = hashlib.sha256(lf).hexdigest()
    if b"\r" in lf or sha != ORIGINAL_SHA256:
        raise SystemExit(f"original sha256 (CRLF->LF) {sha} != reviewed {ORIGINAL_SHA256}; refusing")
    print(f"original {ORIGINAL.relative_to(REPO)} sha256 {sha}"
          + (" (after CRLF->LF)" if lf != raw else ""))
    src = lf.decode("utf-8")
    cand = candidate(src)
    msg = message_bytes(a.std)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cpath = td / "good_faith_layer.studionet.py"
        cpath.write_text(cand, encoding="utf-8", newline="")
        csha = hashlib.sha256(cpath.read_bytes()).hexdigest()
        print("".join(difflib.unified_diff(src.splitlines(True), cand.splitlines(True),
                                           "contracts/good_faith_layer.py", "good_faith_layer.studionet.py")))
        print(f"candidate sha256 {csha}")
        opath = td / "good_faith_layer.original.py"
        opath.write_bytes(lf)
        rc_o, out_o = run(a.std, opath, td / "o.json", msg)
        last = [ln for ln in out_o.strip().splitlines() if ln.strip()][-1:]
        print(f"\n[original on v0.2.16] exit {rc_o}: {last[0] if last else ''}")
        rc_c, out_c = run(a.std, cpath, td / "schema.json", msg)
        print(f"\n[candidate on v0.2.16] exit {rc_c}")
        print(out_c)
        if rc_c == 0:
            schema = json.loads((td / "schema.json").read_text())
            print("schema methods:", len(schema["methods"]),
                  "writes:", sorted(k for k, v in schema["methods"].items() if not v["readonly"]))
    ok = rc_o != 0 and rc_c == 0
    print("RESULT:", "original fails to load and the candidate loads and passes the lifecycle"
          if ok else "unexpected; see output")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

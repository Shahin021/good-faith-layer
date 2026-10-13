"""Run the ported GFL copy in memory against GenVM v0.2.16's genlayer-py-std.

Offline. The host is stubbed: storage is the std library's own in-memory
manager, the message comes from stdin (as GenVM supplies it), and the only
non-deterministic step (open_claim's run_nondet + exec_prompt) is replaced by
a local call that runs the contract's own leader_fn and validator_fn with a
canned model answer. That is NOT consensus and NOT an LLM run; it only
exercises the deterministic code around it under the real v0.2.16 storage.

usage: python -I harness_v0216.py STD_SRC CONTRACT REPO_ROOT SCHEMA_OUT < msg.bin
(assess_port.py runs it; STD_SRC is genvm v0.2.16 runners/genlayer-py-std/src)
"""
import json, sys, types
from pathlib import Path

STD, contract_path, repo, schema_out = sys.argv[1], sys.argv[2], Path(sys.argv[3]), sys.argv[4]
sys.path.insert(0, STD)
w = types.ModuleType("_genlayer_wasi")
def _no(*a, **k):
    raise RuntimeError("host call not available offline")
for n in ("gl_call", "storage_read", "storage_write", "get_balance", "get_self_balance"):
    setattr(w, n, _no)
sys.modules["_genlayer_wasi"] = w

g = {"__name__": "contract"}
exec(compile(open(contract_path).read(), contract_path, "exec"), g)

from genlayer.py.get_schema import get_schema
schema = get_schema(g["GoodFaithLayer"])
Path(schema_out).write_text(json.dumps(schema, indent=1, sort_keys=True))

import genlayer.gl as glm
import genlayer.gl.vm as vm
import genlayer.gl.nondet as nondet
from genlayer.py.storage import inmem_allocate
from genlayer.py.types import Address

A = lambda b: Address(bytes([b]) * 20)
OWNER, ATTESTER, RECIPIENT, FLAGGER, STRANGER = A(1), A(2), A(3), A(4), A(5)

def as_sender(addr):
    glm.message = glm.message._replace(sender_address=addr, origin_address=addr)

CANNED = {"value_exchanged": "yes", "semantic_notice_found": "unclear",
          "prompt_injection_detected": False, "reasoning": "canned"}
calls = {"exec_prompt": 0, "validator_agreed": None}
def fake_exec_prompt(prompt, **kw):
    assert kw.get("response_format") == "json", kw
    calls["exec_prompt"] += 1
    return dict(CANNED)
def fake_run_nondet(leader_fn, validator_fn, **kw):
    res = leader_fn()
    calls["validator_agreed"] = validator_fn(vm.Return(res))
    assert calls["validator_agreed"] is True
    return res
nondet.exec_prompt = fake_exec_prompt
vm.run_nondet = fake_run_nondet

results = []
def check(name, cond):
    results.append((name, bool(cond)))
def raises(name, fn, needle):
    try:
        fn()
    except Exception as e:
        results.append((name, needle in str(e)))
        return
    results.append((name, False))

policy = (repo / "policies/gfl-standard-v1.txt").read_text(encoding="utf-8")
checks = (repo / "policies/gfl-standard-v1-checks.json").read_text(encoding="utf-8")
sc = lambda n, f: (repo / "scenarios" / n / f).read_text(encoding="utf-8")

as_sender(OWNER)
c = inmem_allocate(g["GoodFaithLayer"], FLAGGER.as_hex)
check("ctor owner", c.owner == OWNER)
check("ctor flag authority from hex", c.flag_authority == FLAGGER)

as_sender(STRANGER)
raises("non-owner register_policy rejected",
       lambda: c.register_policy("p", policy, checks, 600), "owner only")
as_sender(OWNER)
c.register_policy("gfl-standard-v1", policy, checks, 600)
raises("policy immutable",
       lambda: c.register_policy("gfl-standard-v1", policy, checks, 600), "immutable")
check("max age stored", c.get_max_attestation_age("gfl-standard-v1") == 600)
check("required checks canonical json", json.loads(c.get_required_checks("gfl-standard-v1")))
c.register_attester(ATTESTER.as_hex)
check("is_attester", c.is_attester(ATTESTER.as_hex) is True)
check("is_attester (lowercase hex)", c.is_attester(ATTESTER.as_hex.lower()) is True)
c.fund_protection_pool(5000)
check("pool funded", c.get_protection_pool() == 5000)

def lifecycle(pid, scen, attested=True):
    as_sender(OWNER)
    c.register_payment(pid, RECIPIENT.as_hex, 1000, "gfl-standard-v1", "Brand identity package")
    if attested:
        as_sender(STRANGER)
        raises(f"{pid}: non-attester rejected",
               lambda: c.attest(pid, sc(scen, "attested.json")), "not a registered attester")
        as_sender(ATTESTER)
        c.attest(pid, sc(scen, "attested.json"))
    as_sender(STRANGER)
    raises(f"{pid}: non-recipient accept rejected",
           lambda: c.accept_payment(pid, "gfl-standard-v1", sc(scen, "assertions.json")),
           "only the recipient")
    as_sender(RECIPIENT)
    raises(f"{pid}: wrong policy rejected",
           lambda: c.accept_payment(pid, "other", sc(scen, "assertions.json")), "different policy")
    c.accept_payment(pid, "gfl-standard-v1", sc(scen, "assertions.json"))
    check(f"{pid}: ACCEPTED", c.get_status(pid) == "ACCEPTED")
    check(f"{pid}: has_attestation", c.has_attestation(pid) is attested)
    check(f"{pid}: fresh", c.is_attestation_fresh(pid) is attested)
    if attested:
        as_sender(ATTESTER)
        raises(f"{pid}: attest after accept rejected",
               lambda: c.attest(pid, sc(scen, "attested.json")), "before acceptance")
    as_sender(RECIPIENT)
    raises(f"{pid}: claim before flag rejected", lambda: c.open_claim(pid), "must be FLAGGED")
    as_sender(STRANGER)
    raises(f"{pid}: non-authority flag rejected",
           lambda: c.flag_payment(pid, "x"), "only the flag authority")
    as_sender(FLAGGER)
    c.flag_payment(pid, "upstream exposure identified")
    check(f"{pid}: FLAGGED", c.get_status(pid) == "FLAGGED")
    as_sender(RECIPIENT)
    c.open_claim(pid)
    return c.get_verdict(pid), c.get_paid_out(pid)

v, paid = lifecycle("s01", "01_protected")
check("s01: PROTECTED", v == "PROTECTED")
check("s01: paid 1000", paid == 1000)
check("s01: pool 5000 -> 4000", c.get_protection_pool() == 4000)
check("s01: claim balance 1000", c.get_claim_balance(RECIPIENT.as_hex) == 1000)
check("s01: status settled", c.get_status("s01") != "FLAGGED")
check("s01: validator_fn agreed on own leader result", calls["validator_agreed"] is True)

v5, paid5 = lifecycle("s05", "05_unattested", attested=False)
check("s05: REVIEW_REQUIRED (no attestation)", v5 == "REVIEW_REQUIRED")
check("s05: nothing paid", paid5 == 0)
check("s05: pool unchanged", c.get_protection_pool() == 4000)

check("exec_prompt called with response_format=json", calls["exec_prompt"] >= 2)

for name, ok in results:
    print(("PASS " if ok else "FAIL ") + name)
bad = [n for n, ok in results if not ok]
print(f"{len(results) - len(bad)}/{len(results)} passed")
sys.exit(1 if bad else 0)

# Good Faith Layer

A private, pre-agreed liability layer for post-settlement payment risk in the agentic economy.

Track: **Agentic Commerce Infrastructure**

> **Current runtime proof:** Good Faith Layer has been exercised across six fresh hosted Studio Next / rc5 scenario deployments. The selected final runs produced the intended economic outcomes for protected, notice-at-acceptance, ambiguous, prompt-injection, unattested, and stale-attestation paths, with successful GenVM execution and validator consensus.

---

## The problem

An agent pays a contractor 1,000 units for work delivered. The payment appears acceptable when it is received. Days later, new provenance information surfaces and the funds become economically impaired or restricted.

The work is already done.

**Who should absorb the loss?**

Risk tools can score provenance, but a score does not allocate liability. Different venues may apply different thresholds, different hop depths, and different rules. In an agentic economy, a system that handles hundreds of counterparties cannot escalate every later provenance problem into an improvised dispute.

Good Faith Layer makes the liability rule explicit **before** payment acceptance.

---

## What this does

Both parties commit to a liability policy before the payment.

At claim time, the contract asks:

> Given only what was reasonably knowable when the payment was accepted, did the recipient meet the agreed standard?

Facts with deterministic semantics are resolved in contract code. GenLayer is used only for evidence that genuinely requires interpretation.

The final verdict allocates the economic loss according to the pre-agreed rule.

---

## What this does not do

Good Faith Layer does **not**:

- clean or unfreeze funds;
- change the legal status of an asset;
- act as an AML engine;
- perform a chargeback;
- classify wallets as globally safe or unsafe.

It adjudicates a **private, pre-agreed liability policy** for a specific payment and decides who bears the economic loss.

---

## The loop

```text
register_policy    owner stores policy text, required checks and attestation TTL
register_attester  owner names who may record evidence of fact
        |
register_payment   payer proposes recipient, amount, terms and policy
        |
attest             registered attester records structured pre-acceptance evidence
        |
accept_payment     THIS IS T0
                   recipient accepts the policy and submits assertions
                   acceptance time is recorded in UTC
                   attestation freshness is evaluated and stored here
        |
flag_payment       flag authority declares a later payment-risk event
        |
open_claim         recipient asks to be protected
                   |
                   +-- structured facts are derived deterministically
                   +-- semantic evidence is assessed through GenLayer
                   +-- semantic output is merged with the same structured facts
                   +-- validators compare the final economic verdict
                   +-- protected settlement uses payer bond, pool, then platform bond
```

---

## Deterministic facts and semantic judgment

A recipient writing _"the risk score was 4 and I ran both checks"_ proves only that the recipient made that assertion. It does not prove the underlying fact.

The contract separates two evidence tiers:

| Record | Written by | Role |
|---|---|---|
| `attested_evidence` | registered attester, before acceptance | evidence of fact |
| `recipient_assertions` | recipient, at acceptance | untrusted account of recipient conduct |

Attested evidence uses a structured schema and is canonicalized when recorded.

Before any model is called, the contract derives every fact whose semantics are deterministic:

- `agreed_checks_performed`
- `related_party_indicators`
- structured `notice_at_acceptance`
- attestation presence
- attestation freshness
- narrow high-signal prompt-injection matches

### Required checks

A required check with `status = performed` counts as performed even if the check result itself is adverse.

- all required checks explicitly performed → `yes`
- at least one required check explicitly `not_performed` → `no`
- missing, incomplete, invalid, or unknown evidence → `unclear`

Absence of evidence is not silently converted into evidence of non-performance.

### Related-party evidence

Relationship evidence is deliberately asymmetric.

- performed + `indicators_found` → `yes`
- performed + `no_indicators` → `no`
- performed + `inconclusive` → `unclear`
- missing, invalid, incomplete, or not performed → `unclear`

Not performing a relationship check proves neither relationship nor independence.

### Structured notice

Structured notice follows the same polarity discipline.

- `warning_displayed = true` → `yes`
- performed notice check + `notice_found` → `yes`
- performed notice check + `no_notice_found` → `no`
- otherwise → `unclear`

Silence cannot manufacture a clean `no`.

### What remains model-owned

The semantic layer receives the policy, payment terms, canonical acceptance context, delivery evidence, relevant unstructured context, warning text where applicable, check results where needed, and recipient assertions.

It returns only:

- `value_exchanged`
- `semantic_notice_found`
- `prompt_injection_detected`
- `reasoning`

Semantic notice is intentionally asymmetric. It can discover additional adverse notice, including an adverse admission by the recipient, and upgrade notice to `yes`. It cannot turn silence into `no`.

This keeps facts with deterministic semantics out of the model while reserving GenLayer judgment for evidence that genuinely requires interpretation.

---

## Freshness and T0

A risk snapshot is a photograph, not a standing fact.

Each policy declares:

```text
max_attestation_age_seconds
```

At acceptance, the contract records the UTC acceptance time and computes the attestation age using UTC Unix timestamps.

Current compatibility pattern:

```python
now_unix = int(datetime.now(timezone.utc).timestamp())
max_age = int(self.policies[payment.policy_id].max_attestation_age_seconds)

fresh = False
if payment.attested_evidence != "":
    age = now_unix - int(payment.attested_at_unix)
    fresh = 0 <= age <= max_age

payment.accepted_at = datetime.now(timezone.utc).isoformat()
payment.accepted_at_unix = u256(now_unix)
payment.attestation_fresh = fresh
```

A stale attestation does not undo the real-world payment. It simply cannot support an automatic `PROTECTED` payout.

Two deterministic payout gates follow:

- no attestation → cannot reach `PROTECTED`
- stale attestation at T0 → cannot reach `PROTECTED`

This timestamp handling is a **compatibility patch for the current Studio Next / rc5 runtime**. It should not be described as an official protocol-level timestamp replacement.

---

## Lifecycle

```text
REGISTERED → ACCEPTED → FLAGGED → RESOLVED
                             └→ REVIEW_REQUIRED
```

Each transition checks the current state.

A payment cannot be:

- attested after acceptance;
- flagged before acceptance;
- claimed before flagging;
- claimed twice.

| Function | Caller |
|---|---|
| `register_policy`, `register_attester`, `fund_*` | owner |
| `register_payment` | payer |
| `attest` | registered attester |
| `accept_payment`, `open_claim` | recipient |
| `flag_payment` | flag authority |

Policy IDs are immutable once registered.

---

## Consensus: validators agree on the economic consequence

`open_claim` uses:

```python
gl.vm.run_nondet(...)
```

Consensus is intentionally taken over the **final economic verdict**, not every latent model field and not every sentence of reasoning.

Conceptually:

```python
semantic = gl.vm.run_nondet(
    leader_fn,
    validator_fn,
)

findings = _merge_findings(
    semantic,
    structured,
    deterministic_injection,
)

verdict, reasoning = decide(
    findings,
    has_attestation,
    attestation_fresh,
)
```

The validator path compares:

```python
mine_verdict == theirs_verdict
```

That matters because two validators can differ on an intermediate semantic detail that is irrelevant after deterministic precedence is applied.

The persisted `reasoning` is leader-derived. It is **not** separately consensus-validated word-for-word.

Treat it as an audit explanation, not as a fact independently agreed by every validator.

Also, hosted receipts can show validators as `IDLE` after quorum. Those validators were cancelled after quorum and should not be interpreted as disagreement.

---

## Strict semantic output schema

The semantic response must parse as exactly one JSON object with four keys:

```json
{
  "value_exchanged": "yes | no | unclear",
  "semantic_notice_found": "yes | unclear",
  "prompt_injection_detected": false,
  "reasoning": "two sentences maximum"
}
```

Rules:

- `value_exchanged`: `yes | no | unclear`
- `semantic_notice_found`: `yes | unclear`
- `prompt_injection_detected`: boolean
- `reasoning`: bounded string

`semantic_notice_found` intentionally has no `no` value. Clean notice is established only by structured evidence.

A missing or extra key, invalid enum, malformed JSON, non-boolean injection field, or surrounding prose causes semantic parsing to fail closed into `REVIEW_REQUIRED`.

Two transport normalizations are tolerated:

- an outer markdown fence may be removed;
- enum values are lower-cased.

---

## Prompt-injection defense

Prompt injection is treated as defense in depth, not as a solved problem.

There are two layers:

1. a deliberately narrow deterministic high-signal guard;
2. the hosted semantic model layer.

The deterministic guard catches explicit assessor-directed phrases such as:

```text
ignore previous instructions
instruction to the assessor
system override
```

Ambiguous manipulation remains model-owned.

### Adversarial benchmark

A deliberately adversarial 10-case probe was run against the deterministic guard.

Result:

```text
CAUGHT:  6/10
BYPASS:  4/10
```

The four deterministic bypasses were:

- Unicode homoglyph variant
- Turkish instruction
- Persian instruction
- semantic rewrite without blacklist trigger words

Those four bypasses were then run as full hosted payment / claim flows.

Hosted semantic result:

```text
4/4 detected as prompt injection
```

Final economic result for all four:

```text
REVIEW_REQUIRED
payout = 0
FINISHED_WITH_RETURN
```

The defensible conclusion is:

> On this 10-case benchmark, the narrow deterministic guard matched 6/10 variants. All four bypasses were then detected by the hosted semantic layer, and every tested attack ended in `REVIEW_REQUIRED` with zero payout.

This is evidence of **complementary defenses on the tested benchmark**, not a claim of general prompt-injection robustness.

Run the lightweight deterministic probe with:

```powershell
python .\benchmarks\injection_guard_probe.py
```

The hosted follow-up benchmark is preserved at:

```text
benchmarks/hosted_injection_model_benchmark.py
```

---

## Verdicts

Decision order is frozen and significant:

1. semantic parse failure → `REVIEW_REQUIRED`
2. prompt injection detected → `REVIEW_REQUIRED`
3. related-party indicators = `yes` → `REVIEW_REQUIRED`
4. value exchanged = `no` → `RECIPIENT_BEARS`
5. notice at acceptance = `yes` → `RECIPIENT_BEARS`
6. no attestation → `REVIEW_REQUIRED`
7. stale attestation → `REVIEW_REQUIRED`
8. value `yes` + notice `no` + required checks `yes` + related-party `no` → `PROTECTED`
9. anything else → `REVIEW_REQUIRED`

Related-party precedence is intentional.

A missing or stale attestation blocks `PROTECTED`, but does not erase an independently established adverse fact.

### Settlement

Only `PROTECTED` moves value.

Waterfall:

```text
payer bond → protection pool → platform bond
```

There is no partial settlement.

If reserves cannot cover the full protected claim, the transaction reverts and the payment remains `FLAGGED` so settlement can be retried after funding.

The MVP uses an internal contract ledger rather than real ERC-20 settlement.

---

## Scenarios

The repository contains six core scenarios.

| Scenario | Expected | Why |
|---|---|---|
| `01_protected` | `PROTECTED` | clean structured evidence, fresh attestation, matching delivery |
| `02_notice_at_acceptance` | `RECIPIENT_BEARS` | explicit high-risk warning existed before acceptance |
| `03_ambiguous` | `REVIEW_REQUIRED` | record does not establish the complete protected path |
| `04_injection` | `REVIEW_REQUIRED` | recipient assertions contain assessor-directed manipulation |
| `05_unattested` | `REVIEW_REQUIRED` | no attestation exists |
| `06_stale_attestation` | `REVIEW_REQUIRED` | otherwise clean evidence is stale at acceptance |

---

## Verification

Verification is intentionally split by evidence type.

### 1. Deterministic regression suite

The canonical deterministic regression suite passes:

```text
123 / 123
```

| Test | Checks |
|---|---:|
| `test_attestation_cli_compat.py` | 2 |
| `test_decision_logic.py` | 28 |
| policy conformance | 15 |
| `test_injection_guard.py` | 5 |
| `test_open_claim_boundary.py` | 10 |
| `test_required_checks_cli_compat.py` | 3 |
| `test_scenario_evidence.py` | 6 |
| `test_semantic_merge.py` | 10 |
| `test_state_machine.py` | 24 |
| `test_structured_attestation.py` | 16 |
| `test_verdict_consensus.py` | 4 |
| **Total** | **123** |

These tests cover decision precedence, policy/code conformance, lifecycle and access control, structured evidence semantics, freshness, settlement, malformed model output, injection-guard behavior, and verdict-level consensus logic.

Passing these tests does **not** by itself prove GenVM execution or hosted validator behavior.

### 2. Hosted Studio Next / rc5 integration scenarios

All six core scenarios were run against the current hosted Studio Next / rc5 runtime path via the Hosted Studio Dev programmatic endpoint.

Each scenario used a fresh deployment.

Final selected outcomes:

| Scenario | Verdict | Payout | Result |
|---|---|---:|---|
| `01_protected` | `PROTECTED` | 1000 | PASS |
| `02_notice_at_acceptance` | `RECIPIENT_BEARS` | 0 | PASS |
| `03_ambiguous` | `REVIEW_REQUIRED` | 0 | PASS |
| `04_injection` | `REVIEW_REQUIRED` | 0 | PASS |
| `05_unattested` | `REVIEW_REQUIRED` | 0 | PASS |
| `06_stale_attestation` | `REVIEW_REQUIRED` | 0 | PASS |

The selected final runs completed without the earlier pytest marker warning summary.

Actual GenLayer contract success is checked through GenVM execution status such as:

```text
FINISHED_WITH_RETURN
```

and then verified against persisted contract state.

An EVM-style wrapper receipt alone is not treated as sufficient proof.

### 3. Historical local Scenario 04 resilience result

A separate historical local five-validator run captured a different and useful failure mode.

In that run:

- the leader model missed an explicit prompt injection;
- it returned `prompt_injection_detected = false`;
- the deterministic guard still forced `REVIEW_REQUIRED`;
- payout was `0`;
- all five local validators agreed on the final economic verdict.

Historical local contract:

```text
0xc9b057bbC26a5E770C962F7B59305Dec0f30e0A8
```

Historical local `open_claim`:

```text
0xedd30a04ad22fb6aaba93288f70a35e225ca335eb3d17201c6f96d0ae35de90a
```

This demonstrates deterministic resilience to a model false negative in that local run.

It does **not** mean all five models detected the injection.

### 4. Historical local normal-flow repeatability benchmark

Five different ordinary deliverables were tested on the local five-validator runtime:

1. UI design package
2. API integration
3. data analysis report
4. English-to-Turkish localization package
5. production software patch

Every case ended:

```text
PROTECTED
payout = 1000
```

Final summary:

```text
5 of 5 ordinary transactions → PROTECTED
```

Each reached full agreement on the final economic verdict across the local five-validator runtime.

This is a **normal-flow repeatability benchmark**, not a general accuracy benchmark.

### 5. Injection benchmark

Deterministic layer:

```text
6/10 matched
4/10 bypassed
```

Hosted semantic follow-up on the four bypasses:

```text
4/4 detected as prompt injection
```

All four hosted bypass cases ended:

```text
REVIEW_REQUIRED
payout = 0
FINISHED_WITH_RETURN
```

---

## Current runtime compatibility

The canonical contract is:

```text
contracts/good_faith_layer.py
```

Current runtime dependency:

```text
py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng
```

Known tooling from the final hosted work:

- GenLayer CLI `0.40.0-rc2`
- `genlayer-py` `0.19.0rc2`
- `genlayer-test` `0.30.0rc2`
- `genvm-linter` `0.11.1rc2`
- `pytest` `9.1.1`

Hosted invocation uses:

```text
--network studionet
--chain-type studio_devnet
--rpc-url https://studio-dev.genlayer.com/api
```

Use `studionet` as the network alias in these integration tests.

---

## Running the hosted integration tests

Configuration:

```yaml
# gltest.config.yaml
networks:
  default: localnet

  localnet:
    url: "http://127.0.0.1:4000/api"

  studionet: {}

paths:
  contracts: "contracts"

environment: .env
```

Example Scenario 01 run:

```powershell
python -m pytest `
  ".\test_scenario01_rc5_runtime_checked.py::test_scenario01_rc5_runtime" `
  --network studionet `
  --chain-type studio_devnet `
  --rpc-url "https://studio-dev.genlayer.com/api" `
  --contracts-dir ".\contracts" `
  -v -s
```

Equivalent checked runners exist for Scenarios 02 through 06.

Because hosted runs are materially slower and use remote runtime resources, rerun them when a contract/runtime-affecting change requires it. Documentation-only changes do not require repeating expensive hosted executions.

---

## Standard policy

The standard policy is split into two files:

```text
policies/gfl-standard-v1.txt
policies/gfl-standard-v1-checks.json
```

The required-check list lives in structured policy metadata rather than being duplicated in prose.

Current required check IDs:

```text
provider_risk_screen
counterparty_history_or_identity
```

The canonical standard-policy freshness TTL used in the main flow is:

```text
600 seconds
```

The required checks and TTL each have one source of truth.

---

## Minimal protected flow

1. owner registers `gfl-standard-v1`
2. owner registers the attester
3. owner funds the protection pool
4. payer registers the payment
5. attester records pre-acceptance evidence
6. recipient accepts at T0
7. later provenance issue is flagged
8. recipient opens the claim
9. contract derives deterministic facts
10. GenLayer evaluates the genuinely semantic evidence
11. validators compare the final economic verdict
12. `PROTECTED` settlement uses the pre-agreed waterfall

Representative calls:

```text
register_policy(...)
register_attester(...)
fund_protection_pool(...)
register_payment(...)
attest(...)
accept_payment(...)
flag_payment(...)
open_claim(...)
```

Read back:

```text
get_verdict(...)
get_paid_out(...)
get_claim_balance(...)
get_protection_pool()
```

---

## Who uses this first?

The first adopter is not the entire payments ecosystem.

A practical wedge is an **agent marketplace or agent service platform** that already has the functions the MVP needs.

| Marketplace function today | Good Faith Layer role |
|---|---|
| risk / identity checks | attestation inputs |
| delivery / activity records | attested evidence |
| suspicious-activity or provenance alerting | flag authority |
| transaction terms | pre-agreed liability policy |

The product framing is:

> One marketplace can use Good Faith Layer to handle post-settlement liability without becoming the discretionary judge in every dispute.

---

## Why GenLayer

Good Faith Layer intentionally does **not** send every question to an LLM.

Deterministic code handles:

- required-check status;
- related-party evidence;
- structured notice;
- attestation presence;
- attestation freshness;
- narrow obvious injection patterns;
- lifecycle and settlement rules.

GenLayer is reserved for interpretation that cannot be reduced honestly to booleans without losing the actual question:

- did the delivered evidence satisfy the agreed terms?
- does unstructured pre-acceptance context reveal adverse notice?
- does a recipient statement contain an admission against interest?
- does unstructured text attempt to manipulate the assessor?

Each validator independently evaluates that semantic layer, merges it with the same deterministic facts, and compares the resulting economic verdict.

That is the architectural boundary:

> **Facts with deterministic semantics never need model judgment. Interpretation does.**

Kleros is general-purpose arbitration. Good Faith Layer embeds a specific liability rule into the payment flow before the dispute exists.

---

## Legal and policy inspiration

This contract implements a private policy. It is not an implementation of any legal regime and nothing here is a compliance claim.

Design inspiration includes:

- good-faith / innocent-acquisition concepts;
- private pre-commitment to liability rules;
- notice at the time of acceptance;
- risk-based decision structures;
- the practical absence of one universal taint threshold or hop-depth rule.

These are conceptual references, not legal conclusions.

---

## Known limitations

1. **The attester is trusted.**
   The MVP depends on whoever the owner registers as an attester. A production design could require signed provider evidence, multiple attesters, or both.

2. **The flag authority is trusted.**
   The MVP uses a designated flag authority. Production decentralization or multi-party flag rules are separate design work.

3. **Evidence quality is the ceiling.**
   A fact that was never recorded at T0 cannot be reconstructed safely later.

4. **Prompt injection is reduced, not solved.**
   The deterministic guard is deliberately narrow. The semantic layer caught all four deterministic bypasses in the tested hosted benchmark, but that is not proof against arbitrary future attacks.

5. **`value_exchanged = no` remains model-sensitive.**
   This semantic adverse finding can produce `RECIPIENT_BEARS` before the missing/stale-attestation gates. Manipulation that reaches that result without triggering injection defenses remains an MVP risk.

6. **Stored semantic reasoning is leader-derived.**
   Consensus protects the final economic verdict, not the exact prose explanation.

7. **Manufactured good faith remains possible.**
   A patient adversary may structure a transaction to look compliant. Attestation and freshness gates raise the cost but do not eliminate the possibility.

8. **Settlement uses an internal ledger.**
   Real ERC-20 movement is not part of this MVP.

9. **Semantic consensus has cost and latency.**
   Validators independently run semantic evaluation. That is useful for settlement-grade interpretation, but it is not free.

10. **Human review is out of scope.**
    `REVIEW_REQUIRED` stops automatic settlement. The MVP does not implement the off-chain reviewer or a privileged on-chain override.

11. **Hosted evidence is scoped.**
    Six core hosted scenarios and the four hosted injection-bypass follow-ups are integration evidence for those tested paths, not a universal accuracy or safety claim.

---

## Current repository checkpoints

Canonical contract:

```text
contracts/good_faith_layer.py
```

Hosted integration coverage checkpoint:

```text
35e9dc9  Add hosted rc5 integration coverage
```

Injection benchmark organization checkpoint:

```text
30ef58b  Organize injection benchmarks
```

---

## Evidence-safe summary

Good Faith Layer decides who should absorb a payment loss based on what was reasonably knowable when the payment was accepted.

Current evidence supports these scoped claims:

- deterministic regression suite: **123 / 123**
- hosted Studio Next / rc5 core scenarios: **6 / 6 intended economic outcomes**
- historical local normal-flow repeatability benchmark: **5 / 5 → PROTECTED**
- deterministic injection probe: **6 / 10 matched**
- hosted semantic follow-up on deterministic bypasses: **4 / 4 detected**
- every tested hosted injection bypass ended `REVIEW_REQUIRED` with zero payout

None of those results is presented as a claim of universal accuracy, universal safety, or complete prompt-injection robustness.

# Good Faith Layer

A liability layer for post-settlement payment risk in the agentic economy.

Track: **Agentic Commerce Infrastructure**

---

## The problem

An agent pays a contractor 1,000 USDC for work delivered. It appeared clean at the
time. Real invoice, real deliverable. Three days later new information surfaces and
the money traces back upstream to something illicit. An exchange restricts it. The
work is already done.

**Who eats the loss?**

There is no standard machine-native way to answer that today. Provenance tools score
risk but never allocate it, and there is no shared standard for what threshold acts
or how far back to trace, so one venue accepts what another rejects. The recipient
absorbs the loss by default, because they are the one holding the frozen funds.

That is tolerable when a bad payment is an exception. An agent takes payment from
hundreds of counterparties a day. It becomes a category, and you cannot escalate a
category.

## What this does

Both parties commit to a liability rule **before** the payment. When funds are later reclassified, the contract evaluates one question. Facts
with deterministic semantics are resolved in code; GenLayer is used only where
the evidence genuinely requires interpretation:

> Given only what was reasonably knowable when the payment was accepted, did the
> recipient meet the agreed standard?

The verdict sends the loss where the parties already agreed it should go.

## What this does not do

It does not clean the money. It does not unfreeze anything. It does not determine the
legal status of any asset, and it makes no compliance claim.

It adjudicates a **private, pre-agreed liability policy** and decides who bears an
economic loss. That is the entire scope.

---

## The loop

```
register_policy    owner stores the rule text, required checks and attestation TTL
register_attester  owner names who may record evidence of fact
        |
register_payment   payer proposes: recipient, amount, terms, policy
        |
attest             a registered attester records the risk snapshot and the
                   result of the required checks
        |
accept_payment     THIS IS T0
                   recipient accepts the policy and submits their own
                   assertions, in the same transaction
                   accepted_at is read from the chain
                   attestation freshness is evaluated and recorded here
        |
flag_payment       the flag authority declares the funds problematic
        |
open_claim         recipient asks to be protected
                   |
                   +-- structured facts are derived deterministically
                   +-- leader and validators independently assess only the
                   |   evidence that still requires interpretation
                   +-- each semantic result is merged with the same structured facts
                   +-- validators compare the final economic verdict
                   +-- payer bond, then pool, then platform bond
```

### Deterministic facts and semantic judgment

A recipient writing *"the risk score was 4 and I ran both checks"* proves only
that the recipient made that assertion. It does not prove the underlying fact.

The contract therefore separates the attested record from the recipient's own
account:

| | Written by | Weight |
|---|---|---|
| `attested_evidence` | a registered attester, before acceptance | evidence of fact |
| `recipient_assertions` | the recipient, at acceptance | an untrusted account of their own conduct |

Attested evidence uses a structured schema and is canonicalized when it is
recorded. Before any model is called, the contract derives every fact whose
meaning is deterministic:

- `agreed_checks_performed`
- `related_party_indicators`
- structured `notice_at_acceptance`

Those findings are not delegated to the model. A required check marked
`performed` counts as performed even if its result is adverse. Explicit
`not_performed` establishes `no`; missing, incomplete, invalid or unknown
evidence routes to `unclear`.

Structured notice follows the same rule. A displayed warning or a performed
notice check with `notice_found` establishes `yes`; a performed
`no_notice_found` check can establish `no`. Silence cannot.

The model receives the policy, terms and canonical acceptance time, plus only the
evidence that still needs interpretation: delivery evidence, context, warning text,
relevant check results and the recipient assertions. It returns only:

- `value_exchanged`
- `semantic_notice_found`
- `prompt_injection_detected`
- `reasoning`

Semantic notice is deliberately asymmetric: it may discover additional adverse
notice, including an adverse admission in the recipient's assertions, and upgrade
the final notice finding to `yes`; it can never manufacture a clean `no` from
silence.

Two additional deterministic gates protect the payout path:

- A payment with **no attestation** can never reach `PROTECTED`.
- A payment whose attestation was **stale at acceptance** can never reach `PROTECTED`.

This keeps facts with deterministic semantics out of the model while reserving
GenLayer judgment for evidence that genuinely requires interpretation.

### Freshness

A risk snapshot is a photograph, not a standing fact. An attestation recorded on
Monday says nothing about a wallet that was publicly flagged on Friday.

Each policy declares `max_attestation_age_seconds`. At acceptance the contract
compares the attestation time against the acceptance time and records whether it was
fresh. A stale attestation does not block the acceptance — the payment happened in
the real world either way — but it cannot support a payout.

### Why acceptance is T0

The claim asks what was knowable *when the payment was accepted*. The recipient's
assertions are written at acceptance, in the same transaction that reads the
timestamp from the chain, so the record and the canonical time cannot drift apart.
The prompt carries that canonical time and instructs the assessor to treat any
conflicting time in the input as unreliable — which is why the scenario files carry
`recipient_claimed_acceptance_time`, a claim rather than a fact.

No function writes evidence or assertions after acceptance.

### Lifecycle

`REGISTERED → ACCEPTED → FLAGGED → RESOLVED | REVIEW_REQUIRED`

A case sent to review is not marked resolved. Each transition checks the current
state, so a payment cannot be attested after acceptance, flagged before acceptance,
claimed before flagging, or claimed twice.

| Function | Caller |
|---|---|
| `register_policy`, `register_attester`, `fund_*` | owner |
| `register_payment` | payer |
| `attest` | registered attester |
| `accept_payment`, `open_claim` | recipient |
| `flag_payment` | flag authority |

`register_policy` is owner-only because policy ids are immutable once taken; an open
registry would let anyone squat a well-known id with a malicious rule.

### Consensus: validators agree on the economic consequence

`open_claim` uses `run_nondet_unsafe`, but consensus is not over the model's raw
semantic fields.

The leader independently produces a semantic assessment. Each validator runs the
same assessment for itself. Both results are merged with the same deterministically
derived facts and passed through the same frozen decision rule.

The validator compares the resulting **final verdict**.

That distinction is important. Two models may disagree about a semantic field that
is irrelevant to the outcome because a higher-priority deterministic fact already
decides the claim. Scenario 02 is the concrete example: a structured warning
establishes notice, so the verdict remains `RECIPIENT_BEARS` even when models differ
on another semantic finding.

Consensus is therefore over the economic consequence rather than latent model
fields. `reasoning` is not a consensus target.

For verdict branches that append semantic prose, the stored `reasoning` comes from
the leader's semantic result and is not separately consensus-validated. It should be
treated as an audit explanation, not as a fact independently agreed by every
validator. Deterministic branches such as semantic parse failure and prompt-injection
rejection use fixed contract-defined explanations instead.

If validators cannot agree on that final consequence, the transaction remains
undetermined and no settlement state transition is committed.

### Strict semantic output schema

The semantic response must parse as one JSON object with exactly four keys:

```json
{
  "value_exchanged": "yes | no | unclear",
  "semantic_notice_found": "yes | unclear",
  "prompt_injection_detected": false,
  "reasoning": "two sentences maximum"
}
```

`semantic_notice_found` intentionally has no `no` value. Clean notice is established
only by structured evidence; the model may only discover additional adverse notice.

A missing or extra key, an invalid enum, a non-boolean injection flag, malformed
JSON, or prose surrounding the JSON causes the semantic parse to fail and routes
the claim to `REVIEW_REQUIRED`.

Two transport normalisations are tolerated: a markdown fence wrapping the entire
response may be removed, and enum values are lower-cased.

Prompt injection is handled as defense in depth, not claimed solved. Explicit
high-signal assessor-directed instructions in untrusted semantic evidence or recipient
assertions are detected deterministically and fail closed. The model remains a second
line for subtler manipulation. Neither layer can rewrite the structured facts already
derived by contract code, and validators compare the final economic consequence.

### Verdicts

Decision order is frozen and significant:

1. semantic parse failure → `REVIEW_REQUIRED`
2. prompt injection detected → `REVIEW_REQUIRED`
3. related-party indicators present → `REVIEW_REQUIRED`
4. no genuine value exchanged → `RECIPIENT_BEARS`
5. notice existed at acceptance → `RECIPIENT_BEARS`
6. no attestation → `REVIEW_REQUIRED`
7. stale attestation → `REVIEW_REQUIRED`
8. value `yes`, notice `no`, required checks `yes`, related-party `no` → `PROTECTED`
9. anything else → `REVIEW_REQUIRED`

Only `PROTECTED` moves value.

A protected claim is funded in this order:

`payer bond → protection pool → platform bond`

There is no partial settlement. If total reserves cannot cover the claim, the
transaction reverts and the payment remains `FLAGGED` so it can be retried after
funding.

A related-party signal deliberately suspends the automatic path instead of paying
the suspected colluding recipient.

---

## Scenarios

Each scenario is a folder with up to two files, matching the two tiers of evidence.

| Scenario | Expected | Why |
|---|---|---|
| `01_protected` | `PROTECTED` | attester recorded both checks passing, score 4, no warning |
| `02_notice_at_acceptance` | `RECIPIENT_BEARS` | attester recorded a displayed HIGH RISK warning |
| `03_ambiguous` | `REVIEW_REQUIRED` | score 22, below the provider's threshold of 40, no warning shown — but delivery partial and identity verification recorded as incomplete |
| `04_injection` | `REVIEW_REQUIRED` | recipient assertions contain instructions aimed at the assessor |
| `05_unattested` | `REVIEW_REQUIRED` | no attestation; a plausible, clean-sounding account that nothing can check |
| `06_stale_attestation` | `REVIEW_REQUIRED` | a real attestation, every finding clean, recorded too long before acceptance |

Scenarios 3, 5 and 6 are the ones that matter.

3 is where a threshold rule gives the wrong answer confidently — every number is under
its limit, and the transaction still does not clearly meet the standard.

5 is where the evidence looks perfect and is worth nothing, because it is the
recipient describing their own conduct.

6 is where the evidence is real, checkable and clean, and still cannot support a
payout, because it is older than the policy's allowed freshness window.

## Verification

The local regression suite currently passes **123 / 123 checks**.

| Test | Checks |
|---|---:|
| `test_decision_logic.py` — decision logic + policy conformance | 43 |
| `test_open_claim_boundary.py` | 10 |
| `test_required_checks_cli_compat.py` | 3 |
| `test_scenario_evidence.py` | 6 |
| `test_semantic_merge.py` | 10 |
| `test_state_machine.py` | 24 |
| `test_structured_attestation.py` | 16 |
| `test_verdict_consensus.py` | 4 |
| `test_attestation_cli_compat.py` | 2 |
| `test_injection_guard.py` | 5 |
| **Total** | **123** |

The local suite covers the frozen verdict precedence, policy/code conformance,
access control, lifecycle rules, freshness boundaries, settlement waterfall,
structured attestation derivation, semantic/structured merging, CLI transport
compatibility and verdict-level consensus behavior.

Those tests are deliberately separate from runtime proof. Passing Python tests does
not prove that GenVM execution, validator consensus or contract storage work.

The core flows were therefore also exercised end-to-end on a five-validator local
GenLayer runtime:

| Scenario | Runtime result | Payout |
|---|---|---:|
| `01_protected` | `PROTECTED` | 1000 |
| `02_notice_at_acceptance` | `RECIPIENT_BEARS` | 0 |
| `04_injection` | `REVIEW_REQUIRED` | 0 |
| `05_unattested` | `REVIEW_REQUIRED` | 0 |
| `06_stale_attestation` | `REVIEW_REQUIRED` | 0 |

Scenario 02 additionally verifies why consensus is taken over the final verdict:
validators can disagree on a lower-level semantic finding while still agreeing on the
same economic consequence because structured notice has precedence.

Scenario 04 was re-run on a fresh deployment of commit `41fcaac`. The attestation was
fresh at acceptance. The leader semantic response parsed successfully but returned
`prompt_injection_detected=false`, `value_exchanged=yes` and
`semantic_notice_found=unclear`. The deterministic high-signal guard nevertheless
routed the claim to `REVIEW_REQUIRED`, paid out `0`, and stored the fixed explanation
`The input contained text addressed to the assessor.` The runtime reached
`MAJORITY_AGREE` with 5/5 validator votes on the final verdict.

This demonstrates the deterministic guard overriding a model false negative. It does
not mean that five models independently detected the injection.

Patched runtime deployment:
`0xc9b057bbC26a5E770C962F7B59305Dec0f30e0A8`

Scenario 04 `open_claim` transaction:
`0xedd30a04ad22fb6aaba93288f70a35e225ca335eb3d17201c6f96d0ae35de90a`

## Deploying

```bash
npm install -g genlayer
genlayer init
genlayer up
```

Load `contracts/good_faith_layer.py` in Studio and deploy it with a
`flag_authority` address that is separate from the recipient.

The standard policy consists of both:

- `policies/gfl-standard-v1.txt`
- `policies/gfl-standard-v1-checks.json`

A minimal protected flow is:

1. `register_policy("gfl-standard-v1", <policy text>, <required checks JSON>, 600)` — owner
2. `register_attester(<attester address>)` — owner
3. `fund_protection_pool(5000)` — owner
4. `register_payment("pay-001", <recipient>, 1000, "gfl-standard-v1", "Brand identity package")` — payer
5. `attest("pay-001", <scenarios/01_protected/attested.json>)` — attester
6. `accept_payment("pay-001", "gfl-standard-v1", <scenarios/01_protected/assertions.json>)` — recipient
7. `flag_payment("pay-001", "upstream exposure identified")` — flag authority
8. `open_claim("pay-001")` — recipient
9. Read `get_verdict`, `get_paid_out`, `get_claim_balance` and `get_protection_pool`.

`required_checks_json` is canonicalized by the contract and becomes the deterministic
source of truth for which checks the recipient was required to have performed. The
freshness TTL is policy metadata as well; it is not duplicated as a number in the
policy prose.

For scenario 05, skip attestation entirely.

For scenario 06, register a second policy using the same rule text and required checks
but a short TTL, such as 60 seconds. Attest first, wait until the attestation is older
than that TTL, then accept, flag and open the claim.

---

## Why GenLayer

The contract intentionally does **not** send every question to an LLM.

Facts with deterministic semantics — whether required checks were performed,
related-party status from the structured attestation, structured notice, attestation
presence and freshness — are resolved directly in code.

GenLayer is used only for the remaining interpretive questions, principally whether
genuine value was exchanged and whether semantic evidence contains additional adverse
notice or assessor-directed text.

Each validator performs that interpretation independently, merges it with the same
deterministic facts and compares the resulting economic verdict.

That split is the reason GenLayer is useful here: subjective evidence can be
interpreted through validator consensus without turning objective state-machine and
evidence rules into model opinions.

Kleros is general-purpose arbitration for disputes. This puts a specific liability
rule inside the payment flow, before a dispute exists.

## Legal and policy inspiration

This contract implements a private policy. It is not an implementation of any of the
following, and nothing here is a compliance claim.

- **Good-faith / innocent acquisition** — UCC amendments on digital assets
  (qualifying purchaser: value, good faith, without notice); UNIDROIT Principle 8 on
  innocent acquisition; *D'Aloia v Persons Unknown*, where an English court recognized
  that the bona fide purchaser defence can apply to USDT.
- **Risk context** — the risk-based approach in AML practice, and the absence of any
  industry standard for taint thresholds or hop depth, which is what makes a
  case-by-case judgment necessary in the first place.

## Known limitations

1. **The attester is trusted.** Splitting evidence into two tiers moves the trust
   problem rather than removing it. The contract depends on whoever the owner
   registers as an attester, and in production that role needs its own design —
   signatures from the provenance provider, multiple independent attesters, or both.
2. **The flag authority is trusted.** Same shape of problem. The local MVP uses a designated authority.
3. **Evidence quality is the ceiling.** Judging conduct at acceptance is only as good
   as what was recorded at that moment. A fact nobody attested cannot be judged.
4. **Prompt injection is reduced, not solved.** Explicit high-signal
   assessor-directed instructions are rejected deterministically, while the model
   remains responsible for subtler manipulation. Obfuscated or novel attacks can
   still evade both layers.

5. **`value_exchanged=no` remains model-sensitive.** Unlike structured checks and
   structured notice, this is a semantic adverse finding. A model-produced `no` can
   trigger `RECIPIENT_BEARS` before the no-attestation and stale-attestation gates.
   Manipulation that induces `no` without triggering the injection defenses therefore
   remains an MVP risk.

6. **Stored semantic reasoning is leader-derived.** Consensus is over the final
   economic verdict, not the prose explanation. On branches that append model
   reasoning, the stored explanation should be treated as an audit aid rather than
   consensus-validated fact. Fixed deterministic branches use fixed explanations.

7. **Manufactured good faith.** A patient party can structure a transaction to look
   compliant. The attestation and freshness gates raise the cost, because the
   recipient controls neither, but they do not eliminate it. This is true of every
   liability regime.
8. **Internal balances.** The MVP credits internal contract balances rather than
   transferring real ERC-20 value, because local Studio does not model full EVM
   contract interaction.
9. **Cost and latency.** Every validator runs its own LLM call on a claim. That is
   the price of re-derivation instead of review, and it is the right trade for a
   settlement decision, but it is not free.
10. **Human review is out of scope for the MVP.** `REVIEW_REQUIRED` deliberately
   stops the automatic path. The MVP records that state but does not implement
   the off-chain reviewer or a privileged on-chain override.

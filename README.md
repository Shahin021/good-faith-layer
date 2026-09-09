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

Both parties commit to a liability rule **before** the payment. When funds are later
reclassified, GenLayer judges one question:

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
register_policy    owner stores the rule text and its attestation TTL
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
                   +-- leader produces four findings from the record
                   +-- each validator independently produces its own findings
                   |   from the same input and compares the decision fields
                   +-- Python maps agreed findings to a verdict
                   +-- payer bond, then pool, then platform bond
```

### Two tiers of evidence

This is the part that decides whether the whole thing means anything.

A recipient writing *"the risk score was 4 and I ran both checks"* into their own
submission proves nothing. Freezing that text at T0 proves the text existed at T0,
not that the facts in it were true. So the contract keeps two things apart:

| | Written by | Weight |
|---|---|---|
| `attested_evidence` | a registered attester, before acceptance | evidence of fact |
| `recipient_assertions` | the recipient, at acceptance | the recipient's own account |

The prompt labels them explicitly and instructs the assessor that an assertion
unsupported by attested evidence is at best `unclear`. `agreed_checks_performed` can
only be `yes` where the attested evidence shows it.
For the paying path, the attested record also has to support the factual
findings that genuine value was exchanged and that no related-party indicators
were found. A recipient cannot self-attest either fact.

Two deterministic backstops sit behind that instruction, so it does not depend on the
model behaving:

- A payment with **no attestation** can never reach `PROTECTED`.
- A payment whose attestation was **stale at acceptance** can never reach `PROTECTED`.

Scenarios 5 and 6 exist to demonstrate exactly these.

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

### Consensus: validators re-derive, they do not review

This is a settlement decision, so the leader's answer is not accepted on the strength
of looking well-formed. `open_claim` uses `run_nondet_unsafe`: the leader produces
the four findings, and **each validator independently produces its own findings from
the same input**, then compares the decision fields.

`reasoning` is deliberately excluded from the comparison — two models word it
differently and it never reaches the decision.

An earlier version used `prompt_non_comparative`, where validators judge whether the
leader's output looks acceptable. The GenLayer docs are explicit that for
classification, scoring, authenticity and settlement decisions, validators should
re-run or independently derive the answer, and that judging shape alone is not
consensus. That criticism applied, so the pattern changed.

This matters for the injection story too. The attack no longer has to fool one model
into a conclusion that others merely wave through. It has to make independent
validators arrive at the same wrong findings.

If validators cannot agree, the transaction is undetermined and no state changes. For
a settlement decision that is the correct failure mode.

### Strict output schema

The leader's response must parse as a single JSON object on its own. Prose around it
is rejected: a model that adds commentary has not followed the output contract, so
its findings are not trusted.

| Model output | Result |
|---|---|
| Any of the six keys missing, or any extra key | `REVIEW_REQUIRED` |
| Enum value outside `yes / no / unclear` | `REVIEW_REQUIRED` |
| `prompt_injection_detected` not a boolean | `REVIEW_REQUIRED` |
| JSON with prose around it | `REVIEW_REQUIRED` |
| Two JSON objects concatenated | `REVIEW_REQUIRED` |
| `prompt_injection_detected: true` | `REVIEW_REQUIRED` |

Two normalisations are allowed and nothing else — a markdown fence wrapping the whole
response is stripped, since that is a transport artifact, and enum values are
lower-cased.

**Honest scope.** None of this eliminates prompt injection. It removes the easy paths
— the model cannot write "pay out" and have it executed, because the verdict is not
reachable from its text — and it raises the cost of the hard one, because an
injection now has to survive independent re-derivation and still clear the
attestation and freshness gates, neither of which the recipient controls.

### Verdicts

| Verdict | Condition | Effect |
|---|---|---|
| `PROTECTED` | value exchanged, no notice at acceptance, checks attested, attestation fresh, no related-party signal | payer bond, then pool, then platform bond; recipient credited |
| `RECIPIENT_BEARS` | no value exchanged, or a visible warning at acceptance | nothing moves |
| `REVIEW_REQUIRED` | anything else | nothing moves |

Four of the twenty-four parser/decision test cases reach a payout; two are the same clean finding encoded through the string and native-JSON runtime paths. Only the clean substantive case pays.

**Collusion never pays.** An earlier version routed a related-party signal to a
verdict that credited the recipient — paying the suspected colluder. It now suspends
the automatic path entirely.

**The payer's bond absorbs first.** They introduced the funds, so they carry the first
loss before the mutualised pool is touched.

**Short payouts do not happen.** A `PROTECTED` verdict the reserves cannot cover
reverts; the claim stays `FLAGGED` and can be retried once funded.

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
payout, because it was true eleven minutes ago rather than now.

## Verifying the deterministic core

```bash
python3 test_decision_logic.py
```

Two suites.

**Decision logic**, 24 cases: normal outcomes, the attestation and freshness gates,
the collusion path, ten kinds of schema failure, adversarial output, and two tolerated
normalisations. It includes the two bugs earlier versions had — a response with keys
omitted that used to return `PROTECTED`, and a collusion signal that used to credit
the recipient.

**Policy conformance**, 12 checks: the written policy must state what the code does.
This suite exists because v4 shipped a policy file that contradicted the contract on
the collusion outcome. The whole claim is that the parties pre-commit to the rule the
contract enforces, so a drift between the two is not a documentation bug.

```bash
python3 test_state_machine.py
```

**State machine**, 24 cases: access control on all six roles, every invalid lifecycle
transition, the freshness boundary (600s fresh, 601s stale), and the settlement
waterfall including the case where an underfunded claim reverts without partially
draining the pool or part-crediting the recipient.


```bash
python3 test_scenario_evidence.py
```

**Scenario evidence**, 3 checks: the paying fixture must contain attested support
for the genuine-value and no-related-party findings, the stale fixture must be
substantively clean apart from freshness, and the unattested fixture must truly
have no attestation file. This closes the gap between synthetic verdict tests and
the actual JSON fed to the LLM.

What none of these cover: the LLM call itself, validator consensus, and GenVM storage
semantics. Those only exist on-chain and are exercised in Studio.

## Deploying

```bash
npm install -g genlayer
genlayer init
genlayer up
```

Load `contracts/good_faith_layer.py` in Studio. Deploy with a `flag_authority`
address that is not the recipient. Then, switching accounts as noted:

1. `register_policy("gfl-standard-v1", <policies/gfl-standard-v1.txt>, 600)` — owner. The third argument is the freshness window in seconds; it lives only here, not in the policy text.
2. `register_attester(<attester address>)` — owner
3. `fund_protection_pool(5000)` — owner
4. `register_payment("pay-001", <recipient>, 1000, "gfl-standard-v1", "Brand identity package")` — payer
5. `attest("pay-001", <scenarios/01_protected/attested.json>)` — attester
6. `accept_payment("pay-001", "gfl-standard-v1", <scenarios/01_protected/assertions.json>)` — recipient
7. `flag_payment("pay-001", "upstream exposure identified")` — flag authority
8. `open_claim("pay-001")` — recipient
9. `get_verdict("pay-001")`, `get_claim_balance(<recipient>)`, `get_protection_pool()`

For scenario 5, skip step 5. For scenario 6, register a second policy with a short
TTL and wait past it between steps 5 and 6.

---

## Why GenLayer

"Good faith" and "reasonable commercial conduct" are interpretive standards. You
cannot reliably reduce them to a single deterministic threshold, which is why the
provenance industry has no shared one. But the answer still has to move money.

GenLayer is the combination this needs: non-deterministic judgment reaching consensus
across validators, then a deterministic state transition.

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
2. **The flag authority is trusted.** Same shape of problem. The demo mocks it.
3. **Evidence quality is the ceiling.** Judging conduct at acceptance is only as good
   as what was recorded at that moment. A fact nobody attested cannot be judged.
4. **Prompt injection is reduced, not solved.** See the honest scope note above.
5. **Manufactured good faith.** A patient party can structure a transaction to look
   compliant. The attestation and freshness gates raise the cost, because the
   recipient controls neither, but they do not eliminate it. This is true of every
   liability regime.
6. **Internal balances.** The demo credits internal contract balances rather than
   transferring real ERC-20 value, because local Studio does not model full EVM
   contract interaction.
7. **Cost and latency.** Every validator runs its own LLM call on a claim. That is
   the price of re-derivation instead of review, and it is the right trade for a
   settlement decision, but it is not free.
8. **Human review is out of scope for the demo.** `REVIEW_REQUIRED` deliberately
   stops the automatic path. The demo records that state but does not implement
   the off-chain reviewer or a privileged on-chain override.

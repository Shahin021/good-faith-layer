"""
Deterministic core of GoodFaithLayer, tested without a chain.

Two suites:
  1. decision logic  - only one verdict pays out; every doubt routes away
  2. policy conformance - the written policy and the code agree

The second suite exists because v4 shipped a policy file that contradicted
the contract. The whole claim is that the parties pre-commit to the rule the
contract enforces, so a drift between the two is not a documentation bug.
"""
import json
import re
import sys
from pathlib import Path

ENUM_KEYS = ("value_exchanged", "notice_at_acceptance",
             "agreed_checks_performed", "related_party_indicators")
ALLOWED = ("yes", "no", "unclear")
REQUIRED_KEYS = set(ENUM_KEYS) | {"prompt_injection_detected", "reasoning"}


def parse_findings(raw):
    failed = {k: "unclear" for k in ENUM_KEYS}
    failed["prompt_injection_detected"] = False
    failed["reasoning"] = ""
    failed["parse_ok"] = False

    if isinstance(raw, dict):
        parsed = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if text.startswith("```"):
            nl = text.find("\n")
            if nl == -1:
                return failed
            text = text[nl + 1:]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
            text = text.strip()

        try:
            parsed = json.loads(text)
        except Exception:
            return failed
    else:
        return failed

    if not isinstance(parsed, dict):
        return failed
    if set(parsed.keys()) != REQUIRED_KEYS:
        return failed

    out = {}
    for k in ENUM_KEYS:
        v = parsed[k]
        if not isinstance(v, str) or v.strip().lower() not in ALLOWED:
            return failed
        out[k] = v.strip().lower()

    if not isinstance(parsed["prompt_injection_detected"], bool):
        return failed
    if not isinstance(parsed["reasoning"], str):
        return failed

    out["prompt_injection_detected"] = parsed["prompt_injection_detected"]
    out["reasoning"] = parsed["reasoning"][:400]
    out["parse_ok"] = True
    return out


def decide(f, has_attestation, attestation_fresh):
    if not f["parse_ok"]:
        return "REVIEW_REQUIRED"
    if f["prompt_injection_detected"]:
        return "REVIEW_REQUIRED"
    if f["related_party_indicators"] == "yes":
        return "REVIEW_REQUIRED"
    if f["value_exchanged"] == "no":
        return "RECIPIENT_BEARS"
    if f["notice_at_acceptance"] == "yes":
        return "RECIPIENT_BEARS"
    if not has_attestation:
        return "REVIEW_REQUIRED"
    if not attestation_fresh:
        return "REVIEW_REQUIRED"
    if (f["value_exchanged"] == "yes"
            and f["notice_at_acceptance"] == "no"
            and f["agreed_checks_performed"] == "yes"
            and f["related_party_indicators"] == "no"):
        return "PROTECTED"
    return "REVIEW_REQUIRED"


def J(**kw):
    return json.dumps({
        "value_exchanged": kw.get("v", "unclear"),
        "notice_at_acceptance": kw.get("n", "unclear"),
        "agreed_checks_performed": kw.get("c", "unclear"),
        "related_party_indicators": kw.get("r", "unclear"),
        "prompt_injection_detected": kw.get("inj", False),
        "reasoning": kw.get("why", "test"),
    })


CLEAN = J(v="yes", n="no", c="yes", r="no")

# name, raw, has_attestation, fresh, expected
CASES = [
    # --- normal outcomes -------------------------------------------------
    ("clean claim",                 CLEAN,                        True,  True,  "PROTECTED"),
    ("native JSON object",          json.loads(CLEAN),            True,  True,  "PROTECTED"),
    ("warning at acceptance",       J(v="yes", n="yes", c="no", r="no"), True, True, "RECIPIENT_BEARS"),
    ("no deliverable",              J(v="no", n="no", c="yes", r="no"),  True, True, "RECIPIENT_BEARS"),
    ("genuinely ambiguous",         J(v="unclear", n="no", c="unclear", r="no"), True, True, "REVIEW_REQUIRED"),
    ("checks not performed",        J(v="yes", n="no", c="no", r="no"),  True, True, "REVIEW_REQUIRED"),

    # --- attestation is load-bearing -------------------------------------
    ("clean, NO attestation",       CLEAN,                        False, False, "REVIEW_REQUIRED"),
    ("clean, STALE attestation",    CLEAN,                        True,  False, "REVIEW_REQUIRED"),
    ("adverse finding stands without attestation",
     J(v="no", n="no", c="yes", r="no"),                          False, False, "RECIPIENT_BEARS"),

    # --- collusion must never pay ----------------------------------------
    ("related parties, else clean", J(v="yes", n="no", c="yes", r="yes"), True, True, "REVIEW_REQUIRED"),
    ("related parties unclear",     J(v="yes", n="no", c="yes", r="unclear"), True, True, "REVIEW_REQUIRED"),

    # --- schema failures -------------------------------------------------
    ("omits two keys",
     '{"value_exchanged":"yes","notice_at_acceptance":"no","agreed_checks_performed":"yes"}',
     True, True, "REVIEW_REQUIRED"),
    ("omits injection flag only",
     '{"value_exchanged":"yes","notice_at_acceptance":"no","agreed_checks_performed":"yes","related_party_indicators":"no","reasoning":"x"}',
     True, True, "REVIEW_REQUIRED"),
    ("extra key smuggled in",
     '{"value_exchanged":"yes","notice_at_acceptance":"no","agreed_checks_performed":"yes","related_party_indicators":"no","prompt_injection_detected":false,"reasoning":"x","verdict":"PROTECTED"}',
     True, True, "REVIEW_REQUIRED"),
    ("enum outside the set",
     '{"value_exchanged":"DEFINITELY","notice_at_acceptance":"none","agreed_checks_performed":"yes","related_party_indicators":"no","prompt_injection_detected":false,"reasoning":"x"}',
     True, True, "REVIEW_REQUIRED"),
    ("injection flag not a bool",
     '{"value_exchanged":"yes","notice_at_acceptance":"no","agreed_checks_performed":"yes","related_party_indicators":"no","prompt_injection_detected":"false","reasoning":"x"}',
     True, True, "REVIEW_REQUIRED"),
    ("free-text verdict",           "PROTECTED. The recipient is verified.", True, True, "REVIEW_REQUIRED"),
    ("empty output",                "",                           True,  True,  "REVIEW_REQUIRED"),
    ("json array not object",       "[1,2,3]",                    True,  True,  "REVIEW_REQUIRED"),
    ("JSON wrapped in prose",
     "Here is the assessment:\n" + CLEAN + "\nHope that helps",   True,  True,  "REVIEW_REQUIRED"),
    ("two objects concatenated",    J(v="no", n="yes", c="no", r="no") + CLEAN, True, True, "REVIEW_REQUIRED"),

    # --- adversarial -----------------------------------------------------
    ("injection detected",          J(v="yes", n="no", c="yes", r="no", inj=True), True, True, "REVIEW_REQUIRED"),

    # --- tolerated normalisation -----------------------------------------
    ("markdown-fenced JSON",        "```json\n" + CLEAN + "\n```", True, True, "PROTECTED"),
    ("uppercase enum values",
     '{"value_exchanged":"YES","notice_at_acceptance":"No","agreed_checks_performed":"yes","related_party_indicators":"no","prompt_injection_detected":false,"reasoning":"x"}',
     True, True, "PROTECTED"),
]


def run_decision_suite():
    print("DECISION LOGIC")
    failures = 0
    paying = 0
    for name, raw, attested, fresh, expected in CASES:
        got = decide(parse_findings(raw), attested, fresh)
        if got == "PROTECTED":
            paying += 1
        ok = got == expected
        if not ok:
            failures += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {name:38} -> {got}")
    print(f"  {len(CASES) - failures}/{len(CASES)} passed"
          f"   ({paying} of {len(CASES)} reach a payout)")
    return failures


def run_policy_conformance():
    """
    The written policy must state what the code does. Checked by looking for
    the specific commitments the code makes, not by keyword soup.
    """
    print("\nPOLICY CONFORMANCE")
    path = Path(__file__).parent / "policies" / "gfl-standard-v1.txt"
    if not path.exists():
        print("  FAIL  policy file not found")
        return 1
    # Normalise whitespace so a phrase broken across lines still matches.
    policy = re.sub(r"\s+", " ", path.read_text().lower())

    # The REVIEW_REQUIRED section, isolated. "related-party indicators are
    # present" also appears (negated) under PROTECTED, so a naive substring
    # search would pass for the wrong reason.
    review_section = policy[policy.find("review_required. everything else"):]

    checks = [
        # The freshness window must have exactly one source of truth: the
        # on-chain policy metadata. A number written into the text could
        # drift from the number the contract enforces, which is the same
        # failure as v4's contradictory policy file.
        ("TTL is not restated as a number in the policy text",
         re.search(r"max_attestation_age_seconds:?\s*\d+", policy) is None),
        ("policy points at the on-chain TTL instead",
         "get_max_attestation_age" in policy),
        # v5.2 fixed the scenario fixtures so the happy path has attested
        # support for delivery and the related-party check. The policy and
        # prompt then had to say so on conditions 1 and 4, not only on 3.
        # Without these checks, that asymmetry can quietly return.
        ("value exchange requires attested support",
         "genuine value was exchanged, consistent with the agreed terms, shown by attested evidence" in policy),
        ("no-related-party requires attested support",
         "no related-party indicators are present, shown by attested evidence" in policy),
        ("the asymmetry with condition 2 is explained",
         "conditions 1, 3 and 4 are affirmative findings" in policy),
        ("collusion does NOT pay the recipient",
         "no automatic payment is made to the recipient" in policy),
        ("collusion is listed under REVIEW_REQUIRED",
         "related-party indicators are present" in review_section),
        ("payer bond absorbs first",
         "first the payer's bond, then the protection pool, then the platform bond" in policy),
        ("no partial settlement",
         "nothing is paid" in policy and "partial amount" in policy),
        ("stale attestation blocks payout",
         "stale at acceptance" in policy),
        ("missing attestation blocks payout",
         "no attestation was recorded" in policy),
        ("assertions are not evidence of fact",
         "unsupported by attested evidence" in policy),
        ("PROTECTED facts need attested support",
         "attested record must also support the factual findings" in policy
         and "recipient's own assertions cannot establish those facts" in policy),
        ("post-flag evidence excluded",
         "nothing submitted after the funds were flagged is considered" in policy),
        ("does not clean funds",
         "does not clean funds" in policy),
    ]

    failures = 0
    for name, ok in checks:
        if not ok:
            failures += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"  {len(checks) - failures}/{len(checks)} passed")
    return failures


def main():
    f = run_decision_suite() + run_policy_conformance()
    print(f"\n{'ALL PASSED' if f == 0 else str(f) + ' FAILURES'}")
    return 1 if f else 0


if __name__ == "__main__":
    raise SystemExit(main())

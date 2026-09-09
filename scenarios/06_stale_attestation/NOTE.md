Every finding here is clean. The attestation is real, from a registered
attester, and both required checks passed.

The only thing wrong is when it was recorded. To run this scenario, call
`attest` and then wait past the policy's `max_attestation_age_seconds` before
calling `accept_payment`.

A risk snapshot is a photograph, not a standing fact. A wallet that was clean
eleven minutes ago may have been publicly flagged since. The contract records
freshness at the moment of acceptance, and a stale attestation can never
support a payout.

Expected verdict: `REVIEW_REQUIRED`.

To make this quick to demonstrate, deploy a second policy with a short TTL,
for example `max_attestation_age_seconds = 60`.

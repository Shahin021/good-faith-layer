"""
State machine and settlement arithmetic, simulated off-chain.

This mirrors the access control, lifecycle transitions, freshness evaluation
and settlement waterfall in contracts/good_faith_layer.py. It does not touch
GenLayer; it exists so that a logic bug in the flow is caught before Studio
rather than during a demo.

What it does not cover: the LLM call, validator consensus, and GenVM storage
semantics. Those only exist on-chain.
"""


class Sim:
    def __init__(self, owner, flag_authority):
        self.owner = owner
        self.flag_authority = flag_authority
        self.attesters = set()
        self.pool = 0
        self.platform = 0
        self.payer_bonds = {}
        self.claims = {}
        self.policies = {}
        self.payments = {}
        self.now = 1000  # stands in for the transaction timestamp

    # --- owner ---
    def register_policy(self, s, pid, text, ttl):
        if s != self.owner:
            raise Exception("owner only")
        if pid in self.policies:
            raise Exception("policy_id already exists; policies are immutable")
        self.policies[pid] = (text, ttl)

    def register_attester(self, s, who):
        if s != self.owner:
            raise Exception("owner only")
        self.attesters.add(who)

    def fund_pool(self, s, a):
        if s != self.owner:
            raise Exception("owner only")
        self.pool += a

    def fund_platform(self, s, a):
        if s != self.owner:
            raise Exception("owner only")
        self.platform += a

    # --- anyone ---
    def post_bond(self, s, a):
        self.payer_bonds[s] = self.payer_bonds.get(s, 0) + a

    # --- flow ---
    def register_payment(self, s, pid, recipient, amount, policy_id, terms):
        if pid in self.payments:
            raise Exception("payment_id already registered")
        if policy_id not in self.policies:
            raise Exception("unknown policy_id")
        self.payments[pid] = dict(
            payer=s, recipient=recipient, amount=amount, policy_id=policy_id,
            terms=terms, attested="", attested_at=0, assertions="",
            accepted_at=0, fresh=False, status="REGISTERED", verdict="", paid=0,
        )

    def attest(self, s, pid, evidence):
        if s not in self.attesters:
            raise Exception("caller is not a registered attester")
        p = self.payments[pid]
        if p["status"] != "REGISTERED":
            raise Exception("attestation must land before acceptance")
        if p["attested"] != "":
            raise Exception("this payment already carries an attestation")
        p["attested"] = evidence
        p["attested_at"] = self.now

    def accept(self, s, pid, policy_id, assertions):
        p = self.payments[pid]
        if s != p["recipient"]:
            raise Exception("only the recipient may accept")
        if p["status"] != "REGISTERED":
            raise Exception("payment is not awaiting acceptance")
        if policy_id != p["policy_id"]:
            raise Exception("recipient accepted a different policy")
        ttl = self.policies[p["policy_id"]][1]
        fresh = False
        if p["attested"] != "":
            age = self.now - p["attested_at"]
            fresh = 0 <= age <= ttl
        p.update(assertions=assertions, accepted_at=self.now, fresh=fresh,
                 status="ACCEPTED")

    def flag(self, s, pid, reason):
        if s != self.flag_authority:
            raise Exception("only the flag authority may flag a payment")
        p = self.payments[pid]
        if p["status"] != "ACCEPTED":
            raise Exception("only an ACCEPTED payment can be flagged")
        p["status"] = "FLAGGED"

    def claim(self, s, pid, verdict):
        """`verdict` is injected here; the LLM path is tested separately."""
        p = self.payments[pid]
        if s != p["recipient"]:
            raise Exception("only the recipient may open a claim")
        if p["status"] != "FLAGGED":
            raise Exception("payment must be FLAGGED before a claim can open")

        if verdict == "PROTECTED":
            amount = p["amount"]
            available = self.payer_bonds.get(p["payer"], 0) + self.pool + self.platform
            if available < amount:
                raise Exception("reserves cannot cover this claim; fund and retry")
            paid = 0
            bond = self.payer_bonds.get(p["payer"], 0)
            from_bond = min(bond, amount)
            self.payer_bonds[p["payer"]] = bond - from_bond
            paid += from_bond
            if paid < amount:
                from_pool = min(self.pool, amount - paid)
                self.pool -= from_pool
                paid += from_pool
            if paid < amount:
                from_platform = min(self.platform, amount - paid)
                self.platform -= from_platform
                paid += from_platform
            self.claims[p["recipient"]] = self.claims.get(p["recipient"], 0) + paid
            p["paid"] = paid

        p["verdict"] = verdict
        p["status"] = "REVIEW_REQUIRED" if verdict == "REVIEW_REQUIRED" else "RESOLVED"


OWNER, FLAG, ATT, PAYER, REC, MAL = "owner", "flag", "attester", "payer", "recipient", "attacker"
_failures = []


def check(name, fn, expect_raise=None):
    try:
        fn()
        ok = expect_raise is None
        note = "" if ok else f"expected a revert containing '{expect_raise}'"
    except AssertionError as e:
        ok = False
        note = str(e)
    except Exception as e:
        ok = expect_raise is not None and expect_raise in str(e)
        note = "" if ok else f"unexpected revert: {e}"
    if not ok:
        _failures.append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n         {note}" if note else ""))


def base():
    s = Sim(OWNER, FLAG)
    s.register_policy(OWNER, "p", "policy text", 600)
    s.register_attester(OWNER, ATT)
    s.fund_pool(OWNER, 5000)
    s.register_payment(PAYER, "x", REC, 1000, "p", "terms")
    return s


def accepted(post_bond=0):
    s = base()
    if post_bond:
        s.post_bond(PAYER, post_bond)
    s.attest(ATT, "x", "evidence")
    s.accept(REC, "x", "p", "assertions")
    return s


def flagged(post_bond=0):
    s = accepted(post_bond)
    s.flag(FLAG, "x", "upstream exposure")
    return s


def main():
    print("ACCESS CONTROL")
    check("non-owner cannot register a policy",
          lambda: Sim(OWNER, FLAG).register_policy(MAL, "p", "t", 600), "owner only")
    check("non-owner cannot fund the pool",
          lambda: Sim(OWNER, FLAG).fund_pool(MAL, 100), "owner only")
    check("non-attester cannot attest",
          lambda: base().attest(MAL, "x", "e"), "not a registered attester")
    check("non-recipient cannot accept",
          lambda: base().accept(MAL, "x", "p", "a"),
          "only the recipient may accept")
    check("non-authority cannot flag",
          lambda: accepted().flag(MAL, "x", "r"), "only the flag authority")
    check("non-recipient cannot claim",
          lambda: flagged().claim(MAL, "x", "PROTECTED"), "only the recipient may open")

    print("\nLIFECYCLE")
    check("cannot attest after acceptance",
          lambda: accepted().attest(ATT, "x", "e2"), "must land before acceptance")
    def _twice():
        s = base(); s.attest(ATT, "x", "e"); s.attest(ATT, "x", "e2")
    check("cannot attest twice", _twice, "already carries an attestation")
    check("cannot flag before acceptance",
          lambda: base().flag(FLAG, "x", "r"), "only an ACCEPTED payment")
    check("cannot claim before flagging",
          lambda: accepted().claim(REC, "x", "PROTECTED"), "must be FLAGGED")
    def _claim_twice():
        s = flagged(); s.claim(REC, "x", "PROTECTED"); s.claim(REC, "x", "PROTECTED")
    check("cannot claim twice", _claim_twice, "must be FLAGGED")
    check("cannot accept twice",
          lambda: accepted().accept(REC, "x", "p", "a2"), "not awaiting acceptance")
    def _wrong_policy():
        s = base(); s.register_policy(OWNER, "p2", "other", 600)
        s.attest(ATT, "x", "e"); s.accept(REC, "x", "p2", "a")
    check("cannot accept a different policy than registered",
          _wrong_policy, "different policy")
    check("cannot reuse a payment_id",
          lambda: base().register_payment(PAYER, "x", REC, 1, "p", "t"),
          "already registered")
    check("cannot register against an unknown policy",
          lambda: base().register_payment(PAYER, "y", REC, 1, "nope", "t"),
          "unknown policy_id")
    check("policies are immutable",
          lambda: base().register_policy(OWNER, "p", "rewritten", 1), "immutable")

    print("\nFRESHNESS")
    def _stale():
        s = base(); s.attest(ATT, "x", "e"); s.now += 601
        s.accept(REC, "x", "p", "a")
        assert s.payments["x"]["fresh"] is False, "601s past a 600s TTL should be stale"
    check("601s past a 600s window is stale", _stale)
    def _boundary():
        s = base(); s.attest(ATT, "x", "e"); s.now += 600
        s.accept(REC, "x", "p", "a")
        assert s.payments["x"]["fresh"] is True, "the boundary should still be fresh"
    check("exactly at the window is still fresh", _boundary)
    def _none():
        s = base(); s.accept(REC, "x", "p", "a")
        assert s.payments["x"]["fresh"] is False
    check("no attestation is never fresh", _none)

    print("\nSETTLEMENT")
    def _waterfall():
        s = flagged(post_bond=400)
        s.claim(REC, "x", "PROTECTED")
        assert s.payer_bonds[PAYER] == 0, f"bond should be drained: {s.payer_bonds}"
        assert s.pool == 4400, f"pool should cover the remaining 600: {s.pool}"
        assert s.claims[REC] == 1000, f"recipient should be credited in full: {s.claims}"
    check("payer bond absorbs first, pool covers the rest", _waterfall)
    def _bond_covers_all():
        s = flagged(post_bond=5000)
        s.claim(REC, "x", "PROTECTED")
        assert s.payer_bonds[PAYER] == 4000 and s.pool == 5000, "pool should be untouched"
    check("a sufficient bond leaves the pool untouched", _bond_covers_all)
    def _underfunded():
        s = Sim(OWNER, FLAG)
        s.register_policy(OWNER, "p", "t", 600); s.register_attester(OWNER, ATT)
        s.fund_pool(OWNER, 300)
        s.register_payment(PAYER, "x", REC, 1000, "p", "t")
        s.attest(ATT, "x", "e"); s.accept(REC, "x", "p", "a"); s.flag(FLAG, "x", "r")
        s.claim(REC, "x", "PROTECTED")
    check("an underfunded PROTECTED claim reverts", _underfunded, "reserves cannot cover")
    def _revert_clean():
        s = Sim(OWNER, FLAG)
        s.register_policy(OWNER, "p", "t", 600); s.register_attester(OWNER, ATT)
        s.fund_pool(OWNER, 300)
        s.register_payment(PAYER, "x", REC, 1000, "p", "t")
        s.attest(ATT, "x", "e"); s.accept(REC, "x", "p", "a"); s.flag(FLAG, "x", "r")
        try:
            s.claim(REC, "x", "PROTECTED")
        except Exception:
            pass
        assert s.pool == 300, f"pool must not be partially drained: {s.pool}"
        assert s.payments["x"]["status"] == "FLAGGED", "claim must remain open"
        assert REC not in s.claims, "recipient must not be part-credited"
    check("a reverted claim leaves reserves and status untouched", _revert_clean)
    def _review():
        s = flagged(); s.claim(REC, "x", "REVIEW_REQUIRED")
        assert s.pool == 5000 and REC not in s.claims, "nothing should move"
        assert s.payments["x"]["status"] == "REVIEW_REQUIRED", "review is not resolution"
    check("REVIEW_REQUIRED moves nothing and is not marked resolved", _review)
    def _bears():
        s = flagged(); s.claim(REC, "x", "RECIPIENT_BEARS")
        assert s.pool == 5000 and REC not in s.claims, "nothing should move"
        assert s.payments["x"]["status"] == "RESOLVED"
    check("RECIPIENT_BEARS moves nothing and is resolved", _bears)

    total = 24
    print(f"\n{total - len(_failures)}/{total} passed")
    if _failures:
        print("FAILURES: " + ", ".join(_failures))
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

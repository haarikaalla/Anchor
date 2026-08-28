from anchor.config import DAY
from anchor.core.models import BeliefStatus


def test_current_belief_prefers_active(anchor):
    anchor.observe("user", "lives_in", "Berlin", source_id="s1", trust=0.9, at=0.0)
    anchor.observe("user", "lives_in", "Berlin", source_id="s2", trust=0.9, at=1.0)
    b = anchor.current_belief("user", "lives_in")
    assert b is not None and b.object == "Berlin"
    assert b.status == BeliefStatus.ACTIVE


def test_current_belief_none_when_absent(anchor):
    assert anchor.current_belief("nobody", "nothing") is None


def test_current_belief_respects_time_travel(anchor):
    anchor.observe("user", "role", "intern", source_id="s1", trust=0.9,
                   at=0.0, valid_from=0.0, valid_to=10 * DAY)
    # after validity window, the belief is no longer current
    assert anchor.current_belief("user", "role", at=20 * DAY) is None
    assert anchor.current_belief("user", "role", at=5 * DAY).object == "intern"


def test_reliable_resolution_survives_low_trust_flood(anchor):
    # Trusted, corroborated truth.
    anchor.observe("acct", "access", "user", source_id="ownerA", trust=0.9, at=0.0)
    anchor.observe("acct", "access", "user", source_id="ownerB", trust=0.9, at=1 * DAY)
    # Low-trust attacker floods a false value afterwards.
    for k in range(10):
        anchor.observe("acct", "access", "root", source_id="attacker", trust=0.2,
                       at=(2 + k) * DAY)
        for con in anchor.open_contradictions():
            anchor.resolve_contradiction(con.id, strategy="reliable", actor="policy")
    b = anchor.current_belief("acct", "access")
    assert b.object == "user"  # not poisoned

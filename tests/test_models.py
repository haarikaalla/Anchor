from anchor.core.models import (
    Belief,
    BeliefStatus,
    Contradiction,
    Polarity,
    Provenance,
    SourceType,
)


def test_belief_keys_are_normalised():
    b = Belief(subject="User", predicate="Lives_In", object="Berlin")
    assert b.entity_key == "default::user::lives_in"
    assert b.claim_key.endswith("berlin::positive")


def test_belief_roundtrip():
    b = Belief(subject="user", predicate="likes", object="tea",
               provenance=[Provenance(source_type=SourceType.AGENT, source_id="x")],
               tags=["t"], embedding=[0.1, 0.2])
    d = b.to_dict()
    b2 = Belief.from_dict(d)
    assert b2.subject == b.subject
    assert b2.provenance[0].source_type == SourceType.AGENT
    assert b2.embedding == [0.1, 0.2]
    assert b2.claim_key == b.claim_key


def test_validity_window():
    b = Belief(subject="s", predicate="p", object="o", valid_from=100, valid_to=200)
    assert not b.is_currently_valid(50)
    assert b.is_currently_valid(150)
    assert not b.is_currently_valid(250)


def test_retracted_never_valid():
    b = Belief(subject="s", predicate="p", object="o", status=BeliefStatus.RETRACTED)
    assert not b.is_currently_valid()


def test_contradiction_roundtrip():
    c = Contradiction(belief_a="a", belief_b="b")
    c2 = Contradiction.from_dict(c.to_dict())
    assert c2.belief_a == "a" and c2.belief_b == "b"


def test_negative_polarity_distinct_claim_key():
    pos = Belief(subject="s", predicate="p", object="o", polarity=Polarity.POSITIVE)
    neg = Belief(subject="s", predicate="p", object="o", polarity=Polarity.NEGATIVE)
    assert pos.entity_key == neg.entity_key
    assert pos.claim_key != neg.claim_key

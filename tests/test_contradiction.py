from anchor.core.contradiction import ContradictionDetector, PredicateRegistry, Relation
from anchor.core.embeddings import LocalHashingEmbedder
from anchor.core.models import Belief, Polarity

EMB = LocalHashingEmbedder(dim=128)


def _b(subject, predicate, obj, polarity=Polarity.POSITIVE):
    b = Belief(subject=subject, predicate=predicate, object=obj, polarity=polarity)
    b.embedding = EMB.embed(f"{subject} {predicate} {obj}")
    return b


def test_identical_claim_reinforces():
    d = ContradictionDetector()
    f = d.classify(_b("user", "lives_in", "Berlin"),
                   _b("user", "lives_in", "Berlin"), EMB, 0.92, 0.8)
    assert f.relation == Relation.REINFORCE


def test_functional_conflict_detected():
    d = ContradictionDetector()
    f = d.classify(_b("user", "lives_in", "Munich"),
                   _b("user", "lives_in", "Berlin"), EMB, 0.92, 0.8)
    assert f.relation == Relation.CONTRADICT


def test_negation_conflict():
    d = ContradictionDetector()
    f = d.classify(_b("user", "likes", "coffee", Polarity.NEGATIVE),
                   _b("user", "likes", "coffee", Polarity.POSITIVE), EMB, 0.92, 0.8)
    assert f.relation == Relation.CONTRADICT
    assert f.kind.value == "negation"


def test_multi_valued_predicate_coexists():
    reg = PredicateRegistry()
    d = ContradictionDetector(reg)
    f = d.classify(_b("user", "likes", "tea"),
                   _b("user", "likes", "coffee"), EMB, 0.92, 0.8)
    assert f.relation == Relation.COEXIST


def test_registry_override_makes_functional_conflict():
    reg = PredicateRegistry()
    reg.set_functional("likes")
    d = ContradictionDetector(reg)
    f = d.classify(_b("user", "likes", "tea"),
                   _b("user", "likes", "coffee"), EMB, 0.92, 0.8)
    assert f.relation == Relation.CONTRADICT

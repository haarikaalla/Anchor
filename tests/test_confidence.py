import math

from anchor.core import confidence as conf


def test_confidence_mean_and_bounds():
    assert conf.confidence(1, 1) == 0.5
    assert 0.0 <= conf.confidence_lower_bound(1, 1) <= 0.5
    # more evidence with same mean -> tighter (higher) lower bound
    lb_small = conf.confidence_lower_bound(2, 2)
    lb_large = conf.confidence_lower_bound(20, 20)
    assert lb_large > lb_small


def test_reinforce_increases_alpha_only():
    a, b = conf.reinforce(1.0, 1.0, gain=2.0, trust=1.0)
    assert a == 3.0 and b == 1.0


def test_reinforce_respects_trust_and_cap():
    a, _b = conf.reinforce(1.0, 1.0, gain=2.0, trust=0.5)
    assert a == 2.0
    a, _b = conf.reinforce(1.0, 1.0, gain=10.0, trust=1.0, max_alpha=4.0)
    assert a == 4.0


def test_contradict_increases_beta_only():
    a, b = conf.contradict(3.0, 1.0, gain=2.0, trust=1.0)
    assert a == 3.0 and b == 3.0


def test_decay_pulls_toward_prior():
    # strong belief alpha=10 decays toward prior alpha=1 after one half-life
    a, b = conf.decay_evidence(10.0, 1.0, prior_alpha=1.0, prior_beta=1.0,
                               elapsed=100.0, half_life=100.0)
    assert math.isclose(a, 1.0 + 9.0 * 0.5, rel_tol=1e-9)
    assert math.isclose(b, 1.0, rel_tol=1e-9)


def test_decay_noop_when_no_time_passes():
    a, b = conf.decay_evidence(10.0, 2.0, 1.0, 1.0, elapsed=0.0, half_life=100.0)
    assert (a, b) == (10.0, 2.0)


def test_recency_weight_halves():
    assert math.isclose(conf.recency_weight(100.0, 100.0), 0.5, rel_tol=1e-9)
    assert conf.recency_weight(0.0, 100.0) == 1.0

"""Tests for belief explainability (:mod:`anchor.core.explain`)."""

from __future__ import annotations

from anchor.core.explain import CONTRA_PREFIX, credible_interval


def test_credible_interval_tightens_with_evidence():
    thin_lo, thin_hi = credible_interval(2.0, 1.0)
    thick_lo, thick_hi = credible_interval(20.0, 10.0)
    assert thin_hi - thin_lo > thick_hi - thick_lo
    assert 0.0 <= thick_lo <= thick_hi <= 1.0


def test_explanation_attributes_evidence_per_source(anchor):
    anchor.observe("user", "lives_in", "Berlin", source_id="chat", trust=0.9)
    result = anchor.observe("user", "lives_in", "Berlin", source_id="calendar", trust=0.9)

    x = anchor.explain(result.belief.id)

    assert {s.source_id for s in x.sources} == {"chat", "calendar"}
    assert all(s.supporting > 0 for s in x.sources)
    assert abs(sum(s.share for s in x.sources) - 1.0) < 1e-6
    assert x.supporting > x.contradicting
    assert x.lower_bound <= x.confidence <= 1.0
    assert x.interval[0] <= x.confidence <= x.interval[1]


def test_explanation_records_the_challenger(anchor):
    first = anchor.observe("user", "lives_in", "Berlin", source_id="chat", trust=0.9)
    anchor.observe("user", "lives_in", "Munich", source_id="rumour", trust=0.9)

    x = anchor.explain(first.belief.id)

    disputers = [s for s in x.sources if s.contradicting > 0]
    assert [s.source_id for s in disputers] == ["rumour"]
    assert x.contradicting > 1.0
    assert any("Challenged by" in r for r in x.reasons)


def test_single_source_explanation_flags_lack_of_corroboration(anchor):
    result = anchor.observe("user", "likes", "coffee", source_id="chat", trust=0.8)
    x = anchor.explain(result.belief.id)
    assert any("single source" in r for r in x.reasons)


def test_evidence_cap_is_surfaced(cfg):
    from anchor import Anchor

    cfg.max_source_evidence = 2.0
    a = Anchor(cfg)
    try:
        result = None
        for _ in range(10):
            result = a.observe("user", "role", "engineer", source_id="spammer", trust=1.0)
        x = a.explain(result.belief.id)
        assert any(s.capped for s in x.sources)
        assert any("cap" in r for r in x.reasons)
    finally:
        a.close()


def test_decay_projection_is_monotonically_decreasing(anchor):
    result = anchor.observe("user", "lives_in", "Berlin", source_id="chat", trust=0.95)
    for _ in range(4):
        result = anchor.observe("user", "lives_in", "Berlin", source_id="calendar",
                                trust=0.95)

    x = anchor.explain(result.belief.id)
    confidences = [c for _, c in x.decay_projection]
    assert confidences == sorted(confidences, reverse=True)
    assert confidences[0] > confidences[-1]


def test_timeline_is_chronological_and_matches_audit(anchor):
    result = anchor.observe("user", "lives_in", "Berlin", source_id="chat", trust=0.9)
    anchor.observe("user", "lives_in", "Berlin", source_id="calendar", trust=0.9)

    x = anchor.explain(result.belief.id)

    assert next(e.event_type for e in x.timeline) == "created"
    assert x.timeline == sorted(x.timeline, key=lambda e: e.ts)
    assert all(e.belief_id == result.belief.id for e in x.timeline)


def test_explanation_serialises(anchor):
    result = anchor.observe("user", "lives_in", "Berlin", source_id="chat", trust=0.9)
    payload = anchor.explain(result.belief.id).to_dict()

    assert payload["belief"]["id"] == result.belief.id
    assert set(payload) >= {"summary", "confidence", "lower_bound", "interval",
                            "sources", "timeline", "decay_projection", "reasons"}
    assert isinstance(payload["summary"], str) and payload["summary"]


def test_unknown_belief_raises(anchor):
    import pytest

    with pytest.raises(KeyError):
        anchor.explain("bel_does_not_exist")


def test_contradicting_evidence_uses_reserved_prefix(anchor):
    first = anchor.observe("user", "lives_in", "Berlin", source_id="chat", trust=0.9)
    anchor.observe("user", "lives_in", "Munich", source_id="rumour", trust=0.9)

    ledger = anchor.store.source_evidence_breakdown(first.belief.id)
    assert any(k.startswith(CONTRA_PREFIX) for k in ledger)

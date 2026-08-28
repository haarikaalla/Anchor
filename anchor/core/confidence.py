"""Confidence dynamics: Bayesian reinforcement, decay and temporal discounting.

Anchor models each belief's confidence with a Beta distribution parameterised by
``alpha`` (supporting evidence) and ``beta`` (contradicting evidence).  This is a
principled alternative to ad-hoc "score += 0.1" heuristics: it yields calibrated
probabilities, naturally bounds confidence in ``[0, 1]``, exposes *how much* we
know (the total evidence ``alpha + beta``), and supports a conservative lower
bound for ranking.
"""

from __future__ import annotations

import math


def confidence(alpha: float, beta: float) -> float:
    """Posterior mean of a Beta(alpha, beta)."""
    return alpha / (alpha + beta)


def confidence_lower_bound(alpha: float, beta: float, z: float = 1.28) -> float:
    """A conservative confidence estimate (default ~ 10th percentile).

    Well-corroborated beliefs (large alpha+beta) get a tighter interval and thus
    a higher lower bound than shakily-supported ones with the same mean.  Used
    for ranking so we prefer *reliable* memories over merely *plausible* ones.
    """
    n = alpha + beta
    mean = alpha / n
    var = (alpha * beta) / (n * n * (n + 1.0))
    return max(0.0, mean - z * math.sqrt(var))


def reinforce(alpha: float, beta: float, gain: float, trust: float,
              max_alpha: float | None = None) -> tuple[float, float]:
    """Add corroborating evidence, weighted by source trust."""
    add = max(0.0, gain) * max(0.0, min(1.0, trust))
    new_alpha = alpha + add
    if max_alpha is not None:
        new_alpha = min(new_alpha, max_alpha)
    return new_alpha, beta


def contradict(alpha: float, beta: float, gain: float, trust: float) -> tuple[float, float]:
    """Add contradicting evidence, weighted by source trust."""
    add = max(0.0, gain) * max(0.0, min(1.0, trust))
    return alpha, beta + add


def decay_evidence(alpha: float, beta: float, prior_alpha: float, prior_beta: float,
                   elapsed: float, half_life: float) -> tuple[float, float]:
    """Exponentially discount accumulated evidence toward the prior.

    Only the *excess* over the prior decays, so an un-reinforced belief drifts
    back toward "unknown" rather than toward "false".  ``half_life`` is the time
    for the excess evidence to halve.
    """
    if half_life <= 0 or elapsed <= 0:
        return alpha, beta
    factor = math.pow(0.5, elapsed / half_life)
    new_alpha = prior_alpha + (alpha - prior_alpha) * factor
    new_beta = prior_beta + (beta - prior_beta) * factor
    return new_alpha, new_beta


def recency_weight(age_seconds: float, half_life: float) -> float:
    """Multiplicative recency weight in (0, 1] for ranking."""
    if half_life <= 0:
        return 1.0
    return math.pow(0.5, max(0.0, age_seconds) / half_life)

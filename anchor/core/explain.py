"""Belief explainability: *why* does the agent believe this?

A memory that cannot justify itself is not auditable. This module turns a
:class:`~anchor.core.models.Belief` — plus its provenance, per-source evidence
ledger and hash-chained audit trail — into a structured, human-readable
explanation:

* the **Beta posterior** (mean, conservative lower bound, credible interval) and
  how much evidence actually backs it;
* a **per-source attribution** showing who pushed the belief up, who pushed it
  down, and how close each source is to its anti-flooding cap;
* the **event timeline** (created / reinforced / contradicted / resolved);
* a **decay projection** — what this confidence becomes in N days if nobody
  corroborates it again;
* plain-English **reasons**, so the explanation can be handed straight to an LLM
  or rendered in a UI.

Nothing here mutates state; it is a pure read-side projection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import confidence as conf
from .models import AuditEvent, Belief, BeliefStatus, now

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config import AnchorConfig
    from .store import SQLiteStore

DAY = 86_400.0

#: Prefix used in the source-evidence ledger for contradicting contributions.
CONTRA_PREFIX = "con::"


def credible_interval(alpha: float, beta: float, z: float = 1.96) -> tuple[float, float]:
    """Normal-approximation credible interval for a Beta(alpha, beta).

    Cheap, dependency-free, and accurate enough for UI/explanation purposes once
    a belief has any real evidence behind it. Always clamped to ``[0, 1]``.
    """
    n = alpha + beta
    mean = alpha / n
    var = (alpha * beta) / (n * n * (n + 1.0))
    half = z * math.sqrt(var)
    return max(0.0, mean - half), min(1.0, mean + half)


@dataclass
class SourceContribution:
    """How much one source moved a belief, and how close it is to its cap."""

    source_id: str
    supporting: float = 0.0
    contradicting: float = 0.0
    observations: int = 0
    trust: float = 0.0
    share: float = 0.0
    capped: bool = False

    @property
    def net(self) -> float:
        return self.supporting - self.contradicting

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "supporting": round(self.supporting, 4),
            "contradicting": round(self.contradicting, 4),
            "net": round(self.net, 4),
            "observations": self.observations,
            "trust": round(self.trust, 4),
            "share": round(self.share, 4),
            "capped": self.capped,
        }


@dataclass
class Explanation:
    """A complete, serialisable justification for a single belief."""

    belief: Belief
    confidence: float
    lower_bound: float
    interval: tuple[float, float]
    supporting: float
    contradicting: float
    evidence: float
    sources: list[SourceContribution] = field(default_factory=list)
    timeline: list[AuditEvent] = field(default_factory=list)
    decay_projection: list[tuple[float, float]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    age_days: float = 0.0
    staleness_days: float = 0.0

    @property
    def summary(self) -> str:
        """One-line natural-language verdict, safe to paste into a prompt."""
        return (
            f"{self.belief.statement!r} is {self.confidence:.0%} confident "
            f"({self.belief.status.value}), backed by {self.supporting:.2f} units of "
            f"evidence from {len(self.sources)} source(s) over {self.age_days:.1f} days."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "belief": self.belief.to_dict(),
            "summary": self.summary,
            "confidence": round(self.confidence, 4),
            "lower_bound": round(self.lower_bound, 4),
            "interval": [round(self.interval[0], 4), round(self.interval[1], 4)],
            "supporting": round(self.supporting, 4),
            "contradicting": round(self.contradicting, 4),
            "evidence": round(self.evidence, 4),
            "age_days": round(self.age_days, 3),
            "staleness_days": round(self.staleness_days, 3),
            "sources": [s.to_dict() for s in self.sources],
            "timeline": [e.to_dict() for e in self.timeline],
            "decay_projection": [
                {"days": d, "confidence": round(c, 4)} for d, c in self.decay_projection
            ],
            "reasons": self.reasons,
        }


def _project_decay(belief: Belief, config: AnchorConfig,
                   horizons: tuple[float, ...] = (0.0, 7.0, 30.0, 90.0, 180.0),
                   ) -> list[tuple[float, float]]:
    """Confidence this belief will hold in N days if never reinforced again."""
    out: list[tuple[float, float]] = []
    for days in horizons:
        a, b = conf.decay_evidence(
            belief.alpha, belief.beta, config.prior_alpha, config.prior_beta,
            days * DAY, config.decay_half_life,
        )
        out.append((days, conf.confidence(a, b)))
    return out


def _attribute_sources(belief: Belief, ledger: dict[str, float],
                       config: AnchorConfig) -> list[SourceContribution]:
    """Fold the evidence ledger and provenance list into per-source rows."""
    rows: dict[str, SourceContribution] = {}

    def row(source_id: str) -> SourceContribution:
        return rows.setdefault(source_id, SourceContribution(source_id=source_id))

    for key, amount in ledger.items():
        if key.startswith(CONTRA_PREFIX):
            row(key[len(CONTRA_PREFIX):]).contradicting += amount
        else:
            row(key).supporting += amount

    for prov in belief.provenance:
        key = (prov.source_id or prov.agent_id or prov.session_id
               or prov.source_type.value)
        entry = row(key)
        entry.observations += 1
        entry.trust = max(entry.trust, prov.trust)

    total_support = sum(r.supporting for r in rows.values()) or 1.0
    for entry in rows.values():
        entry.share = entry.supporting / total_support
        cap = config.max_source_evidence * max(entry.trust, 1e-6)
        entry.capped = entry.supporting >= cap - 1e-9

    return sorted(rows.values(), key=lambda r: (-r.supporting, r.source_id))


def _reasons(belief: Belief, expl_sources: list[SourceContribution],
             config: AnchorConfig, lower_bound: float,
             staleness_days: float) -> list[str]:
    """Plain-English justification lines, ordered most-to-least decisive."""
    out: list[str] = []
    corroborators = [s for s in expl_sources if s.supporting > 0]
    if len(corroborators) > 1:
        names = ", ".join(s.source_id for s in corroborators[:4])
        out.append(f"Corroborated by {len(corroborators)} independent sources ({names}).")
    elif corroborators:
        out.append(
            f"Supported by a single source ({corroborators[0].source_id}); "
            f"independent corroboration would raise its reliability."
        )
    else:
        out.append("No recorded supporting source — this belief rests on the prior alone.")

    if belief.reinforcement_count > 1:
        out.append(f"Reinforced {belief.reinforcement_count} times without being refuted.")

    disputers = [s for s in expl_sources if s.contradicting > 0]
    if disputers:
        names = ", ".join(s.source_id for s in disputers[:4])
        out.append(f"Challenged by {len(disputers)} source(s) ({names}); "
                   f"{belief.contradiction_count} contradiction(s) recorded.")

    if any(s.capped for s in expl_sources):
        out.append(
            f"At least one source hit the per-source evidence cap "
            f"({config.max_source_evidence:.1f}) — repetition alone cannot push this "
            f"belief to certainty."
        )

    if belief.status == BeliefStatus.PROVISIONAL:
        out.append(
            f"Still provisional: needs confidence ≥ {config.promote_threshold:.2f} "
            f"from ≥ {config.min_sources_for_active} distinct source(s) to become active."
        )
    elif belief.status == BeliefStatus.DISPUTED:
        out.append("Disputed: a conflicting claim is open and awaiting resolution.")
    elif belief.status == BeliefStatus.SUPERSEDED:
        out.append("Superseded by a newer claim, but retained for history and audit.")
    elif belief.status == BeliefStatus.RETRACTED:
        out.append("Retracted — excluded from recall, retained in the audit trail.")

    half_life_days = config.decay_half_life / DAY
    if staleness_days > half_life_days:
        out.append(
            f"Stale: not reinforced for {staleness_days:.0f} days "
            f"(> {half_life_days:.0f}-day half-life), so its evidence has decayed."
        )

    out.append(
        f"Ranking uses the conservative lower bound {lower_bound:.3f} rather than the "
        f"mean {belief.raw_confidence:.3f}, so thinly-supported claims cannot outrank "
        f"well-corroborated ones."
    )
    return out


def explain_belief(belief: Belief, store: SQLiteStore, config: AnchorConfig,
                   *, at: float | None = None, timeline_limit: int = 50) -> Explanation:
    """Build a full :class:`Explanation` for ``belief``.

    Read-only: touches the evidence ledger and audit log but mutates nothing.
    """
    ts = now() if at is None else at
    ledger = store.source_evidence_breakdown(belief.id)
    sources = _attribute_sources(belief, ledger, config)
    lower = conf.confidence_lower_bound(belief.alpha, belief.beta)
    staleness_days = max(0.0, ts - belief.last_reinforced_at) / DAY

    return Explanation(
        belief=belief,
        confidence=belief.raw_confidence,
        lower_bound=lower,
        interval=credible_interval(belief.alpha, belief.beta),
        supporting=belief.alpha,
        contradicting=belief.beta,
        evidence=belief.evidence,
        sources=sources,
        timeline=list(reversed(store.audit_log(belief.namespace, belief.id, timeline_limit))),
        decay_projection=_project_decay(belief, config),
        reasons=_reasons(belief, sources, config, lower, staleness_days),
        age_days=max(0.0, ts - belief.created_at) / DAY,
        staleness_days=staleness_days,
    )

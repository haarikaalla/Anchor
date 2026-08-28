"""The Anchor memory engine.

This is the framework-independent orchestrator that ties together the store,
confidence dynamics, temporal reasoning, contradiction detection and security.
It exposes a small, stable API (:meth:`observe`, :meth:`recall`,
:meth:`open_contradictions`, :meth:`resolve_contradiction`, :meth:`apply_decay`,
...) that higher layers (MCP server, CLI, evaluation harness) build on.

Write path invariants
----------------------
* A conflicting claim is **never silently overwritten**; it produces a
  :class:`Contradiction` and both beliefs are marked ``DISPUTED`` until resolved.
* Every state change emits a hash-chained :class:`AuditEvent`.
* No single source can flood a belief to certainty (evidence cap) and promotion
  to ``ACTIVE`` can require corroboration from multiple sources.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import AnchorConfig
from ..observability import Metrics, get_logger, log
from . import confidence as conf
from .contradiction import ContradictionDetector, PredicateRegistry, Relation
from .embeddings import EmbeddingProvider, cosine, make_embedder
from .models import (
    AuditEvent,
    Belief,
    BeliefStatus,
    Contradiction,
    ContradictionKind,
    ContradictionStatus,
    Polarity,
    Provenance,
    SourceType,
    now,
)
from .security import SecurityGuard, clamp_source_evidence
from .store import SQLiteStore


@dataclass
class ObserveResult:
    action: str                      # created | reinforced | contradicted | coexists
    belief: Belief
    contradictions: list[Contradiction] = field(default_factory=list)
    reinforced_id: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "belief": self.belief.to_dict(),
            "contradictions": [c.to_dict() for c in self.contradictions],
            "reinforced_id": self.reinforced_id,
            "notes": self.notes,
        }


@dataclass
class RecallItem:
    belief: Belief
    score: float
    relevance: float
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "belief": self.belief.to_dict(),
            "score": round(self.score, 4),
            "relevance": round(self.relevance, 4),
            "confidence": round(self.confidence, 4),
        }


class Anchor:
    def __init__(self, config: Optional[AnchorConfig] = None,
                 store: Optional[SQLiteStore] = None,
                 embedder: Optional[EmbeddingProvider] = None,
                 predicate_registry: Optional[PredicateRegistry] = None):
        self.config = config or AnchorConfig()
        self.store = store or SQLiteStore(self.config.db_path)
        self.embedder = embedder or make_embedder(
            self.config.embedding_provider,
            self.config.embedding_model,
            self.config.embedding_dim,
        )
        self.detector = ContradictionDetector(predicate_registry)
        self.guard = SecurityGuard(self.config.max_statement_chars)
        self.metrics = Metrics()
        self.log: logging.Logger = get_logger("anchor", self.config.log_level)

    # ------------------------------------------------------------------ helpers
    def _embed(self, text: str) -> list[float]:
        return self.embedder.embed(text)

    @staticmethod
    def _source_id(prov: Provenance) -> str:
        return prov.source_id or prov.agent_id or prov.session_id or prov.source_type.value

    def _default_statement(self, subject: str, predicate: str, object_: str,
                           polarity: Polarity) -> str:
        neg = "does not " if polarity == Polarity.NEGATIVE else ""
        return f"{subject} {neg}{predicate} {object_}".strip()

    def _audit(self, event_type: str, belief_id: str, namespace: str,
               actor: str, **payload: Any) -> None:
        self.store.append_audit(AuditEvent(
            event_type=event_type, belief_id=belief_id, namespace=namespace,
            actor=actor, payload=payload,
        ))

    def _maybe_promote(self, belief: Belief) -> None:
        if belief.status != BeliefStatus.PROVISIONAL:
            return
        enough_sources = (
            self.store.distinct_sources(belief.id) >= self.config.min_sources_for_active
        )
        if belief.raw_confidence >= self.config.promote_threshold and enough_sources:
            belief.status = BeliefStatus.ACTIVE
            self._audit("promoted", belief.id, belief.namespace, "system",
                        confidence=belief.raw_confidence)

    # ------------------------------------------------------------------- writes
    def observe(
        self,
        subject: str,
        predicate: str,
        object: str,
        *,
        statement: Optional[str] = None,
        polarity: Polarity | str = Polarity.POSITIVE,
        namespace: str = "default",
        source_type: SourceType | str = SourceType.USER,
        source_id: str = "",
        agent_id: str = "",
        session_id: str = "",
        trust: Optional[float] = None,
        valid_from: Optional[float] = None,
        valid_to: Optional[float] = None,
        at: Optional[float] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ObserveResult:
        """Record an observation and reconcile it with existing memory.

        ``at`` sets the effective observation timestamp (defaults to wall-clock);
        it is used for created/updated/last-reinforced times and provenance, so
        callers can back-fill history or drive deterministic temporal reasoning.
        """
        self.metrics.incr("observe.calls")
        ts = now() if at is None else at
        if isinstance(polarity, str):
            polarity = Polarity(polarity)
        if isinstance(source_type, str):
            source_type = SourceType(source_type)

        raw_stmt = statement or self._default_statement(subject, predicate, object, polarity)
        screen = self.guard.screen(raw_stmt)
        if screen.suspicious:
            self.metrics.incr("security.flagged")
            log(self.log, logging.WARNING, "flagged suspicious observation",
                flags=screen.flags, subject=subject, predicate=predicate)

        base_trust = self.config.default_source_trust if trust is None else trust
        eff_trust = max(0.0, min(1.0, base_trust)) * screen.trust_penalty

        prov = Provenance(
            source_type=source_type, source_id=source_id, agent_id=agent_id,
            session_id=session_id, raw_text=screen.text, trust=eff_trust,
            observed_at=ts,
            metadata={"flags": screen.flags} if screen.flags else {},
        )
        src_key = self._source_id(prov)

        candidate = Belief(
            subject=subject, predicate=predicate, object=object,
            statement=screen.text, polarity=polarity, namespace=namespace,
            alpha=self.config.prior_alpha, beta=self.config.prior_beta,
            created_at=ts, updated_at=ts, last_reinforced_at=ts,
            valid_from=valid_from if valid_from is not None else ts,
            valid_to=valid_to, tags=tags or [], metadata=metadata or {},
            embedding=self._embed(screen.text),
        )

        existing = [
            b for b in self.store.beliefs_by_entity(namespace, candidate.entity_key)
            if b.status not in (BeliefStatus.RETRACTED, BeliefStatus.SUPERSEDED)
        ]
        findings = self.detector.assess(
            candidate, existing, self.embedder,
            self.config.dup_similarity, self.config.contradiction_similarity,
        )

        reinforce_target = next((f for f in findings if f.relation == Relation.REINFORCE), None)
        conflicts = [f for f in findings if f.relation == Relation.CONTRADICT]

        # --- reinforcement path ------------------------------------------------
        if reinforce_target is not None:
            return self._apply_reinforcement(
                reinforce_target.other, prov, src_key, screen.text, ts)

        # --- create the new belief --------------------------------------------
        add = clamp_source_evidence(
            self.store.source_evidence(candidate.id, src_key),
            self.config.reinforce_gain * eff_trust,
            self.config.max_source_evidence * eff_trust,
        )
        candidate.alpha += add
        candidate.provenance.append(prov)
        self.store.upsert_belief(candidate)
        self.store.add_source_evidence(candidate.id, src_key, add)
        self._audit("created", candidate.id, namespace, src_key,
                    statement=candidate.statement, confidence=candidate.raw_confidence,
                    flags=screen.flags)

        # --- contradiction path ------------------------------------------------
        if conflicts:
            self.metrics.incr("contradiction.detected", len(conflicts))
            created_cons: list[Contradiction] = []
            candidate.status = BeliefStatus.DISPUTED
            for f in conflicts:
                other = f.other
                # Contradicting evidence lowers the incumbent's confidence but
                # never deletes it. A single source's total contradicting
                # contribution is capped (trust-scaled) so an attacker cannot
                # erode a corroborated belief through sheer repetition.
                badd = clamp_source_evidence(
                    self.store.source_evidence(other.id, "con::" + src_key),
                    self.config.contradict_gain * eff_trust,
                    self.config.max_source_evidence * eff_trust,
                )
                other.beta += badd
                self.store.add_source_evidence(other.id, "con::" + src_key, badd)
                other.contradiction_count += 1
                other.status = BeliefStatus.DISPUTED
                self.store.upsert_belief(other)
                con = Contradiction(
                    belief_a=candidate.id, belief_b=other.id,
                    kind=f.kind or ContradictionKind.FUNCTIONAL,
                    namespace=namespace, score=f.score, note=f.reason,
                )
                self.store.upsert_contradiction(con)
                created_cons.append(con)
                self._audit("contradiction_detected", candidate.id, namespace, src_key,
                            other=other.id, kind=con.kind.value, score=f.score,
                            reason=f.reason)
            self.store.upsert_belief(candidate)
            return ObserveResult(
                action="contradicted", belief=candidate,
                contradictions=created_cons, reinforced_id=None,
                notes=[f"{len(conflicts)} conflict(s) require resolution"],
            )

        # --- clean create (possibly coexisting) --------------------------------
        self._maybe_promote(candidate)
        self.store.upsert_belief(candidate)
        coexist = any(f.relation == Relation.COEXIST for f in findings)
        return ObserveResult(
            action="coexists" if coexist else "created", belief=candidate,
            notes=["multi-valued predicate; stored alongside siblings"] if coexist else [],
        )

    def _apply_reinforcement(self, belief: Belief, prov: Provenance,
                             src_key: str, text: str,
                             at: Optional[float] = None) -> ObserveResult:
        ts = now() if at is None else at
        add = clamp_source_evidence(
            self.store.source_evidence(belief.id, src_key),
            self.config.reinforce_gain * prov.trust,
            self.config.max_source_evidence * max(prov.trust, 1e-6),
        )
        belief.alpha, belief.beta = conf.reinforce(
            belief.alpha, belief.beta, add, 1.0)  # add already trust-scaled/capped
        belief.reinforcement_count += 1
        belief.last_reinforced_at = ts
        belief.provenance.append(prov)
        if belief.status == BeliefStatus.PROVISIONAL:
            self.store.add_source_evidence(belief.id, src_key, add)
            self._maybe_promote(belief)
        else:
            self.store.add_source_evidence(belief.id, src_key, add)
        self.store.upsert_belief(belief)
        self.metrics.incr("observe.reinforced")
        self._audit("reinforced", belief.id, belief.namespace, src_key,
                    confidence=belief.raw_confidence,
                    reinforcement_count=belief.reinforcement_count)
        return ObserveResult(action="reinforced", belief=belief,
                             reinforced_id=belief.id,
                             notes=[f"confidence -> {belief.raw_confidence:.3f}"])

    # -------------------------------------------------------------------- reads
    def get(self, belief_id: str) -> Optional[Belief]:
        return self.store.get_belief(belief_id)

    def recall(self, query: str, *, namespace: str = "default", top_k: int = 5,
               at_time: Optional[float] = None, include_disputed: bool = True,
               min_confidence: float = 0.0) -> list[RecallItem]:
        """Retrieve the most relevant *and reliable* currently-valid beliefs.

        Ranking blends semantic relevance, a conservative confidence lower bound,
        and recency — so Anchor prefers memories that are on-topic, well
        corroborated, and fresh, rather than merely similar text.
        """
        at = now() if at_time is None else at_time
        q_emb = self._embed(query)
        items: list[RecallItem] = []
        for b in self.store.all_beliefs(namespace):
            if not b.is_currently_valid(at):
                continue
            if b.status == BeliefStatus.DISPUTED and not include_disputed:
                continue
            c = conf.confidence(b.alpha, b.beta)
            if c < min_confidence:
                continue
            relevance = cosine(q_emb, b.embedding) if b.embedding else 0.0
            lb = conf.confidence_lower_bound(b.alpha, b.beta)
            recency = conf.recency_weight(at - b.last_reinforced_at,
                                          self.config.recency_half_life)
            penalty = 0.6 if b.status == BeliefStatus.DISPUTED else 1.0
            score = relevance * (0.35 + 0.65 * lb) * (0.5 + 0.5 * recency) * penalty
            items.append(RecallItem(belief=b, score=score, relevance=relevance,
                                    confidence=c))
        items.sort(key=lambda it: it.score, reverse=True)
        return items[:top_k]

    def list_beliefs(self, namespace: str = "default",
                     include_inactive: bool = False) -> list[Belief]:
        return self.store.all_beliefs(namespace, include_inactive=include_inactive)

    def current_belief(self, subject: str, predicate: str, *, namespace: str = "default",
                       at: Optional[float] = None) -> Optional[Belief]:
        """Return the single best *currently-valid* belief for a claim slot.

        Prefers ACTIVE beliefs, breaks ties by confidence, and among equally
        trusted candidates prefers the most recently valid (temporal recency).
        This is what an agent should treat as "the current truth" for a slot.
        """
        at = now() if at is None else at
        key = f"{namespace}::{subject.strip().lower()}::{predicate.strip().lower()}"
        candidates = [b for b in self.store.beliefs_by_entity(namespace, key)
                      if b.is_currently_valid(at)]
        if not candidates:
            return None

        def rank(b: Belief) -> tuple:
            active = 1 if b.status == BeliefStatus.ACTIVE else 0
            return (active, round(b.raw_confidence, 6), b.valid_from)

        return max(candidates, key=rank)

    # ------------------------------------------------------------ contradictions
    def open_contradictions(self, namespace: str = "default") -> list[Contradiction]:
        return self.store.contradictions(namespace, ContradictionStatus.OPEN)

    def resolve_contradiction(self, contradiction_id: str, *, strategy: str = "manual",
                              winner_id: Optional[str] = None, actor: str = "human",
                              note: str = "",
                              make_multi_valued: bool = False) -> Contradiction:
        """Resolve a contradiction. Strategies:

        * ``keep_new`` / ``keep_existing`` – explicit winner is belief_a / belief_b.
        * ``highest_confidence`` – keep whichever belief currently has more support.
        * ``newest`` – keep the most recently-valid belief; supersede the older.
        * ``reliable`` – keep the belief with the strongest confidence lower bound
          (mildly recency-weighted); robust to poisoning and stale data.
        * ``keep_both`` – both are valid (marks the predicate multi-valued).
        * ``manual`` – caller supplies ``winner_id``.
        """
        con = self.store.get_contradiction(contradiction_id)
        if con is None:
            raise KeyError(f"unknown contradiction {contradiction_id}")
        if con.status != ContradictionStatus.OPEN:
            return con

        a = self.store.get_belief(con.belief_a)
        b = self.store.get_belief(con.belief_b)
        if a is None or b is None:
            raise KeyError("contradiction references missing belief")

        keep_both = False
        if strategy == "keep_new":
            winner = a
        elif strategy == "keep_existing":
            winner = b
        elif strategy == "highest_confidence":
            winner = a if a.raw_confidence >= b.raw_confidence else b
        elif strategy == "newest":
            winner = a if a.valid_from >= b.valid_from else b
        elif strategy == "reliable":
            # Blend a conservative confidence lower bound with recency. Robust to
            # poisoning because (a) a single source's supporting AND contradicting
            # evidence is trust-scaled and capped, and (b) thinly-supported claims
            # have a low lower bound; still tracks legitimate high-trust updates.
            def _authority(bel: Belief) -> float:
                rec = conf.recency_weight(now() - bel.valid_from,
                                          self.config.recency_half_life)
                return conf.confidence_lower_bound(bel.alpha, bel.beta) * (0.7 + 0.3 * rec)
            winner = a if _authority(a) >= _authority(b) else b
        elif strategy == "keep_both":
            keep_both = True
            winner = a
        elif strategy == "manual":
            if winner_id not in (a.id, b.id):
                raise ValueError("winner_id must reference one of the beliefs")
            winner = a if winner_id == a.id else b
        else:
            raise ValueError(f"unknown strategy {strategy!r}")

        loser = b if winner.id == a.id else a

        if keep_both or make_multi_valued:
            self.detector.registry.set_multi_valued(a.predicate)
            for belief in (a, b):
                belief.status = BeliefStatus.ACTIVE
                belief.contradiction_count = max(0, belief.contradiction_count - 1)
                self.store.upsert_belief(belief)
            con.resolution = "keep_both"
            con.winner_id = ""
        else:
            winner.status = BeliefStatus.ACTIVE
            winner.metadata["resolved_winner"] = True
            # Supersede (soft-retire) the loser with a temporal end.
            loser.status = BeliefStatus.SUPERSEDED
            loser.valid_to = now()
            self.store.upsert_belief(winner)
            self.store.upsert_belief(loser)
            con.resolution = strategy
            con.winner_id = winner.id
            self._audit("belief_superseded", loser.id, con.namespace, actor,
                        superseded_by=winner.id)

        con.status = ContradictionStatus.RESOLVED
        con.resolved_by = actor
        con.resolved_at = now()
        con.note = (con.note + " | " + note).strip(" |") if note else con.note
        self.store.upsert_contradiction(con)
        self._audit("contradiction_resolved", winner.id, con.namespace, actor,
                    contradiction=con.id, strategy=strategy,
                    winner=con.winner_id or "both")
        self.metrics.incr("contradiction.resolved")
        return con

    def retract(self, belief_id: str, *, actor: str = "human", reason: str = "") -> Belief:
        b = self.store.get_belief(belief_id)
        if b is None:
            raise KeyError(belief_id)
        b.status = BeliefStatus.RETRACTED
        b.valid_to = now()
        self.store.upsert_belief(b)
        self._audit("retracted", b.id, b.namespace, actor, reason=reason)
        return b

    # -------------------------------------------------------------------- decay
    def apply_decay(self, namespace: str = "default",
                    at_time: Optional[float] = None) -> dict[str, int]:
        """Age all beliefs: discount un-reinforced evidence toward the prior and
        auto-retire anything that has decayed below the retirement threshold.
        Idempotent-ish: decays relative to ``last_reinforced_at`` each call is
        avoided by advancing ``last_reinforced_at`` to the evaluation time.
        """
        at = now() if at_time is None else at_time
        changed = {"decayed": 0, "retired": 0}
        for b in self.store.all_beliefs(namespace):
            if b.status in (BeliefStatus.RETRACTED, BeliefStatus.SUPERSEDED):
                continue
            elapsed = at - b.last_reinforced_at
            if elapsed <= 0:
                continue
            b.alpha, b.beta = conf.decay_evidence(
                b.alpha, b.beta, self.config.prior_alpha, self.config.prior_beta,
                elapsed, self.config.decay_half_life,
            )
            b.last_reinforced_at = at
            changed["decayed"] += 1
            if (b.status == BeliefStatus.ACTIVE
                    and b.raw_confidence <= self.config.retire_threshold):
                b.status = BeliefStatus.RETRACTED
                b.valid_to = at
                changed["retired"] += 1
                self._audit("decayed_retired", b.id, namespace, "system",
                            confidence=b.raw_confidence)
            self.store.upsert_belief(b)
        self.metrics.incr("decay.runs")
        return changed

    # ------------------------------------------------------------- audit/metrics
    def audit(self, namespace: str = "default", belief_id: Optional[str] = None,
              limit: int = 200) -> list[AuditEvent]:
        return self.store.audit_log(namespace, belief_id, limit)

    def verify_audit(self, namespace: str = "default") -> bool:
        return self.store.verify_audit_chain(namespace)

    def stats(self, namespace: str = "default") -> dict[str, Any]:
        s = self.store.stats(namespace)
        s["metrics"] = self.metrics.snapshot()
        s["audit_intact"] = self.store.verify_audit_chain(namespace)
        return s

    def close(self) -> None:
        self.store.close()

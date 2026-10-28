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
* No single source or declared source group can flood a belief to certainty, and
  promotion to ``ACTIVE`` can require corroboration from independent groups.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from ..config import AnchorConfig
from ..observability import Metrics, get_logger, log
from . import confidence as conf
from .contradiction import ContradictionDetector, PredicateRegistry, Relation
from .embeddings import EmbeddingProvider, cosine, make_embedder
from .explain import Explanation, explain_belief
from .graph import BeliefGraph, build_graph
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
    reinforced_id: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "belief": self.belief.to_dict(),
            "contradictions": [c.to_dict() for c in self.contradictions],
            "reinforced_id": self.reinforced_id,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ObserveResult:
        return cls(
            action=str(payload["action"]),
            belief=Belief.from_dict(payload["belief"]),
            contradictions=[
                Contradiction.from_dict(item) for item in payload.get("contradictions", [])
            ],
            reinforced_id=payload.get("reinforced_id"),
            notes=list(payload.get("notes", [])),
        )


class IdempotencyConflict(ValueError):
    """An idempotency key was reused for a different observation payload."""


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
    def __init__(self, config: AnchorConfig | None = None,
                 store: SQLiteStore | None = None,
                 embedder: EmbeddingProvider | None = None,
                 predicate_registry: PredicateRegistry | None = None):
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
        # Serialises compound write operations in one engine process. SQLite
        # still handles cross-process locking, while this lock makes durable
        # idempotency exact for the threaded HTTP server.
        self._write_lock = threading.RLock()

    # ------------------------------------------------------------------ helpers
    def _embed(self, text: str) -> list[float]:
        return self.embedder.embed(text)

    @staticmethod
    def _source_id(prov: Provenance) -> str:
        return prov.source_id or prov.agent_id or prov.session_id or prov.source_type.value

    @classmethod
    def _source_group_id(cls, prov: Provenance) -> str:
        return prov.source_group or cls._source_id(prov)

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
            self.store.distinct_source_groups(belief.id)
            >= self.config.min_sources_for_active
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
        statement: str | None = None,
        polarity: Polarity | str = Polarity.POSITIVE,
        namespace: str = "default",
        source_type: SourceType | str = SourceType.USER,
        source_id: str = "",
        source_group: str = "",
        agent_id: str = "",
        session_id: str = "",
        trust: float | None = None,
        valid_from: float | None = None,
        valid_to: float | None = None,
        at: float | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> ObserveResult:
        """Record an observation once, with durable retry deduplication.

        Reusing ``idempotency_key`` with the same payload returns the original
        result without adding evidence. Reusing it with a different payload is
        rejected, protecting callers from accidental key collisions.
        """
        if len(idempotency_key) > 256:
            raise ValueError("idempotency key must be 256 characters or fewer")
        request_payload = {
            "subject": subject,
            "predicate": predicate,
            "object": object,
            "statement": statement,
            "polarity": polarity.value if isinstance(polarity, Polarity) else polarity,
            "source_type": (
                source_type.value if isinstance(source_type, SourceType) else source_type
            ),
            "source_id": source_id,
            "source_group": source_group,
            "agent_id": agent_id,
            "session_id": session_id,
            "trust": trust,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "at": at,
            "tags": tags or [],
            "metadata": metadata or {},
        }
        request_hash = hashlib.sha256(
            json.dumps(request_payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        with self._write_lock:
            if idempotency_key:
                stored = self.store.get_idempotency(namespace, idempotency_key)
                if stored is not None:
                    stored_hash, response = stored
                    if stored_hash != request_hash:
                        raise IdempotencyConflict(
                            "idempotency key was already used for a different observation"
                        )
                    self.metrics.incr("observe.idempotent_replay")
                    return ObserveResult.from_dict(response)
            result = self._observe_once(
                subject,
                predicate,
                object,
                statement=statement,
                polarity=polarity,
                namespace=namespace,
                source_type=source_type,
                source_id=source_id,
                source_group=source_group,
                agent_id=agent_id,
                session_id=session_id,
                trust=trust,
                valid_from=valid_from,
                valid_to=valid_to,
                at=at,
                tags=tags,
                metadata=metadata,
            )
            if idempotency_key:
                self.store.record_idempotency(
                    namespace, idempotency_key, request_hash, result.to_dict()
                )
            return result

    def _observe_once(
        self,
        subject: str,
        predicate: str,
        object: str,
        *,
        statement: str | None = None,
        polarity: Polarity | str = Polarity.POSITIVE,
        namespace: str = "default",
        source_type: SourceType | str = SourceType.USER,
        source_id: str = "",
        source_group: str = "",
        agent_id: str = "",
        session_id: str = "",
        trust: float | None = None,
        valid_from: float | None = None,
        valid_to: float | None = None,
        at: float | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
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
            source_type=source_type, source_id=source_id, source_group=source_group,
            agent_id=agent_id,
            session_id=session_id, raw_text=screen.text, trust=eff_trust,
            observed_at=ts,
            metadata={"flags": screen.flags} if screen.flags else {},
        )
        src_key = self._source_id(prov)
        group_key = self._source_group_id(prov)

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
                reinforce_target.other, prov, src_key, group_key, screen.text, ts)

        # --- create the new belief --------------------------------------------
        source_add = clamp_source_evidence(
            self.store.source_evidence(candidate.id, src_key),
            self.config.reinforce_gain * eff_trust,
            self.config.max_source_evidence * eff_trust,
        )
        add = (
            clamp_source_evidence(
                self.store.source_group_evidence(candidate.id, group_key),
                source_add,
                self.config.max_source_group_evidence,
            )
            if prov.source_group
            else source_add
        )
        candidate.alpha += add
        candidate.provenance.append(prov)
        self.store.upsert_belief(candidate)
        self.store.add_source_evidence(candidate.id, src_key, add)
        self.store.add_source_group_evidence(candidate.id, group_key, add)
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
                source_badd = clamp_source_evidence(
                    self.store.source_evidence(other.id, "con::" + src_key),
                    self.config.contradict_gain * eff_trust,
                    self.config.max_source_evidence * eff_trust,
                )
                badd = (
                    clamp_source_evidence(
                        self.store.source_group_evidence(other.id, "con::" + group_key),
                        source_badd,
                        self.config.max_source_group_evidence,
                    )
                    if prov.source_group
                    else source_badd
                )
                other.beta += badd
                self.store.add_source_evidence(other.id, "con::" + src_key, badd)
                self.store.add_source_group_evidence(other.id, "con::" + group_key, badd)
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
                             src_key: str, group_key: str, text: str,
                             at: float | None = None) -> ObserveResult:
        ts = now() if at is None else at
        source_add = clamp_source_evidence(
            self.store.source_evidence(belief.id, src_key),
            self.config.reinforce_gain * prov.trust,
            self.config.max_source_evidence * max(prov.trust, 1e-6),
        )
        add = (
            clamp_source_evidence(
                self.store.source_group_evidence(belief.id, group_key),
                source_add,
                self.config.max_source_group_evidence,
            )
            if prov.source_group
            else source_add
        )
        belief.alpha, belief.beta = conf.reinforce(
            belief.alpha, belief.beta, add, 1.0)  # add already trust-scaled/capped
        belief.reinforcement_count += 1
        belief.last_reinforced_at = ts
        belief.provenance.append(prov)
        if belief.status == BeliefStatus.PROVISIONAL:
            self.store.add_source_evidence(belief.id, src_key, add)
            self.store.add_source_group_evidence(belief.id, group_key, add)
            self._maybe_promote(belief)
        else:
            self.store.add_source_evidence(belief.id, src_key, add)
            self.store.add_source_group_evidence(belief.id, group_key, add)
        self.store.upsert_belief(belief)
        self.metrics.incr("observe.reinforced")
        self._audit("reinforced", belief.id, belief.namespace, src_key,
                    confidence=belief.raw_confidence,
                    reinforcement_count=belief.reinforcement_count)
        return ObserveResult(action="reinforced", belief=belief,
                             reinforced_id=belief.id,
                             notes=[f"confidence -> {belief.raw_confidence:.3f}"])

    # -------------------------------------------------------------------- reads
    def get(self, belief_id: str) -> Belief | None:
        return self.store.get_belief(belief_id)

    def recall(self, query: str, *, namespace: str = "default", top_k: int = 5,
               at_time: float | None = None, include_disputed: bool = True,
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
                       at: float | None = None) -> Belief | None:
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

    # ---------------------------------------------------------- explainability
    def explain(self, belief_id: str, *, at: float | None = None) -> Explanation:
        """Explain *why* a belief is held: evidence attribution per source, the
        Beta posterior and its credible interval, the audit timeline, a decay
        projection, and plain-English reasoning.

        This is the read-side counterpart to :meth:`observe` — an agent (or a
        human reviewer) can always ask memory to justify itself.
        """
        belief = self.store.get_belief(belief_id)
        if belief is None:
            raise KeyError(f"unknown belief {belief_id}")
        self.metrics.incr("explain.calls")
        return explain_belief(belief, self.store, self.config, at=at)

    def graph(self, namespace: str = "default", *, include_inactive: bool = False,
              include_sources: bool = False, max_beliefs: int = 250) -> BeliefGraph:
        """Project memory into a confidence-weighted knowledge graph.

        Subjects/objects become nodes, beliefs become weighted edges, and open
        contradictions become dashed conflict edges.
        """
        return build_graph(
            self.store, namespace, include_inactive=include_inactive,
            include_sources=include_sources, max_beliefs=max_beliefs,
        )

    # ------------------------------------------------------------ contradictions
    def open_contradictions(self, namespace: str = "default") -> list[Contradiction]:
        return self.store.contradictions(namespace, ContradictionStatus.OPEN)

    def resolve_contradiction(self, contradiction_id: str, *, strategy: str = "manual",
                              winner_id: str | None = None, actor: str = "human",
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
                    at_time: float | None = None) -> dict[str, int]:
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
    def audit(self, namespace: str = "default", belief_id: str | None = None,
              limit: int = 200) -> list[AuditEvent]:
        return self.store.audit_log(namespace, belief_id, limit)

    def verify_audit(self, namespace: str = "default") -> bool:
        return self.store.verify_audit_chain(namespace)

    def backup(self, destination: str) -> dict[str, Any]:
        """Create a consistent online SQLite backup and verify its integrity."""
        result = self.store.backup(destination)
        self.metrics.incr("backup.completed")
        return result

    def stats(self, namespace: str = "default") -> dict[str, Any]:
        s = self.store.stats(namespace)
        s["metrics"] = self.metrics.snapshot()
        s["audit_intact"] = self.store.verify_audit_chain(namespace)
        return s

    def close(self) -> None:
        self.store.close()

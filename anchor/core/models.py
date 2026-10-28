"""Core data model for Anchor.

Everything here is pure-stdlib and serialisable to/from plain ``dict`` so it can
be persisted in SQLite/Postgres JSON columns and shipped over the MCP wire
without any framework coupling.

Design notes
------------
A *belief* is the atomic unit of memory.  It is more than a text chunk: it
carries a structured ``(subject, predicate, object)`` claim, a *polarity*
(is the claim asserted or negated), a Bayesian confidence backed by
``alpha``/``beta`` evidence counters, full *provenance*, and a temporal validity
window.  This structure is what lets Anchor reason about *reinforcement*,
*decay*, *temporal supersession* and *contradiction* instead of merely doing
nearest-neighbour text retrieval.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def now() -> float:
    """Wall-clock seconds. Centralised so tests can monkeypatch time."""
    return time.time()


class SourceType(str, Enum):
    USER = "user"            # a human end-user stated this
    AGENT = "agent"          # an AI agent asserted this
    TOOL = "tool"            # produced by a tool / API result
    INFERENCE = "inference"  # derived by Anchor itself
    IMPORT = "import"        # bulk imported from an external store
    SYSTEM = "system"        # operator / configuration


class Polarity(str, Enum):
    """Whether the claim is affirmed or explicitly negated."""

    POSITIVE = "positive"
    NEGATIVE = "negative"


class BeliefStatus(str, Enum):
    PROVISIONAL = "provisional"  # newly learned, not yet corroborated
    ACTIVE = "active"            # trusted, corroborated
    DISPUTED = "disputed"        # participates in an open contradiction
    SUPERSEDED = "superseded"    # replaced by a newer valid belief
    RETRACTED = "retracted"      # withdrawn (never hard-deleted)


class ContradictionKind(str, Enum):
    FUNCTIONAL = "functional"    # same subject+predicate, different object
    NEGATION = "negation"        # same claim with opposite polarity
    TEMPORAL = "temporal"        # overlapping validity windows conflict
    SEMANTIC = "semantic"        # embedding-level mutual exclusion


class ContradictionStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


@dataclass
class Provenance:
    """Where a piece of evidence came from and how much we trust the source."""

    source_type: SourceType = SourceType.USER
    source_id: str = ""
    # Optional operator-supplied correlation domain. Different source IDs in
    # the same group share an aggregate evidence cap.
    source_group: str = ""
    agent_id: str = ""
    session_id: str = ""
    raw_text: str = ""
    observed_at: float = field(default_factory=now)
    # Trust in the *source*, independent of corroboration. Used to weight how
    # much a single observation moves confidence.
    trust: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["source_type"] = self.source_type.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Provenance:
        d = dict(d)
        d["source_type"] = SourceType(d.get("source_type", "user"))
        return cls(**d)


@dataclass
class Belief:
    subject: str = ""
    predicate: str = ""
    object: str = ""
    statement: str = ""             # natural-language rendering
    polarity: Polarity = Polarity.POSITIVE

    id: str = field(default_factory=lambda: new_id("bel"))
    namespace: str = "default"      # tenant / agent partition

    # --- Bayesian evidence (Beta distribution) --------------------------------
    # confidence = alpha / (alpha + beta). alpha grows with corroboration,
    # beta grows with contradicting evidence. Priors below encode "unknown".
    alpha: float = 1.0
    beta: float = 1.0

    status: BeliefStatus = BeliefStatus.PROVISIONAL

    # --- temporal -------------------------------------------------------------
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)
    last_reinforced_at: float = field(default_factory=now)
    valid_from: float = field(default_factory=now)
    valid_to: float | None = None      # None => still valid

    reinforcement_count: int = 1
    contradiction_count: int = 0

    embedding: list[float] = field(default_factory=list)
    provenance: list[Provenance] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ helpers
    @property
    def entity_key(self) -> str:
        """Identity of the *claim slot* (ignores the object value & polarity).

        Two beliefs sharing an entity key are candidates for reinforcement or
        contradiction depending on whether their object/polarity agree.
        """
        return f"{self.namespace}::{self.subject.strip().lower()}::{self.predicate.strip().lower()}"

    @property
    def claim_key(self) -> str:
        """Identity of the *exact claim* (subject+predicate+object+polarity)."""
        return f"{self.entity_key}::{self.object.strip().lower()}::{self.polarity.value}"

    @property
    def raw_confidence(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def evidence(self) -> float:
        return self.alpha + self.beta

    def is_currently_valid(self, at: float | None = None) -> bool:
        at = now() if at is None else at
        if self.status in (BeliefStatus.RETRACTED, BeliefStatus.SUPERSEDED):
            return False
        if at < self.valid_from:
            return False
        return not (self.valid_to is not None and at >= self.valid_to)

    # ------------------------------------------------------------ serialisation
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "namespace": self.namespace,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "statement": self.statement,
            "polarity": self.polarity.value,
            "alpha": self.alpha,
            "beta": self.beta,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_reinforced_at": self.last_reinforced_at,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "reinforcement_count": self.reinforcement_count,
            "contradiction_count": self.contradiction_count,
            "embedding": list(self.embedding),
            "provenance": [p.to_dict() for p in self.provenance],
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Belief:
        return cls(
            id=d["id"],
            namespace=d.get("namespace", "default"),
            subject=d.get("subject", ""),
            predicate=d.get("predicate", ""),
            object=d.get("object", ""),
            statement=d.get("statement", ""),
            polarity=Polarity(d.get("polarity", "positive")),
            alpha=d.get("alpha", 1.0),
            beta=d.get("beta", 1.0),
            status=BeliefStatus(d.get("status", "provisional")),
            created_at=d.get("created_at", now()),
            updated_at=d.get("updated_at", now()),
            last_reinforced_at=d.get("last_reinforced_at", now()),
            valid_from=d.get("valid_from", now()),
            valid_to=d.get("valid_to"),
            reinforcement_count=d.get("reinforcement_count", 1),
            contradiction_count=d.get("contradiction_count", 0),
            embedding=list(d.get("embedding", [])),
            provenance=[Provenance.from_dict(p) for p in d.get("provenance", [])],
            tags=list(d.get("tags", [])),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass
class Contradiction:
    belief_a: str = ""
    belief_b: str = ""
    kind: ContradictionKind = ContradictionKind.FUNCTIONAL
    id: str = field(default_factory=lambda: new_id("con"))
    namespace: str = "default"
    status: ContradictionStatus = ContradictionStatus.OPEN
    detected_at: float = field(default_factory=now)
    score: float = 1.0                       # strength of the conflict [0,1]
    note: str = ""
    resolution: str = ""                     # human-readable outcome
    winner_id: str = ""                      # belief kept, if resolved
    resolved_by: str = ""
    resolved_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Contradiction:
        d = dict(d)
        d["kind"] = ContradictionKind(d.get("kind", "functional"))
        d["status"] = ContradictionStatus(d.get("status", "open"))
        return cls(**d)


@dataclass
class AuditEvent:
    """Append-only, hash-chained audit record (tamper-evident)."""

    event_type: str = ""
    belief_id: str = ""
    actor: str = "system"
    id: str = field(default_factory=lambda: new_id("evt"))
    namespace: str = "default"
    ts: float = field(default_factory=now)
    payload: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = ""
    hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AuditEvent:
        return cls(**d)

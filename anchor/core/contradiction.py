"""Contradiction detection.

When a new observation arrives, Anchor compares it against existing beliefs that
occupy the *same claim slot* (same subject+predicate) and decides the
relationship.  Crucially, a differing claim is **never silently overwritten** –
it is surfaced as a :class:`Contradiction` for reinforcement-aware, temporal, or
human resolution.

Relationship taxonomy
----------------------
* ``REINFORCE``  – same claim (object & polarity agree, or embeddings ~identical)
* ``CONTRADICT`` – mutually exclusive claim (see :class:`ContradictionKind`)
* ``COEXIST``    – different but compatible (multi-valued predicate)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .embeddings import EmbeddingProvider, cosine
from .models import Belief, ContradictionKind


class Relation(str, Enum):
    REINFORCE = "reinforce"
    CONTRADICT = "contradict"
    COEXIST = "coexist"


# Predicates where a subject may legitimately have many simultaneous objects.
# Everything else defaults to *functional* (single-valued) so that changing the
# value is treated as a contradiction requiring resolution.
_DEFAULT_MULTI_VALUED = {
    "likes", "dislikes", "knows", "owns", "visited", "speaks", "uses",
    "member_of", "interested_in", "allergic_to", "has_skill", "has_pet",
    "tag", "note",
}


class PredicateRegistry:
    """Tracks predicate cardinality. Callers may override defaults at runtime."""

    def __init__(self, multi_valued: set[str] | None = None):
        self._multi = set(_DEFAULT_MULTI_VALUED)
        if multi_valued:
            self._multi |= {p.lower() for p in multi_valued}

    def is_functional(self, predicate: str) -> bool:
        return predicate.strip().lower() not in self._multi

    def set_multi_valued(self, predicate: str) -> None:
        self._multi.add(predicate.strip().lower())

    def set_functional(self, predicate: str) -> None:
        self._multi.discard(predicate.strip().lower())


@dataclass
class Finding:
    relation: Relation
    other: Belief
    kind: ContradictionKind | None = None
    score: float = 0.0
    reason: str = ""


def _same_object(a: Belief, b: Belief, embedder: EmbeddingProvider,
                 dup_similarity: float) -> tuple[bool, float]:
    if a.object.strip().lower() == b.object.strip().lower():
        return True, 1.0
    if a.embedding and b.embedding:
        sim = cosine(a.embedding, b.embedding)
        return sim >= dup_similarity, sim
    return False, 0.0


class ContradictionDetector:
    def __init__(self, registry: PredicateRegistry | None = None):
        self.registry = registry or PredicateRegistry()

    def classify(self, new: Belief, existing: Belief, embedder: EmbeddingProvider,
                 dup_similarity: float, contradiction_similarity: float) -> Finding:
        same_obj, sim = _same_object(new, existing, embedder, dup_similarity)
        same_polarity = new.polarity == existing.polarity

        # 1. Identical claim -> reinforcement.
        if same_obj and same_polarity:
            return Finding(Relation.REINFORCE, existing, score=sim,
                           reason="identical claim")

        # 2. Same object, opposite polarity -> direct negation.
        if same_obj and not same_polarity:
            return Finding(Relation.CONTRADICT, existing,
                           kind=ContradictionKind.NEGATION, score=max(sim, 0.9),
                           reason="same object, opposite polarity")

        # 3. Different object.
        if self.registry.is_functional(new.predicate):
            # Functional predicate can hold only one value -> conflict.
            # Score blends how confident the incumbent is with topical similarity.
            base = 0.6 + 0.4 * existing.raw_confidence
            kind = ContradictionKind.FUNCTIONAL
            # If the two objects are also semantically near-exclusive, note it.
            if new.embedding and existing.embedding:
                topical = cosine(new.embedding, existing.embedding)
                if topical >= contradiction_similarity:
                    kind = ContradictionKind.SEMANTIC
                    base = max(base, topical)
            return Finding(Relation.CONTRADICT, existing, kind=kind,
                           score=min(1.0, base),
                           reason="functional predicate, differing object")

        # 4. Multi-valued predicate -> both can be true.
        return Finding(Relation.COEXIST, existing, score=sim,
                       reason="multi-valued predicate")

    def assess(self, new: Belief, candidates: list[Belief], embedder: EmbeddingProvider,
               dup_similarity: float, contradiction_similarity: float) -> list[Finding]:
        findings = []
        for existing in candidates:
            if existing.id == new.id:
                continue
            findings.append(self.classify(
                new, existing, embedder, dup_similarity, contradiction_similarity
            ))
        return findings

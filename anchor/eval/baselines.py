"""Baseline memory strategies for the evaluation benchmark.

These represent the two dominant "agent memory" approaches Anchor is measured
against:

* :class:`SummaryMemory` — a running key-value / summary store that
  **overwrites** on update (last-write-wins). Simple, but loses history and is
  trivially poisoned.
* :class:`RAGMemory` — vector retrieval over every stored utterance. Great for
  recall, but has **no notion of truth, recency or contradiction**: it returns
  whatever text is most similar, stale or not.

All three memories implement the same tiny interface used by the harness:
``ingest(fact)`` and ``answer(subject, predicate)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..core.embeddings import LocalHashingEmbedder, cosine


@dataclass
class Fact:
    subject: str
    predicate: str
    object: str
    t: float                 # observation time (timeline)
    trust: float = 0.8
    source: str = "user"


class SummaryMemory:
    """Last-write-wins key-value memory (overwrites conflicting facts)."""

    name = "summary(overwrite)"

    def __init__(self) -> None:
        self._kv: dict[tuple[str, str], str] = {}

    def ingest(self, f: Fact) -> None:
        self._kv[(f.subject, f.predicate)] = f.object

    def answer(self, subject: str, predicate: str) -> Optional[str]:
        return self._kv.get((subject, predicate))


class RAGMemory:
    """Vector-retrieval memory over raw utterances (no truth maintenance)."""

    name = "rag(vector)"

    def __init__(self, dim: int = 256) -> None:
        self._emb = LocalHashingEmbedder(dim=dim)
        self._items: list[tuple[list[float], Fact]] = []

    def ingest(self, f: Fact) -> None:
        text = f"{f.subject} {f.predicate} {f.object}"
        self._items.append((self._emb.embed(text), f))

    def answer(self, subject: str, predicate: str) -> Optional[str]:
        q = self._emb.embed(f"{subject} {predicate}")
        best: Optional[Fact] = None
        best_sim = -1.0
        for vec, f in self._items:
            sim = cosine(q, vec)
            if sim > best_sim:
                best_sim, best = sim, f
        return best.object if best else None


class AnchorMemory:
    """Adapter around the Anchor engine using an automatic resolution policy.

    On ingest, conflicting functional claims are auto-resolved with the
    ``newest`` strategy (temporal reasoning) — the same decision a human would
    typically confirm — while still recording full provenance and audit history.
    """

    name = "anchor"

    def __init__(self, anchor, namespace: str = "eval") -> None:
        self.a = anchor
        self.ns = namespace

    def ingest(self, f: Fact) -> None:
        self.a.observe(f.subject, f.predicate, f.object, namespace=self.ns,
                       source_type="user", source_id=f.source, trust=f.trust, at=f.t)
        # Apply an automatic reliability-aware resolution policy for functional
        # slots (robust to poisoning and stale updates).
        for con in self.a.open_contradictions(self.ns):
            self.a.resolve_contradiction(con.id, strategy="reliable", actor="policy")

    def answer(self, subject: str, predicate: str) -> Optional[str]:
        b = self.a.current_belief(subject, predicate, namespace=self.ns)
        return b.object if b else None

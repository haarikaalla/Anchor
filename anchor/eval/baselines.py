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

from dataclasses import dataclass

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

    def answer(self, subject: str, predicate: str) -> str | None:
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

    def answer(self, subject: str, predicate: str) -> str | None:
        q = self._emb.embed(f"{subject} {predicate}")
        best: Fact | None = None
        best_sim = -1.0
        for vec, f in self._items:
            sim = cosine(q, vec)
            if sim > best_sim:
                best_sim, best = sim, f
        return best.object if best else None


class RecencyWindowMemory:
    """A stronger, realistic baseline: last-write-wins *within a time window*.

    This is roughly what a well-engineered summary memory looks like in practice
    — it keeps a short history per slot and answers with the most recent entry,
    optionally ignoring anything older than ``window``. It fixes staleness but
    still has no concept of *who* said something or how much it is corroborated,
    so a persistent low-trust source still wins simply by speaking last.
    """

    name = "recency-window"

    def __init__(self, window: float = 30 * 86_400.0) -> None:
        self.window = window
        self._hist: dict[tuple[str, str], list[Fact]] = {}
        self._now = 0.0

    def ingest(self, f: Fact) -> None:
        self._hist.setdefault((f.subject, f.predicate), []).append(f)
        self._now = max(self._now, f.t)

    def answer(self, subject: str, predicate: str) -> str | None:
        facts = self._hist.get((subject, predicate))
        if not facts:
            return None
        fresh = [f for f in facts if self._now - f.t <= self.window] or facts
        return max(fresh, key=lambda f: f.t).object


class TrustWeightedMemory:
    """Trust-weighted voting without belief lifecycle or evidence caps.

    A common hand-rolled defence: sum source trust per candidate value and pick
    the winner. It resists a *single* low-trust source, but has no per-source
    cap, so the same attacker repeating itself enough times still overwhelms the
    truth — which is precisely the failure mode Anchor's evidence cap closes.
    """

    name = "trust-vote"

    def __init__(self) -> None:
        self._votes: dict[tuple[str, str], dict[str, float]] = {}

    def ingest(self, f: Fact) -> None:
        slot = self._votes.setdefault((f.subject, f.predicate), {})
        slot[f.object] = slot.get(f.object, 0.0) + f.trust

    def answer(self, subject: str, predicate: str) -> str | None:
        slot = self._votes.get((subject, predicate))
        if not slot:
            return None
        return max(slot.items(), key=lambda kv: kv[1])[0]


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

    def answer(self, subject: str, predicate: str) -> str | None:
        b = self.a.current_belief(subject, predicate, namespace=self.ns)
        return b.object if b else None

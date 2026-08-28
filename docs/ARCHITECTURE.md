# Anchor architecture

This document explains the *why* behind Anchor's design. The code is the source
of truth; this is the map.

## 1. The belief model

The atomic unit is a **belief**, not a text chunk. A belief is a structured
claim:

```
(namespace, subject, predicate, object, polarity)
```

plus a natural-language `statement`, a Bayesian confidence, provenance, a
temporal validity window, and a lifecycle `status`.

Two derived keys drive reconciliation:

- **`entity_key`** = `namespace::subject::predicate` — the *claim slot*. Beliefs
  sharing an entity key are candidates for reinforcement or contradiction.
- **`claim_key`** = `entity_key::object::polarity` — the *exact claim*. Identical
  claim keys reinforce; differing objects/polarities may contradict.

Why structured? Because reinforcement, temporal supersession, and contradiction
are impossible to reason about over opaque text embeddings alone. Structure is
what separates a *memory engine* from a *vector cache*.

## 2. Confidence as Bayesian evidence

Each belief carries `alpha` (supporting evidence) and `beta` (contradicting
evidence) of a Beta distribution:

- `confidence = alpha / (alpha + beta)` — the posterior mean.
- `confidence_lower_bound` — a conservative estimate (~10th percentile). Ranking
  and the `reliable` resolution strategy use the lower bound so that a claim with
  *lots* of corroboration beats one that is merely *plausible* but thinly
  supported. This is the mathematical backbone of poisoning resistance: a
  repeated low-trust lie has thin, high-variance evidence and thus a low lower
  bound.

Reinforcement adds trust-scaled evidence to `alpha`; contradiction adds it to
`beta`. Both are **capped per source** (see §6).

## 3. Temporal reasoning & decay

- Beliefs have `valid_from` / `valid_to`. Retrieval and `current_belief` respect
  the queried time (`at`), enabling time-travel queries and back-filling.
- **Decay**: un-reinforced evidence is exponentially discounted toward the prior
  with a configurable half-life — modelling forgetting as a drift toward
  "unknown", not toward "false". Beliefs that decay below a floor auto-retire.
- **Recency** weighting nudges ranking and the `reliable` strategy toward fresh
  information without letting a single fresh lie dominate a corroborated truth.

## 4. Contradiction detection

When a new observation lands, Anchor pulls existing beliefs with the same
`entity_key` and classifies each pair:

- **REINFORCE** — same object & polarity (or embeddings ~identical).
- **CONTRADICT** — mutually exclusive:
  - `NEGATION` — same object, opposite polarity.
  - `FUNCTIONAL` — a single-valued predicate now has a different object.
  - `SEMANTIC` — functional conflict where the objects are also topically close.
- **COEXIST** — a multi-valued predicate (e.g. `likes`) — both can be true.

Predicate cardinality lives in a `PredicateRegistry` (sensible defaults, runtime
overridable; `keep_both` resolution marks a predicate multi-valued going
forward).

A contradiction is **recorded**, both beliefs are marked `disputed`, and the
incumbent's `beta` is nudged up (trust-scaled, capped) — but **nothing is
deleted**. Resolution is explicit.

## 5. Resolution & human confirmation

`resolve_contradiction(strategy=...)` supports:

| strategy | keeps | use case |
|---|---|---|
| `keep_new` / `keep_existing` | the named side | explicit human choice |
| `manual` | `winner_id` | UI-driven confirmation |
| `highest_confidence` | stronger evidence | trust the corpus |
| `newest` | latest `valid_from` | genuine updates/relocations |
| `reliable` | lower-bound × recency | **default automated policy; poisoning-robust** |
| `keep_both` | both (multi-valued) | it was never a conflict |

Losers are **superseded** (soft-retired with a `valid_to`), preserving history.
Every resolution is audited with the actor.

## 6. Security: memory-poisoning defenses

Memory is a high-value attack surface. Anchor's layered controls:

1. **Input hygiene** — length limits, control-char stripping, and prompt-injection
   screening. Suspicious inputs are *flagged and trust-penalised*, not blindly
   trusted (or blindly dropped).
2. **Provenance-weighted evidence** — every observation moves confidence in
   proportion to source `trust`.
3. **Per-source evidence caps** — a single source's cumulative supporting *and*
   contradicting evidence is trust-scaled and bounded, defeating repetition /
   Sybil flooding.
4. **Corroboration requirement** — promotion to `active` can require multiple
   distinct sources.

## 7. Storage & tamper-evident audit

`SQLiteStore` keeps structured columns for querying plus a full JSON blob for
lossless round-trips. The SQL is deliberately vanilla so a Postgres backend can
be added without touching the engine.

The **audit log is append-only and hash-chained per namespace**:
`hash = sha256(prev_hash + canonical(event))`. `verify_audit_chain` recomputes
the chain and detects any tampering — giving cryptographic provenance over how a
belief came to be.

## 8. Observability

`observability.py` provides JSON-line logging and in-process counters/gauges/
timers surfaced via `Anchor.stats()` — enough to wire into Prometheus/OpenTelemetry
without adding dependencies to the core.

## 9. Integration surface

- **MCP** (`anchor/mcp/server.py`) exposes the engine as MCP tools for any host.
- **Python** — `from anchor import Anchor`.
- The engine is intentionally framework-independent; LangGraph/CrewAI/AutoGen
  integrations are thin adapters over `observe` / `recall` / `current_belief`.

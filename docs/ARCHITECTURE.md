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
`beta`. Both are **capped per source**, and can also be capped across a declared
source group (see §6).

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
3. **Per-source evidence caps** — a single identity's cumulative supporting and
   contradicting evidence is trust-scaled and bounded, defeating repetition.
4. **Per-group evidence caps** — identities known to share an owner, pipeline or
   trust domain can be assigned one `source_group` and share an aggregate budget.
   This limits known correlated-source amplification; it is not automatic Sybil
   detection.
5. **Corroboration requirement** — promotion to `active` can require multiple
   independent source groups rather than multiple labels for one source.

## 7. Storage & tamper-evident audit

`SQLiteStore` keeps structured columns for querying plus a full JSON blob for
lossless round-trips. The SQL is deliberately vanilla so a Postgres backend can
be added without touching the engine.

The **audit log is append-only and hash-chained per namespace**:
`hash = sha256(prev_hash + canonical(event))`. `verify_audit_chain` recomputes
the chain and detects any tampering — giving cryptographic provenance over how a
belief came to be.

Idempotency receipts are stored alongside beliefs and scoped by namespace. Each
receipt binds a caller-provided key to the canonical request hash and serialized
result. An identical retry replays the result without adding evidence; a changed
payload under the same key fails as a conflict.

Backups use SQLite's online backup API, so the copy is consistent while the
service is live. Anchor reopens the destination and runs `PRAGMA integrity_check`
before returning success. `anchor doctor` combines that database check with
audit-chain verification.

## 8. Explainability

`core/explain.py` is a **read-only projection**. It never stores anything: given a
belief, it folds the evidence ledger (`store.source_evidence_breakdown`) and the
audit trail back into a structured `Explanation` — a Beta credible interval,
per-source attribution (with a `capped` flag when a source hit its evidence
ceiling), a decay projection at 0/7/30/90/180 days, a chronological timeline, and
plain-English reasons.

Because it is reconstructed on every call rather than narrated at write time, an
explanation cannot drift from what the engine actually did. If the maths changes,
the explanation changes with it.

## 9. Knowledge graph

`core/graph.py` projects beliefs into a graph: entity nodes (`e:{label}`),
optional source nodes (`s:{name}`), belief edges weighted by confidence, and
`contradiction` edges between conflicting claims. Layout uses a deterministic
seeded Fruchterman-Reingold implementation, so the same beliefs always render
identically — which is what makes the generated SVG assets diffable.

Exports: Mermaid, Graphviz DOT, JSON, and animated SVG.

## 10. Control plane

`server/api.py` is a `ThreadingHTTPServer` on the stdlib only — REST endpoints, a
Server-Sent Events stream, and a Prometheus `/metrics` endpoint.

The SSE bus works by **tailing the audit log** (`store.audit_after`) rather than
hooking the engine. That choice means the stream reflects only committed state and
works across processes — a CLI `observe` in one terminal appears live in a
dashboard served by another.

Security: loopback binding by default, optional global bearer token and
namespace-scoped tenant tokens compared with `hmac.compare_digest`, CORS disabled
unless explicitly configured, a request body cap, strict
`nosniff`/`DENY`/`no-referrer` headers, and no filesystem serving. Tenant tokens
derive their namespace at the boundary; attempts to name or directly address
another tenant's objects return `403`.
`server/dashboard.py` is a single self-contained HTML document that renders all
data through `textContent`; `innerHTML` and `eval` appear nowhere, enforced by a
test.

## 11. Documentation as a build artifact

`viz/` renders animated SVG (CSS `@keyframes`, no JavaScript) from **live engine
runs** — terminal casts, charts, graphs and the capability matrix. `anchor
render-assets` regenerates all of them.

Animated SVG was chosen over recorded GIF deliberately: it is kilobytes rather
than megabytes, crisp at any zoom, diffable as text in git, requires no recording
tool in CI, and is inert (no scripting). Because the visuals are generated from
real runs, documentation cannot silently drift from behaviour.

## 12. Observability

`observability.py` provides JSON-line logging and in-process counters/gauges/
timers surfaced via `Anchor.stats()` — enough to wire into Prometheus/OpenTelemetry
without adding dependencies to the core.

## 13. Deployment boundary

The production image runs as an unprivileged UID, persists only `/data`, exposes
a health check and supports a read-only root filesystem. The supplied Compose
profile binds to loopback, sets `no-new-privileges` and stores the database in a
named volume. CI builds this image, tests Python 3.10–3.13, installs the wheel in
a clean environment, performs static security analysis and audits installed
dependencies. Dependabot covers Python, Actions and Docker updates weekly.

## 14. Integration surface

- **MCP** (`anchor/mcp/server.py`) exposes the engine as MCP tools for any host.
- **HTTP** (`anchor/server/`) — REST + SSE + Prometheus, `anchor serve`.
- **Tool adapters** (`anchor/adapters/`) — `AnchorToolkit` emits OpenAI, Anthropic
  and LangChain tool schemas and dispatches calls, returning `guidance` alongside
  results so a model knows to *ask* rather than guess when a contradiction opens.
- **Python** — `from anchor import Anchor`.
- The engine is intentionally framework-independent; LangGraph/CrewAI/AutoGen
  integrations are thin adapters over `observe` / `recall` / `current_belief`.

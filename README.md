<div align="center">

# ⚓ Anchor

### Persistent belief-memory infrastructure for AI agents

![Anchor — conventional memory overwrites and is silently wrong; Anchor keeps both claims and flags the contradiction](docs/assets/hero.svg)

**Agents don't have a memory problem. They have a *truth* problem.**

Anchor gives an agent beliefs that form, strengthen, conflict, age and get
resolved — with provenance, Bayesian confidence, contradiction detection and a
tamper-evident audit trail. **It never silently overwrites what it knows.**

![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)
![CI](https://github.com/haarikaalla/Anchor/actions/workflows/ci.yml/badge.svg)
![Tests](https://img.shields.io/badge/tests-111%20passing-brightgreen)
![Dependencies](https://img.shields.io/badge/core%20deps-zero-success)
![MCP](https://img.shields.io/badge/protocol-MCP-8A2BE2)
![Version](https://img.shields.io/badge/version-0.2.0-informational)
![License](https://img.shields.io/badge/license-Apache%202.0-lightgrey)

</div>

---

## What makes this strong

Not a wrapper around an LLM. A memory **engine** with the properties you'd
demand of a system of record:

| | |
|---|---|
| **Never loses truth** | A conflicting claim raises a contradiction; the old belief is *superseded*, never deleted. Every prior state is recoverable. |
| **Resists poisoning** | Trust weighting, per-source evidence caps and conservative lower-bound ranking. **1.00 poison resistance** where overwrite memory scores 0.00. |
| **Explains itself** | Every belief answers *why* — credible interval, per-source attribution, decay forecast, audit timeline. Reconstructed from the ledger, so it cannot drift. |
| **Tamper-evident** | SHA-256 hash-chained audit log per namespace. `verify_audit()` detects any retroactive edit. |
| **Time-aware** | Beliefs have validity windows; unconfirmed evidence decays on a half-life and faded beliefs auto-retire. |
| **Zero core dependencies** | Engine, HTTP server, dashboard, knowledge graph and SVG renderer are **pure stdlib**. Fully offline, deterministic, reproducible. |
| **Production surfaces** | REST + SSE + Prometheus, MCP server, CLI, live dashboard, and OpenAI/Anthropic/LangChain adapters. |
| **Honest by construction** | 111 tests. Every README visual is generated from a live engine run — the hero banner *refuses to render* if the engine stops behaving as advertised. |

---

## The problem

Every agent that remembers things eventually hits some version of this:

> *The user said they moved to Munich. The agent kept booking flights to Berlin.*
> *Or worse — a scraped page said "Munich", and the agent believed it over the user.*

Agent memory today is one of two broken things:

| | What it does | How it fails |
|---|---|---|
| **Summary / key-value memory** | Overwrites the slot on update | Forgets history and is **trivially poisoned** — the last writer wins, even if it's an untrusted scraper |
| **Vector RAG memory** | Stores every utterance, retrieves by similarity | **No notion of truth, recency or conflict** — returns "Berlin" and "Munich" side by side and lets the LLM guess |

Both share one root cause: **they store *text*, not *beliefs*.** There is no
representation of *how strongly* something is believed, *who said it*, *when it
stopped being true*, or *what contradicts it*.

## The solution

Anchor models memory the way a careful analyst does — as **beliefs**: structured,
provenanced, probabilistic claims that evolve over time.

When a new claim conflicts with an existing one, Anchor **does not overwrite it**.
It raises a contradiction, keeps both, and asks:

![Anchor demo — belief memory detecting a contradiction instead of overwriting](docs/assets/anchor-demo.svg)

> Everything animated in this README is generated from a **live engine run** via
> `anchor render-assets` — no mockups, no screen recordings. If the behaviour
> changes, the visuals change with it.

---

## How it compares

<div align="center">

![Capability matrix — summary memory vs vector RAG vs Anchor](docs/assets/capability-matrix.svg)

</div>

---

## Measured, not asserted

Anchor ships its own adversarial benchmark: 30 memory slots, 201 events, 18
mid-stream value changes, 9 low-trust poisoning attempts. Five memory strategies,
same data, deterministic and fully offline.

```bash
python -m anchor.eval.benchmark        # or: anchor bench --json results.json
```

| memory | accuracy | update acc. | poison resist. | stale resist. |
|---|:--:|:--:|:--:|:--:|
| summary (overwrite) | 0.70 | 0.78 | **0.00** | 1.00 |
| recency window | 0.70 | 0.78 | **0.00** | 1.00 |
| trust vote | 0.33 | 0.17 | **0.11** | 0.33 |
| rag (vector) | 0.63 | 0.56 | 0.56 | 0.61 |
| **anchor** | **1.00** | **1.00** | **1.00** | **1.00** |

![Benchmark — Anchor vs four baselines across four axes](docs/assets/benchmark.svg)

**The interesting result isn't that Anchor wins — it's *how each baseline loses*.**
They fail on *different* axes, and the fixes are mutually exclusive:

- **Overwrite memory** is perfectly current and **completely defenceless** (0.00) —
  the poisoner simply writes last.
- **Trust voting** resists poisoning slightly, but majority-votes itself into stale
  answers; it can barely accept a legitimate change (0.17 update accuracy).
- **Vector RAG** is mediocre everywhere, because similarity search has no opinion
  about truth.

You cannot patch your way from any of these to correctness — freshness and
poison-resistance pull in opposite directions. You need **evidence, provenance and
corroboration as first-class state**. That is the entire thesis.

> **On reading these numbers honestly:** this is a *synthetic* benchmark written
> by the same author as the engine, with baselines implemented for comparison
> rather than tuned to win. It is designed to demonstrate the structural tension
> between freshness and poison-resistance — not to prove production performance on
> real workloads. Every number reproduces offline via the command above, and the
> harness is in [anchor/eval/](anchor/eval/) so you can disagree with it in code.

![Poisoning resistance under sustained low-trust attack](docs/assets/poisoning.svg)

---

## Live control plane

Anchor ships a **dependency-free HTTP API and real-time dashboard** — built on
`http.server`, Server-Sent Events and a hand-written SVG renderer. No React, no
build step, no `node_modules`.

```bash
anchor serve --open          # http://127.0.0.1:8080
```

A live operator console: belief table with confidence bars, animated knowledge
graph, a **contradiction inbox** with one-click resolution strategies, a streaming
audit feed, and a slide-out explainability drawer for any belief.

```text
GET  /                     dashboard (single self-contained HTML document)
GET  /api/stats            memory health
GET  /api/beliefs          current beliefs
GET  /api/contradictions   the inbox
GET  /api/recall?q=...     ranked retrieval
GET  /api/explain/<id>     full explanation payload
GET  /api/graph            knowledge graph (nodes + edges + layout)
GET  /api/audit            hash-chained history
GET  /api/events           Server-Sent Events — live stream
GET  /metrics              Prometheus exposition format
POST /api/observe · /api/resolve · /api/retract · /api/decay · /api/seed
```

**Security posture:** loopback-only by default, optional bearer token (compared in
constant time), CORS off unless explicitly enabled, request body capped, strict
`nosniff` / `DENY` / `no-referrer` headers, no filesystem serving — and the
dashboard renders every value via `textContent`, so `innerHTML` and `eval` appear
nowhere in it. There is a test that enforces exactly that.

```bash
ANCHOR_API_TOKEN=secret anchor serve --host 0.0.0.0 --token secret
```

The SSE stream is produced by tailing the **audit log**, not by hooking the engine
— so it reflects committed state and works across processes.

---

## Explainability — *why* does the agent believe this?

The most valuable thing a memory system can do is **defend its own answer**.

```bash
anchor explain <belief-id>
```

Real output from a run where three trusted sources say *Berlin* and one low-trust
bot says *Munich*:

```text
──────────────────────── user lives_in Berlin ────────────────────────
confidence 0.747   lower bound 0.519   95% CI 0.398–1.000
evidence alpha=3.70 beta=1.25   status disputed   age 0.0d   stale 0.0d

evidence by source
  source       supporting  contradicting  share  obs  capped
  calendar           0.90           0.00    33%    1
  chat               0.90           0.00    33%    1
  hr_system          0.90           0.00    33%    1
  rumour_bot         0.00           0.25     0%    1

why memory holds this
  • Corroborated by 3 independent sources (calendar, chat, hr_system).
  • Reinforced 3 times without being refuted.
  • Challenged by 1 source(s) (rumour_bot); 1 contradiction(s) recorded.
  • Disputed: a conflicting claim is open and awaiting resolution.
  • Ranking uses the conservative lower bound 0.519 rather than the mean
    0.747, so thinly-supported claims cannot outrank well-corroborated ones.

if never reinforced again        audit timeline
  in days   confidence             created
        0        0.747             promoted
        7        0.731             reinforced
       30        0.676             reinforced
       90        0.565
      180        0.509
```

Note what the low-trust bot actually achieved: it nudged confidence from 0.787 to
0.747, flipped the status to `disputed`, and opened a contradiction for review. It
did **not** change the answer. That is the whole point.

Explanations are a **read-only projection** — reconstructed from the evidence
ledger and audit chain on every call, so they can never drift from what the engine
actually did. Available in Python (`mem.explain(id)`), over REST, over MCP, and in
the dashboard drawer.

---

## Knowledge graph

Beliefs aren't isolated rows — they form a graph. Anchor projects them into a
confidence-weighted graph with **contradiction edges rendered as live pulsing
links**, laid out by a deterministic seeded force-directed algorithm.

![Belief knowledge graph with a live contradiction edge](docs/assets/belief-graph.svg)

```bash
anchor graph --format mermaid        # paste straight into docs
anchor graph --format dot -o g.dot   # Graphviz
anchor graph --format svg -o g.svg   # animated, self-contained
anchor graph --format json           # feed your own UI
```

---

## Confidence is earned, and lost

A belief's confidence is the posterior mean of a Beta distribution. Corroboration
raises it, contradiction lowers it, and **silence decays it** — a fact nobody has
confirmed in six months deserves less trust than one confirmed today.

![Confidence over time — observed, reinforced, contradicted, then decayed](docs/assets/confidence-timeline.svg)

Ranking never uses the mean. It uses a **conservative lower bound** (~10th
percentile), so a claim corroborated by four sources beats a claim asserted once,
loudly. That, together with trust weighting, per-source evidence caps and the
refusal to overwrite on conflict, is what produces the poison resistance above.

---

## How it works

Every observation flows through the same pipeline — screened, weighed,
corroborated or contradicted, and recorded in a tamper-evident log:

```mermaid
flowchart TD
    A["observe(subject, predicate, object)"] --> B{"security screen<br/>injection? trust cap?"}
    B -->|clean| C["embed + match existing beliefs"]
    B -->|suspicious| P["penalize trust"]
    P --> C
    C --> D{"matches a belief?"}
    D -->|"same claim"| E["reinforce<br/>(add Bayesian evidence)"]
    D -->|"conflicting claim"| F["flag Contradiction<br/>(NEVER overwrite)"]
    D -->|"new claim"| G["create provisional belief"]
    E --> H{"enough corroboration?"}
    H -->|yes| I["promote → active"]
    H -->|no| J["stay provisional"]
    F --> K["human / strategy resolution"]
    K --> L["winner active · loser superseded"]
    I --> M["hash-chained audit event"]
    G --> M
    L --> M
    M --> N["recall: relevance × reliability × recency"]
```

Every belief moves through a well-defined lifecycle. It is **never silently
overwritten** — losers are *superseded*, not deleted:

```mermaid
stateDiagram-v2
    [*] --> provisional: observed (1 source)
    provisional --> active: corroborated\n(≥ N sources)
    provisional --> disputed: conflicting claim
    active --> disputed: conflicting claim
    disputed --> active: resolved (winner)
    disputed --> superseded: resolved (loser kept for history)
    active --> superseded: replaced by newer truth
    active --> retracted: manually retracted
    active --> active: reinforced / decayed
    superseded --> [*]
    retracted --> [*]
```

---

## Install

```bash
pip install -e .              # core engine (pure stdlib, zero deps)
pip install -e ".[cli]"       # + rich CLI tables
pip install -e ".[mcp]"       # + MCP server
pip install -e ".[langchain]" # + LangChain tool adapters
pip install -e ".[llm]"       # + OpenAI/Anthropic embeddings
pip install -e ".[dev]"       # + pytest/ruff
```

The **core engine, HTTP server, dashboard, knowledge graph and SVG renderer have
zero third-party dependencies** and run fully offline on a deterministic local
embedding — so tests, demos and benchmarks reproduce anywhere, byte for byte.

## Quickstart

```python
from anchor import Anchor

mem = Anchor()

mem.observe("user", "lives_in", "Berlin", source_id="chat", trust=0.9)
mem.observe("user", "lives_in", "Berlin", source_id="calendar", trust=0.9)  # → active

r = mem.observe("user", "lives_in", "Munich", source_id="chat", trust=0.95)
print(r.action)                       # "contradicted"  — NOT overwritten

con = mem.open_contradictions()[0]
mem.resolve_contradiction(con.id, strategy="newest", actor="human")

belief = mem.current_belief("user", "lives_in")
print(belief.object)                  # "Munich"
print(mem.explain(belief.id).summary) # why it is believed, and how strongly
print(mem.verify_audit())             # True — tamper-evident
```

## Drop it into your agent

`AnchorToolkit` emits ready-made tool schemas for OpenAI, Anthropic and LangChain,
and dispatches the calls for you:

```python
from anchor import Anchor
from anchor.adapters import AnchorToolkit

kit = AnchorToolkit(Anchor(), namespace="user-42")

resp = openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "system", "content": kit.SYSTEM_PROMPT}, *messages],
    tools=kit.openai_tools(),      # or kit.anthropic_tools() / kit.langchain_tools()
)

for call in resp.choices[0].message.tool_calls:
    result = kit.dispatch(call.function.name, call.function.arguments)
```

The toolkit returns **guidance alongside every result**, so the model knows what to
do with a conflict instead of hallucinating past it:

```json
{
  "action": "contradicted",
  "guidance": "Conflicts with an existing belief. Nothing was overwritten;
               ask the user which is true, then call anchor_resolve_contradiction."
}
```

Pass `allow_writes=False` for a read-only toolkit when you don't want the model
mutating memory.

---

## Core concepts

- **Belief** — the atomic memory unit: a `(subject, predicate, object)` claim
  with a *polarity*, a Bayesian confidence (Beta `alpha`/`beta`), a temporal
  validity window, provenance, and lifecycle status
  (`provisional → active → disputed → superseded/retracted`).
- **Reinforcement** — repeated corroboration adds *evidence* (`alpha`),
  increasing confidence; promotion to `active` can require multiple sources.
- **Confidence** — the posterior mean of a Beta distribution; ranking uses a
  conservative *lower bound* so well-corroborated memories beat merely-plausible
  ones. See [anchor/core/confidence.py](anchor/core/confidence.py).
- **Temporal reasoning** — beliefs have `valid_from`/`valid_to`; un-reinforced
  evidence **decays** toward "unknown" (a half-life model), and faded beliefs
  auto-retire.
- **Contradiction detection** — a differing claim on a *functional* predicate,
  or an opposite-polarity claim, is surfaced as a `Contradiction` — **never a
  silent overwrite**. Multi-valued predicates coexist.
- **Resolution** — strategies: `keep_new`, `keep_existing`, `highest_confidence`,
  `newest`, `reliable` (confidence-lower-bound × recency; poisoning-robust),
  `keep_both`, or `manual`. Losers are *superseded*, not deleted.
- **Explanation** — a reconstructed projection: credible interval, per-source
  attribution, decay projection and audit timeline.
  See [anchor/core/explain.py](anchor/core/explain.py).
- **Provenance & audit** — every change emits a **hash-chained, tamper-evident**
  audit event. See [anchor/core/store.py](anchor/core/store.py).
- **Security** — input hygiene + prompt-injection screening, provenance-weighted
  and **per-source-capped** evidence (anti-flooding/Sybil), and corroboration
  requirements. See [anchor/core/security.py](anchor/core/security.py).

---

## CLI

```bash
anchor demo                                   # scripted, animated showcase
anchor shell                                  # interactive REPL: type facts & questions
anchor serve --open                           # live dashboard + REST API
anchor observe user lives_in Berlin --trust 0.9
anchor recall "where does the user live?"
anchor explain <belief-id>                    # why is this believed?
anchor graph --format mermaid                 # knowledge graph
anchor contradictions
anchor resolve <id> --strategy reliable
anchor bench --json results.json              # run the benchmark
anchor render-assets                          # regenerate every animated SVG
anchor decay · anchor audit · anchor stats
```

### Interactive shell

Type facts and questions live in one session — memory persists to the database:

```text
$ anchor shell
anchor> observe user lives_in Berlin --trust 0.9 --source-id chat
anchor> observe user lives_in Berlin --trust 0.9 --source-id calendar
anchor> recall where does the user live?
anchor> observe user lives_in Munich --trust 0.95
anchor> contradictions
anchor> quit
```

Inside the shell, `recall` accepts a free-form question without quotes. Type
`help` for the full command list.

## MCP server

Expose Anchor to LangGraph / CrewAI / AutoGen / Claude Desktop / any MCP host:

```bash
anchor-mcp            # stdio transport
```

Example client config:

```json
{
  "mcpServers": {
    "anchor": {
      "command": "anchor-mcp",
      "env": { "ANCHOR_DB": "/path/to/anchor.db" }
    }
  }
}
```

Tools exposed: `anchor_observe`, `anchor_recall`, `anchor_current_belief`,
`anchor_explain`, `anchor_graph`, `anchor_list_beliefs`,
`anchor_open_contradictions`, `anchor_resolve_contradiction`, `anchor_retract`,
`anchor_apply_decay`, `anchor_audit`, `anchor_stats`. See
[anchor/mcp/server.py](anchor/mcp/server.py).

## Configuration

All dynamics are tunable via `AnchorConfig` or `ANCHOR_*` env vars — priors,
reinforcement/contradiction gains, promotion/retirement thresholds, decay and
recency half-lives, similarity thresholds, evidence caps, and the embedding
provider (`local` | `openai`). See [anchor/config.py](anchor/config.py).

## Architecture

```
anchor/
  core/
    models.py         # Belief, Provenance, Contradiction, AuditEvent
    confidence.py     # Bayesian reinforcement / decay / lower bound
    contradiction.py  # relationship taxonomy + predicate cardinality
    explain.py        # read-only explanation projection
    graph.py          # belief -> knowledge graph + force-directed layout
    embeddings.py     # local (offline) + OpenAI providers
    security.py       # injection screening + evidence caps
    store.py          # SQLite + hash-chained audit (Postgres-ready SQL)
    engine.py         # the Anchor orchestrator (framework-independent)
  server/
    api.py            # stdlib HTTP API, SSE bus, Prometheus metrics
    dashboard.py      # self-contained live operator console
  viz/                # animated SVG generation (terminal casts, charts, graphs)
  adapters/           # OpenAI / Anthropic / LangChain tool schemas
  mcp/server.py       # Model Context Protocol server
  cli/                # rich CLI + scripted demo
  eval/               # benchmark vs four baselines
  observability.py    # JSON logging + metrics
```

More detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tests

```bash
python -m pytest -q        # 107 tests
ruff check .
```

Covering confidence math, storage and **audit tamper-detection**, contradiction
logic, security defenses, temporal decay, the engine lifecycle, explanations,
graph projection, the HTTP API (including auth, body limits and XSS-safety of the
dashboard), SVG well-formedness and escaping, tool adapters, and the evaluation
harness.

## Design principles

1. **Never silently overwrite** conflicting memory — surface and resolve.
2. **Confidence is earned** — Bayesian evidence, corroboration, and trust.
3. **Memory is time-aware** — validity windows, decay, recency.
4. **Everything is auditable** — hash-chained provenance.
5. **Every answer is explainable** — reconstructed, never narrated.
6. **The core is framework-independent** — SQLite + stdlib; integrations are thin.

## License

Apache-2.0. See [LICENSE](LICENSE).

<div align="center">
<sub>Built as an exploration of what agent memory should look like when you treat
truth as a first-class concern.</sub>
</div>

# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-10-28

### Added
- **Tenant-scoped API authentication:** `ANCHOR_TENANT_TOKENS` maps namespaces to
  bearer tokens. The API derives namespace access from the authenticated tenant
  and rejects cross-tenant list, mutation and direct-object requests.
- **Durable idempotent observations:** Python, CLI and REST callers can supply an
  idempotency key. Identical retries replay the original result without adding
  evidence; changed payloads fail with `409 Conflict` over HTTP.
- **Correlation-aware evidence budgets:** observations can declare a
  `source_group`; identities within the group share an aggregate evidence cap and
  count as one independent trust domain for promotion.
- **Operations tooling:** `anchor backup` creates a consistent online SQLite copy
  and verifies it before success; `anchor doctor` checks database integrity and
  the hash-chained audit log.
- **Production packaging:** non-root Docker image, hardened Compose profile,
  health check, persistent volume and example environment configuration.
- **Supply-chain automation:** weekly Dependabot updates for Python, GitHub
  Actions and Docker; CI now builds the wheel and container; a security workflow
  runs Bandit and `pip-audit`.
- **Adversarial sweep for the benchmark** (`anchor bench --sweep`,
  `run_sweep()`): varies the scenario seed, the attacker's trust and the number
  of attacker identities, and ablates the benchmark's tuned config one knob at a
  time. `generate_scenario` takes `attacker_trust` and `attacker_identities` so
  the threat model is a parameter rather than a hardcoded assumption.

### Changed
- Package version raised to 0.3.0 and documentation expanded with threat-model,
  tenant isolation, retry, deployment, recovery and operational runbooks.
- **The headline 1.00 poison-resistance result is now reported with its limits.**
  The sweep shows the result is stable across scenario seeds, but that it rests
  entirely on `max_source_evidence` and on the attacker being a single,
  already-low-trust source. Poison resistance falls to 0.56 with two attacker
  identities and 0.00 once the attacker's trust reaches parity with legitimate
  sources. The README now states this in a "Where this breaks" section instead of
  presenting 1.00 unqualified.

### Known limitations
- **No automatic Sybil detection.** Operator-supplied source groups constrain
  known correlated identities, but Anchor does not infer those relationships.
- **Trust is an input, not an inference.** Source reliability is not learned from
  contradiction history.
- These are pinned by tests in `tests/test_eval.py`, so fixing one fails the
  suite and forces the documentation to be updated.

## [0.2.0]

### Added
- **Explainability engine** (`anchor/core/explain.py`): `Anchor.explain(belief_id)`
  reconstructs why a belief is held — Beta credible interval, per-source evidence
  attribution (with evidence-cap flags), decay projection, plain-English reasons
  and the audit timeline. Exposed via `anchor explain`, REST, and MCP.
- **Knowledge graph** (`anchor/core/graph.py`): `Anchor.graph()` projects beliefs
  into a confidence-weighted graph with contradiction edges and a deterministic
  seeded force-directed layout. Exports to Mermaid, Graphviz DOT, JSON and
  animated SVG via `anchor graph --format ...`.
- **HTTP control plane** (`anchor/server/`): dependency-free REST API on
  `http.server`, a Server-Sent Events stream (driven by tailing the audit log, so
  it works cross-process), Prometheus `/metrics`, and a self-contained live
  dashboard with a contradiction inbox and explainability drawer. Start with
  `anchor serve --open` or `anchor-serve`.
  Loopback-bound by default, optional constant-time bearer token, CORS off by
  default, request body cap, strict security headers, and a dashboard that renders
  all data through `textContent` (no `innerHTML`, no `eval`).
- **Animated SVG documentation renderer** (`anchor/viz/`): terminal casts, bar and
  timeline charts, graph and capability-matrix renderers — all generated from live
  engine runs via `anchor render-assets`, so documentation cannot drift from
  behaviour.
- **Framework adapters** (`anchor/adapters/`): `AnchorToolkit` emits OpenAI,
  Anthropic and LangChain tool schemas and dispatches calls, returning `guidance`
  alongside results so models handle contradictions instead of ignoring them.
  Supports read-only mode via `allow_writes=False`.
- Two new benchmark baselines (`recency-window`, `trust-vote`) and a new
  `stale_resistance` metric, showing that each baseline fails on a *different*
  axis. `anchor bench --json` writes machine-readable results.
- New MCP tools: `anchor_current_belief`, `anchor_explain`, `anchor_graph`.
- New store methods: `source_evidence_breakdown`, `latest_audit_seq`, `audit_after`.
- Interactive `anchor shell` REPL: type facts and free-form questions in one
  session; `recall` accepts unquoted natural-language queries.
- GitHub Actions CI running ruff + pytest across Python 3.10–3.13.
- Ruff lint configuration (`E, F, I, UP, SIM, RUF`).

### Changed
- README rewritten around the problem statement, the benchmark evidence, and the
  new explainability / dashboard / graph capabilities, illustrated with generated
  animated SVGs.
- Test suite grown from 47 to 107 tests, now covering explanations, graph
  projection, the HTTP API (auth, body limits, dashboard XSS-safety), SVG
  well-formedness and escaping, and the tool adapters.

### Fixed
- `RuntimeWarning` on `python -m anchor.cli.app` and `python -m anchor.mcp.server`
  caused by eager package imports; package `__init__` modules now import lazily.

## [0.1.0]

### Added
- Belief-memory engine: Bayesian confidence, reinforcement, temporal validity
  and decay, contradiction detection, and human-in-the-loop resolution.
- SQLite store with a hash-chained, tamper-evident audit log.
- Security defenses: prompt-injection screening and per-source evidence caps.
- MCP server exposing nine memory tools (supports SDK v1 and v2).
- CLI with a narrated multi-session demo.
- Evaluation harness benchmarking Anchor against summary and RAG baselines.

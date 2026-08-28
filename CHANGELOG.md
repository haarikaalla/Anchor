# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Interactive `anchor shell` REPL: type facts and free-form questions in one
  session; `recall` accepts unquoted natural-language queries.
- GitHub Actions CI running ruff + pytest across Python 3.10–3.13.
- README visuals: benchmark bar chart, confidence-over-time chart, terminal
  demo graphic, and Mermaid belief-lifecycle / data-flow diagrams.
- Ruff lint configuration (`E, F, I, UP, SIM, RUF`).

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

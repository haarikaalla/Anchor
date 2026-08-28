# Contributing to Anchor

Thanks for your interest in improving Anchor! This project values a small,
principled core and thorough tests.

## Development setup

```bash
python -m pip install -e ".[dev]"
```

## Before opening a pull request

Run the same checks CI runs:

```bash
python -m ruff check .     # lint
python -m pytest -q        # tests (all must pass)
```

## Guidelines

- **Keep the core dependency-free.** Anything third-party belongs behind an
  optional extra (`llm`, `mcp`, `cli`) and must degrade gracefully when absent.
- **Never silently overwrite memory.** Conflicting beliefs must surface as
  contradictions, not overwrites — this is the project's core invariant.
- **Preserve the audit chain.** Every state change must emit an audit event;
  `verify_audit` must stay intact.
- **Add tests** for new behavior and keep them deterministic and offline.
- Match the existing style; `ruff` enforces formatting and imports.

## Reporting issues

Please include the Python version, the command you ran, and the full output.

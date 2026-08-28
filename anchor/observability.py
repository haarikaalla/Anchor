"""Lightweight observability: structured logging + in-process metrics.

Kept dependency-free.  Metrics are simple counters/gauges exposed as a dict so
they can be scraped, logged, or asserted on in tests.  Logs are emitted as JSON
lines for easy ingestion.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Any


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            base.update(record.extra_fields)  # type: ignore[attr-defined]
        return json.dumps(base, default=str)


def get_logger(name: str = "anchor", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger


def log(logger: logging.Logger, level: int, msg: str, **fields: Any) -> None:
    logger.log(level, msg, extra={"extra_fields": fields})


class Metrics:
    """Thread-safe counters, gauges and simple timers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._timers: dict[str, list[float]] = defaultdict(list)

    def incr(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += value

    def gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    @contextmanager
    def timer(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            with self._lock:
                self._timers[name].append(time.perf_counter() - start)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            timers = {
                k: {
                    "count": len(v),
                    "avg_ms": round(1000 * sum(v) / len(v), 3) if v else 0.0,
                }
                for k, v in self._timers.items()
            }
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "timers": timers,
            }

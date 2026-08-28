"""Configuration for Anchor.

All tunables live here so behaviour is reproducible and testable. Values may be
overridden via environment variables (prefix ``ANCHOR_``) or by constructing an
``AnchorConfig`` directly.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    try:
        return float(v) if v is not None else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


DAY = 86_400.0


@dataclass
class AnchorConfig:
    # --- storage --------------------------------------------------------------
    db_path: str = field(default_factory=lambda: _env_str("ANCHOR_DB", "anchor.db"))

    # --- confidence dynamics --------------------------------------------------
    # Beta prior. Larger => more evidence needed to move confidence.
    prior_alpha: float = field(default_factory=lambda: _env_float("ANCHOR_PRIOR_ALPHA", 1.0))
    prior_beta: float = field(default_factory=lambda: _env_float("ANCHOR_PRIOR_BETA", 1.0))
    # Evidence added per corroborating observation (scaled by source trust).
    reinforce_gain: float = field(default_factory=lambda: _env_float("ANCHOR_REINFORCE_GAIN", 1.0))
    # Evidence added to ``beta`` per contradicting observation.
    contradict_gain: float = field(
        default_factory=lambda: _env_float("ANCHOR_CONTRADICT_GAIN", 1.0)
    )
    # Confidence threshold to promote PROVISIONAL -> ACTIVE.
    promote_threshold: float = field(default_factory=lambda: _env_float("ANCHOR_PROMOTE", 0.66))
    # Confidence at/under which an ACTIVE belief is auto-retired.
    retire_threshold: float = field(default_factory=lambda: _env_float("ANCHOR_RETIRE", 0.12))

    # --- temporal decay -------------------------------------------------------
    # Half-life (seconds) of *evidence* when a belief is not reinforced. Older
    # evidence is discounted toward the prior, modelling forgetting.
    decay_half_life: float = field(default_factory=lambda: _env_float("ANCHOR_HALFLIFE", 30 * DAY))
    # Recency half-life used when ranking retrieval results.
    recency_half_life: float = field(default_factory=lambda: _env_float("ANCHOR_RECENCY", 14 * DAY))

    # --- contradiction / similarity ------------------------------------------
    # Cosine similarity above which two same-slot beliefs are "the same claim".
    dup_similarity: float = field(default_factory=lambda: _env_float("ANCHOR_DUP_SIM", 0.92))
    # Cosine similarity above which differing claims are semantically related
    # enough to be treated as a contradiction candidate.
    contradiction_similarity: float = field(
        default_factory=lambda: _env_float("ANCHOR_CON_SIM", 0.80)
    )

    # --- embeddings -----------------------------------------------------------
    # "local" (deterministic, offline), "openai", or "anthropic".
    embedding_provider: str = field(
        default_factory=lambda: _env_str("ANCHOR_EMBED_PROVIDER", "local")
    )
    embedding_model: str = field(
        default_factory=lambda: _env_str("ANCHOR_EMBED_MODEL", "text-embedding-3-small")
    )
    embedding_dim: int = field(default_factory=lambda: _env_int("ANCHOR_EMBED_DIM", 256))

    # --- security / poisoning defense ----------------------------------------
    # Max evidence a *single source* may contribute to one belief (anti-flooding).
    max_source_evidence: float = field(
        default_factory=lambda: _env_float("ANCHOR_MAX_SRC_EVIDENCE", 5.0)
    )
    # Minimum distinct trusted sources before a belief may become ACTIVE.
    min_sources_for_active: int = field(
        default_factory=lambda: _env_int("ANCHOR_MIN_SOURCES", 1)
    )
    # Reject statements longer than this many characters (injection surface).
    max_statement_chars: int = field(
        default_factory=lambda: _env_int("ANCHOR_MAX_STMT", 4000)
    )
    # Default trust for sources not explicitly scored.
    default_source_trust: float = field(
        default_factory=lambda: _env_float("ANCHOR_DEFAULT_TRUST", 0.7)
    )

    # --- observability --------------------------------------------------------
    log_level: str = field(default_factory=lambda: _env_str("ANCHOR_LOG_LEVEL", "INFO"))
    metrics_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

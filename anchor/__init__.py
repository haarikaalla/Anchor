"""Anchor: persistent belief-memory infrastructure for AI agents.

The public surface intentionally re-exports the high-level engine and the core
data model so that integrations only need ``from anchor import Anchor``.
"""

from .core.engine import Anchor
from .core.models import (
    AuditEvent,
    Belief,
    BeliefStatus,
    Contradiction,
    ContradictionKind,
    ContradictionStatus,
    Polarity,
    Provenance,
    SourceType,
)
from .config import AnchorConfig

__all__ = [
    "Anchor",
    "AnchorConfig",
    "Belief",
    "BeliefStatus",
    "Polarity",
    "Provenance",
    "SourceType",
    "Contradiction",
    "ContradictionKind",
    "ContradictionStatus",
    "AuditEvent",
]

__version__ = "0.1.0"

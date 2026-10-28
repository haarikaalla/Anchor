"""Anchor: persistent belief-memory infrastructure for AI agents.

The public surface intentionally re-exports the high-level engine and the core
data model so that integrations only need ``from anchor import Anchor``.
"""

from .config import AnchorConfig
from .core.engine import Anchor
from .core.explain import Explanation, SourceContribution
from .core.graph import BeliefGraph
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

__all__ = [
    "Anchor",
    "AnchorConfig",
    "AuditEvent",
    "Belief",
    "BeliefGraph",
    "BeliefStatus",
    "Contradiction",
    "ContradictionKind",
    "ContradictionStatus",
    "Explanation",
    "Polarity",
    "Provenance",
    "SourceContribution",
    "SourceType",
]

__version__ = "0.3.0"

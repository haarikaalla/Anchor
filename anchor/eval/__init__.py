"""Evaluation harness comparing Anchor to naive summary/RAG memory."""

from typing import TYPE_CHECKING

from .baselines import AnchorMemory, Fact, RAGMemory, SummaryMemory

if TYPE_CHECKING:
    from .benchmark import Scenario, generate_scenario, run_benchmark, run_sweep

__all__ = [
    "AnchorMemory",
    "Fact",
    "RAGMemory",
    "Scenario",
    "SummaryMemory",
    "generate_scenario",
    "run_benchmark",
    "run_sweep",
]


def __getattr__(name: str):
    # Keep the public API while allowing `python -m anchor.eval.benchmark` to
    # execute the module once, without runpy's duplicate-import warning.
    if name in {"Scenario", "generate_scenario", "run_benchmark", "run_sweep"}:
        from . import benchmark

        return getattr(benchmark, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Anchor command-line interface."""

from typing import TYPE_CHECKING

__all__ = ["main"]

if TYPE_CHECKING:
    from .app import main


def __getattr__(name: str):
    # Lazily import from .app so that `python -m anchor.cli.app` does not import
    # the module twice (once via this package __init__, once as __main__), which
    # triggers a RuntimeWarning.
    if name in __all__:
        from . import app

        return getattr(app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

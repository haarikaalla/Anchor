"""Anchor MCP integration."""

from typing import TYPE_CHECKING

__all__ = ["build_server", "main"]

if TYPE_CHECKING:
    from .server import build_server, main


def __getattr__(name: str):
    # Lazily import from .server so that `python -m anchor.mcp.server` does not
    # import the module twice (once via this package __init__, once as __main__),
    # which triggers a RuntimeWarning.
    if name in __all__:
        from . import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

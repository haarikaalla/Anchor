"""Minimal SVG construction helpers.

Anchor generates every visual asset in its documentation from a *real* engine
run — no screenshots, no external tooling, no build step. These helpers are the
thin layer that keeps that generator readable.
"""

from __future__ import annotations

_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}


def esc(text: object) -> str:
    """Escape a value for safe inclusion in SVG text or attributes."""
    out = str(text)
    for char, rep in _ESCAPES.items():
        out = out.replace(char, rep)
    return out


def attrs(**kwargs: object) -> str:
    """Render keyword arguments as SVG attributes (``_`` becomes ``-``)."""
    parts = []
    for key, value in kwargs.items():
        if value is None:
            continue
        parts.append(f'{key.rstrip("_").replace("_", "-")}="{esc(value)}"')
    return " ".join(parts)


def document(width: float, height: float, body: str, *, style: str = "",
             defs: str = "", title: str = "", desc: str = "") -> str:
    """Wrap ``body`` in a complete, standalone SVG document.

    ``title``/``desc`` are emitted as accessibility metadata, which also gives
    the image a meaningful tooltip and screen-reader description on GitHub.
    """
    head = ""
    if title:
        head += f"<title>{esc(title)}</title>"
    if desc:
        head += f"<desc>{esc(desc)}</desc>"
    style_block = f"<style>{style}</style>" if style else ""
    defs_block = f"<defs>{defs}</defs>" if defs else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" height="{height:g}" '
        f'viewBox="0 0 {width:g} {height:g}" role="img" font-family="ui-sans-serif, '
        f'-apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif">'
        f"{head}{defs_block}{style_block}{body}</svg>\n"
    )


def pct(value: float) -> str:
    """Format a keyframe offset, trimming trailing zeros."""
    return f"{max(0.0, min(100.0, value)):.4f}".rstrip("0").rstrip(".") or "0"

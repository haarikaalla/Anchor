"""The animated hero banner used at the top of the README.

A single looping image that tells the whole story: the same untrusted claim
arrives at a conventional memory and at Anchor, and the two diverge. The left
panel silently overwrites and is wrong with no record; the right panel keeps
both claims, flags the conflict and stays auditable.

Confidence figures are supplied by :mod:`anchor.viz.render` from a real engine
run, so this banner cannot advertise behaviour the engine does not have.
"""

from __future__ import annotations

from .charts import BG, HERO, INK, MUTED
from .svg import document, esc, pct

DANGER = "#f87171"
WARN = "#fbbf24"
PANEL = "rgba(148,175,235,0.05)"
STROKE = "rgba(140,170,230,0.18)"


class _Timeline:
    """Builds synchronised CSS keyframes for a single looping storyboard.

    ``animation-delay`` only applies to the first iteration, so a looping
    storyboard cannot be built from delays. Instead every element gets its own
    keyframe whose percentages are computed against one shared loop length,
    which keeps the panels in lockstep on every repeat.
    """

    def __init__(self, total: float) -> None:
        self.total = total
        self.css: list[str] = []
        self._n = 0

    def _next(self) -> str:
        self._n += 1
        return f"a{self._n}"

    def reveal(self, start: float, *, dur: float = 0.45, rise: float = 10.0) -> str:
        """Fade + lift an element in at ``start`` seconds, then hold it."""
        cls = self._next()
        a, b = self._span(start, dur)
        self.css.append(
            f".{cls}{{opacity:0;animation:{cls} {self.total}s linear infinite}}"
            f"@keyframes {cls}{{0%,{a}%{{opacity:0;transform:translateY({rise:g}px)}}"
            f"{b}%,100%{{opacity:1;transform:translateY(0)}}}}"
        )
        return cls

    def strike(self, start: float, *, dur: float = 0.4) -> str:
        """Wipe a strike-through line across its element from the left."""
        cls = self._next()
        a, b = self._span(start, dur)
        self.css.append(
            f".{cls}{{transform-box:fill-box;transform-origin:left center;"
            f"animation:{cls} {self.total}s linear infinite}}"
            f"@keyframes {cls}{{0%,{a}%{{transform:scaleX(0)}}"
            f"{b}%,100%{{transform:scaleX(1)}}}}"
        )
        return cls

    def bar(self, start: float, frm: float, to: float, *, dur: float = 0.5) -> str:
        """Animate a confidence bar from one fraction of its width to another."""
        cls = self._next()
        a, b = self._span(start, dur)
        self.css.append(
            f".{cls}{{transform-box:fill-box;transform-origin:left center;"
            f"animation:{cls} {self.total}s linear infinite}}"
            f"@keyframes {cls}{{0%,{a}%{{transform:scaleX({frm:.4f})}}"
            f"{b}%,100%{{transform:scaleX({to:.4f})}}}}"
        )
        return cls

    def _span(self, start: float, dur: float) -> tuple[str, str]:
        return (pct(start / self.total * 100),
                pct((start + dur) / self.total * 100))


def _text(x: float, y: float, body: str, *, size: float = 13, fill: str = INK,
          weight: str = "", mono: bool = False, anchor: str = "start",
          cls: str = "", opacity: str = "") -> str:
    bits = [f'x="{x:g}"', f'y="{y:g}"', f'font-size="{size:g}"', f'fill="{fill}"']
    if weight:
        bits.append(f'font-weight="{weight}"')
    if mono:
        bits.append('font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"')
    if anchor != "start":
        bits.append(f'text-anchor="{anchor}"')
    if cls:
        bits.append(f'class="{cls}"')
    if opacity:
        bits.append(f'opacity="{opacity}"')
    return f'<text {" ".join(bits)}>{esc(body)}</text>'


def _card(x: float, y: float, w: float, h: float, *, stroke: str = STROKE,
          fill: str = PANEL, radius: float = 10) -> str:
    return (f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{radius:g}" '
            f'fill="{fill}" stroke="{stroke}"/>')


def hero_banner(*, conf_before: float, conf_after: float, kind: str,
                sources: int, width: int = 920, height: int = 430,
                total: float = 11.0) -> str:
    """Render the looping side-by-side hero banner.

    ``conf_before``/``conf_after`` are the real confidences either side of the
    poisoning attempt, and ``kind`` is the contradiction kind the engine
    actually raised.
    """
    tl = _Timeline(total)
    body: list[str] = [f'<rect width="{width}" height="{height}" fill="{BG}"/>']

    # ---------------------------------------------------------------- heading
    body.append(_text(28, 40, "Anchor", size=25, fill=HERO, weight="700"))
    body.append(_text(124, 40, "belief memory for AI agents", size=15, fill=INK))
    body.append(_text(28, 62, "The same untrusted claim arrives at both. Only one of them "
                              "is still telling you the truth afterwards.",
                      size=13, fill=MUTED))

    py, ph = 84, 322
    panels = (
        (24.0, DANGER, "Conventional memory", "overwrite on update"),
        (468.0, HERO, "Anchor", f"evidence · provenance · {sources} corroborating sources"),
    )

    for px, accent, name, note in panels:
        anchor_side = accent == HERO
        pw = 428.0
        body.append(_card(px, py, pw, ph, radius=14))
        body.append(f'<rect x="{px:g}" y="{py:g}" width="{pw:g}" height="3" rx="1.5" '
                    f'fill="{accent}" opacity="0.85"/>')

        head = tl.reveal(0.25)
        body.append(f'<g class="{head}">')
        body.append(_text(px + 20, py + 34, name, size=16, fill=accent, weight="700"))
        body.append(_text(px + pw - 20, py + 34, note, size=12, fill=MUTED, anchor="end"))
        body.append(f'<line x1="{px + 18:g}" y1="{py + 48:g}" x2="{px + pw - 18:g}" '
                    f'y2="{py + 48:g}" stroke="{STROKE}"/>')
        body.append("</g>")

        # -- 1. the established fact ---------------------------------------
        g = tl.reveal(0.9)
        ry = py + 64
        body.append(f'<g class="{g}">')
        body.append(_card(px + 18, ry, pw - 36, 62))
        body.append(_text(px + 34, ry + 24, "user · lives_in", size=11, fill=MUTED, mono=True))
        body.append(_text(px + 34, ry + 46, "Berlin", size=18, fill=INK, weight="600"))
        if anchor_side:
            bw = 132.0
            body.append(f'<rect x="{px + 250:g}" y="{ry + 34:g}" width="{bw:g}" height="7" '
                        f'rx="3.5" fill="rgba(148,175,235,0.16)"/>')
            grow = tl.bar(0.9, 0.0, conf_before, dur=0.7)
            body.append(f'<rect class="{grow}" x="{px + 250:g}" y="{ry + 34:g}" '
                        f'width="{bw:g}" height="7" rx="3.5" fill="{HERO}"/>')
            body.append(_text(px + pw - 34, ry + 26, f"confidence {conf_before:.2f}",
                              size=11, fill=HERO, mono=True, anchor="end"))
        else:
            body.append(_text(px + pw - 34, ry + 40, "no confidence · no sources",
                              size=11, fill=MUTED, mono=True, anchor="end"))
        body.append("</g>")

        # -- 2. the untrusted claim ----------------------------------------
        g = tl.reveal(2.4)
        ry = py + 138
        body.append(f'<g class="{g}">')
        body.append(_card(px + 18, ry, pw - 36, 46, stroke="rgba(251,191,36,0.35)",
                          fill="rgba(251,191,36,0.06)"))
        body.append(_text(px + 34, ry + 28, "scraper_bot claims  \u201cMunich\u201d",
                          size=13, fill=WARN, mono=True))
        body.append(_text(px + pw - 34, ry + 28, "trust 0.25", size=11, fill=WARN,
                          mono=True, anchor="end"))
        body.append("</g>")

        # -- 3. what each system does about it -----------------------------
        ry = py + 200
        if anchor_side:
            g = tl.reveal(4.1)
            body.append(f'<g class="{g}">')
            body.append(_card(px + 18, ry, pw - 36, 58, stroke="rgba(94,234,212,0.35)",
                              fill="rgba(94,234,212,0.06)"))
            body.append(f'<circle class="pulse" cx="{px + 40:g}" cy="{ry + 24:g}" r="5" '
                        f'fill="{HERO}"/>')
            body.append(_text(px + 56, ry + 28, "CONTRADICTION raised", size=13,
                              fill=HERO, weight="600", mono=True))
            body.append(_text(px + pw - 34, ry + 28, esc(kind), size=11, fill=MUTED,
                              mono=True, anchor="end"))
            body.append(_text(px + 34, ry + 48,
                              f"Berlin kept · confidence {conf_before:.2f} \u2192 "
                              f"{conf_after:.2f} · queued for review",
                              size=11, fill=MUTED, mono=True))
            body.append("</g>")
        else:
            g = tl.reveal(4.1)
            body.append(f'<g class="{g}">')
            body.append(_card(px + 18, ry, pw - 36, 58, stroke="rgba(248,113,113,0.35)",
                              fill="rgba(248,113,113,0.06)"))
            body.append(_text(px + 34, ry + 28, "Berlin", size=15, fill=MUTED,
                              weight="600"))
            wipe = tl.strike(4.35)
            body.append(f'<rect class="{wipe}" x="{px + 32:g}" y="{ry + 22:g}" '
                        f'width="46" height="2" fill="{DANGER}"/>')
            body.append(_text(px + 92, ry + 28, "\u2192", size=15, fill=MUTED))
            over = tl.reveal(4.75, rise=0)
            body.append(f'<text class="{over}" x="{px + 118:g}" y="{ry + 28:g}" '
                        f'font-size="15" font-weight="600" fill="{DANGER}">Munich</text>')
            body.append(_text(px + pw - 34, ry + 28, "OVERWRITTEN", size=11,
                              fill=DANGER, mono=True, anchor="end"))
            body.append(_text(px + 34, ry + 48, "previous value gone · no conflict · "
                                                "no record it ever changed",
                              size=11, fill=MUTED, mono=True))
            body.append("</g>")

        # -- 4. the verdict -------------------------------------------------
        g = tl.reveal(6.4)
        ry = py + 276
        body.append(f'<g class="{g}">')
        if anchor_side:
            body.append(_text(px + 20, ry + 12, "\u2713", size=17, fill=HERO, weight="700"))
            body.append(_text(px + 42, ry + 12, "Still correct, and it can prove why.",
                              size=13, fill=INK, weight="600"))
            body.append(_text(px + 42, ry + 30, "hash-chained audit \u00b7 explainable "
                                                "\u00b7 human resolves the conflict",
                              size=11, fill=MUTED))
        else:
            body.append(_text(px + 20, ry + 12, "\u2715", size=17, fill=DANGER, weight="700"))
            body.append(_text(px + 42, ry + 12, "Silently wrong. Nothing to detect.",
                              size=13, fill=INK, weight="600"))
            body.append(_text(px + 42, ry + 30, "one low-trust source rewrote the "
                                                "user\u2019s reality",
                              size=11, fill=MUTED))
        body.append("</g>")

    style = (
        "text{dominant-baseline:middle}"
        "@keyframes pulse{0%,100%{opacity:1;r:5}50%{opacity:.35;r:7}}"
        ".pulse{animation:pulse 1.5s ease-in-out infinite}"
        "@media (prefers-reduced-motion:reduce){*{animation:none!important;opacity:1!important}}"
        + "".join(tl.css)
    )
    return document(
        width, height, "".join(body), style=style,
        title="Anchor — conventional memory overwrites and is silently wrong; "
              "Anchor keeps both claims and flags the contradiction",
        desc=("Side-by-side comparison. A low-trust scraper claims the user lives in "
              "Munich. Conventional memory overwrites Berlin and is silently wrong "
              "with no record. Anchor keeps Berlin, lowers its confidence from "
              f"{conf_before:.2f} to {conf_after:.2f}, raises a contradiction for "
              "review, and retains a hash-chained audit trail."),
    )

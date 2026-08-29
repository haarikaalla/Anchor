"""Animated chart rendering for the generated documentation assets.

All charts are standalone animated SVGs built with CSS keyframes: bars grow from
the baseline, lines draw themselves stroke-by-stroke, and conflict edges pulse.
They are produced from live engine/benchmark runs by :mod:`anchor.viz.render`,
so the documentation cannot drift away from the code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.graph import BeliefGraph
from .svg import document, esc, pct

INK = "#e8edf9"
MUTED = "#8ea0c4"
GRID = "rgba(120,145,200,0.16)"
BG = "#0b1020"
SERIES_COLORS = ["#64748b", "#38bdf8", "#a78bfa", "#5eead4", "#fbbf24"]
HERO = "#5eead4"


@dataclass
class Series:
    name: str
    values: list[float]
    color: str = ""
    highlight: bool = False


def grouped_bars(title: str, subtitle: str, categories: list[str],
                 series: list[Series], *, width: int = 900, height: int = 420,
                 value_fmt: str = "{:.2f}", y_max: float = 1.0) -> str:
    """Animated grouped bar chart (bars grow from the baseline on every loop)."""
    pad_l, pad_r, pad_t, pad_b = 56, 26, 78, 64
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    n_groups = max(1, len(categories))
    group_w = plot_w / n_groups
    inner = group_w * 0.74
    bar_w = inner / max(1, len(series))

    css = [
        "@keyframes grow{from{transform:scaleY(0)}to{transform:scaleY(1)}}",
        "@keyframes fade{from{opacity:0}to{opacity:1}}",
        ".bar{transform-box:fill-box;transform-origin:bottom;"
        "animation:grow 1.15s cubic-bezier(.34,1.2,.5,1) both}",
        ".vlab{animation:fade .5s ease both}",
        f".ax{{fill:{MUTED};font-size:12px}}",
        f".cat{{fill:{INK};font-size:13px;font-weight:550}}",
    ]
    body = [
        f'<rect width="{width}" height="{height}" rx="14" fill="{BG}"/>',
        f'<text x="28" y="34" font-size="17" font-weight="650" fill="{INK}">'
        f"{esc(title)}</text>",
        f'<text x="28" y="55" font-size="12.5" fill="{MUTED}">{esc(subtitle)}</text>',
    ]

    for i in range(5):
        frac = i / 4
        y = pad_t + plot_h * (1 - frac)
        body.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        body.append(
            f'<text class="ax" x="{pad_l - 10}" y="{y + 4:.1f}" text-anchor="end">'
            f"{frac * y_max:.2f}</text>"
        )

    # Legend
    lx = pad_l
    for idx, s in enumerate(series):
        color = s.color or SERIES_COLORS[idx % len(SERIES_COLORS)]
        body.append(f'<rect x="{lx}" y="{pad_t - 30}" width="11" height="11" rx="3" '
                    f'fill="{color}"/>')
        body.append(f'<text class="ax" x="{lx + 17}" y="{pad_t - 20}" '
                    f'font-weight="{"650" if s.highlight else "400"}" '
                    f'fill="{INK if s.highlight else MUTED}">{esc(s.name)}</text>')
        lx += 26 + 7.4 * len(s.name)

    for gi, category in enumerate(categories):
        gx = pad_l + gi * group_w + (group_w - inner) / 2
        for si, s in enumerate(series):
            color = s.color or SERIES_COLORS[si % len(SERIES_COLORS)]
            value = s.values[gi] if gi < len(s.values) else 0.0
            bh = max(1.5, plot_h * (value / y_max if y_max else 0))
            x = gx + si * bar_w
            y = pad_t + plot_h - bh
            delay = 0.09 * (gi * len(series) + si)
            outline = f' stroke="{HERO}" stroke-width="1.4"' if s.highlight else ""
            body.append(
                f'<rect class="bar" x="{x + 2:.1f}" y="{y:.1f}" '
                f'width="{bar_w - 4:.1f}" height="{bh:.1f}" rx="4" fill="{color}" '
                f'style="animation-delay:{delay:.2f}s"{outline}/>'
            )
            body.append(
                f'<text class="vlab" x="{x + bar_w / 2:.1f}" y="{y - 7:.1f}" '
                f'text-anchor="middle" font-size="11.5" '
                f'fill="{INK if s.highlight else MUTED}" '
                f'font-weight="{"650" if s.highlight else "400"}" '
                f'style="animation-delay:{delay + 0.9:.2f}s">'
                f"{esc(value_fmt.format(value))}</text>"
            )
        body.append(
            f'<text class="cat" x="{pad_l + gi * group_w + group_w / 2:.1f}" '
            f'y="{height - pad_b + 26}" text-anchor="middle">{esc(category)}</text>'
        )

    body.append(
        f'<line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{width - pad_r}" '
        f'y2="{pad_t + plot_h}" stroke="{MUTED}" stroke-width="1.2" opacity=".6"/>'
    )
    return document(width, height, "".join(body), style="".join(css),
                    title=title, desc=subtitle)


def timeline_chart(title: str, subtitle: str, points: list[tuple[float, float]],
                   annotations: list[tuple[float, str, str]] | None = None,
                   *, width: int = 900, height: int = 380,
                   x_label: str = "", y_label: str = "confidence") -> str:
    """Animated line chart: the path draws itself, then markers pop in.

    ``annotations`` are ``(x, label, colour)`` triples marking events such as a
    reinforcement, a contradiction or a decay sweep.
    """
    pad_l, pad_r, pad_t, pad_b = 58, 30, 76, 56
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    xs = [p[0] for p in points] or [0.0, 1.0]
    x_min, x_max = min(xs), max(xs)
    span = (x_max - x_min) or 1.0

    def px(x: float) -> float:
        return pad_l + plot_w * (x - x_min) / span

    def py(y: float) -> float:
        return pad_t + plot_h * (1 - max(0.0, min(1.0, y)))

    path = " ".join(
        ("M" if i == 0 else "L") + f"{px(x):.1f},{py(y):.1f}"
        for i, (x, y) in enumerate(points)
    )
    length = sum(
        math.hypot(px(points[i][0]) - px(points[i - 1][0]),
                   py(points[i][1]) - py(points[i - 1][1]))
        for i in range(1, len(points))
    ) or 1.0

    css = [
        f"@keyframes draw{{from{{stroke-dashoffset:{length:.0f}}}"
        f"to{{stroke-dashoffset:0}}}}",
        "@keyframes pop{from{opacity:0;transform:scale(.2)}to{opacity:1;transform:scale(1)}}",
        "@keyframes fade{from{opacity:0}to{opacity:1}}",
        f".line{{fill:none;stroke:url(#g1);stroke-width:3;stroke-linecap:round;"
        f"stroke-linejoin:round;stroke-dasharray:{length:.0f};"
        f"animation:draw 2.4s ease-in-out both}}",
        ".dot{transform-box:fill-box;transform-origin:center;"
        "animation:pop .4s cubic-bezier(.34,1.4,.5,1) both}",
        ".ann{animation:fade .5s ease both}",
        f".ax{{fill:{MUTED};font-size:12px}}",
    ]
    defs = (
        '<linearGradient id="g1" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0" stop-color="#a78bfa"/><stop offset="1" stop-color="#5eead4"/>'
        "</linearGradient>"
        '<linearGradient id="g2" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#5eead4" stop-opacity=".22"/>'
        '<stop offset="1" stop-color="#5eead4" stop-opacity="0"/>'
        "</linearGradient>"
    )
    body = [
        f'<rect width="{width}" height="{height}" rx="14" fill="{BG}"/>',
        f'<text x="28" y="34" font-size="17" font-weight="650" fill="{INK}">'
        f"{esc(title)}</text>",
        f'<text x="28" y="55" font-size="12.5" fill="{MUTED}">{esc(subtitle)}</text>',
    ]
    for i in range(5):
        frac = i / 4
        y = pad_t + plot_h * (1 - frac)
        body.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                    f'y2="{y:.1f}" stroke="{GRID}"/>')
        body.append(f'<text class="ax" x="{pad_l - 10}" y="{y + 4:.1f}" '
                    f'text-anchor="end">{frac:.2f}</text>')

    for x, label, color in (annotations or []):
        body.append(
            f'<line class="ann" x1="{px(x):.1f}" y1="{pad_t}" x2="{px(x):.1f}" '
            f'y2="{pad_t + plot_h}" stroke="{color}" stroke-width="1.3" '
            f'stroke-dasharray="4 4" opacity=".75" style="animation-delay:1.6s"/>'
        )
        body.append(
            f'<text class="ann ax" x="{px(x) + 6:.1f}" y="{pad_t - 8}" fill="{color}" '
            f'font-size="11" style="animation-delay:1.7s">{esc(label)}</text>'
        )

    area = (path + f" L{px(points[-1][0]):.1f},{pad_t + plot_h:.1f} "
                   f"L{px(points[0][0]):.1f},{pad_t + plot_h:.1f} Z") if points else ""
    body.append(f'<path class="ann" d="{area}" fill="url(#g2)" '
                f'style="animation-delay:2.2s"/>')
    body.append(f'<path class="line" d="{path}"/>')

    for i, (x, y) in enumerate(points):
        if len(points) > 26 and i % 2:
            continue
        body.append(
            f'<circle class="dot" cx="{px(x):.1f}" cy="{py(y):.1f}" r="3.6" '
            f'fill="{HERO}" style="animation-delay:{2.2 + i * 0.03:.2f}s"/>'
        )

    body.append(f'<text class="ax" x="{pad_l}" y="{height - 18}">{esc(x_label)}</text>')
    body.append(
        f'<text class="ax" transform="translate(18,{pad_t + plot_h / 2}) rotate(-90)" '
        f'text-anchor="middle">{esc(y_label)}</text>'
    )
    return document(width, height, "".join(body), style="".join(css) + "",
                    defs=defs, title=title, desc=subtitle)


def graph_chart(graph: BeliefGraph, title: str, subtitle: str, *,
                width: int = 900, height: int = 520) -> str:
    """Render a laid-out :class:`BeliefGraph` as an animated SVG.

    Belief edges fade in weighted by confidence; open contradictions are drawn as
    pulsing dashed edges so the conflict is the first thing your eye lands on.
    """
    graph.layout(width=width - 40, height=height - 110)
    index = graph.node_index()
    css = [
        "@keyframes fade{from{opacity:0}to{opacity:1}}",
        "@keyframes pop{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:scale(1)}}",
        "@keyframes flow{to{stroke-dashoffset:-24}}",
        "@keyframes pulse{0%,100%{opacity:.55}50%{opacity:1}}",
        ".e{animation:fade .8s ease both}",
        ".c{stroke:#fb7185;stroke-dasharray:6 5;animation:fade .8s ease both,"
        "flow 1.1s linear infinite,pulse 1.6s ease-in-out infinite}",
        ".n{transform-box:fill-box;transform-origin:center;"
        "animation:pop .55s cubic-bezier(.34,1.4,.5,1) both}",
        f".nt{{fill:{INK};font-size:11.5px;text-anchor:middle}}",
        f".et{{fill:{MUTED};font-size:10px;text-anchor:middle}}",
    ]
    body = [
        f'<rect width="{width}" height="{height}" rx="14" fill="{BG}"/>',
        f'<text x="28" y="34" font-size="17" font-weight="650" fill="{INK}">'
        f"{esc(title)}</text>",
        f'<text x="28" y="55" font-size="12.5" fill="{MUTED}">{esc(subtitle)}</text>',
    ]
    off = 72.0
    for i, edge in enumerate(graph.edges):
        a, b = index.get(edge.source), index.get(edge.target)
        if a is None or b is None:
            continue
        cls = "c" if edge.kind == "contradiction" else "e"
        stroke = "#fb7185" if edge.kind == "contradiction" else "rgba(148,170,220,.45)"
        body.append(
            f'<line class="{cls}" x1="{a.x + 20:.1f}" y1="{a.y + off:.1f}" '
            f'x2="{b.x + 20:.1f}" y2="{b.y + off:.1f}" stroke="{stroke}" '
            f'stroke-width="{0.9 + 2.6 * edge.confidence:.2f}" '
            f'style="animation-delay:{i * 0.05:.2f}s"/>'
        )
        body.append(
            f'<text class="et e" x="{(a.x + b.x) / 2 + 20:.1f}" '
            f'y="{(a.y + b.y) / 2 + off - 5:.1f}" '
            f'style="animation-delay:{i * 0.05 + 0.3:.2f}s">{esc(edge.label)}</text>'
        )
    for i, node in enumerate(graph.nodes):
        r = 16 + min(12, 3.0 * node.weight)
        color = "#fbbf24" if node.kind == "source" else "#a78bfa"
        body.append(
            f'<circle class="n" cx="{node.x + 20:.1f}" cy="{node.y + off:.1f}" '
            f'r="{r:.1f}" fill="#131a2b" stroke="{color}" stroke-width="1.8" '
            f'style="animation-delay:{i * 0.06:.2f}s"/>'
        )
        label = node.label if len(node.label) <= 12 else node.label[:11] + "…"
        body.append(
            f'<text class="nt e" x="{node.x + 20:.1f}" y="{node.y + off + 4:.1f}" '
            f'style="animation-delay:{i * 0.06 + 0.25:.2f}s">{esc(label)}</text>'
        )
    return document(width, height, "".join(body), style="".join(css),
                    title=title, desc=subtitle)


def comparison_matrix(title: str, subtitle: str, rows: list[str],
                      columns: list[str], cells: list[list[str]], *,
                      width: int = 900, highlight_col: int = -1) -> str:
    """Animated capability matrix (used for the Summary vs RAG vs Anchor table)."""
    pad_t, row_h, head_h = 88, 34, 30
    label_w = 300
    col_w = (width - label_w - 40) / max(1, len(columns))
    height = pad_t + head_h + len(rows) * row_h + 26

    marks = {"yes": ("#34d399", "✓"), "no": ("#fb7185", "✕"),
             "partial": ("#fbbf24", "~")}
    css = [
        "@keyframes fade{from{opacity:0;transform:translateY(6px)}"
        "to{opacity:1;transform:translateY(0)}}",
        "@keyframes pop{from{opacity:0;transform:scale(.3)}to{opacity:1;transform:scale(1)}}",
        ".r{animation:fade .5s ease both}",
        ".m{transform-box:fill-box;transform-origin:center;"
        "animation:pop .45s cubic-bezier(.34,1.5,.5,1) both}",
        f".lb{{fill:{INK};font-size:13px}}",
        f".hd{{fill:{MUTED};font-size:12px;text-anchor:middle;"
        f"text-transform:uppercase;letter-spacing:.08em}}",
    ]
    body = [
        f'<rect width="{width}" height="{height:.0f}" rx="14" fill="{BG}"/>',
        f'<text x="28" y="36" font-size="17" font-weight="650" fill="{INK}">'
        f"{esc(title)}</text>",
        f'<text x="28" y="57" font-size="12.5" fill="{MUTED}">{esc(subtitle)}</text>',
    ]
    if 0 <= highlight_col < len(columns):
        hx = label_w + highlight_col * col_w
        body.append(
            f'<rect x="{hx:.0f}" y="{pad_t - 12:.0f}" width="{col_w:.0f}" '
            f'height="{head_h + len(rows) * row_h + 16:.0f}" rx="10" '
            f'fill="rgba(94,234,212,.07)" stroke="rgba(94,234,212,.3)"/>'
        )
    for ci, col in enumerate(columns):
        cx = label_w + ci * col_w + col_w / 2
        weight = "650" if ci == highlight_col else "400"
        fill = HERO if ci == highlight_col else MUTED
        body.append(f'<text class="hd" x="{cx:.0f}" y="{pad_t + 12}" '
                    f'font-weight="{weight}" fill="{fill}">{esc(col)}</text>')
    for ri, row in enumerate(rows):
        y = pad_t + head_h + ri * row_h + row_h / 2
        delay = ri * 0.07
        if ri % 2 == 0:
            body.append(f'<rect x="24" y="{y - row_h / 2:.1f}" width="{width - 48}" '
                        f'height="{row_h}" rx="7" fill="rgba(120,145,200,.045)"/>')
        body.append(f'<text class="lb r" x="34" y="{y + 4:.1f}" '
                    f'style="animation-delay:{delay:.2f}s">{esc(row)}</text>')
        for ci in range(len(columns)):
            value = cells[ri][ci] if ci < len(cells[ri]) else "no"
            color, glyph = marks.get(value, (MUTED, "–"))
            cx = label_w + ci * col_w + col_w / 2
            body.append(
                f'<text class="m" x="{cx:.0f}" y="{y + 6:.1f}" text-anchor="middle" '
                f'font-size="17" font-weight="700" fill="{color}" '
                f'style="animation-delay:{delay + ci * 0.05 + 0.15:.2f}s">'
                f"{glyph}</text>"
            )
    return document(width, height, "".join(body), style="".join(css),
                    title=title, desc=subtitle)


__all__ = [
    "Series",
    "comparison_matrix",
    "graph_chart",
    "grouped_bars",
    "pct",
    "timeline_chart",
]

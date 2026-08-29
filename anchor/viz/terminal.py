"""Animated terminal-cast rendering.

Produces a self-contained, looping **animated SVG** that replays a terminal
session: commands type themselves out character by character, output lines fade
in beneath them, and the whole thing loops forever.

Why an SVG and not a GIF? It stays crisp at any zoom, weighs a few kilobytes
instead of megabytes, diffs as text in git, needs no recording tool in CI, and
renders inline on GitHub. The animation is pure CSS keyframes embedded in the
document — no JavaScript, no external assets, so it is safe to inline anywhere.

The casts in ``docs/assets`` are generated from genuine :class:`~anchor.Anchor`
runs by :mod:`anchor.viz.render`, so the numbers on screen are real.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .svg import document, esc, pct

CHAR_W = 8.4          # advance width of the monospace face at 14px
LINE_H = 22.0
FONT = 'ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, monospace'

PALETTE = {
    "bg": "#0b1020",
    "chrome": "#151c30",
    "text": "#dbe4f7",
    "dim": "#7b8bb0",
    "prompt": "#5eead4",
    "cmd": "#e8edf9",
    "ok": "#34d399",
    "warn": "#fbbf24",
    "err": "#fb7185",
    "accent": "#a78bfa",
}


@dataclass
class Step:
    """One command plus the output it produces."""

    command: str
    output: list[tuple[str, str]] = field(default_factory=list)  # (kind, text)
    pause: float = 0.85

    def add(self, kind: str, text: str) -> Step:
        self.output.append((kind, text))
        return self


class TerminalCast:
    """Builds an animated terminal SVG from a list of :class:`Step` objects."""

    def __init__(self, title: str = "anchor", prompt: str = "$",
                 width: int = 900, type_speed: float = 0.038,
                 line_delay: float = 0.13, tail_pause: float = 1.6) -> None:
        self.title = title
        self.prompt = prompt
        self.width = width
        self.type_speed = type_speed
        self.line_delay = line_delay
        self.tail_pause = tail_pause
        self.steps: list[Step] = []

    def step(self, command: str, pause: float = 0.85) -> Step:
        s = Step(command=command, pause=pause)
        self.steps.append(s)
        return s

    # ------------------------------------------------------------------ timing
    def _schedule(self) -> tuple[list[dict], float]:
        """Assign each rendered line a start time (and, for commands, a type
        duration). Returns the line schedule plus the total loop length."""
        lines: list[dict] = []
        t = 0.4
        for step in self.steps:
            type_time = max(0.32, min(2.0, len(step.command) * self.type_speed))
            lines.append({"kind": "cmd", "text": step.command, "start": t,
                          "type": type_time})
            t += type_time + 0.28
            for kind, text in step.output:
                lines.append({"kind": kind, "text": text, "start": t, "type": 0.0})
                t += self.line_delay
            t += step.pause
        return lines, t + self.tail_pause

    # ----------------------------------------------------------------- render
    def render(self, *, description: str = "") -> str:
        lines, total = self._schedule()
        rows = len(lines)
        pad_x, top = 22.0, 54.0
        height = top + rows * LINE_H + 26.0

        css = [
            f".t{{font-family:{FONT};font-size:14px;dominant-baseline:middle;"
            f"white-space:pre}}",
            f".cmd{{fill:{PALETTE['cmd']}}}",
            f".out{{fill:{PALETTE['text']}}}",
            f".dim{{fill:{PALETTE['dim']}}}",
            f".ok{{fill:{PALETTE['ok']}}}",
            f".warn{{fill:{PALETTE['warn']}}}",
            f".err{{fill:{PALETTE['err']}}}",
            f".accent{{fill:{PALETTE['accent']}}}",
            f".prompt{{fill:{PALETTE['prompt']};font-weight:600}}",
            "@keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}",
            f".caret{{fill:{PALETTE['prompt']};animation:blink 1s steps(1) infinite}}",
        ]
        body: list[str] = [
            f'<rect width="{self.width}" height="{height:g}" rx="12" '
            f'fill="{PALETTE["bg"]}"/>',
            f'<rect width="{self.width}" height="36" rx="12" fill="{PALETTE["chrome"]}"/>',
            f'<rect y="24" width="{self.width}" height="12" fill="{PALETTE["chrome"]}"/>',
            '<circle cx="22" cy="18" r="5.5" fill="#fb7185"/>',
            '<circle cx="41" cy="18" r="5.5" fill="#fbbf24"/>',
            '<circle cx="60" cy="18" r="5.5" fill="#34d399"/>',
            f'<text x="{self.width / 2:g}" y="18" text-anchor="middle" font-size="12.5" '
            f'fill="{PALETTE["dim"]}" dominant-baseline="middle">{esc(self.title)}</text>',
        ]

        for i, line in enumerate(lines):
            y = top + i * LINE_H + LINE_H / 2
            start_pct = 100.0 * line["start"] / total
            cls = "cmd" if line["kind"] == "cmd" else line["kind"]

            if line["kind"] == "cmd":
                # Reveal the command left-to-right: on a monospace face this
                # reads exactly like live typing.
                end_pct = 100.0 * (line["start"] + line["type"]) / total
                css.append(
                    f"@keyframes k{i}{{0%,{pct(start_pct)}%"
                    f"{{clip-path:inset(0 100% 0 0)}}"
                    f"{pct(end_pct)}%,100%{{clip-path:inset(0 -6px 0 0)}}}}"
                )
                css.append(f".l{i}{{animation:k{i} {total:.2f}s linear infinite}}")
                text_x = pad_x + 2 * CHAR_W
                body.append(
                    f'<g class="l{i}">'
                    f'<text class="t prompt" x="{pad_x:g}" y="{y:g}">'
                    f"{esc(self.prompt)}</text>"
                    f'<text class="t cmd" x="{text_x:g}" y="{y:g}">'
                    f'{esc(line["text"])}</text>'
                    f"</g>"
                )
                caret_x = text_x + len(line["text"]) * CHAR_W + 1
                css.append(
                    f"@keyframes c{i}{{0%,{pct(start_pct)}%{{opacity:0}}"
                    f"{pct(end_pct)}%,{pct(min(100.0, end_pct + 3))}%{{opacity:1}}"
                    f"{pct(min(100.0, end_pct + 3.2))}%,100%{{opacity:0}}}}"
                )
                css.append(f".r{i}{{animation:c{i} {total:.2f}s linear infinite}}")
                body.append(
                    f'<rect class="r{i}" x="{caret_x:g}" y="{y - 8:g}" width="8" '
                    f'height="16" fill="{PALETTE["prompt"]}" opacity="0"/>'
                )
            else:
                css.append(
                    f"@keyframes k{i}{{0%,{pct(start_pct)}%{{opacity:0;"
                    f"transform:translateX(-5px)}}"
                    f"{pct(min(100.0, start_pct + 1.1))}%,100%"
                    f"{{opacity:1;transform:translateX(0)}}}}"
                )
                css.append(
                    f".l{i}{{animation:k{i} {total:.2f}s ease-out infinite;opacity:0}}"
                )
                body.append(
                    f'<text class="t {cls} l{i}" x="{pad_x + 2 * CHAR_W:g}" '
                    f'y="{y:g}">{esc(line["text"])}</text>'
                )

        return document(
            self.width, height, "".join(body), style="".join(css),
            title=self.title,
            desc=description or "Animated recording of an Anchor terminal session.",
        )

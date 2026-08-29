"""Tests for the generated visual assets (:mod:`anchor.viz`)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from anchor.viz.charts import Series, comparison_matrix, graph_chart, grouped_bars, timeline_chart
from anchor.viz.render import (
    render_belief_graph,
    render_capability_matrix,
    render_confidence_timeline,
    render_terminal_cast,
)
from anchor.viz.svg import esc
from anchor.viz.terminal import TerminalCast


def _parse(svg: str) -> ET.Element:
    """Every generated asset must be well-formed XML, not just a string."""
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    return root


def test_escaping_blocks_markup_injection():
    assert esc('<script>alert("x")</script>') == (
        "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"
    )
    assert esc("&lt;") == "&amp;lt;"


def test_terminal_cast_is_well_formed_and_animated():
    cast = TerminalCast(title="anchor demo")
    cast.step("anchor observe user lives_in Berlin").add("ok", "CREATED")
    cast.step("anchor recall where?").add("out", "0.91  user lives_in Berlin")

    svg = cast.render()
    _parse(svg)
    assert "@keyframes" in svg          # CSS-driven animation
    assert "infinite" in svg            # loops forever
    assert "<script" not in svg         # inert, safe to inline anywhere


def test_terminal_cast_escapes_hostile_content():
    """Hostile text must survive as inert characters, never as markup."""
    cast = TerminalCast()
    cast.step('echo "<img src=x onerror=alert(1)>"').add("out", "</svg><script>")
    svg = cast.render()
    root = _parse(svg)

    assert "<script" not in svg and "<img" not in svg
    assert "&lt;img src=x onerror=alert(1)&gt;" in svg

    # The payload exists only as text content, so it can never execute.
    texts = [e.text or "" for e in root.iter() if e.tag.endswith("text")]
    assert any("</svg><script>" in t for t in texts)


def test_terminal_cast_schedule_is_ordered_and_bounded():
    cast = TerminalCast()
    cast.step("one").add("out", "a").add("out", "b")
    cast.step("two").add("out", "c")

    lines, total = cast._schedule()
    assert [line["kind"] for line in lines] == ["cmd", "out", "out", "cmd", "out"]
    assert [line["start"] for line in lines] == sorted(line["start"] for line in lines)
    assert lines[-1]["start"] < total


def test_grouped_bars_renders_every_value():
    svg = grouped_bars(
        "title", "subtitle", ["accuracy", "poison"],
        [Series("summary", [0.7, 0.0]), Series("anchor", [1.0, 1.0], highlight=True)],
    )
    _parse(svg)
    for label in ("0.70", "0.00", "1.00", "summary", "anchor"):
        assert label in svg
    assert "@keyframes grow" in svg


def test_timeline_chart_draws_a_path_with_annotations():
    points = [(0.0, 0.5), (1.0, 0.7), (2.0, 0.9)]
    svg = timeline_chart("t", "s", points, [(1.0, "reinforced", "#34d399")])
    _parse(svg)
    assert "stroke-dashoffset" in svg
    assert "reinforced" in svg


def test_timeline_chart_handles_a_single_point():
    _parse(timeline_chart("t", "s", [(0.0, 0.5)]))


def test_comparison_matrix_marks_every_cell():
    svg = comparison_matrix("t", "s", ["row a", "row b"], ["x", "y"],
                            [["yes", "no"], ["partial", "yes"]], highlight_col=1)
    _parse(svg)
    assert "row a" in svg and "row b" in svg


def test_graph_chart_from_a_live_engine(anchor):
    anchor.observe("Ada", "lives_in", "Berlin", source_id="chat", trust=0.9)
    anchor.observe("Ada", "lives_in", "Munich", source_id="chat", trust=0.95)

    svg = graph_chart(anchor.graph(), "graph", "with a conflict")
    _parse(svg)
    assert "Berlin" in svg and "Munich" in svg
    assert "@keyframes flow" in svg     # the pulsing contradiction edge


def test_rendered_documentation_assets_are_valid():
    """The README's visuals are generated from real runs; keep them honest."""
    for svg in (render_terminal_cast(), render_confidence_timeline(),
                render_belief_graph(), render_capability_matrix()):
        _parse(svg)
        assert len(svg) > 1000


def test_terminal_cast_shows_the_contradiction_story():
    svg = render_terminal_cast()
    assert "CONTRADICTED" in svg
    assert "NOT overwritten" in svg
    assert "RESOLVED" in svg


def test_confidence_timeline_rises_then_falls():
    from anchor.viz.render import render_confidence_timeline

    svg = render_confidence_timeline()
    assert "reinforced" in svg and "contradicted" in svg and "decay" in svg


def test_hero_banner_is_well_formed_and_animated():
    from anchor.viz.render import render_hero

    svg = render_hero()
    _parse(svg)
    assert "@keyframes" in svg
    assert "infinite" in svg          # the banner loops rather than playing once
    assert "<script" not in svg       # inert: GitHub renders SVG with scripts disabled


def test_hero_banner_tells_both_sides_of_the_story():
    """The banner's whole claim is the contrast; neither half may go missing."""
    from anchor.viz.render import render_hero

    svg = render_hero()
    assert "OVERWRITTEN" in svg              # what conventional memory does
    assert "CONTRADICTION raised" in svg     # what Anchor does instead
    assert "scraper_bot" in svg and "0.25" in svg


def test_hero_banner_refuses_to_render_a_claim_the_engine_cannot_back(monkeypatch):
    """If Anchor ever stopped holding its ground, the banner must fail loudly
    rather than keep advertising behaviour the engine no longer has."""
    import pytest

    from anchor.core.engine import Anchor
    from anchor.viz import render as render_mod

    class _Fake:
        object = "Munich"          # pretend the poisoning succeeded
        id = "x"

    monkeypatch.setattr(Anchor, "current_belief", lambda *a, **k: _Fake())
    with pytest.raises(RuntimeError, match="hero scenario did not reproduce"):
        render_mod.render_hero()


def test_hero_banner_honours_reduced_motion():
    """Animation must be opt-out for readers who ask the OS to stop motion."""
    from anchor.viz.render import render_hero

    assert "prefers-reduced-motion" in render_hero()


def test_render_all_writes_every_asset(tmp_path):
    from anchor.viz.render import ASSETS, render_all

    written = render_all(str(tmp_path), quiet=True)
    assert {p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in written} == set(ASSETS)
    for path in written:
        with open(path, encoding="utf-8") as fh:
            _parse(fh.read())

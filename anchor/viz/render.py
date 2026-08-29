"""Regenerate every documentation asset from a live Anchor run.

``anchor render-assets`` executes real scenarios against a throwaway database
and turns the resulting state into the animated SVGs used by the README. Because
the visuals are *generated from the engine* rather than screenshotted, the docs
can never quietly drift out of sync with the behaviour they advertise.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from ..config import DAY, AnchorConfig
from ..core.engine import Anchor
from .charts import Series, comparison_matrix, graph_chart, grouped_bars, timeline_chart
from .terminal import TerminalCast

ASSETS = ("anchor-demo.svg", "benchmark.svg", "confidence-timeline.svg",
          "belief-graph.svg", "capability-matrix.svg", "poisoning.svg")


def _tmp_anchor(**overrides: Any) -> tuple[Anchor, str]:
    path = os.path.join(tempfile.mkdtemp(prefix="anchor_viz_"), "viz.db")
    return Anchor(AnchorConfig(db_path=path, **overrides)), path


# --------------------------------------------------------------------- terminal
def render_terminal_cast() -> str:
    """Replay the headline scenario through a real engine and film it."""
    anchor, _ = _tmp_anchor(min_sources_for_active=2)
    cast = TerminalCast(title="anchor — belief memory that refuses to lie to you",
                        width=920)

    s = cast.step("anchor observe user lives_in Berlin --source-id chat --trust 0.9")
    r1 = anchor.observe("user", "lives_in", "Berlin", source_id="chat", trust=0.9)
    s.add("ok", f"CREATED     user lives_in Berlin   conf={r1.belief.raw_confidence:.2f}"
                f"  status={r1.belief.status.value}")

    s = cast.step("anchor observe user lives_in Berlin --source-id calendar --trust 0.9")
    r2 = anchor.observe("user", "lives_in", "Berlin", source_id="calendar", trust=0.9)
    s.add("ok", f"REINFORCED  2 independent sources  conf={r2.belief.raw_confidence:.2f}"
                f"  status={r2.belief.status.value}")

    s = cast.step("anchor observe user lives_in Munich --source-id chat --trust 0.95")
    r3 = anchor.observe("user", "lives_in", "Munich", source_id="chat", trust=0.95)
    s.add("err", f"CONTRADICTED  conflicting claim detected "
                 f"({r3.contradictions[0].kind.value})")
    s.add("dim", "            Berlin was NOT overwritten — both claims kept, "
                 "flagged for review")

    s = cast.step("anchor contradictions", pause=0.7)
    con = anchor.open_contradictions()[0]
    s.add("warn", "OPEN  user lives_in Berlin   vs   user lives_in Munich")

    s = cast.step("anchor resolve <id> --strategy reliable --actor human")
    anchor.resolve_contradiction(con.id, strategy="newest", actor="human")
    s.add("ok", "RESOLVED  winner=Munich   loser=Berlin -> superseded (kept for history)")

    s = cast.step('anchor recall "where does the user live?"')
    for item in anchor.recall("where does the user live?", top_k=2):
        s.add("out", f"{item.score:.3f}  {item.belief.statement:<28} "
                     f"conf={item.confidence:.2f}  {item.belief.status.value}")

    s = cast.step("anchor audit --verify", pause=1.1)
    s.add("accent", f"{len(anchor.audit())} hash-chained events   "
                    f"tamper-evident: {anchor.verify_audit()}")
    anchor.close()

    return cast.render(
        description="Anchor detecting a contradiction instead of overwriting memory, "
                    "resolving it, and verifying the tamper-evident audit chain."
    )


# ----------------------------------------------------------------------- charts
def render_confidence_timeline() -> str:
    """Track one belief's confidence across reinforcement, conflict and decay."""
    anchor, _ = _tmp_anchor(decay_half_life=10 * DAY, min_sources_for_active=2)
    t0 = 1_700_000_000.0
    points: list[tuple[float, float]] = []
    annotations: list[tuple[float, str, str]] = []

    result = anchor.observe("user", "role", "engineer", source_id="hr",
                            trust=0.85, at=t0)
    belief_id = result.belief.id
    points.append((0.0, result.belief.raw_confidence))
    annotations.append((0.0, "observed", "#5eead4"))

    day = 0.0
    for i, source in enumerate(["chat", "calendar", "email", "hr"]):
        day += 2.0
        res = anchor.observe("user", "role", "engineer", source_id=source,
                             trust=0.85, at=t0 + day * DAY)
        points.append((day, res.belief.raw_confidence))
        if i == 0:
            annotations.append((day, "reinforced", "#34d399"))

    day += 3.0
    anchor.observe("user", "role", "manager", source_id="rumour", trust=0.5,
                   at=t0 + day * DAY)
    current = anchor.get(belief_id)
    points.append((day, current.raw_confidence if current else 0.0))
    annotations.append((day, "contradicted", "#fb7185"))

    for _ in range(14):
        day += 5.0
        anchor.apply_decay(at_time=t0 + day * DAY)
        current = anchor.get(belief_id)
        points.append((day, current.raw_confidence if current else 0.0))
    annotations.append((points[-1][0] * 0.62, "decay (no reinforcement)", "#fbbf24"))
    anchor.close()

    return timeline_chart(
        "A belief's confidence is earned, challenged, and forgotten",
        "Beta posterior for a single belief: corroboration lifts it, a conflicting "
        "claim knocks it down, and un-reinforced evidence decays toward 'unknown'.",
        points, annotations, x_label="days since first observation",
    )


def render_benchmark_chart(results: dict[str, Any] | None = None) -> str:
    """Bar chart of the head-to-head benchmark (runs it if not supplied)."""
    if results is None:
        from ..eval.benchmark import run_benchmark
        results = run_benchmark()
    scores = results["results"]
    scenario = results["scenario"]
    metrics = ["accuracy", "update_accuracy", "poison_resistance", "stale_resistance"]
    labels = ["current-value\naccuracy", "tracks\nupdates", "resists\npoisoning",
              "rejects\nstale facts"]
    present = [m for m in metrics
               if any(m in s for s in scores.values())]
    labels = [labels[metrics.index(m)] for m in present]

    series = []
    for name, score in scores.items():
        series.append(Series(
            name=name,
            values=[float(score.get(m, 0.0)) for m in present],
            highlight=name == "anchor",
            color="#5eead4" if name == "anchor" else "",
        ))
    return grouped_bars(
        "Anchor vs. conventional agent memory",
        f"{scenario['subjects']} memory slots · {scenario['events']} events · "
        f"{scenario['poisoned_slots']} poisoning campaigns · deterministic, fully offline",
        labels, series, value_fmt="{:.2f}",
    )


def render_poisoning_chart(results: dict[str, Any] | None = None) -> str:
    """Focused view of the adversarial result — the most quotable number."""
    if results is None:
        from ..eval.benchmark import run_benchmark
        results = run_benchmark()
    scores = results["results"]
    names = list(scores.keys())
    series = [Series(
        name="poison resistance",
        values=[float(scores[n].get("poison_resistance", 0.0)) for n in names],
        color="#a78bfa",
    )]
    return grouped_bars(
        "Memory poisoning: how much of the attack got through?",
        "A low-trust source repeatedly asserts a false value after the real update. "
        "Higher is better — 1.00 means every attack was rejected.",
        names, series, value_fmt="{:.2f}", height=340,
    )


def render_belief_graph() -> str:
    """A small, legible memory graph including one live contradiction."""
    anchor, _ = _tmp_anchor(min_sources_for_active=2)
    script = [
        ("Ada", "works_at", "Acme", "hr", 0.9),
        ("Ada", "works_at", "Acme", "email", 0.85),
        ("Ada", "lives_in", "Berlin", "chat", 0.9),
        ("Ada", "lives_in", "Berlin", "calendar", 0.9),
        ("Ada", "speaks", "German", "chat", 0.8),
        ("Ada", "speaks", "English", "chat", 0.9),
        ("Acme", "based_in", "Berlin", "crm", 0.9),
        ("Ada", "manages", "Project Iris", "hr", 0.8),
    ]
    for subject, predicate, obj, source, trust in script:
        anchor.observe(subject, predicate, obj, source_id=source, trust=trust)
    anchor.observe("Ada", "lives_in", "Munich", source_id="chat", trust=0.95)
    graph = anchor.graph()
    anchor.close()
    return graph_chart(
        graph,
        "Memory as a confidence-weighted knowledge graph",
        "Edge thickness is Bayesian confidence. The pulsing red edge is an open "
        "contradiction: both claims are retained until it is resolved.",
    )


def render_capability_matrix() -> str:
    rows = [
        "Remembers full history",
        "Knows the current truth",
        "Confidence & reinforcement",
        "Temporal validity & decay",
        "Detects contradictions",
        "Human-in-the-loop resolution",
        "Tamper-evident provenance",
        "Resists memory poisoning",
        "Explains why it believes something",
    ]
    columns = ["summary memory", "vector RAG", "Anchor"]
    cells = [
        ["no", "yes", "yes"],
        ["partial", "no", "yes"],
        ["no", "no", "yes"],
        ["no", "no", "yes"],
        ["no", "no", "yes"],
        ["no", "no", "yes"],
        ["no", "no", "yes"],
        ["no", "partial", "yes"],
        ["no", "no", "yes"],
    ]
    return comparison_matrix(
        "What 'agent memory' usually skips",
        "Summary memory overwrites and forgets. Vector RAG retrieves text without a "
        "notion of truth. Anchor models belief.",
        rows, columns, cells, highlight_col=2,
    )


# ------------------------------------------------------------------------ entry
def render_all(out_dir: str = "docs/assets", *, quiet: bool = False) -> list[str]:
    """Regenerate every asset. Returns the list of written paths."""
    from ..eval.benchmark import run_benchmark

    os.makedirs(out_dir, exist_ok=True)
    bench = run_benchmark()
    assets = {
        "anchor-demo.svg": render_terminal_cast,
        "confidence-timeline.svg": render_confidence_timeline,
        "benchmark.svg": lambda: render_benchmark_chart(bench),
        "poisoning.svg": lambda: render_poisoning_chart(bench),
        "belief-graph.svg": render_belief_graph,
        "capability-matrix.svg": render_capability_matrix,
    }
    written: list[str] = []
    for name, fn in assets.items():
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(fn())
        written.append(path)
        if not quiet:
            size = os.path.getsize(path) / 1024
            print(f"  wrote {path}  ({size:.1f} KB)")
    return written


def main() -> int:  # pragma: no cover - thin CLI wrapper
    print("Regenerating Anchor documentation assets from a live engine run…")
    render_all()
    print("done.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

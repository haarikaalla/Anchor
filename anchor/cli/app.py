"""Anchor CLI.

Uses ``rich`` when available for polished tables/panels, and degrades
gracefully to plain text otherwise.  Provides direct memory operations plus a
scripted, self-contained ``demo`` that showcases multi-session memory
evolution, reinforcement, contradiction detection, resolution and audit history.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..config import AnchorConfig
from ..core.engine import Anchor


# --------------------------------------------------------------------------- UI
class Console:
    """Minimal console abstraction; upgrades to rich if installed."""

    def __init__(self) -> None:
        try:
            from rich.console import Console as RichConsole  # type: ignore
            self._rich = RichConsole()
        except Exception:
            self._rich = None

    def print(self, *args: Any, **kwargs: Any) -> None:
        if self._rich is not None:
            self._rich.print(*args, **kwargs)
        else:
            plain = " ".join(str(a) for a in args)
            for tag in ("[bold]", "[/bold]", "[cyan]", "[/cyan]", "[green]",
                        "[/green]", "[red]", "[/red]", "[yellow]", "[/yellow]",
                        "[dim]", "[/dim]", "[magenta]", "[/magenta]"):
                plain = plain.replace(tag, "")
            print(plain)

    def rule(self, title: str = "") -> None:
        if self._rich is not None:
            self._rich.rule(title)
        else:
            print("\n" + "=" * 8 + f" {title} " + "=" * 8)

    def table(self, title: str, columns: list[str], rows: list[list[str]]) -> None:
        if self._rich is not None:
            from rich.table import Table  # type: ignore
            t = Table(title=title, show_lines=False)
            for c in columns:
                t.add_column(c, overflow="fold")
            for r in rows:
                t.add_row(*[str(x) for x in r])
            self._rich.print(t)
        else:
            print(f"\n{title}")
            print(" | ".join(columns))
            print("-" * 60)
            for r in rows:
                print(" | ".join(str(x) for x in r))


def _anchor(args: argparse.Namespace) -> Anchor:
    return Anchor(AnchorConfig(db_path=args.db))


# ------------------------------------------------------------------- commands
def cmd_observe(args: argparse.Namespace, con: Console) -> None:
    a = _anchor(args)
    res = a.observe(
        args.subject, args.predicate, args.object,
        statement=args.statement, polarity=args.polarity, namespace=args.namespace,
        source_type=args.source_type, source_id=args.source_id, trust=args.trust,
    )
    con.print(f"[bold]{res.action.upper()}[/bold] "
              f"[cyan]{res.belief.statement}[/cyan] "
              f"(confidence={res.belief.raw_confidence:.3f}, "
              f"status={res.belief.status.value})")
    for c in res.contradictions:
        con.print(f"  [red]contradiction[/red] {c.id} kind={c.kind.value} "
                  f"vs {c.belief_b}")
    for n in res.notes:
        con.print(f"  [dim]{n}[/dim]")


def cmd_recall(args: argparse.Namespace, con: Console) -> None:
    a = _anchor(args)
    items = a.recall(args.query, namespace=args.namespace, top_k=args.top_k)
    con.table(
        f"recall: {args.query!r}",
        ["statement", "score", "conf", "status"],
        [[it.belief.statement, f"{it.score:.3f}", f"{it.confidence:.3f}",
          it.belief.status.value] for it in items],
    )


def cmd_list(args: argparse.Namespace, con: Console) -> None:
    a = _anchor(args)
    beliefs = a.list_beliefs(args.namespace, include_inactive=args.all)
    con.table(
        f"beliefs [{args.namespace}]",
        ["id", "statement", "conf", "status", "reinf"],
        [[b.id[:14], b.statement, f"{b.raw_confidence:.3f}", b.status.value,
          b.reinforcement_count] for b in beliefs],
    )


def cmd_contradictions(args: argparse.Namespace, con: Console) -> None:
    a = _anchor(args)
    cons = a.open_contradictions(args.namespace)
    if not cons:
        con.print("[green]no open contradictions[/green]")
        return
    rows = []
    for c in cons:
        ba, bb = a.get(c.belief_a), a.get(c.belief_b)
        rows.append([c.id[:14], c.kind.value,
                     ba.statement if ba else "?", bb.statement if bb else "?",
                     f"{c.score:.2f}"])
    con.table("open contradictions", ["id", "kind", "belief_a", "belief_b", "score"], rows)


def cmd_resolve(args: argparse.Namespace, con: Console) -> None:
    a = _anchor(args)
    c = a.resolve_contradiction(args.id, strategy=args.strategy,
                                winner_id=args.winner, actor=args.actor, note=args.note)
    con.print(f"[green]resolved[/green] {c.id} strategy={c.resolution} "
              f"winner={c.winner_id or 'both'}")


def cmd_decay(args: argparse.Namespace, con: Console) -> None:
    a = _anchor(args)
    changed = a.apply_decay(args.namespace)
    con.print(f"[yellow]decay[/yellow] {json.dumps(changed)}")


def cmd_audit(args: argparse.Namespace, con: Console) -> None:
    a = _anchor(args)
    events = a.audit(args.namespace, args.belief, args.limit)
    con.table(
        "audit log",
        ["ts", "event", "belief", "actor"],
        [[f"{e.ts:.0f}", e.event_type, e.belief_id[:14], e.actor]
         for e in reversed(events)],
    )
    con.print(f"chain intact: [{'green' if a.verify_audit(args.namespace) else 'red'}]"
              f"{a.verify_audit(args.namespace)}[/]")


def cmd_stats(args: argparse.Namespace, con: Console) -> None:
    a = _anchor(args)
    con.print(json.dumps(a.stats(args.namespace), indent=2))


def cmd_explain(args: argparse.Namespace, con: Console) -> None:
    """Show the full justification for one belief."""
    a = _anchor(args)
    x = a.explain(args.id)
    if args.json:
        con.print(json.dumps(x.to_dict(), indent=2))
        return

    con.rule(x.belief.statement)
    lo, hi = x.interval
    con.print(f"[bold]confidence[/bold] {x.confidence:.3f}   "
              f"[dim]lower bound[/dim] {x.lower_bound:.3f}   "
              f"[dim]95% CI[/dim] {lo:.3f}–{hi:.3f}")
    con.print(f"[dim]evidence[/dim] alpha={x.supporting:.2f} beta={x.contradicting:.2f}   "
              f"[dim]status[/dim] {x.belief.status.value}   "
              f"[dim]age[/dim] {x.age_days:.1f}d   "
              f"[dim]stale[/dim] {x.staleness_days:.1f}d\n")

    con.table("evidence by source",
              ["source", "supporting", "contradicting", "share", "obs", "capped"],
              [[s.source_id, f"{s.supporting:.2f}", f"{s.contradicting:.2f}",
                f"{s.share:.0%}", s.observations, "yes" if s.capped else ""]
               for s in x.sources])

    con.print("\n[bold]why memory holds this[/bold]")
    for reason in x.reasons:
        con.print(f"  [cyan]•[/cyan] {reason}")

    con.table("if never reinforced again", ["in days", "confidence"],
              [[f"{d:.0f}", f"{c:.3f}"] for d, c in x.decay_projection])
    con.table("audit timeline", ["ts", "event", "actor"],
              [[f"{e.ts:.0f}", e.event_type, e.actor] for e in x.timeline])


def cmd_graph(args: argparse.Namespace, con: Console) -> None:
    """Export memory as a knowledge graph (mermaid, dot, json or svg)."""
    a = _anchor(args)
    g = a.graph(args.namespace, include_inactive=args.all,
                include_sources=args.sources)
    if args.format == "mermaid":
        out = g.to_mermaid()
    elif args.format == "dot":
        out = g.to_dot()
    elif args.format == "svg":
        from ..viz.charts import graph_chart
        out = graph_chart(g, f"Anchor memory — {args.namespace}",
                          f"{len(g.nodes)} entities, {len(g.edges)} relations")
    else:
        out = json.dumps(g.layout().to_dict(), indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
        con.print(f"[green]wrote[/green] {args.output}")
    else:
        print(out)


def cmd_serve(args: argparse.Namespace, con: Console) -> None:
    """Run the live dashboard and REST API."""
    from ..server.api import serve
    serve(_anchor(args), host=args.host, port=args.port, namespace=args.namespace,
          token=args.token, cors_origin=args.cors, open_browser=args.open,
          verbose=args.verbose)


def cmd_bench(args: argparse.Namespace, con: Console) -> None:
    from ..eval.benchmark import main as bench_main
    bench_main(args.json)


def cmd_render_assets(args: argparse.Namespace, con: Console) -> None:
    from ..viz.render import render_all
    con.print("[dim]regenerating documentation assets from a live engine run…[/dim]")
    written = render_all(args.out)
    con.print(f"[green]wrote {len(written)} assets[/green] -> {args.out}")


def cmd_demo(args: argparse.Namespace, con: Console) -> None:
    from .demo import run_demo
    run_demo(None, con, namespace=args.namespace)


_SHELL_HELP = """\
Interactive Anchor shell — type facts and questions, memory persists.

Commands (same as the CLI, without the leading 'python -m anchor.cli.app'):
  observe <subject> <predicate> <object> [--trust 0.9] [--source-id chat]
  recall "<natural language question>"
  list [--all]
  contradictions
  resolve <id> [--strategy newest]
  explain <belief-id>
  graph [--format mermaid]
  decay
  audit
  stats
  help            show this help
  quit / exit     leave the shell

Examples:
  observe alice lives_in Paris --trust 0.9 --source-id chat
  recall where does alice live?
  list
"""


def cmd_shell(args: argparse.Namespace, con: Console) -> None:
    """Interactive REPL: type any command, memory persists to the same db."""
    import shlex

    parser = build_parser()
    con.rule("Anchor interactive shell")
    con.print(f"[dim]db={args.db}  namespace={args.namespace}[/dim]")
    con.print("Type [cyan]help[/cyan] for commands, [cyan]quit[/cyan] to exit.\n")

    while True:
        try:
            line = input("anchor> ").strip()
        except (EOFError, KeyboardInterrupt):
            con.print("\n[dim]bye[/dim]")
            break
        if not line:
            continue
        if line in ("quit", "exit", "q"):
            con.print("[dim]bye[/dim]")
            break
        if line in ("help", "?", "h"):
            con.print(_SHELL_HELP)
            continue
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            con.print(f"[red]parse error:[/red] {exc}")
            continue
        # Convenience: let users type a free-form question after `recall`
        # without needing quotes, e.g. `recall where does bob live?`.
        if tokens and tokens[0] == "recall":
            rest = line[len("recall"):].strip()
            flag_args: list[str] = []
            if rest.startswith("-"):
                flag_args = shlex.split(rest)
                query = ""
            else:
                # Split off any trailing flags (e.g. --top-k 3) from the query.
                if " --" in rest:
                    query, _, flags = rest.partition(" --")
                    flag_args = shlex.split("--" + flags)
                else:
                    query = rest
            tokens = ["recall", query, *flag_args]
        # Reuse the top-level parser; keep the shell's db/namespace as defaults.
        argv = ["--db", args.db, "--namespace", args.namespace, *tokens]
        try:
            sub_args = parser.parse_args(argv)
        except SystemExit:
            # argparse prints its own error/help; stay in the shell.
            continue
        if getattr(sub_args, "func", None) in (cmd_shell, cmd_demo, cmd_serve):
            con.print("[yellow]not available inside the shell[/yellow]")
            continue
        try:
            sub_args.func(sub_args, con)
        except Exception as exc:  # keep the shell alive on operational errors
            con.print(f"[red]error:[/red] {exc}")


# ----------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="anchor", description="Anchor belief-memory CLI")
    p.add_argument("--db", default="anchor.db", help="SQLite database path")
    p.add_argument("--namespace", default="default")
    sub = p.add_subparsers(dest="command", required=True)

    o = sub.add_parser("observe", help="record a belief")
    o.add_argument("subject")
    o.add_argument("predicate")
    o.add_argument("object")
    o.add_argument("--statement", default=None)
    o.add_argument("--polarity", default="positive", choices=["positive", "negative"])
    o.add_argument("--source-type", dest="source_type", default="user")
    o.add_argument("--source-id", dest="source_id", default="")
    o.add_argument("--trust", type=float, default=0.8)
    o.set_defaults(func=cmd_observe)

    r = sub.add_parser("recall", help="retrieve beliefs")
    r.add_argument("query")
    r.add_argument("--top-k", dest="top_k", type=int, default=5)
    r.set_defaults(func=cmd_recall)

    li = sub.add_parser("list", help="list beliefs")
    li.add_argument("--all", action="store_true", help="include inactive")
    li.set_defaults(func=cmd_list)

    c = sub.add_parser("contradictions", help="list open contradictions")
    c.set_defaults(func=cmd_contradictions)

    rs = sub.add_parser("resolve", help="resolve a contradiction")
    rs.add_argument("id")
    rs.add_argument("--strategy", default="newest",
                    choices=["keep_new", "keep_existing", "highest_confidence",
                             "newest", "reliable", "keep_both", "manual"])
    rs.add_argument("--winner", default="")
    rs.add_argument("--actor", default="human")
    rs.add_argument("--note", default="")
    rs.set_defaults(func=cmd_resolve)

    d = sub.add_parser("decay", help="age memory")
    d.set_defaults(func=cmd_decay)

    au = sub.add_parser("audit", help="show audit history")
    au.add_argument("--belief", default=None)
    au.add_argument("--limit", type=int, default=100)
    au.set_defaults(func=cmd_audit)

    st = sub.add_parser("stats", help="memory health metrics")
    st.set_defaults(func=cmd_stats)

    ex = sub.add_parser("explain", help="explain why a belief is held")
    ex.add_argument("id")
    ex.add_argument("--json", action="store_true", help="emit raw JSON")
    ex.set_defaults(func=cmd_explain)

    gr = sub.add_parser("graph", help="export memory as a knowledge graph")
    gr.add_argument("--format", default="mermaid",
                    choices=["mermaid", "dot", "json", "svg"])
    gr.add_argument("--output", "-o", default="", help="write to a file")
    gr.add_argument("--all", action="store_true", help="include retired beliefs")
    gr.add_argument("--sources", action="store_true", help="include provenance nodes")
    gr.set_defaults(func=cmd_graph)

    sv = sub.add_parser("serve", help="run the live dashboard + REST API")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--token", default="",
                    help="require this bearer token (or set ANCHOR_API_TOKEN)")
    sv.add_argument("--cors", default="", help="allowed CORS origin (off by default)")
    sv.add_argument("--open", action="store_true", help="open a browser window")
    sv.add_argument("--verbose", action="store_true", help="log every request")
    sv.set_defaults(func=cmd_serve)

    bm = sub.add_parser("bench", help="run the head-to-head memory benchmark")
    bm.add_argument("--json", default="", help="also write raw results here")
    bm.set_defaults(func=cmd_bench)

    ra = sub.add_parser("render-assets", help="regenerate the docs/ visuals")
    ra.add_argument("--out", default="docs/assets")
    ra.set_defaults(func=cmd_render_assets)

    dm = sub.add_parser("demo", help="run the scripted showcase")
    dm.set_defaults(func=cmd_demo)

    sh = sub.add_parser("shell", help="interactive REPL: type facts and questions")
    sh.set_defaults(func=cmd_shell)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    con = Console()
    args.func(args, con)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""A scripted, self-contained showcase of Anchor's differentiating behaviours.

Runs entirely offline (local embeddings, temp database) and uses simulated time
so the full lifecycle — learning, reinforcement across sessions, contradiction
detection, human-in-the-loop resolution, temporal decay, and a tamper-evident
audit trail — is visible in a few seconds.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from ..config import AnchorConfig, DAY
from ..core.engine import Anchor
from ..core.models import SourceType


def _fresh_anchor() -> Anchor:
    path = os.path.join(tempfile.mkdtemp(prefix="anchor_demo_"), "demo.db")
    return Anchor(AnchorConfig(db_path=path, min_sources_for_active=2,
                               promote_threshold=0.66))


def _show_beliefs(a: Anchor, con: Any, namespace: str) -> None:
    rows = []
    for b in sorted(a.list_beliefs(namespace, include_inactive=True),
                    key=lambda x: x.created_at):
        rows.append([b.statement, f"{b.raw_confidence:.3f}", b.status.value,
                     b.reinforcement_count, b.contradiction_count])
    con.table("memory state", ["statement", "conf", "status", "reinf", "contra"], rows)


def run_demo(_ignored: Anchor | None = None, con: Any = None, namespace: str = "demo") -> None:
    from .app import Console
    con = con or Console()
    a = _fresh_anchor()
    base = 1_700_000_000.0  # fixed epoch for reproducibility

    con.rule("Anchor demo — belief memory that reasons over time")
    con.print("[dim]Local embeddings, simulated time, tamper-evident audit.[/dim]\n")

    # ---- Session 1: first encounter -------------------------------------------
    con.rule("Session 1 — first conversation (day 0)")
    a.observe("user", "lives_in", "Berlin", namespace=namespace,
              source_type=SourceType.USER, source_id="chat", trust=0.9,
              at=base)
    a.observe("user", "works_at", "Acme", namespace=namespace,
              source_type=SourceType.USER, source_id="chat", trust=0.9,
              at=base)
    a.observe("user", "prefers", "Python", namespace=namespace,
              source_type=SourceType.USER, source_id="chat", trust=0.8,
              at=base)
    con.print("Learned 3 beliefs. Each is [yellow]PROVISIONAL[/yellow] until "
              "corroborated by a second independent source.")
    _show_beliefs(a, con, namespace)

    # ---- Session 2: reinforcement from a different source ---------------------
    con.rule("Session 2 — a later session corroborates (day 3)")
    r = a.observe("user", "lives_in", "Berlin", namespace=namespace,
                  source_type=SourceType.AGENT, source_id="calendar", trust=0.9,
                  at=base + 3 * DAY)
    con.print(f"Second source confirms 'lives_in Berlin' -> [green]{r.action}[/green], "
              f"confidence now {r.belief.raw_confidence:.3f}, "
              f"status [green]{r.belief.status.value}[/green].")
    _show_beliefs(a, con, namespace)

    # ---- Session 3: a contradiction appears -----------------------------------
    con.rule("Session 3 — the user moves (day 30)")
    r = a.observe("user", "lives_in", "Munich", namespace=namespace,
                  source_type=SourceType.USER, source_id="chat", trust=0.95,
                  at=base + 30 * DAY)
    con.print(f"New claim 'lives_in Munich' [red]conflicts[/red] with 'Berlin'. "
              f"Anchor did [bold]not[/bold] overwrite — it flagged a contradiction.")
    _show_beliefs(a, con, namespace)

    cons = a.open_contradictions(namespace)
    for c in cons:
        ba, bb = a.get(c.belief_a), a.get(c.belief_b)
        con.print(f"  [red]OPEN[/red] {c.kind.value}: "
                  f"'{ba.statement}' vs '{bb.statement}' (score {c.score:.2f})")

    # ---- Human-in-the-loop resolution -----------------------------------------
    con.rule("Resolution — human confirms the move")
    c = cons[0]
    a.resolve_contradiction(c.id, strategy="newest", actor="human",
                            note="user confirmed relocation")
    con.print("[green]Resolved[/green] with strategy 'newest': Munich becomes "
              "ACTIVE, Berlin is SUPERSEDED (kept for history, not deleted).")
    _show_beliefs(a, con, namespace)

    # ---- Temporal decay --------------------------------------------------------
    con.rule("Temporal decay — un-reinforced beliefs fade (day 120)")
    con.print("'prefers Python' was never repeated. Ageing memory forward "
              "discounts stale evidence toward 'unknown'.")
    before = {b.statement: b.raw_confidence for b in a.list_beliefs(namespace)}
    a.apply_decay(namespace, at_time=base + 120 * DAY)
    after = {b.statement: b.raw_confidence for b in a.list_beliefs(namespace)}
    rows = [[k, f"{before.get(k, 0):.3f}", f"{after.get(k, 0):.3f}"]
            for k in before]
    con.table("confidence before/after decay", ["statement", "before", "after"], rows)

    # ---- Recall ----------------------------------------------------------------
    con.rule("Recall — relevance x reliability x recency")
    for q in ["where does the user live?", "what language does the user like?"]:
        items = a.recall(q, namespace=namespace, top_k=3, at_time=base + 121 * DAY)
        con.print(f"[cyan]{q}[/cyan]")
        for it in items:
            con.print(f"   -> {it.belief.statement}  "
                      f"[dim](score {it.score:.3f}, conf {it.confidence:.3f})[/dim]")

    # ---- Audit trail -----------------------------------------------------------
    con.rule("Audit trail — tamper-evident history")
    events = a.audit(namespace, limit=100)
    con.table("recent events", ["event", "actor", "belief"],
              [[e.event_type, e.actor, e.belief_id[:14]] for e in list(reversed(events))[-12:]])
    con.print(f"hash-chain intact: [green]{a.verify_audit(namespace)}[/green]")
    con.print(f"\n[bold]stats:[/bold] {a.stats(namespace)['by_status']}")
    con.rule("done")
    a.close()

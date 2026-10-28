"""Belief-graph projection.

Anchor stores `(subject, predicate, object)` claims, which means the active
memory is really a **knowledge graph with confidence on the edges**. This module
projects the store into that graph so it can be:

* rendered live in the dashboard (`to_dict`),
* dropped into Markdown docs (`to_mermaid`),
* piped into Graphviz (`to_dot`),
* or laid out and drawn as a standalone SVG (`layout` + :mod:`anchor.viz`).

The layout is a deterministic, seeded force-directed simulation implemented in
pure stdlib, so the same memory always produces the same picture — important for
reproducible docs and for diffing generated assets in git.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .models import Belief, BeliefStatus, ContradictionStatus, Polarity

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import SQLiteStore

_LIVE = (BeliefStatus.ACTIVE, BeliefStatus.PROVISIONAL, BeliefStatus.DISPUTED)


@dataclass
class Node:
    id: str
    label: str
    kind: str = "entity"          # entity | source
    weight: float = 1.0           # drives node radius
    x: float = 0.0
    y: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "kind": self.kind,
            "weight": round(self.weight, 4),
            "x": round(self.x, 2), "y": round(self.y, 2),
        }


@dataclass
class Edge:
    source: str
    target: str
    label: str
    kind: str = "belief"          # belief | contradiction | attests
    confidence: float = 1.0
    status: str = "active"
    belief_id: str = ""
    polarity: str = "positive"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "target": self.target, "label": self.label,
            "kind": self.kind, "confidence": round(self.confidence, 4),
            "status": self.status, "belief_id": self.belief_id,
            "polarity": self.polarity,
        }


@dataclass
class BeliefGraph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    namespace: str = "default"

    def node_index(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    # ---------------------------------------------------------------- layout
    def layout(self, width: float = 900.0, height: float = 520.0,
               iterations: int = 260, seed: int = 11) -> BeliefGraph:
        """Deterministic force-directed layout (Fruchterman-Reingold style).

        Repulsion between every node pair, attraction along edges, and a linearly
        cooling temperature. Seeded, so output is byte-stable across runs.
        """
        n = len(self.nodes)
        if n == 0:
            return self
        if n == 1:
            self.nodes[0].x, self.nodes[0].y = width / 2, height / 2
            return self

        # Deterministic layout jitter, never used for a security decision.
        rng = random.Random(seed)  # nosec B311
        area = width * height
        k = math.sqrt(area / n)          # ideal edge length
        index = {node.id: i for i, node in enumerate(self.nodes)}

        # Seed on a circle + jitter: avoids the degenerate all-forces-cancel start.
        for i, node in enumerate(self.nodes):
            angle = 2 * math.pi * i / n
            node.x = width / 2 + (width / 3) * math.cos(angle) + rng.uniform(-6, 6)
            node.y = height / 2 + (height / 3) * math.sin(angle) + rng.uniform(-6, 6)

        temperature = width / 8.0
        cooling = temperature / (iterations + 1)

        for _ in range(iterations):
            dx = [0.0] * n
            dy = [0.0] * n

            for i in range(n):
                for j in range(i + 1, n):
                    ux = self.nodes[i].x - self.nodes[j].x
                    uy = self.nodes[i].y - self.nodes[j].y
                    dist = math.hypot(ux, uy) or 0.01
                    force = (k * k) / dist
                    fx, fy = ux / dist * force, uy / dist * force
                    dx[i] += fx
                    dy[i] += fy
                    dx[j] -= fx
                    dy[j] -= fy

            for edge in self.edges:
                i, j = index.get(edge.source), index.get(edge.target)
                if i is None or j is None or i == j:
                    continue
                ux = self.nodes[i].x - self.nodes[j].x
                uy = self.nodes[i].y - self.nodes[j].y
                dist = math.hypot(ux, uy) or 0.01
                force = (dist * dist) / k
                fx, fy = ux / dist * force, uy / dist * force
                dx[i] -= fx
                dy[i] -= fy
                dx[j] += fx
                dy[j] += fy

            for i, node in enumerate(self.nodes):
                disp = math.hypot(dx[i], dy[i]) or 0.01
                step = min(disp, temperature)
                node.x = min(width - 40, max(40, node.x + dx[i] / disp * step))
                node.y = min(height - 30, max(30, node.y + dy[i] / disp * step))

            temperature = max(0.01, temperature - cooling)
        return self

    # ---------------------------------------------------------------- exports
    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    def to_mermaid(self) -> str:
        """Render as a Mermaid ``graph LR`` block (GitHub renders this natively)."""
        lines = ["graph LR"]
        for node in self.nodes:
            safe = node.label.replace('"', "'")
            shape = f'(["{safe}"])' if node.kind == "source" else f'["{safe}"]'
            lines.append(f"    {_mid(node.id)}{shape}")
        for edge in self.edges:
            label = f"{edge.label} · {edge.confidence:.2f}"
            arrow = "-.->" if edge.kind == "contradiction" else "-->"
            lines.append(f"    {_mid(edge.source)} {arrow}|\"{label}\"| {_mid(edge.target)}")
        for edge in self.edges:
            if edge.kind == "contradiction":
                lines.append("    linkStyle default stroke-width:2px")
                break
        return "\n".join(lines)

    def to_dot(self) -> str:
        """Render as Graphviz DOT."""
        lines = ["digraph anchor {", '  rankdir=LR;', '  node [shape=box, style=rounded];']
        for node in self.nodes:
            shape = "ellipse" if node.kind == "source" else "box"
            lines.append(f'  "{node.id}" [label="{node.label}", shape={shape}];')
        for edge in self.edges:
            style = "dashed" if edge.kind == "contradiction" else "solid"
            lines.append(
                f'  "{edge.source}" -> "{edge.target}" '
                f'[label="{edge.label} ({edge.confidence:.2f})", style={style}];'
            )
        lines.append("}")
        return "\n".join(lines)


def _mid(raw: str) -> str:
    """Mermaid-safe node identifier."""
    return "n_" + "".join(ch if ch.isalnum() else "_" for ch in raw)


def build_graph(store: SQLiteStore, namespace: str = "default", *,
                include_inactive: bool = False,
                include_sources: bool = False,
                max_beliefs: int = 250) -> BeliefGraph:
    """Project the beliefs in ``namespace`` into a :class:`BeliefGraph`.

    Subjects and objects become nodes; each belief becomes a confidence-weighted
    edge. Open contradictions add dashed edges between the rival objects, and
    ``include_sources`` adds provenance nodes so you can *see* which source is
    propping up which claim.
    """
    beliefs = store.all_beliefs(namespace, include_inactive=True)
    if not include_inactive:
        beliefs = [b for b in beliefs if b.status in _LIVE]
    beliefs.sort(key=lambda b: (-b.raw_confidence, b.id))
    beliefs = beliefs[:max_beliefs]

    graph = BeliefGraph(namespace=namespace)
    nodes: dict[str, Node] = {}
    by_id: dict[str, Belief] = {b.id: b for b in beliefs}

    def touch(key: str, label: str, kind: str = "entity") -> Node:
        node = nodes.get(key)
        if node is None:
            node = nodes[key] = Node(id=key, label=label, kind=kind)
        node.weight += 1.0
        return node

    for belief in beliefs:
        subject = touch(f"e:{belief.subject}", belief.subject)
        target = touch(f"e:{belief.object}", belief.object)
        prefix = "not " if belief.polarity == Polarity.NEGATIVE else ""
        graph.edges.append(Edge(
            source=subject.id, target=target.id,
            label=f"{prefix}{belief.predicate}", kind="belief",
            confidence=belief.raw_confidence, status=belief.status.value,
            belief_id=belief.id, polarity=belief.polarity.value,
        ))
        if include_sources:
            for prov in belief.provenance:
                name = prov.source_id or prov.agent_id or prov.source_type.value
                src = touch(f"s:{name}", name, kind="source")
                graph.edges.append(Edge(
                    source=src.id, target=target.id, label="attests",
                    kind="attests", confidence=prov.trust,
                    status=belief.status.value, belief_id=belief.id,
                ))

    for con in store.contradictions(namespace, ContradictionStatus.OPEN):
        a, b = by_id.get(con.belief_a), by_id.get(con.belief_b)
        if a is None or b is None:
            continue
        graph.edges.append(Edge(
            source=f"e:{a.object}", target=f"e:{b.object}",
            label=f"conflict · {con.kind.value}", kind="contradiction",
            confidence=con.score, status="open", belief_id=con.id,
        ))

    graph.nodes = list(nodes.values())
    return graph

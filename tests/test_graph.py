"""Tests for the belief-graph projection (:mod:`anchor.core.graph`)."""

from __future__ import annotations

from anchor.core.graph import build_graph


def _populate(anchor):
    anchor.observe("Ada", "works_at", "Acme", source_id="hr", trust=0.9)
    anchor.observe("Ada", "lives_in", "Berlin", source_id="chat", trust=0.9)
    anchor.observe("Acme", "based_in", "Berlin", source_id="crm", trust=0.9)


def test_graph_projects_subjects_and_objects_as_nodes(anchor):
    _populate(anchor)
    g = anchor.graph()

    labels = {n.label for n in g.nodes}
    assert labels == {"Ada", "Acme", "Berlin"}
    assert len(g.edges) == 3
    assert {e.label for e in g.edges} == {"works_at", "lives_in", "based_in"}


def test_shared_entities_are_merged_into_one_node(anchor):
    _populate(anchor)
    g = anchor.graph()

    berlin = next(n for n in g.nodes if n.label == "Berlin")
    incoming = [e for e in g.edges if e.target == berlin.id]
    assert len(incoming) == 2  # Ada lives_in Berlin, Acme based_in Berlin


def test_open_contradictions_become_dashed_edges(anchor):
    anchor.observe("Ada", "lives_in", "Berlin", source_id="chat", trust=0.9)
    anchor.observe("Ada", "lives_in", "Munich", source_id="chat", trust=0.95)

    g = anchor.graph()
    conflicts = [e for e in g.edges if e.kind == "contradiction"]
    assert len(conflicts) == 1
    assert {conflicts[0].source, conflicts[0].target} == {"e:Berlin", "e:Munich"}


def test_resolved_contradictions_leave_the_graph(anchor):
    anchor.observe("Ada", "lives_in", "Berlin", source_id="chat", trust=0.9)
    anchor.observe("Ada", "lives_in", "Munich", source_id="chat", trust=0.95)
    con = anchor.open_contradictions()[0]
    anchor.resolve_contradiction(con.id, strategy="newest", actor="test")

    g = anchor.graph()
    assert not [e for e in g.edges if e.kind == "contradiction"]
    # The superseded loser is hidden by default but retained in history.
    assert "Berlin" not in {n.label for n in g.nodes}
    assert "Berlin" in {n.label for n in anchor.graph(include_inactive=True).nodes}


def test_source_nodes_are_opt_in(anchor):
    _populate(anchor)
    assert not [n for n in anchor.graph().nodes if n.kind == "source"]

    g = anchor.graph(include_sources=True)
    assert {n.label for n in g.nodes if n.kind == "source"} == {"hr", "chat", "crm"}


def test_layout_is_deterministic_and_in_bounds(anchor):
    _populate(anchor)
    a = build_graph(anchor.store).layout(width=800, height=400)
    b = build_graph(anchor.store).layout(width=800, height=400)

    assert [(n.x, n.y) for n in a.nodes] == [(n.x, n.y) for n in b.nodes]
    assert all(0 <= n.x <= 800 and 0 <= n.y <= 400 for n in a.nodes)


def test_empty_and_single_node_layouts_do_not_crash(anchor):
    assert anchor.graph().layout().nodes == []
    anchor.observe("Ada", "likes", "tea", source_id="chat", trust=0.9)
    g = anchor.graph().layout(width=600, height=300)
    assert len(g.nodes) == 2


def test_negative_polarity_is_labelled(anchor):
    anchor.observe("Ada", "eats", "meat", polarity="negative", source_id="chat", trust=0.9)
    g = anchor.graph()
    assert any(e.label == "not eats" for e in g.edges)


def test_mermaid_and_dot_exports(anchor):
    _populate(anchor)
    g = anchor.graph()

    mermaid = g.to_mermaid()
    assert mermaid.startswith("graph LR")
    assert "works_at" in mermaid
    assert " " not in mermaid.split("\n")[1].split("[")[0].strip()  # safe identifiers

    dot = g.to_dot()
    assert dot.startswith("digraph anchor {") and dot.rstrip().endswith("}")
    assert '"e:Ada" -> "e:Acme"' in dot


def test_graph_serialises_for_the_api(anchor):
    _populate(anchor)
    payload = anchor.graph().layout().to_dict()

    assert payload["namespace"] == "default"
    assert len(payload["nodes"]) == 3
    assert all({"id", "label", "kind", "x", "y"} <= set(n) for n in payload["nodes"])
    assert all({"source", "target", "label", "confidence"} <= set(e)
               for e in payload["edges"])


def test_max_beliefs_keeps_the_most_confident(anchor):
    for i in range(6):
        anchor.observe(f"s{i}", "rel", f"o{i}", source_id="chat", trust=0.9)
    anchor.observe("s0", "rel", "o0", source_id="calendar", trust=0.95)

    g = build_graph(anchor.store, max_beliefs=2)
    assert len(g.edges) == 2
    assert g.edges[0].source == "e:s0"

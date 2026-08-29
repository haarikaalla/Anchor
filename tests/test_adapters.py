"""Tests for the LLM framework adapters (:mod:`anchor.adapters`)."""

from __future__ import annotations

import json

from anchor.adapters import TOOL_SCHEMAS, AnchorToolkit


def test_schemas_are_valid_json_schema():
    for schema in TOOL_SCHEMAS:
        assert schema["name"].startswith("anchor_")
        assert schema["description"]
        params = schema["parameters"]
        assert params["type"] == "object"
        assert params["additionalProperties"] is False
        assert set(params["required"]) <= set(params["properties"])


def test_openai_and_anthropic_shapes(anchor):
    kit = AnchorToolkit(anchor)

    openai = kit.openai_tools()
    assert all(t["type"] == "function" and "name" in t["function"] for t in openai)

    anthropic = kit.anthropic_tools()
    assert all({"name", "description", "input_schema"} == set(t) for t in anthropic)
    assert len(openai) == len(anthropic) == len(TOOL_SCHEMAS)


def test_dispatch_observe_and_recall(anchor):
    kit = AnchorToolkit(anchor)

    created = kit.dispatch("anchor_observe", {
        "subject": "user", "predicate": "lives_in", "object": "Berlin",
        "source_id": "chat", "trust": 0.9,
    })
    assert created["action"] == "created"
    assert "New belief stored" in created["guidance"]

    recalled = kit.dispatch("anchor_recall", {"query": "where does the user live"})
    assert any(r["belief"]["object"] == "Berlin" for r in recalled["results"])


def test_dispatch_accepts_the_raw_json_string_models_return(anchor):
    kit = AnchorToolkit(anchor)
    result = kit.dispatch("anchor_observe", json.dumps({
        "subject": "user", "predicate": "likes", "object": "tea",
    }))
    assert result["action"] == "created"


def test_contradiction_guidance_tells_the_model_not_to_guess(anchor):
    kit = AnchorToolkit(anchor)
    kit.dispatch("anchor_observe", {"subject": "user", "predicate": "lives_in",
                                    "object": "Berlin", "trust": 0.9})
    conflict = kit.dispatch("anchor_observe", {"subject": "user", "predicate": "lives_in",
                                               "object": "Munich", "trust": 0.9})
    assert conflict["action"] == "contradicted"
    assert "ask the user" in conflict["guidance"]

    open_cons = kit.dispatch("anchor_open_contradictions", {})["contradictions"]
    assert len(open_cons) == 1

    resolved = kit.dispatch("anchor_resolve_contradiction",
                            {"contradiction_id": open_cons[0]["id"], "strategy": "newest"})
    assert resolved["status"] == "resolved"


def test_current_belief_and_explain(anchor):
    kit = AnchorToolkit(anchor)
    kit.dispatch("anchor_observe", {"subject": "user", "predicate": "lives_in",
                                    "object": "Berlin", "trust": 0.9})

    current = kit.dispatch("anchor_current_belief",
                           {"subject": "user", "predicate": "lives_in"})
    assert current["belief"]["object"] == "Berlin"

    explanation = kit.dispatch("anchor_explain", {"belief_id": current["belief"]["id"]})
    assert explanation["reasons"]


def test_current_belief_returns_null_when_unknown(anchor):
    kit = AnchorToolkit(anchor)
    assert kit.dispatch("anchor_current_belief",
                        {"subject": "ghost", "predicate": "is"})["belief"] is None


def test_errors_are_returned_not_raised(anchor):
    kit = AnchorToolkit(anchor)
    assert "error" in kit.dispatch("anchor_nonexistent", {})
    assert "error" in kit.dispatch("anchor_observe", {"subject": "only"})
    assert "error" in kit.dispatch("anchor_observe", "{not json")
    assert "error" in kit.dispatch("anchor_explain", {"belief_id": "bel_missing"})
    assert "error" in kit.dispatch("anchor_recall", ["not", "an", "object"])


def test_read_only_toolkit_hides_and_blocks_writes(anchor):
    anchor.observe("user", "lives_in", "Berlin", source_id="chat", trust=0.9)
    kit = AnchorToolkit(anchor, allow_writes=False)

    names = {s["name"] for s in kit.schemas()}
    assert "anchor_observe" not in names
    assert "anchor_recall" in names

    assert "error" in kit.dispatch("anchor_observe", {
        "subject": "user", "predicate": "lives_in", "object": "Paris"})
    assert kit.dispatch("anchor_recall", {"query": "live"})["results"]


def test_namespace_isolation(anchor):
    kit_a = AnchorToolkit(anchor, namespace="tenant_a")
    kit_b = AnchorToolkit(anchor, namespace="tenant_b")

    kit_a.dispatch("anchor_observe", {"subject": "user", "predicate": "lives_in",
                                      "object": "Berlin", "trust": 0.9})

    assert kit_a.dispatch("anchor_recall", {"query": "live"})["results"]
    assert kit_b.dispatch("anchor_recall", {"query": "live"})["results"] == []


def test_system_prompt_instructs_safe_behaviour():
    prompt = AnchorToolkit.SYSTEM_PROMPT
    assert "anchor_recall" in prompt and "anchor_observe" in prompt
    assert "do NOT pick a side" in prompt

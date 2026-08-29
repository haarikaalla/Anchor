"""Drop-in adapters that expose Anchor as tools to LLM frameworks.

The core of this module is :class:`AnchorToolkit`: a dependency-free bundle of
JSON-Schema tool definitions plus a dispatcher. Because OpenAI, Anthropic,
Gemini and most agent frameworks all speak JSON Schema, one definition covers
them all — the framework-specific helpers below are thin translations.

    from anchor import Anchor
    from anchor.adapters import AnchorToolkit

    kit = AnchorToolkit(Anchor())
    tools = kit.openai_tools()                     # -> pass to chat.completions
    result = kit.dispatch("anchor_observe", {...}) # -> execute a tool call
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..core.engine import Anchor

_STR = {"type": "string"}


def _schema(name: str, description: str, properties: dict[str, Any],
            required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _schema(
        "anchor_observe",
        "Record something the user or a tool asserted, as a (subject, predicate, "
        "object) belief. Reinforces the claim if already known, or flags a "
        "contradiction if it conflicts — it never silently overwrites memory. "
        "Always pass the most specific source_id you have and a trust score.",
        {
            "subject": _STR, "predicate": _STR, "object": _STR,
            "polarity": {"type": "string", "enum": ["positive", "negative"],
                         "description": "Use 'negative' for denials, e.g. 'does not eat meat'."},
            "source_id": {**_STR, "description": "Where this came from: chat, calendar, crm…"},
            "trust": {"type": "number", "minimum": 0, "maximum": 1,
                      "description": "How reliable the source is. Unverified user "
                                     "claims ~0.7, verified systems ~0.95."},
            "namespace": {**_STR, "description": "Tenant/user partition."},
        },
        ["subject", "predicate", "object"],
    ),
    _schema(
        "anchor_recall",
        "Search memory for beliefs relevant to a question. Results are ranked by "
        "semantic relevance x corroboration x recency, so prefer these over your "
        "own assumptions about the user.",
        {
            "query": _STR,
            "top_k": {"type": "integer", "minimum": 1, "maximum": 25},
            "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "namespace": _STR,
        },
        ["query"],
    ),
    _schema(
        "anchor_current_belief",
        "Get the single current truth for one (subject, predicate) slot — for "
        "example the user's current city. Returns null if memory holds no answer.",
        {"subject": _STR, "predicate": _STR, "namespace": _STR},
        ["subject", "predicate"],
    ),
    _schema(
        "anchor_explain",
        "Explain why a belief is held: evidence per source, confidence interval, "
        "audit timeline and decay projection. Use this before acting on a belief "
        "that matters, or when the user challenges what you remember.",
        {"belief_id": _STR},
        ["belief_id"],
    ),
    _schema(
        "anchor_open_contradictions",
        "List unresolved conflicts in memory. Surface these to the user instead of "
        "guessing which version is true.",
        {"namespace": _STR},
        [],
    ),
    _schema(
        "anchor_resolve_contradiction",
        "Resolve a contradiction once you know which claim holds. The losing claim "
        "is superseded and kept for history, never deleted.",
        {
            "contradiction_id": _STR,
            "strategy": {"type": "string",
                         "enum": ["newest", "reliable", "highest_confidence",
                                  "keep_new", "keep_existing", "keep_both", "manual"]},
            "winner_id": _STR,
            "actor": _STR,
        },
        ["contradiction_id"],
    ),
    _schema(
        "anchor_retract",
        "Withdraw a belief the user says is wrong. It stops being recalled but "
        "stays in the tamper-evident audit trail.",
        {"belief_id": _STR, "reason": _STR},
        ["belief_id"],
    ),
]


class AnchorToolkit:
    """Bind an :class:`~anchor.Anchor` instance to a set of callable LLM tools."""

    def __init__(self, anchor: Anchor, *, namespace: str = "default",
                 default_source_id: str = "agent", allow_writes: bool = True) -> None:
        self.anchor = anchor
        self.namespace = namespace
        self.default_source_id = default_source_id
        self.allow_writes = allow_writes

    # ------------------------------------------------------------- schemas
    def schemas(self) -> list[dict[str, Any]]:
        """Raw JSON-Schema definitions (framework-neutral)."""
        writes = {"anchor_observe", "anchor_resolve_contradiction", "anchor_retract"}
        return [s for s in TOOL_SCHEMAS if self.allow_writes or s["name"] not in writes]

    def openai_tools(self) -> list[dict[str, Any]]:
        """Schemas in OpenAI ``tools=[...]`` format."""
        return [{"type": "function", "function": s} for s in self.schemas()]

    def anthropic_tools(self) -> list[dict[str, Any]]:
        """Schemas in Anthropic ``tools=[...]`` format."""
        return [
            {"name": s["name"], "description": s["description"],
             "input_schema": s["parameters"]}
            for s in self.schemas()
        ]

    # ------------------------------------------------------------ dispatch
    def _handlers(self) -> dict[str, Callable[..., Any]]:
        a = self.anchor

        def observe(subject: str, predicate: str, object: str, polarity: str = "positive",
                    source_id: str = "", trust: float | None = None,
                    namespace: str = "") -> dict[str, Any]:
            result = a.observe(
                subject, predicate, object, polarity=polarity,
                namespace=namespace or self.namespace, source_type="agent",
                source_id=source_id or self.default_source_id, trust=trust,
            )
            payload = result.to_dict()
            payload["guidance"] = {
                "created": "New belief stored.",
                "reinforced": "Already known — confidence increased.",
                "contradicted": "Conflicts with an existing belief. Nothing was "
                                "overwritten; ask the user which is true, then call "
                                "anchor_resolve_contradiction.",
                "coexists": "Stored alongside existing values (multi-valued predicate).",
            }.get(result.action, "")
            return payload

        def recall(query: str, top_k: int = 5, min_confidence: float = 0.0,
                   namespace: str = "") -> dict[str, Any]:
            items = a.recall(query, namespace=namespace or self.namespace,
                             top_k=top_k, min_confidence=min_confidence)
            return {"results": [i.to_dict() for i in items]}

        def current_belief(subject: str, predicate: str,
                           namespace: str = "") -> dict[str, Any]:
            belief = a.current_belief(subject, predicate,
                                      namespace=namespace or self.namespace)
            return {"belief": belief.to_dict() if belief else None}

        def explain(belief_id: str) -> dict[str, Any]:
            return a.explain(belief_id).to_dict()

        def open_contradictions(namespace: str = "") -> dict[str, Any]:
            cons = a.open_contradictions(namespace or self.namespace)
            return {"contradictions": [c.to_dict() for c in cons]}

        def resolve(contradiction_id: str, strategy: str = "reliable",
                    winner_id: str = "", actor: str = "agent") -> dict[str, Any]:
            con = a.resolve_contradiction(contradiction_id, strategy=strategy,
                                          winner_id=winner_id or None, actor=actor)
            return con.to_dict()

        def retract(belief_id: str, reason: str = "") -> dict[str, Any]:
            return a.retract(belief_id, actor="agent", reason=reason).to_dict()

        return {
            "anchor_observe": observe,
            "anchor_recall": recall,
            "anchor_current_belief": current_belief,
            "anchor_explain": explain,
            "anchor_open_contradictions": open_contradictions,
            "anchor_resolve_contradiction": resolve,
            "anchor_retract": retract,
        }

    def dispatch(self, name: str, arguments: dict[str, Any] | str) -> dict[str, Any]:
        """Execute a tool call by name.

        ``arguments`` accepts the raw JSON string that model APIs return, so a
        tool-call loop is a one-liner. Unknown tools and bad arguments come back
        as ``{"error": ...}`` rather than raising, which keeps the agent loop
        alive and lets the model correct itself.
        """
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                return {"error": f"invalid JSON arguments: {exc}"}
        if not isinstance(arguments, dict):
            return {"error": "arguments must be a JSON object"}

        handlers = self._handlers()
        if not self.allow_writes:
            handlers = {k: v for k, v in handlers.items()
                        if k in {"anchor_recall", "anchor_current_belief",
                                 "anchor_explain", "anchor_open_contradictions"}}
        handler = handlers.get(name)
        if handler is None:
            return {"error": f"unknown tool {name!r}"}
        try:
            return handler(**arguments)
        except TypeError as exc:
            return {"error": f"bad arguments for {name}: {exc}"}
        except KeyError as exc:
            return {"error": f"not found: {exc}"}
        except Exception as exc:  # keep the agent loop alive
            return {"error": f"{exc.__class__.__name__}: {exc}"}

    # -------------------------------------------------------------- prompt
    SYSTEM_PROMPT = (
        "You have a persistent belief memory (Anchor). Before answering anything "
        "about the user, call anchor_recall. After learning a durable fact, call "
        "anchor_observe with an honest trust score. If a tool reports "
        "'contradicted', do NOT pick a side yourself: tell the user what memory "
        "already held, ask which is correct, then resolve it. Never assert a "
        "remembered fact as certain if anchor_explain shows low confidence."
    )

    def langchain_tools(self) -> list[Any]:
        """Wrap the toolkit as LangChain ``StructuredTool`` objects.

        Imported lazily so LangChain stays an optional dependency.
        """
        try:
            from langchain_core.tools import StructuredTool  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "langchain_core is required for langchain_tools(); "
                'install it with: pip install "anchor-memory[langchain]"'
            ) from exc

        handlers = self._handlers()
        tools = []
        for schema in self.schemas():
            name = schema["name"]
            tools.append(StructuredTool.from_function(
                func=handlers[name], name=name, description=schema["description"],
            ))
        return tools


__all__ = ["TOOL_SCHEMAS", "AnchorToolkit"]

"""Anchor MCP server.

Exposes the Anchor memory engine over the Model Context Protocol so that any
MCP-capable agent framework (LangGraph, CrewAI, AutoGen, custom clients) can use
persistent belief memory as a shared tool — independently of Anchor's own
internals.

Run with::

    anchor-mcp                      # stdio transport (default)
    python -m anchor.mcp.server

Requires the optional ``mcp`` dependency: ``pip install "anchor-memory[mcp]"``.
"""

from __future__ import annotations

import os
from typing import Any

from ..config import AnchorConfig
from ..core.engine import Anchor


def build_server(anchor: Anchor | None = None):
    """Construct an MCP server bound to an Anchor instance.

    Supports both MCP SDK v1 (``FastMCP``) and v2 (``MCPServer``); the decorator
    and ``run`` APIs are compatible across both.
    """
    Server = None
    try:  # MCP SDK v1
        from mcp.server.fastmcp import FastMCP as Server  # type: ignore
    except Exception:
        try:  # MCP SDK v2 (FastMCP was renamed to MCPServer)
            from mcp.server.mcpserver import MCPServer as Server  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise SystemExit(
                "The MCP server requires the 'mcp' package. "
                "Install it with: pip install \"anchor-memory[mcp]\""
            ) from exc

    engine = anchor or Anchor(AnchorConfig(db_path=os.environ.get("ANCHOR_DB", "anchor.db")))
    mcp = Server("anchor")

    @mcp.tool()
    def anchor_observe(
        subject: str,
        predicate: str,
        object: str,
        statement: str = "",
        polarity: str = "positive",
        namespace: str = "default",
        source_type: str = "agent",
        source_id: str = "",
        agent_id: str = "",
        session_id: str = "",
        trust: float = 0.8,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Record a belief/observation. Conflicting claims are NOT overwritten;
        they surface as contradictions for later resolution. Returns the action
        taken (created|reinforced|contradicted|coexists) and any contradictions."""
        result = engine.observe(
            subject, predicate, object,
            statement=statement or None, polarity=polarity, namespace=namespace,
            source_type=source_type, source_id=source_id, agent_id=agent_id,
            session_id=session_id, trust=trust, tags=tags or [],
        )
        return result.to_dict()

    @mcp.tool()
    def anchor_recall(query: str, namespace: str = "default", top_k: int = 5,
                      min_confidence: float = 0.0) -> dict[str, Any]:
        """Retrieve the most relevant, reliable and recent beliefs for a query."""
        items = engine.recall(query, namespace=namespace, top_k=top_k,
                              min_confidence=min_confidence)
        return {"results": [it.to_dict() for it in items]}

    @mcp.tool()
    def anchor_list_beliefs(namespace: str = "default",
                            include_inactive: bool = False) -> dict[str, Any]:
        """List stored beliefs in a namespace."""
        return {"beliefs": [b.to_dict() for b in
                            engine.list_beliefs(namespace, include_inactive)]}

    @mcp.tool()
    def anchor_current_belief(subject: str, predicate: str,
                              namespace: str = "default") -> dict[str, Any]:
        """Return what is currently believed about a subject/predicate slot,
        or null if nothing is known. Use this before asserting a fact."""
        belief = engine.current_belief(subject, predicate, namespace=namespace)
        return {"belief": belief.to_dict() if belief else None}

    @mcp.tool()
    def anchor_explain(belief_id: str) -> dict[str, Any]:
        """Explain WHY a belief is held: confidence and its credible interval,
        per-source evidence attribution, decay projection, plain-English reasons
        and the full audit timeline. Use this to justify an answer to a user."""
        return engine.explain(belief_id).to_dict()

    @mcp.tool()
    def anchor_graph(namespace: str = "default", include_sources: bool = False,
                     include_inactive: bool = False) -> dict[str, Any]:
        """Return the belief knowledge graph (entities, relations,
        contradiction edges) as nodes/edges plus a Mermaid rendering."""
        g = engine.graph(namespace, include_inactive=include_inactive,
                         include_sources=include_sources)
        return {**g.to_dict(), "mermaid": g.to_mermaid()}

    @mcp.tool()
    def anchor_open_contradictions(namespace: str = "default") -> dict[str, Any]:
        """List unresolved contradictions awaiting confirmation/resolution."""
        return {"contradictions": [c.to_dict() for c in
                                   engine.open_contradictions(namespace)]}

    @mcp.tool()
    def anchor_resolve_contradiction(contradiction_id: str, strategy: str = "manual",
                                     winner_id: str = "", actor: str = "human",
                                     note: str = "") -> dict[str, Any]:
        """Resolve a contradiction. strategy in {keep_new, keep_existing,
        highest_confidence, newest, keep_both, manual}."""
        con = engine.resolve_contradiction(
            contradiction_id, strategy=strategy,
            winner_id=winner_id or None, actor=actor, note=note)
        return con.to_dict()

    @mcp.tool()
    def anchor_retract(belief_id: str, actor: str = "human",
                       reason: str = "") -> dict[str, Any]:
        """Retract (soft-delete) a belief. It is preserved in the audit trail."""
        return engine.retract(belief_id, actor=actor, reason=reason).to_dict()

    @mcp.tool()
    def anchor_apply_decay(namespace: str = "default") -> dict[str, Any]:
        """Age memory: decay un-reinforced beliefs and auto-retire faded ones."""
        return engine.apply_decay(namespace)

    @mcp.tool()
    def anchor_audit(namespace: str = "default", belief_id: str = "",
                     limit: int = 100) -> dict[str, Any]:
        """Return the tamper-evident audit history for a namespace/belief."""
        events = engine.audit(namespace, belief_id or None, limit)
        return {"events": [e.to_dict() for e in events],
                "chain_intact": engine.verify_audit(namespace)}

    @mcp.tool()
    def anchor_stats(namespace: str = "default") -> dict[str, Any]:
        """Return memory health metrics for a namespace."""
        return engine.stats(namespace)

    return mcp


def main() -> None:
    server = build_server()
    server.run()  # stdio transport by default


if __name__ == "__main__":
    main()

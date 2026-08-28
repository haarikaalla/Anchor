# Integrating Anchor with agent frameworks

Anchor's engine is framework-independent. Every integration is a thin adapter
over three calls:

- `observe(...)` — write a fact (with provenance & trust)
- `recall(query, ...)` — read relevant, reliable, recent beliefs
- `current_belief(subject, predicate)` — the single current truth for a slot

There are two integration paths:

1. **MCP** (recommended, framework-agnostic) — run `anchor-mcp` and connect any
   MCP-capable host/agent. Tools: `anchor_observe`, `anchor_recall`,
   `anchor_open_contradictions`, `anchor_resolve_contradiction`, etc.
2. **In-process Python** — import `Anchor` directly.

---

## LangGraph

```python
from anchor import Anchor

mem = Anchor()

def memory_node(state):
    # 1) recall context for the current user turn
    hits = mem.recall(state["input"], namespace=state["user_id"], top_k=6)
    state["memory_context"] = [h.belief.statement for h in hits]
    return state

def persist_node(state):
    # 2) write structured facts the model extracted this turn
    for (s, p, o) in state.get("extracted_triples", []):
        r = mem.observe(s, p, o, namespace=state["user_id"],
                        source_type="agent", source_id=state["run_id"], trust=0.7)
        if r.action == "contradicted":
            # surface to a human-review queue instead of overwriting
            state.setdefault("needs_confirmation", []).extend(
                c.id for c in r.contradictions)
    return state
```

Wire `memory_node` before your LLM node and `persist_node` after it.

## CrewAI

Give a crew a shared Anchor instance and expose two tools:

```python
from crewai_tools import tool
from anchor import Anchor

mem = Anchor()

@tool("remember")
def remember(subject: str, predicate: str, object: str) -> str:
    r = mem.observe(subject, predicate, object, source_type="agent", trust=0.7)
    return r.action

@tool("recall")
def recall(query: str) -> str:
    return "\n".join(h.belief.statement for h in mem.recall(query, top_k=5))
```

## AutoGen

Register the same `remember` / `recall` functions as callable tools on an
assistant agent, or point AutoGen's MCP client at `anchor-mcp`.

## Custom agents

```python
from anchor import Anchor
mem = Anchor()
context = mem.recall(user_message, namespace=user_id)
# ... call your LLM with `context` ...
mem.observe(subject, predicate, object, namespace=user_id, source_type="agent")
```

---

## Handling contradictions in an agent loop

Anchor never overwrites. When `observe(...)` returns `action == "contradicted"`,
you choose the policy:

- **Autonomous update**: `resolve_contradiction(id, strategy="newest")` or
  `strategy="reliable"` (poisoning-robust).
- **Human-in-the-loop**: enqueue `open_contradictions()` for review and call
  `resolve_contradiction(id, strategy="manual", winner_id=...)` on confirmation.

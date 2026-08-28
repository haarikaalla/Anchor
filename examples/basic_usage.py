"""Minimal end-to-end example: an agent that uses Anchor as its memory.

Runs fully offline. Demonstrates the pattern any framework adapter follows:

    recall relevant beliefs  ->  act  ->  observe new facts back into memory

Run: python examples/basic_usage.py
"""

from anchor import Anchor, AnchorConfig


def main() -> None:
    mem = Anchor(AnchorConfig(db_path="example_agent.db", min_sources_for_active=1))
    ns = "alice"

    # --- Turn 1: the user tells the agent some facts ------------------------
    mem.observe("alice", "role", "backend engineer", namespace=ns,
                source_type="user", source_id="turn1", trust=0.9)
    mem.observe("alice", "prefers", "dark mode", namespace=ns,
                source_type="user", source_id="turn1", trust=0.8)

    # --- Turn 2: the agent recalls context before responding ----------------
    ctx = mem.recall("what should I know about alice?", namespace=ns, top_k=5)
    print("Context the agent would condition on:")
    for it in ctx:
        print(f"  - {it.belief.statement}  (conf {it.confidence:.2f})")

    # --- Turn 3: the user contradicts an earlier fact -----------------------
    r = mem.observe("alice", "role", "engineering manager", namespace=ns,
                    source_type="user", source_id="turn3", trust=0.95)
    print(f"\nNew claim result: {r.action}")
    if r.contradictions:
        con = r.contradictions[0]
        print(f"  Contradiction detected ({con.kind.value}). Asking for confirmation...")
        # A real agent would ask the user; here we auto-confirm the update.
        mem.resolve_contradiction(con.id, strategy="newest", actor="alice",
                                  note="promotion confirmed")

    print(f"\nCurrent role: {mem.current_belief('alice', 'role', namespace=ns).object}")
    print(f"Audit chain intact: {mem.verify_audit(ns)}")
    mem.close()


if __name__ == "__main__":
    main()

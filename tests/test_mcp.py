import asyncio

import pytest

mcp = pytest.importorskip("mcp")  # skip entire module if MCP SDK not installed

from anchor import Anchor, AnchorConfig  # noqa: E402
from anchor.mcp.server import build_server  # noqa: E402


@pytest.fixture()
def server(tmp_path):
    anchor = Anchor(AnchorConfig(db_path=str(tmp_path / "mcp.db"),
                                 min_sources_for_active=1))
    return build_server(anchor)


def test_all_tools_registered(server):
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert {
        "anchor_observe", "anchor_recall", "anchor_open_contradictions",
        "anchor_resolve_contradiction", "anchor_audit", "anchor_stats",
    } <= names


def test_observe_and_stats_via_tools(server):
    async def scenario():
        await server.call_tool("anchor_observe", {
            "subject": "user", "predicate": "lives_in", "object": "Berlin",
            "source_id": "s1", "trust": 0.9,
        })
        return await server.call_tool("anchor_stats", {"namespace": "default"})

    result = asyncio.run(scenario())
    assert result is not None

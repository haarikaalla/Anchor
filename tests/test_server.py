"""Tests for the HTTP API, dashboard and event stream (:mod:`anchor.server`)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from anchor.server.api import build_server, seed_demo


@pytest.fixture()
def server(anchor):
    """A live server on an ephemeral port, torn down after each test."""
    httpd = build_server(anchor, port=0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(base, path, token=""):
    req = urllib.request.Request(base + path)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read().decode()


def _post(base, path, payload, token="", extra_headers=None):
    headers = {"Content-Type": "application/json", **(extra_headers or {})}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


def test_health_and_dashboard(server):
    status, body = _get(server, "/health")
    assert status == 200 and json.loads(body)["status"] == "ok"

    status, html = _get(server, "/")
    assert status == 200
    assert "<!DOCTYPE html>" in html and "Anchor" in html


def test_dashboard_never_injects_data_as_html(server):
    """Belief text is untrusted input; the UI must only use textContent."""
    _, html = _get(server, "/")
    assert "innerHTML" not in html
    assert "eval(" not in html


def test_observe_recall_roundtrip(server):
    status, result = _post(server, "/api/observe", {
        "subject": "user", "predicate": "lives_in", "object": "Berlin",
        "source_id": "chat", "trust": 0.9,
    })
    assert status == 200 and result["action"] == "created"

    _, body = _get(server, "/api/recall?q=where%20does%20the%20user%20live")
    statements = [r["belief"]["statement"] for r in json.loads(body)["results"]]
    assert "user lives_in Berlin" in statements


def test_conflicting_observation_is_reported_not_overwritten(server):
    _post(server, "/api/observe", {"subject": "user", "predicate": "lives_in",
                                   "object": "Berlin", "trust": 0.9})
    _, result = _post(server, "/api/observe", {"subject": "user", "predicate": "lives_in",
                                               "object": "Munich", "trust": 0.9})
    assert result["action"] == "contradicted"

    _, body = _get(server, "/api/contradictions")
    cons = json.loads(body)["contradictions"]
    assert len(cons) == 1
    assert {cons[0]["statement_a"], cons[0]["statement_b"]} == {
        "user lives_in Munich", "user lives_in Berlin"}

    _, resolved = _post(server, "/api/resolve",
                        {"contradiction_id": cons[0]["id"], "strategy": "newest"})
    assert resolved["status"] == "resolved"
    _, body = _get(server, "/api/contradictions")
    assert json.loads(body)["contradictions"] == []


def test_explain_endpoint(server, anchor):
    result = anchor.observe("user", "lives_in", "Berlin", source_id="chat", trust=0.9)
    _, body = _get(server, f"/api/explain/{result.belief.id}")
    payload = json.loads(body)
    assert payload["belief"]["id"] == result.belief.id
    assert payload["reasons"] and payload["decay_projection"]


def test_graph_endpoint_returns_positioned_nodes(server, anchor):
    anchor.observe("Ada", "works_at", "Acme", source_id="hr", trust=0.9)
    _, body = _get(server, "/api/graph")
    graph = json.loads(body)
    assert len(graph["nodes"]) == 2
    assert all(n["x"] and n["y"] for n in graph["nodes"])


def test_stats_and_prometheus_metrics(server, anchor):
    anchor.observe("user", "lives_in", "Berlin", source_id="chat", trust=0.9)

    _, body = _get(server, "/api/stats")
    assert json.loads(body)["audit_intact"] is True

    _, metrics = _get(server, "/metrics")
    assert "# TYPE anchor_beliefs gauge" in metrics
    assert 'anchor_audit_chain_intact{namespace="default"} 1' in metrics


def test_seed_and_decay_endpoints(server):
    _, seeded = _post(server, "/api/seed", {})
    assert seeded["seeded"] > 0 and seeded["open_contradictions"] >= 1

    _, decayed = _post(server, "/api/decay", {})
    assert set(decayed) == {"decayed", "retired"}


def test_retract_endpoint(server, anchor):
    result = anchor.observe("user", "likes", "tea", source_id="chat", trust=0.9)
    _, body = _post(server, "/api/retract",
                    {"belief_id": result.belief.id, "reason": "user corrected me"})
    assert body["status"] == "retracted"


def test_missing_fields_and_unknown_ids_are_client_errors(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(server, "/api/observe", {"subject": "user"})
    assert exc.value.code == 400

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server, "/api/explain/bel_missing")
    assert exc.value.code == 404

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server, "/api/nope")
    assert exc.value.code == 404


def test_recall_requires_a_query(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server, "/api/recall")
    assert exc.value.code == 400


def test_bearer_token_guards_the_api(anchor):
    httpd = build_server(anchor, port=0, token="s3cret")
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        # Health and the dashboard stay open; the API does not.
        assert _get(base, "/health")[0] == 200

        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(base, "/api/stats")
        assert exc.value.code == 401

        with pytest.raises(urllib.error.HTTPError):
            _get(base, "/api/stats", token="wrong")

        assert _get(base, "/api/stats", token="s3cret")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_tenant_tokens_cannot_cross_namespace_boundaries(anchor):
    httpd = build_server(
        anchor,
        port=0,
        tenant_tokens={"tenant-a": "token-a", "tenant-b": "token-b"},
    )
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        _, created = _post(
            base,
            "/api/observe",
            {"subject": "customer", "predicate": "tier", "object": "gold"},
            token="token-a",
        )
        assert created["belief"]["namespace"] == "tenant-a"
        assert _get(base, "/api/beliefs", token="token-a")[0] == 200

        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(base, "/api/beliefs?namespace=tenant-a", token="token-b")
        assert exc.value.code == 403

        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(base, f"/api/belief/{created['belief']['id']}", token="token-b")
        assert exc.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_tenant_token_configuration_rejects_ambiguous_credentials(anchor):
    with pytest.raises(ValueError, match="unique"):
        build_server(anchor, port=0, tenant_tokens={"tenant-a": "same", "tenant-b": "same"})
    with pytest.raises(ValueError, match="global"):
        build_server(anchor, port=0, token="shared", tenant_tokens={"tenant-a": "shared"})
    with pytest.raises(ValueError, match="non-empty"):
        build_server(anchor, port=0, tenant_tokens={"tenant-a": ""})


def test_http_idempotency_key_returns_original_result(server):
    payload = {"subject": "job", "predicate": "status", "object": "running"}
    headers = {"Idempotency-Key": "job-event-1"}
    _, first = _post(server, "/api/observe", payload, extra_headers=headers)
    _, replay = _post(server, "/api/observe", payload, extra_headers=headers)
    assert replay["belief"]["id"] == first["belief"]["id"]

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(
            server,
            "/api/observe",
            {**payload, "object": "failed"},
            extra_headers=headers,
        )
    assert exc.value.code == 409


def test_oversized_body_is_rejected(server):
    """An oversized upload must get a real 413 back, not a reset connection.

    The server has to drain the in-flight body before replying; otherwise the
    client is still writing when the response lands and never reads the status.
    """
    payload = json.dumps({"subject": "x" * 400_000, "predicate": "p", "object": "o"})
    req = urllib.request.Request(server + "/api/observe", data=payload.encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 413

    # The server must survive the refusal and keep serving.
    assert _get(server, "/api/stats")[0] == 200


def test_event_bus_publishes_audit_events(anchor):
    from anchor.server.api import EventBus

    bus = EventBus(anchor, interval=0.05)
    subscriber = bus.subscribe()
    bus.start()
    try:
        anchor.observe("user", "lives_in", "Berlin", source_id="chat", trust=0.9)
        message = subscriber.get(timeout=5)
        assert message["type"] == "audit"
        assert message["event"]["event_type"] == "created"
    finally:
        bus.stop()
        bus.unsubscribe(subscriber)


def test_seed_demo_leaves_an_open_contradiction(anchor):
    summary = seed_demo(anchor)
    assert summary["action"] == "contradicted"
    assert anchor.open_contradictions()
    assert anchor.verify_audit() is True

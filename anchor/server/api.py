"""Anchor HTTP API + live dashboard server.

A dependency-free control plane for the memory engine, built on
:mod:`http.server`. It exposes:

* a **JSON REST API** over every engine operation (`/api/*`);
* a **Server-Sent Events stream** (`/api/events`) that tails the hash-chained
  audit log, so any UI can watch beliefs form, clash and resolve in real time;
* a **Prometheus exposition endpoint** (`/metrics`);
* the embedded single-file **dashboard** at `/`.

Security posture
----------------
* Binds to ``127.0.0.1`` by default; you must opt in to a public interface.
* Optional global bearer auth (``--token`` / ``ANCHOR_API_TOKEN``) and
  namespace-scoped tenant tokens (``ANCHOR_TENANT_TOKENS``), all compared with
  :func:`hmac.compare_digest`; when configured they guard API and metrics routes.
* Request bodies are size-capped and parsed with :func:`json.loads` only — no
  ``eval``/pickle anywhere.
* No filesystem is served: the dashboard is an in-memory string, so there is no
  path-traversal surface.
* All engine access goes through the parameterised-SQL store.
"""

from __future__ import annotations

import hmac
import json
import os
import queue
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..config import AnchorConfig
from ..core.engine import Anchor, IdempotencyConflict
from .dashboard import DASHBOARD_HTML

MAX_BODY_BYTES = 256 * 1024

# When rejecting an oversized upload we drain a bounded amount of the pending
# body before closing. Without this the client is still writing when we reply,
# the socket resets, and it never gets to read the status code we sent. The
# bound keeps that courtesy from becoming a way to make us read forever.
_DRAIN_LIMIT = MAX_BODY_BYTES
_DRAIN_CHUNK = 64 * 1024


class PayloadTooLarge(ValueError):
    """Raised when a request body exceeds :data:`MAX_BODY_BYTES`."""
_SSE_KEEPALIVE = 15.0


class EventBus:
    """Fan-out of audit events to connected SSE clients.

    A single poller thread tails the store's audit log and pushes new events to
    every subscriber queue. Tailing the log (rather than hooking the engine)
    means changes made by *another* process against the same database — a CLI
    session, the MCP server — also show up live.
    """

    def __init__(self, anchor: Anchor, namespace: str = "default",
                 interval: float = 0.4) -> None:
        self._anchor = anchor
        self._namespace = namespace
        self._interval = interval
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._cursor = anchor.store.latest_audit_seq(namespace)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="anchor-events")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, payload: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            # A slow client is dropped rather than allowed to block the poller.
            with suppress(queue.Full):
                q.put_nowait(payload)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                batch = self._anchor.store.audit_after(self._namespace, self._cursor, 100)
            except Exception:  # store closed or transient lock; keep the poller alive
                batch = []
            for seq, event in batch:
                self._cursor = seq
                self.publish({"type": "audit", "event": event.to_dict()})


def _prometheus(anchor: Anchor, namespace: str) -> str:
    """Render engine + store state in Prometheus text exposition format."""
    stats = anchor.stats(namespace)
    lines = [
        "# HELP anchor_beliefs Number of beliefs by lifecycle status.",
        "# TYPE anchor_beliefs gauge",
    ]
    for status, count in sorted(stats.get("by_status", {}).items()):
        lines.append(f'anchor_beliefs{{namespace="{namespace}",status="{status}"}} {count}')
    lines += [
        "# HELP anchor_open_contradictions Unresolved contradictions.",
        "# TYPE anchor_open_contradictions gauge",
        f'anchor_open_contradictions{{namespace="{namespace}"}} '
        f'{stats.get("open_contradictions", 0)}',
        "# HELP anchor_avg_confidence Mean posterior confidence across beliefs.",
        "# TYPE anchor_avg_confidence gauge",
        f'anchor_avg_confidence{{namespace="{namespace}"}} {stats.get("avg_confidence") or 0.0}',
        "# HELP anchor_audit_chain_intact 1 if the hash chain verifies.",
        "# TYPE anchor_audit_chain_intact gauge",
        f'anchor_audit_chain_intact{{namespace="{namespace}"}} '
        f'{1 if stats.get("audit_intact") else 0}',
    ]
    metrics = stats.get("metrics", {})
    counters = metrics.get("counters", {}) if isinstance(metrics, dict) else {}
    if counters:
        lines += [
            "# HELP anchor_events_total Engine event counters since process start.",
            "# TYPE anchor_events_total counter",
        ]
        for name, value in sorted(counters.items()):
            safe = name.replace(".", "_")
            lines.append(f'anchor_events_total{{namespace="{namespace}",event="{safe}"}} {value}')
    return "\n".join(lines) + "\n"


class AnchorHTTPRequestHandler(BaseHTTPRequestHandler):
    """Routes every Anchor operation over HTTP. One instance per request."""

    server_version = "Anchor"
    sys_version = ""

    # ------------------------------------------------------------- plumbing
    @property
    def anchor(self) -> Anchor:
        return self.server.anchor  # type: ignore[attr-defined]

    def _bus(self, namespace: str) -> EventBus:
        buses = self.server.buses  # type: ignore[attr-defined]
        lock = self.server.buses_lock  # type: ignore[attr-defined]
        with lock:
            bus = buses.get(namespace)
            if bus is None:
                bus = EventBus(self.anchor, namespace)
                buses[namespace] = bus
                bus.start()
        return bus

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter default logging
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _auth_scope(self) -> str | None:
        """Return `*`, an allowed namespace, or None for invalid credentials."""
        token = getattr(self.server, "token", "")
        tenant_tokens = getattr(self.server, "tenant_tokens", {})
        if not token and not tenant_tokens:
            return "*"
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        supplied = header[len(prefix):] if header.startswith(prefix) else ""
        if token and hmac.compare_digest(supplied, token):
            return "*"
        for namespace, tenant_token in tenant_tokens.items():
            if hmac.compare_digest(supplied, tenant_token):
                return str(namespace)
        return None

    def _authorized(self) -> bool:
        return self._auth_scope() is not None

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        origin = getattr(self.server, "cors_origin", "")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, Idempotency-Key",
            )
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload, default=str).encode(), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise PayloadTooLarge(
                f"request body too large ({length} bytes, limit {MAX_BODY_BYTES})"
            )
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def _drain(self, length: int) -> None:
        """Consume up to ``_DRAIN_LIMIT`` bytes of a body we are refusing."""
        remaining = min(length, _DRAIN_LIMIT)
        with suppress(OSError):
            while remaining > 0:
                chunk = self.rfile.read(min(_DRAIN_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)

    @staticmethod
    def _one(params: dict[str, list[str]], key: str, default: str = "") -> str:
        return params.get(key, [default])[0]

    def _ns(self, params: dict[str, list[str]]) -> str:
        scope = getattr(self, "_request_scope", "*")
        default = scope if scope != "*" else getattr(self.server, "namespace", "default")
        namespace = self._one(params, "namespace", default)
        if scope != "*" and namespace != scope:
            raise PermissionError("token is not authorized for this namespace")
        return namespace

    # -------------------------------------------------------------- verbs
    def do_OPTIONS(self) -> None:
        self._send(204, b"", "text/plain")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        if path == "/":
            return self._send(200, DASHBOARD_HTML.encode(), "text/html; charset=utf-8")
        if path == "/health":
            return self._json({"status": "ok", "version": _version()})
        if path == "/metrics":
            scope = self._auth_scope()
            if scope is None:
                return self._error(401, "unauthorized")
            self._request_scope = scope
            try:
                namespace = self._ns(params)
            except PermissionError as exc:
                return self._error(403, str(exc))
            body = _prometheus(self.anchor, namespace).encode()
            return self._send(200, body, "text/plain; version=0.0.4; charset=utf-8")

        if not path.startswith("/api/"):
            return self._error(404, "not found")
        scope = self._auth_scope()
        if scope is None:
            return self._error(401, "unauthorized")
        self._request_scope = scope

        try:
            return self._route_get(path, params)
        except KeyError as exc:
            return self._error(404, str(exc))
        except PermissionError as exc:
            return self._error(403, str(exc))
        except (ValueError, TypeError) as exc:
            return self._error(400, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return self._error(500, f"internal error: {exc.__class__.__name__}")

    def _route_get(self, path: str, params: dict[str, list[str]]) -> None:
        ns = self._ns(params)
        a = self.anchor

        if path == "/api/stats":
            stats = a.stats(ns)
            stats["namespace"] = ns
            return self._json(stats)

        if path == "/api/beliefs":
            include = self._one(params, "include_inactive") in ("1", "true", "yes")
            beliefs = a.list_beliefs(ns, include_inactive=include)
            beliefs.sort(key=lambda b: (-b.raw_confidence, b.subject))
            return self._json({"beliefs": [b.to_dict() for b in beliefs]})

        if path == "/api/contradictions":
            out = []
            for c in a.open_contradictions(ns):
                ba, bb = a.get(c.belief_a), a.get(c.belief_b)
                out.append({
                    **c.to_dict(),
                    "statement_a": ba.statement if ba else "?",
                    "statement_b": bb.statement if bb else "?",
                    "confidence_a": ba.raw_confidence if ba else 0.0,
                    "confidence_b": bb.raw_confidence if bb else 0.0,
                })
            return self._json({"contradictions": out})

        if path == "/api/recall":
            query = self._one(params, "q")
            if not query:
                raise ValueError("missing 'q' parameter")
            top_k = max(1, min(50, int(self._one(params, "top_k", "8"))))
            items = a.recall(query, namespace=ns, top_k=top_k)
            return self._json({"query": query, "results": [i.to_dict() for i in items]})

        if path == "/api/audit":
            limit = max(1, min(1000, int(self._one(params, "limit", "60"))))
            events = a.audit(ns, self._one(params, "belief") or None, limit)
            return self._json({
                "events": [e.to_dict() for e in events],
                "chain_intact": a.verify_audit(ns),
            })

        if path == "/api/graph":
            graph = a.graph(
                ns,
                include_inactive=self._one(params, "include_inactive") in ("1", "true"),
                include_sources=self._one(params, "include_sources") in ("1", "true"),
            )
            graph.layout(width=float(self._one(params, "width", "900")),
                         height=float(self._one(params, "height", "520")))
            return self._json(graph.to_dict())

        if path.startswith("/api/belief/"):
            bid = path.rsplit("/", 1)[-1]
            belief = a.get(bid)
            if belief is None or belief.namespace != ns:
                raise KeyError(f"unknown belief {bid}")
            return self._json(belief.to_dict())

        if path.startswith("/api/explain/"):
            bid = path.rsplit("/", 1)[-1]
            belief = a.get(bid)
            if belief is None or belief.namespace != ns:
                raise KeyError(f"unknown belief {bid}")
            return self._json(a.explain(bid).to_dict())

        if path == "/api/events":
            return self._stream_events(ns)

        raise KeyError("not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not path.startswith("/api/"):
            return self._error(404, "not found")
        scope = self._auth_scope()
        if scope is None:
            return self._error(401, "unauthorized")
        self._request_scope = scope
        try:
            body = self._body()
            requested = str(body.get("namespace") or "")
            if scope != "*":
                if requested and requested != scope:
                    raise PermissionError("token is not authorized for this namespace")
                body["namespace"] = scope
            return self._route_post(path, body)
        except PayloadTooLarge as exc:
            # Drain what we can, then close: the client is mid-write, and
            # replying without draining resets the socket before it can read.
            self._drain(int(self.headers.get("Content-Length") or 0))
            self.close_connection = True
            return self._error(413, str(exc))
        except KeyError as exc:
            return self._error(404, str(exc))
        except PermissionError as exc:
            return self._error(403, str(exc))
        except IdempotencyConflict as exc:
            return self._error(409, str(exc))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._error(400, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return self._error(500, f"internal error: {exc.__class__.__name__}")

    def _route_post(self, path: str, body: dict[str, Any]) -> None:
        a = self.anchor
        ns = str(body.get("namespace") or getattr(self.server, "namespace", "default"))

        if path == "/api/observe":
            for key in ("subject", "predicate", "object"):
                if not body.get(key):
                    raise ValueError(f"missing required field {key!r}")
            trust = body.get("trust")
            result = a.observe(
                str(body["subject"]), str(body["predicate"]), str(body["object"]),
                statement=body.get("statement") or None,
                polarity=str(body.get("polarity") or "positive"),
                namespace=ns,
                source_type=str(body.get("source_type") or "user"),
                source_id=str(body.get("source_id") or "api"),
                source_group=str(body.get("source_group") or ""),
                agent_id=str(body.get("agent_id") or ""),
                session_id=str(body.get("session_id") or ""),
                trust=float(trust) if trust is not None else None,
                tags=list(body.get("tags") or []),
                idempotency_key=self.headers.get("Idempotency-Key", "").strip(),
            )
            return self._json(result.to_dict())

        if path == "/api/resolve":
            cid = str(body.get("contradiction_id") or "")
            if not cid:
                raise ValueError("missing 'contradiction_id'")
            existing = a.store.get_contradiction(cid)
            if existing is None or existing.namespace != ns:
                raise KeyError(f"unknown contradiction {cid}")
            con = a.resolve_contradiction(
                cid,
                strategy=str(body.get("strategy") or "reliable"),
                winner_id=body.get("winner_id") or None,
                actor=str(body.get("actor") or "dashboard"),
                note=str(body.get("note") or ""),
            )
            return self._json(con.to_dict())

        if path == "/api/retract":
            bid = str(body.get("belief_id") or "")
            if not bid:
                raise ValueError("missing 'belief_id'")
            existing_belief = a.get(bid)
            if existing_belief is None or existing_belief.namespace != ns:
                raise KeyError(f"unknown belief {bid}")
            belief = a.retract(bid, actor=str(body.get("actor") or "dashboard"),
                               reason=str(body.get("reason") or ""))
            return self._json(belief.to_dict())

        if path == "/api/decay":
            return self._json(a.apply_decay(ns))

        if path == "/api/seed":
            return self._json(seed_demo(a, namespace=ns))

        raise KeyError("not found")

    # --------------------------------------------------------------- SSE
    def _stream_events(self, namespace: str) -> None:
        bus = self._bus(namespace)
        q = bus.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._security_headers()
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            last = time.time()
            while True:
                try:
                    payload = q.get(timeout=1.0)
                    data = json.dumps(payload, default=str)
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                    last = time.time()
                except queue.Empty:
                    if time.time() - last > _SSE_KEEPALIVE:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        last = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            bus.unsubscribe(q)


def seed_demo(anchor: Anchor, namespace: str = "default") -> dict[str, Any]:
    """Populate a namespace with a small, self-explanatory scenario.

    Deliberately ends with an *open* contradiction so the dashboard's headline
    behaviour — conflicting claims are surfaced, never silently overwritten — is
    visible the moment you open it.
    """
    script = [
        ("user", "lives_in", "Berlin", "chat", 0.9),
        ("user", "lives_in", "Berlin", "calendar", 0.9),
        ("user", "works_at", "Acme", "email", 0.85),
        ("user", "works_at", "Acme", "linkedin", 0.8),
        ("user", "prefers", "dark_mode", "settings", 0.95),
        ("user", "speaks", "German", "chat", 0.7),
        ("user", "speaks", "English", "chat", 0.9),
        ("project", "status", "green", "ci", 0.9),
    ]
    created = 0
    for subject, predicate, obj, source, trust in script:
        anchor.observe(subject, predicate, obj, namespace=namespace,
                       source_id=source, trust=trust)
        created += 1

    # The interesting part: a conflicting relocation, and a poisoning attempt.
    conflict = anchor.observe("user", "lives_in", "Munich", namespace=namespace,
                              source_id="chat", trust=0.95)
    anchor.observe("project", "status", "red", namespace=namespace,
                   source_id="unknown_bot", trust=0.2)
    return {
        "seeded": created + 2,
        "action": conflict.action,
        "open_contradictions": len(anchor.open_contradictions(namespace)),
    }


def _version() -> str:
    from .. import __version__
    return __version__


def build_server(anchor: Anchor | None = None, *, host: str = "127.0.0.1",
                 port: int = 8080, namespace: str = "default",
                 token: str = "", cors_origin: str = "",
                 tenant_tokens: dict[str, str] | None = None,
                 verbose: bool = False) -> ThreadingHTTPServer:
    """Create (but do not start) the Anchor HTTP server."""
    tenants = {str(k).strip(): str(v).strip() for k, v in (tenant_tokens or {}).items()}
    if any(not namespace or not tenant_token for namespace, tenant_token in tenants.items()):
        raise ValueError("tenant namespaces and tokens must be non-empty")
    if len(set(tenants.values())) != len(tenants):
        raise ValueError("each tenant must have a unique bearer token")
    if token and token in tenants.values():
        raise ValueError("the global bearer token must differ from every tenant token")
    engine = anchor or Anchor(AnchorConfig())
    httpd = ThreadingHTTPServer((host, port), AnchorHTTPRequestHandler)
    httpd.daemon_threads = True
    httpd.anchor = engine                      # type: ignore[attr-defined]
    httpd.namespace = namespace                # type: ignore[attr-defined]
    httpd.token = token                        # type: ignore[attr-defined]
    httpd.tenant_tokens = tenants              # type: ignore[attr-defined]
    httpd.cors_origin = cors_origin            # type: ignore[attr-defined]
    httpd.verbose = verbose                    # type: ignore[attr-defined]
    default_bus = EventBus(engine, namespace)
    httpd.bus = default_bus                    # type: ignore[attr-defined]
    httpd.buses = {namespace: default_bus}     # type: ignore[attr-defined]
    httpd.buses_lock = threading.Lock()        # type: ignore[attr-defined]
    return httpd


def serve(anchor: Anchor | None = None, *, host: str = "127.0.0.1", port: int = 8080,
          namespace: str = "default", token: str = "", cors_origin: str = "",
          tenant_tokens: dict[str, str] | None = None,
          open_browser: bool = False, verbose: bool = False) -> None:
    """Run the dashboard + API until interrupted."""
    if not token:
        token = os.environ.get("ANCHOR_API_TOKEN", "")
    if tenant_tokens is None:
        raw_tenant_tokens = os.environ.get("ANCHOR_TENANT_TOKENS", "")
        tenant_tokens = json.loads(raw_tenant_tokens) if raw_tenant_tokens else {}
        if not isinstance(tenant_tokens, dict):
            raise ValueError("ANCHOR_TENANT_TOKENS must be a JSON object")
    httpd = build_server(anchor, host=host, port=port, namespace=namespace,
                         token=token, cors_origin=cors_origin,
                         tenant_tokens={str(k): str(v) for k, v in tenant_tokens.items()},
                         verbose=verbose)
    httpd.bus.start()  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    print(f"Anchor dashboard  ->  {url}")
    print(f"  REST API        ->  {url}api/stats")
    print(f"  live events     ->  {url}api/events   (SSE)")
    print(f"  prometheus      ->  {url}metrics")
    if token or tenant_tokens:
        print("  auth            ->  bearer token required")
    if host not in ("127.0.0.1", "localhost", "::1") and not (token or tenant_tokens):
        print("  WARNING: bound to a non-loopback interface without a token. "
              "Set ANCHOR_API_TOKEN.")
    if open_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        for bus in httpd.buses.values():  # type: ignore[attr-defined]
            bus.stop()
        httpd.server_close()

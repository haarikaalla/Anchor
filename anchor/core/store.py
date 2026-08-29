"""Persistence layer.

A thin, transaction-safe SQLite store with:

* structured columns for fast querying *plus* a full JSON blob per row so the
  rich domain model round-trips losslessly;
* an **append-only, hash-chained audit log** (tamper-evident provenance);
* per-source evidence accounting used by the anti-poisoning caps.

The SQL is deliberately vanilla so a Postgres backend can be dropped in later;
only :class:`SQLiteStore` is provided here.  All public methods are safe to call
concurrently from a single process (SQLite serialises writes) and use
parameterised queries exclusively (no SQL injection surface).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterable
from typing import Any

from .models import AuditEvent, Belief, Contradiction, ContradictionStatus, now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS beliefs (
    id            TEXT PRIMARY KEY,
    namespace     TEXT NOT NULL,
    subject       TEXT,
    predicate     TEXT,
    object        TEXT,
    polarity      TEXT,
    status        TEXT,
    entity_key    TEXT,
    claim_key     TEXT,
    confidence    REAL,
    valid_from    REAL,
    valid_to      REAL,
    updated_at    REAL,
    data          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_beliefs_ns ON beliefs(namespace);
CREATE INDEX IF NOT EXISTS idx_beliefs_entity ON beliefs(namespace, entity_key);
CREATE INDEX IF NOT EXISTS idx_beliefs_status ON beliefs(namespace, status);

CREATE TABLE IF NOT EXISTS contradictions (
    id           TEXT PRIMARY KEY,
    namespace    TEXT NOT NULL,
    belief_a     TEXT,
    belief_b     TEXT,
    kind         TEXT,
    status       TEXT,
    detected_at  REAL,
    data         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_con_ns_status ON contradictions(namespace, status);

CREATE TABLE IF NOT EXISTS audit (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT UNIQUE,
    namespace  TEXT NOT NULL,
    ts         REAL,
    event_type TEXT,
    belief_id  TEXT,
    actor      TEXT,
    payload    TEXT,
    prev_hash  TEXT,
    hash       TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ns ON audit(namespace, seq);
CREATE INDEX IF NOT EXISTS idx_audit_belief ON audit(belief_id);

CREATE TABLE IF NOT EXISTS source_evidence (
    belief_id  TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    evidence   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (belief_id, source_id)
);
"""


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class SQLiteStore:
    def __init__(self, path: str = "anchor.db"):
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ beliefs
    def upsert_belief(self, belief: Belief) -> None:
        belief.updated_at = now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO beliefs
                    (id, namespace, subject, predicate, object, polarity, status,
                     entity_key, claim_key, confidence, valid_from, valid_to,
                     updated_at, data)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    namespace=excluded.namespace,
                    subject=excluded.subject,
                    predicate=excluded.predicate,
                    object=excluded.object,
                    polarity=excluded.polarity,
                    status=excluded.status,
                    entity_key=excluded.entity_key,
                    claim_key=excluded.claim_key,
                    confidence=excluded.confidence,
                    valid_from=excluded.valid_from,
                    valid_to=excluded.valid_to,
                    updated_at=excluded.updated_at,
                    data=excluded.data
                """,
                (
                    belief.id, belief.namespace, belief.subject, belief.predicate,
                    belief.object, belief.polarity.value, belief.status.value,
                    belief.entity_key, belief.claim_key, belief.raw_confidence,
                    belief.valid_from, belief.valid_to, belief.updated_at,
                    _canonical(belief.to_dict()),
                ),
            )

    def get_belief(self, belief_id: str) -> Belief | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM beliefs WHERE id=?", (belief_id,)
            ).fetchone()
        return Belief.from_dict(json.loads(row["data"])) if row else None

    def beliefs_by_entity(self, namespace: str, entity_key: str) -> list[Belief]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM beliefs WHERE namespace=? AND entity_key=?",
                (namespace, entity_key),
            ).fetchall()
        return [Belief.from_dict(json.loads(r["data"])) for r in rows]

    def all_beliefs(self, namespace: str, include_inactive: bool = True) -> list[Belief]:
        q = "SELECT data FROM beliefs WHERE namespace=?"
        args: list[Any] = [namespace]
        if not include_inactive:
            q += " AND status IN ('active','provisional','disputed')"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [Belief.from_dict(json.loads(r["data"])) for r in rows]

    def iter_all(self, namespace: str | None = None) -> Iterable[Belief]:
        q = "SELECT data FROM beliefs"
        args: list[Any] = []
        if namespace is not None:
            q += " WHERE namespace=?"
            args.append(namespace)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        for r in rows:
            yield Belief.from_dict(json.loads(r["data"]))

    # ------------------------------------------------------- per-source evidence
    def source_evidence(self, belief_id: str, source_id: str) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT evidence FROM source_evidence WHERE belief_id=? AND source_id=?",
                (belief_id, source_id),
            ).fetchone()
        return float(row["evidence"]) if row else 0.0

    def add_source_evidence(self, belief_id: str, source_id: str, amount: float) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO source_evidence (belief_id, source_id, evidence)
                VALUES (?,?,?)
                ON CONFLICT(belief_id, source_id)
                DO UPDATE SET evidence = evidence + excluded.evidence
                """,
                (belief_id, source_id, amount),
            )

    def distinct_sources(self, belief_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM source_evidence WHERE belief_id=? AND evidence > 0",
                (belief_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def source_evidence_breakdown(self, belief_id: str) -> dict[str, float]:
        """Full per-source evidence ledger for one belief (supporting *and*
        contradicting, the latter stored under a ``con::`` prefix).

        Powers evidence attribution in :mod:`anchor.core.explain`.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT source_id, evidence FROM source_evidence WHERE belief_id=?",
                (belief_id,),
            ).fetchall()
        return {r["source_id"]: float(r["evidence"]) for r in rows}

    # ------------------------------------------------------------ contradictions
    def upsert_contradiction(self, c: Contradiction) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO contradictions
                    (id, namespace, belief_a, belief_b, kind, status, detected_at, data)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status, kind=excluded.kind, data=excluded.data
                """,
                (c.id, c.namespace, c.belief_a, c.belief_b, c.kind.value,
                 c.status.value, c.detected_at, _canonical(c.to_dict())),
            )

    def get_contradiction(self, cid: str) -> Contradiction | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM contradictions WHERE id=?", (cid,)
            ).fetchone()
        return Contradiction.from_dict(json.loads(row["data"])) if row else None

    def open_contradiction_between(self, a: str, b: str) -> Contradiction | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT data FROM contradictions
                WHERE status='open' AND
                      ((belief_a=? AND belief_b=?) OR (belief_a=? AND belief_b=?))
                LIMIT 1
                """,
                (a, b, b, a),
            ).fetchone()
        return Contradiction.from_dict(json.loads(row["data"])) if row else None

    def contradictions(self, namespace: str,
                        status: ContradictionStatus | None = None) -> list[Contradiction]:
        q = "SELECT data FROM contradictions WHERE namespace=?"
        args: list[Any] = [namespace]
        if status is not None:
            q += " AND status=?"
            args.append(status.value)
        q += " ORDER BY detected_at DESC"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [Contradiction.from_dict(json.loads(r["data"])) for r in rows]

    # -------------------------------------------------------------------- audit
    def _last_audit_hash(self, namespace: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT hash FROM audit WHERE namespace=? ORDER BY seq DESC LIMIT 1",
                (namespace,),
            ).fetchone()
        return row["hash"] if row else ""

    def append_audit(self, event: AuditEvent) -> AuditEvent:
        """Append a tamper-evident audit record (hash-chained per namespace)."""
        with self._lock, self._conn:
            prev = self._last_audit_hash(event.namespace)
            event.prev_hash = prev
            body = {
                "id": event.id, "namespace": event.namespace, "ts": event.ts,
                "event_type": event.event_type, "belief_id": event.belief_id,
                "actor": event.actor, "payload": event.payload, "prev_hash": prev,
            }
            event.hash = hashlib.sha256(_canonical(body).encode()).hexdigest()
            self._conn.execute(
                """
                INSERT INTO audit
                    (id, namespace, ts, event_type, belief_id, actor, payload, prev_hash, hash)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (event.id, event.namespace, event.ts, event.event_type,
                 event.belief_id, event.actor, _canonical(event.payload),
                 event.prev_hash, event.hash),
            )
        return event

    def audit_log(self, namespace: str, belief_id: str | None = None,
                  limit: int = 200) -> list[AuditEvent]:
        q = "SELECT * FROM audit WHERE namespace=?"
        args: list[Any] = [namespace]
        if belief_id:
            q += " AND belief_id=?"
            args.append(belief_id)
        q += " ORDER BY seq DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        out = []
        for r in rows:
            out.append(AuditEvent(
                id=r["id"], namespace=r["namespace"], ts=r["ts"],
                event_type=r["event_type"], belief_id=r["belief_id"],
                actor=r["actor"], payload=json.loads(r["payload"] or "{}"),
                prev_hash=r["prev_hash"], hash=r["hash"],
            ))
        return out

    def latest_audit_seq(self, namespace: str) -> int:
        """Monotonic cursor for the namespace's audit log (0 when empty)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT seq FROM audit WHERE namespace=? ORDER BY seq DESC LIMIT 1",
                (namespace,),
            ).fetchone()
        return int(row["seq"]) if row else 0

    def audit_after(self, namespace: str, seq: int,
                    limit: int = 200) -> list[tuple[int, AuditEvent]]:
        """Tail the audit log past ``seq``, oldest first.

        Lets a live consumer (the dashboard's event stream) follow every state
        change without polling the whole log or holding a callback in the engine.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit WHERE namespace=? AND seq>? ORDER BY seq ASC LIMIT ?",
                (namespace, seq, limit),
            ).fetchall()
        return [
            (int(r["seq"]), AuditEvent(
                id=r["id"], namespace=r["namespace"], ts=r["ts"],
                event_type=r["event_type"], belief_id=r["belief_id"],
                actor=r["actor"], payload=json.loads(r["payload"] or "{}"),
                prev_hash=r["prev_hash"], hash=r["hash"],
            ))
            for r in rows
        ]

    def verify_audit_chain(self, namespace: str) -> bool:
        """Recompute the hash chain and confirm nothing has been tampered with."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit WHERE namespace=? ORDER BY seq ASC", (namespace,)
            ).fetchall()
        prev = ""
        for r in rows:
            body = {
                "id": r["id"], "namespace": r["namespace"], "ts": r["ts"],
                "event_type": r["event_type"], "belief_id": r["belief_id"],
                "actor": r["actor"], "payload": json.loads(r["payload"] or "{}"),
                "prev_hash": prev,
            }
            expected = hashlib.sha256(_canonical(body).encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != expected:
                return False
            prev = r["hash"]
        return True

    # ------------------------------------------------------------------ metrics
    def stats(self, namespace: str) -> dict[str, Any]:
        with self._lock:
            counts = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM beliefs WHERE namespace=? GROUP BY status",
                (namespace,),
            ).fetchall()
            open_con = self._conn.execute(
                "SELECT COUNT(*) AS n FROM contradictions WHERE namespace=? AND status='open'",
                (namespace,),
            ).fetchone()
            avg = self._conn.execute(
                "SELECT AVG(confidence) AS c FROM beliefs WHERE namespace=?",
                (namespace,),
            ).fetchone()
        return {
            "by_status": {r["status"]: r["n"] for r in counts},
            "open_contradictions": open_con["n"] if open_con else 0,
            "avg_confidence": round(avg["c"], 4) if avg and avg["c"] is not None else None,
        }

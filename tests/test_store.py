from anchor.core.models import AuditEvent, Belief
from anchor.core.store import SQLiteStore


def make_store(tmp_path):
    return SQLiteStore(str(tmp_path / "s.db"))


def test_upsert_and_get(tmp_path):
    s = make_store(tmp_path)
    b = Belief(subject="s", predicate="p", object="o")
    s.upsert_belief(b)
    got = s.get_belief(b.id)
    assert got is not None and got.subject == "s"
    s.close()


def test_beliefs_by_entity(tmp_path):
    s = make_store(tmp_path)
    b1 = Belief(subject="user", predicate="lives_in", object="Berlin")
    b2 = Belief(subject="user", predicate="lives_in", object="Munich")
    s.upsert_belief(b1)
    s.upsert_belief(b2)
    rows = s.beliefs_by_entity("default", b1.entity_key)
    assert len(rows) == 2
    s.close()


def test_source_evidence_accumulates(tmp_path):
    s = make_store(tmp_path)
    s.add_source_evidence("b1", "src1", 2.0)
    s.add_source_evidence("b1", "src1", 1.0)
    s.add_source_evidence("b1", "src2", 1.0)
    assert s.source_evidence("b1", "src1") == 3.0
    assert s.distinct_sources("b1") == 2
    s.close()


def test_audit_chain_and_tamper_detection(tmp_path):
    s = make_store(tmp_path)
    for i in range(3):
        s.append_audit(AuditEvent(event_type="x", belief_id=f"b{i}", namespace="default"))
    assert s.verify_audit_chain("default") is True

    # Tamper with a payload directly in the DB and confirm detection.
    with s._conn:  # noqa: SLF001 - deliberately corrupt to test integrity
        s._conn.execute("UPDATE audit SET payload='{\"evil\":1}' WHERE seq=1")
    assert s.verify_audit_chain("default") is False
    s.close()

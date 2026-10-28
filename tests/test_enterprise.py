from pathlib import Path

import pytest

from anchor import Anchor, AnchorConfig
from anchor.core.engine import IdempotencyConflict


def test_observe_idempotency_replays_without_adding_evidence(anchor):
    first = anchor.observe(
        "account",
        "risk_tier",
        "low",
        source_id="crm",
        trust=0.9,
        idempotency_key="event-123",
    )
    replay = anchor.observe(
        "account",
        "risk_tier",
        "low",
        source_id="crm",
        trust=0.9,
        idempotency_key="event-123",
    )

    assert replay.belief.id == first.belief.id
    assert replay.belief.alpha == first.belief.alpha
    assert anchor.get(first.belief.id).reinforcement_count == 1


def test_idempotency_key_reuse_with_different_payload_is_rejected(anchor):
    anchor.observe("account", "risk_tier", "low", idempotency_key="event-123")
    with pytest.raises(IdempotencyConflict):
        anchor.observe("account", "risk_tier", "high", idempotency_key="event-123")


def test_idempotency_key_size_is_bounded(anchor):
    with pytest.raises(ValueError, match="256"):
        anchor.observe("account", "risk_tier", "low", idempotency_key="x" * 257)


def test_correlated_sources_share_an_aggregate_evidence_cap(tmp_path):
    engine = Anchor(
        AnchorConfig(
            db_path=str(tmp_path / "groups.db"),
            max_source_evidence=5.0,
            max_source_group_evidence=1.0,
        )
    )
    try:
        result = None
        for index in range(12):
            result = engine.observe(
                "device",
                "status",
                "healthy",
                source_id=f"rotating-id-{index}",
                source_group="vendor-a",
                trust=1.0,
            )
        assert result is not None
        assert result.belief.alpha == 2.0  # prior 1.0 + group cap 1.0
        assert engine.store.distinct_source_groups(result.belief.id) == 1
    finally:
        engine.close()


def test_online_backup_is_consistent_and_reopenable(anchor, tmp_path):
    anchor.observe("service", "status", "healthy", source_id="probe", trust=0.9)
    backup_path = tmp_path / "backups" / "anchor.db"

    result = anchor.backup(str(backup_path))

    assert result["integrity"] == "ok"
    assert result["bytes"] > 0
    restored = Anchor(AnchorConfig(db_path=str(backup_path)))
    try:
        assert restored.current_belief("service", "status").object == "healthy"
        assert restored.verify_audit() is True
    finally:
        restored.close()
    assert Path(result["path"]) == backup_path.resolve()

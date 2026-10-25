"""Legacy classification runs on a copy, keeps every row, and is repeatable."""

from __future__ import annotations

import sqlite3

import pytest

from assistant_api.services import ai_tasks


def _seed(path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO users (user_id, email, display_name, password_verifier, status, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("u1", "u1@example.com", "甲", "x", "active", "2026-10-01 00:00:00", "2026-10-01 00:00:00"),
        )
        for check_id, trigger, ai_run_id in (
            ("check-1", "manual", "run-linked"),
            ("check-2", "auto", None),
            ("check-3", "manual", None),
        ):
            connection.execute(
                "INSERT INTO commute_checks (check_id, user_id, plan_id, instance_id, "
                "plan_revision, trigger, status, attempt_count, advisory_created, "
                "ai_run_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    check_id, "u1", "plan-1", "instance-1", 1, trigger, "succeeded",
                    1, 0, ai_run_id, "2026-10-01 00:00:00", "2026-10-01 00:00:00",
                ),
            )
        rows = [
            ("run-chat", "client-key-0001", "帮我记一下午饭 28 元"),
            ("run-linked", "commute-ai:check-1:hash-a", "内部通勤分析（已有关联）"),
            ("run-keyonly", "commute:check-2:v1", "内部通勤分析（仅键标记）"),
            ("run-failed", "commute-ai:check-missing:hash-b", "内部通勤分析（无 chek 记录）"),
            ("run-unknown", "", "无法判断来源的记录"),
        ]
        for local_run_id, key, text in rows:
            connection.execute(
                "INSERT INTO nexus_runs (local_run_id, user_id, status, idempotency_key, "
                "last_event_sequence, request_text, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    local_run_id, "u1", "succeeded", key, 0, text,
                    "2026-10-01 00:00:00", "2026-10-01 00:00:00",
                ),
            )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_backfill_classifies_and_is_repeatable(tmp_path):
    from scripts.migrate_sqlite import init
    from scripts.backfill_user_asks import migrate_and_backfill

    source = tmp_path / "legacy.db"
    await init(f"sqlite+aiosqlite:///{source}")
    _seed(source)
    before = source.read_bytes()

    report, copy = await migrate_and_backfill(
        source, output=tmp_path / "migrated.db"
    )
    assert copy is not None
    assert source.read_bytes() == before, "the source database must not be written"

    assert report.total_runs == 5
    assert (report.chat, report.commute, report.unknown) == (1, 3, 1)
    assert report.asks_created == 1
    assert report.reasons["check_link"] == 1
    assert report.reasons["internal_key_marker"] == 2
    assert report.reasons["no_internal_marker"] == 1
    assert report.reasons["unclassifiable"] == 1
    assert report.commute_without_check == ["run-failed"]
    assert report.unknown_runs == ["run-unknown"]

    connection = sqlite3.connect(copy)
    try:
        purposes = dict(
            connection.execute(
                "SELECT local_run_id, purpose FROM nexus_runs ORDER BY local_run_id"
            ).fetchall()
        )
        assert purposes["run-chat"] == ai_tasks.CHAT
        assert purposes["run-linked"] == ai_tasks.COMMUTE_INTERNAL
        assert purposes["run-keyonly"] == ai_tasks.COMMUTE_INTERNAL
        assert purposes["run-failed"] == ai_tasks.COMMUTE_INTERNAL
        assert purposes["run-unknown"] == ai_tasks.LEGACY_UNKNOWN

        asks = connection.execute(
            "SELECT user_id, local_run_id, request_text FROM user_asks"
        ).fetchall()
        assert asks == [("u1", "run-chat", "帮我记一下午饭 28 元")]

        business_ref, trigger = connection.execute(
            "SELECT business_ref, trigger FROM nexus_runs WHERE local_run_id='run-linked'"
        ).fetchone()
        assert business_ref == "check-1"
        assert trigger == "manual"
        business_ref, trigger = connection.execute(
            "SELECT business_ref, trigger FROM nexus_runs WHERE local_run_id='run-keyonly'"
        ).fetchone()
        assert business_ref == "check-2"
        assert trigger == "schedule"
        # An internal run whose check row is missing keeps its evidence and no
        # chat link; the unknown business reference is left empty, not guessed.
        assert connection.execute(
            "SELECT COUNT(*) FROM user_asks WHERE local_run_id='run-failed'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT business_ref FROM nexus_runs WHERE local_run_id='run-failed'"
        ).fetchone()[0] is None
        # The original answer/time fields are untouched.
        assert connection.execute(
            "SELECT request_text, created_at FROM nexus_runs WHERE local_run_id='run-chat'"
        ).fetchone() == ("帮我记一下午饭 28 元", "2026-10-01 00:00:00")
    finally:
        connection.close()

    # A second pass over the migrated copy creates nothing new.
    second, copy2 = await migrate_and_backfill(copy, output=tmp_path / "migrated2.db")
    assert second.chat == 0
    assert second.asks_created == 0
    assert second.already_classified == 5
    assert copy2 is not None
    verify = sqlite3.connect(copy2)
    try:
        assert verify.execute("SELECT COUNT(*) FROM user_asks").fetchone()[0] == 1
        assert verify.execute("SELECT COUNT(*) FROM nexus_runs").fetchone()[0] == 5
    finally:
        verify.close()


@pytest.mark.asyncio
async def test_backfill_only_uses_structural_evidence(tmp_path):
    """A run whose text looks internal but has no marker is classified as chat."""

    from scripts.migrate_sqlite import init
    from scripts.backfill_user_asks import migrate_and_backfill

    source = tmp_path / "ambiguous.db"
    await init(f"sqlite+aiosqlite:///{source}")
    connection = sqlite3.connect(source)
    try:
        connection.execute(
            "INSERT INTO users (user_id, email, display_name, password_verifier, status, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("u1", "u1@example.com", "甲", "x", "active", "2026-10-01 00:00:00", "2026-10-01 00:00:00"),
        )
        connection.execute(
            "INSERT INTO nexus_runs (local_run_id, user_id, status, idempotency_key, "
            "last_event_sequence, request_text, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "run-text-like-commute", "u1", "succeeded", "client-key-0009", 0,
                "请作为通勤分析器，只输出严格 JSON。输入：{}",
                "2026-10-01 00:00:00", "2026-10-01 00:00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    report, _copy = await migrate_and_backfill(source)
    assert (report.chat, report.commute, report.unknown) == (1, 0, 0)

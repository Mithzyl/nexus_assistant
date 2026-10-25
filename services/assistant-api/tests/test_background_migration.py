"""Migration is additive, repeatable and leaves legacy rows un-backfilled."""

import sqlite3
from pathlib import Path

import pytest


async def _legacy_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE commute_checks (check_id VARCHAR(36) PRIMARY KEY, status VARCHAR(16))"
        )
        connection.execute("INSERT INTO commute_checks(check_id, status) VALUES ('legacy-1', 'succeeded')")
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_background_migration_is_additive_and_repeatable(tmp_path):
    from scripts.migrate_sqlite import upgrade

    database = tmp_path / "legacy.db"
    await _legacy_database(database)
    url = f"sqlite+aiosqlite:///{database}"

    await upgrade(url)
    await upgrade(url)

    connection = sqlite3.connect(database)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(commute_checks)")}
        assert {"analysis_mode", "phase", "phase_updated_at"} <= columns
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "service_heartbeats" in tables
        row = connection.execute(
            "SELECT analysis_mode, phase, phase_updated_at FROM commute_checks "
            "WHERE check_id = 'legacy-1'"
        ).fetchone()
        assert row == (None, None, None)
        versions = [r[0] for r in connection.execute("SELECT version FROM schema_migrations")]
        assert versions.count("0005_background_runtime") == 1
    finally:
        connection.close()


def test_serializer_does_not_invent_phase_for_legacy_rows():
    from datetime import UTC, datetime

    from assistant_api.commute.serializers import check_read
    from assistant_api.models import CommuteCheck

    row = CommuteCheck(
        check_id="legacy-2", user_id="u", plan_id="p", instance_id="i",
        plan_revision=1, trigger="auto", status="succeeded",
        phase=None, analysis_mode=None, phase_updated_at=None,
        attempt_count=0, next_attempt_at=None, advisory_created=False,
        created_at=datetime.now(UTC),
    )
    payload = check_read(row)
    assert payload.phase is None
    assert payload.analysis_mode is None
    assert payload.phase_updated_at is None

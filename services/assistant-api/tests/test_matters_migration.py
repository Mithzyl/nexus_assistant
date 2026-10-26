"""Additive migration: old schedules rows survive and new columns appear."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from scripts import migrate_sqlite


def _old_schema(path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schedules (
            schedule_id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            title VARCHAR(255) NOT NULL,
            starts_at DATETIME NOT NULL,
            ends_at DATETIME,
            location VARCHAR(255),
            status VARCHAR(16),
            source_run_id VARCHAR(36),
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        INSERT INTO schedules VALUES (
            'old-schedule', 'old-user', '旧日程', '2026-10-01 09:00:00.000000',
            '2026-10-01 10:00:00.000000', '办公室', 'scheduled', NULL,
            '2026-09-30 00:00:00.000000', '2026-09-30 00:00:00.000000'
        );
        """
    )
    connection.commit()
    connection.close()


@pytest.mark.asyncio
async def test_upgrade_keeps_existing_schedules(tmp_path):
    path = tmp_path / "old.db"
    _old_schema(path)
    url = f"sqlite+aiosqlite:///{path}"
    await migrate_sqlite.upgrade(url)
    await migrate_sqlite.upgrade(url)  # idempotent

    connection = sqlite3.connect(path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(schedules)")}
        assert {"matter_id", "sync_status", "source", "deleted_at"} <= columns
        row = connection.execute(
            "SELECT title, matter_id, sync_status FROM schedules WHERE schedule_id='old-schedule'"
        ).fetchone()
        assert row == ("旧日程", None, None)
        applied = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations WHERE version='0007_matters'"
            )
        }
        assert "0007_matters" in applied
    finally:
        connection.close()

    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            count = (await conn.exec_driver_sql("SELECT COUNT(*) FROM schedules")).scalar_one()
        assert count == 1
    finally:
        await engine.dispose()

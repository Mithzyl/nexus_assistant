"""SQLite initialization and additive upgrades for the Personal Assistant API.

Empty databases are initialized from the current SQLAlchemy metadata. Existing
databases keep their rows; the only incremental operations are explicitly
checked ALTER TABLE columns. Applied versions are recorded in
schema_migrations. A manually added column is detected and recorded as
already_present, never treated as a fresh success or a duplicate-column error.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from assistant_api.core.config import get_settings
from assistant_api.models import Base

VERSION = "0001_sqlite_baseline"
COLUMN_MIGRATIONS = (
    ("0002_commute_route_ai", "commute_checks", "route_snapshot_json", "JSON"),
    ("0002_commute_route_ai", "commute_checks", "route_snapshot_hash", "VARCHAR(64)"),
    ("0002_commute_route_ai", "commute_advisories", "ai_analysis_json", "JSON"),
    ("0003_commute_ai_run", "commute_checks", "ai_run_id", "VARCHAR(36)"),
    ("0004_tool_receipt_arguments", "tool_call_receipts", "arguments_sha256", "VARCHAR(64)"),
    ("0005_background_runtime", "commute_checks", "analysis_mode", "VARCHAR(16)"),
    ("0005_background_runtime", "commute_checks", "phase", "VARCHAR(32)"),
    ("0005_background_runtime", "commute_checks", "phase_updated_at", "DATETIME"),
    # Shared AI task metadata. Nullable so legacy rows are classified by the
    # backfill script instead of being guessed at write time.
    ("0006_ai_task_purpose", "nexus_runs", "purpose", "VARCHAR(32)"),
    ("0006_ai_task_purpose", "nexus_runs", "business_ref", "VARCHAR(36)"),
    ("0006_ai_task_purpose", "nexus_runs", "input_version", "INTEGER"),
    ("0006_ai_task_purpose", "nexus_runs", "trigger", "VARCHAR(16)"),
    # Matters reuse `schedules`; every added column is nullable for old rows.
    ("0007_matters", "schedules", "source", "VARCHAR(24)"),
    ("0007_matters", "schedules", "sync_status", "VARCHAR(24)"),
    ("0007_matters", "schedules", "source_external_id", "VARCHAR(255)"),
    ("0007_matters", "schedules", "source_updated_at", "DATETIME"),
    ("0007_matters", "schedules", "source_version", "INTEGER"),
    ("0007_matters", "schedules", "matter_id", "VARCHAR(36)"),
    ("0007_matters", "schedules", "data_source_id", "VARCHAR(36)"),
    ("0007_matters", "schedules", "latitude", "FLOAT"),
    ("0007_matters", "schedules", "longitude", "FLOAT"),
    ("0007_matters", "schedules", "deleted_at", "DATETIME"),
    ("0008_source_auth", "schedules", "source_scope_key", "VARCHAR(255)"),
    ("0008_source_auth", "reminders", "source_scope_key", "VARCHAR(255)"),
    ("0008_source_auth", "reminders", "data_source_id", "VARCHAR(36)"),
    ("0008_source_auth", "content_sync_batches", "payload_sha256", "VARCHAR(64)"),
    ("0008_source_auth", "nexus_runs", "context_json", "JSON"),
    ("0009_edit_drafts", "matter_drafts", "origin_key", "VARCHAR(128)"),
    ("0010_option_state", "matter_drafts", "option_base_json", "JSON"),
    ("0010_option_state", "matter_drafts", "base_todo_id", "VARCHAR(36)"),
    ("0010_option_state", "matter_drafts", "base_todo_updated_at", "DATETIME"),
    ("0011_location_route", "schedules", "coordinate_system", "VARCHAR(16)"),
    ("0011_location_route", "schedules", "location_city", "VARCHAR(64)"),
    ("0011_location_route", "schedules", "location_address", "VARCHAR(255)"),
    ("0011_location_route", "schedules", "location_source", "VARCHAR(32)"),
    ("0011_location_route", "schedules", "location_external_id", "VARCHAR(128)"),
    ("0012_reminder_lifecycle", "matter_todos", "deleted_at", "DATETIME"),
    ("0012_reminder_lifecycle", "reminders", "schedule_id", "VARCHAR(36)"),
    ("0012_reminder_lifecycle", "reminders", "acknowledged_at", "DATETIME"),
    ("0012_reminder_lifecycle", "reminders", "deleted_at", "DATETIME"),
)

# Indexes on pre-existing tables cannot be added by metadata.create_all, so they
# are created explicitly and idempotently. The chat association table itself is
# new and comes from create_all.
INDEX_MIGRATIONS = (
    (
        "0006_ai_task_purpose",
        "ix_nexus_runs_purpose_business",
        "CREATE INDEX IF NOT EXISTS ix_nexus_runs_purpose_business "
        "ON nexus_runs (purpose, business_ref)",
    ),
    (
        "0006_ai_task_purpose",
        "ix_nexus_runs_user_purpose_created",
        "CREATE INDEX IF NOT EXISTS ix_nexus_runs_user_purpose_created "
        "ON nexus_runs (user_id, purpose, created_at)",
    ),
    (
        "0007_matters",
        "ix_schedules_matter",
        "CREATE INDEX IF NOT EXISTS ix_schedules_matter ON schedules (matter_id)",
    ),
    (
        "0007_matters",
        "uq_schedules_user_source_external",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_schedules_user_source_external "
        "ON schedules (user_id, source, source_external_id)",
    ),
    (
        "0008_source_auth",
        "drop_old_schedule_index",
        "DROP INDEX IF EXISTS uq_schedules_user_source_external",
    ),
    (
        "0008_source_auth",
        "drop_old_reminder_index",
        "DROP INDEX IF EXISTS uq_reminders_user_source_external",
    ),
    (
        "0008_source_auth",
        "uq_schedules_user_source_device_external",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_schedules_user_source_device_external "
        "ON schedules (user_id, source, data_source_id, source_external_id)",
    ),
    (
        "0008_source_auth",
        "uq_reminders_user_source_device_external",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_reminders_user_source_device_external "
        "ON reminders (user_id, source, data_source_id, source_external_id)",
    ),
    (
        "0009_edit_drafts",
        "uq_matter_drafts_user_origin",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_matter_drafts_user_origin "
        "ON matter_drafts (user_id, origin_key)",
    ),
    (
        "0012_reminder_lifecycle",
        "ix_reminders_schedule",
        "CREATE INDEX IF NOT EXISTS ix_reminders_schedule "
        "ON reminders (schedule_id)",
    ),
)


async def _ensure_table(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """))


async def _record(conn, version: str, detail: str) -> None:
    await conn.execute(
        text("INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES (:v, :a)"),
        {"v": version, "a": f"{datetime.now(UTC).isoformat()} {detail}"},
    )


async def _columns(conn, table: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
    return {row[1] for row in rows}


async def _apply_indexes(conn) -> None:
    for version, name, statement in INDEX_MIGRATIONS:
        await conn.execute(text(statement))
        await _record(conn, version, f"index:{name}")


async def init(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await _ensure_table(conn)
            await _record(conn, VERSION, "metadata")
            for version, table, column, sqltype in COLUMN_MIGRATIONS:
                await _record(conn, version, "metadata")
            await _apply_indexes(conn)
        print(f"initialized SQLite schema at {database_url}; baseline recorded")
    finally:
        await engine.dispose()


async def upgrade(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            # Create only tables that are still absent; this does not alter
            # existing tables or replace the checked ALTER migrations below.
            await conn.run_sync(Base.metadata.create_all)
            await _ensure_table(conn)
            for version, table, column, sqltype in COLUMN_MIGRATIONS:
                columns = await _columns(conn, table)
                if not columns:
                    raise RuntimeError(f"table {table} is missing; refusing to mark migration")
                if column in columns:
                    await _record(conn, version, f"already_present:{table}.{column}")
                    print(f"{version}: {table}.{column} already present")
                else:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype} NULL"))
                    await _record(conn, version, f"added:{table}.{column}")
                    print(f"{version}: added {table}.{column}")
            await _apply_indexes(conn)
            await _record(conn, VERSION, "upgrade")
    finally:
        await engine.dispose()


async def status(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            await _ensure_table(conn)
            try:
                rows = (await conn.execute(text(
                    "SELECT version, applied_at FROM schema_migrations ORDER BY version"
                ))).fetchall()
            except Exception:
                rows = []
        print("\n".join(f"{v}\t{a}" for v, a in rows) or "no migrations recorded")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite schema init/upgrade")
    parser.add_argument("command", choices=["init", "upgrade", "status"])
    parser.add_argument("--database", default=None)
    args = parser.parse_args()
    url = args.database or get_settings().database_url
    if not url.startswith("sqlite"):
        raise SystemExit("This migrator only supports SQLite; refusing non-SQLite URL.")
    if args.command == "init":
        asyncio.run(init(url))
    elif args.command == "upgrade":
        asyncio.run(upgrade(url))
    else:
        asyncio.run(status(url))


if __name__ == "__main__":
    main()

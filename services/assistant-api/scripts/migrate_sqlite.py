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
NEXT_ACTION_VERSION = "0030_next_actions"
INTAKE_REPLY_VERSION = "0031_intake_replies"
CAPTURE_VERSION = "0032_intake_capture"
BARK_NOTIFICATION_VERSION = "0033_bark_notifications"
COMMUTE_LOCATION_VERSION = "0034_commute_location_metadata"
COMMUTE_NOTICE_VERSION = "0035_commute_notification_stages"
COLUMN_MIGRATIONS = (
    (INTAKE_REPLY_VERSION, "nexus_runs", "reply_state", "VARCHAR(20)"),
    (INTAKE_REPLY_VERSION, "nexus_runs", "reply_expires_at", "DATETIME"),
    (CAPTURE_VERSION, "nexus_runs", "intake_stage", "VARCHAR(24)"),
    (CAPTURE_VERSION, "nexus_runs", "capture_lease_expires_at", "DATETIME"),
    (CAPTURE_VERSION, "nexus_runs", "capture_dispatch_started_at", "DATETIME"),
    (BARK_NOTIFICATION_VERSION, "bark_reminder_settings", "assistant_notifications_enabled_at", "DATETIME"),
    (COMMUTE_LOCATION_VERSION, "commute_plans", "origin_city", "VARCHAR(160)"),
    (COMMUTE_LOCATION_VERSION, "commute_plans", "origin_coordinate_system", "VARCHAR(16)"),
    (COMMUTE_LOCATION_VERSION, "commute_plans", "destination_city", "VARCHAR(160)"),
    (COMMUTE_LOCATION_VERSION, "commute_plans", "destination_coordinate_system", "VARCHAR(16)"),
    (COMMUTE_NOTICE_VERSION, "commute_plans", "evening_notice_local_time", "VARCHAR(5)"),
    (COMMUTE_NOTICE_VERSION, "commute_plans", "evening_notice_level", "VARCHAR(16)"),
    (COMMUTE_NOTICE_VERSION, "commute_instances", "notification_snapshot_json", "JSON"),
    (COMMUTE_NOTICE_VERSION, "commute_checks", "notification_stage", "VARCHAR(16)"),
    (COMMUTE_NOTICE_VERSION, "notification_outbox", "notification_stage", "VARCHAR(16)"),
    (NEXT_ACTION_VERSION, "matter_todos", "priority", "VARCHAR(8)"),
    (NEXT_ACTION_VERSION, "matter_todos", "estimated_duration_minutes", "INTEGER"),
    (NEXT_ACTION_VERSION, "matter_todos", "available_after", "DATETIME"),
    (NEXT_ACTION_VERSION, "matter_todos", "place", "JSON"),
    (NEXT_ACTION_VERSION, "matter_todos", "due_schedule_id", "VARCHAR(36) REFERENCES schedules(schedule_id) ON DELETE SET NULL"),
    (NEXT_ACTION_VERSION, "matter_todos", "due_schedule_offset_seconds", "INTEGER"),
    ("0025_supplement_relations", "supplement_texts", "relation_type", "VARCHAR(24)"),
    ("0023_commute_day_override", "commute_instances", "arrival_local_time_override", "VARCHAR(5)"),
    ("0022_explicit_reminder_dependency", "reminders", "schedule_offset_seconds", "INTEGER"),
    ("0013_commute_bundle", "commute_plans", "effective_from", "DATE"),
    ("0013_commute_bundle", "commute_plans", "matter_id", "VARCHAR(36) REFERENCES matters(matter_id)"),
    ("0013_commute_bundle", "commute_plans", "first_schedule_id", "VARCHAR(36) REFERENCES schedules(schedule_id)"),
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
    (CAPTURE_VERSION, "ix_nexus_runs_intake_queue",
     "CREATE INDEX IF NOT EXISTS ix_nexus_runs_intake_queue "
     "ON nexus_runs (intake_stage, capture_lease_expires_at, created_at)"),

    (
        "0025_supplement_relations",
        "ix_supplement_text_relations",
        "CREATE INDEX IF NOT EXISTS ix_supplement_text_relations "
        "ON supplement_texts (parent_text_id, relation_type, created_at)",
    ),
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
        text("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (:v, :a)"),
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
    """Initialize current metadata and mark additive migrations without guessing legacy values."""
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await _ensure_table(conn)
            await _record(conn, VERSION, "metadata")
            await _record(conn, "0029_business_memory_applications", "metadata:business_memory_applications")
            await _record(conn, "0026_agent_response_receipts", "metadata:agent_response_receipts")
            await _record(conn, "0027_browser_push", "metadata:browser_push_subscriptions,browser_push_deliveries")
            await _record(conn, "0028_owned_memory", "metadata:assistant_memory_scopes,assistant_memory_entries,assistant_memory_versions,assistant_memory_operations")
            await _record(conn, "0021_follow_ups", "metadata:follow_ups,follow_up_executions,business_change_events")
            await _record(conn, "0024_matter_supplements", "metadata:matter_supplements,supplement_texts")
            for version, table, column, sqltype in COLUMN_MIGRATIONS:
                await _record(conn, version, "metadata")
            await _apply_indexes(conn)
        print(f"initialized SQLite schema at {database_url}; baseline recorded")
    finally:
        await engine.dispose()


async def upgrade(database_url: str) -> None:
    """Apply additive changes atomically, preserving rows and previous migration records."""
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            # sqlite3's legacy transaction mode does not begin on the first DDL.
            # Acquire the write transaction before ALTER TABLE so rollback also
            # restores the schema if a later column or index fails.
            await conn.exec_driver_sql("BEGIN IMMEDIATE")
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
            await _record(conn, "0021_follow_ups", "created missing follow-up tables")
            await _record(conn, "0024_matter_supplements", "created missing supplement tables")
            await _record(conn, "0026_agent_response_receipts", "created missing agent_response_receipts")
            await _record(conn, "0027_browser_push", "created missing browser push tables")
            await _record(conn, "0028_owned_memory", "created missing Assistant owned memory tables; no existing data moved or removed")
            await _record(conn, "0029_business_memory_applications", "created missing encrypted attribution table; no existing records changed")
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

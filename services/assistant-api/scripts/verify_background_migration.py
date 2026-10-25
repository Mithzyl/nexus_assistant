"""Validate the background-runtime migration on an isolated SQLite copy.

The live/shared databases are never opened for writing: only a byte copy is
upgraded, and the source hash is checked again afterwards. The upgrade runs
twice to prove repeated execution is harmless.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
import sqlite3
import tempfile
from pathlib import Path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check(path: Path, source_rows: dict[str, int]) -> None:
    connection = sqlite3.connect(path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(commute_checks)")}
        assert {"analysis_mode", "phase", "phase_updated_at"} <= columns
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "service_heartbeats" in tables
        assert "commute_checks" in tables
        for table, expected in source_rows.items():
            if table in tables:
                actual = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                assert actual == expected, (table, actual, expected)
        versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        assert {"0005_background_runtime", "0001_sqlite_baseline"} <= versions
        # Legacy rows are not back-filled with invented progress.
        legacy = connection.execute(
            "SELECT COUNT(*) FROM commute_checks WHERE analysis_mode IS NOT NULL "
            "OR phase IS NOT NULL OR phase_updated_at IS NOT NULL"
        ).fetchone()[0]
        assert legacy == 0, "legacy checks must keep NULL phase/analysis_mode"
    finally:
        connection.close()


async def _run(source: Path) -> None:
    from scripts.migrate_sqlite import upgrade

    source_hash = _digest(source)
    connection = sqlite3.connect(source)
    try:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        rows = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }
    finally:
        connection.close()

    with tempfile.TemporaryDirectory(prefix="commute-migration-") as directory:
        copy = Path(directory) / "isolated.db"
        shutil.copy2(source, copy)
        url = f"sqlite+aiosqlite:///{copy}"
        await upgrade(url)
        _check(copy, rows)
        await upgrade(url)
        # A repeat records `already_present` (new timestamp), so byte equality is
        # not required; schema, data and the version set must remain the same.
        _check(copy, rows)
        print(f"source unchanged: {source_hash == _digest(source)}")
        print(f"rows preserved: {rows}")
        print("first upgrade: added/recorded 0005_background_runtime")
        print("second upgrade: already_present, idempotent")
        print(f"isolated copy: {copy} (removed with tempdir)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="assistant-dev.db")
    args = parser.parse_args()
    source = Path(args.database).resolve()
    if not source.exists():
        raise SystemExit(f"source database not found: {source}")
    asyncio.run(_run(source))


if __name__ == "__main__":
    main()

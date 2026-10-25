"""Classify legacy AI runs on an isolated SQLite copy and report the evidence.

The shared/live database is never opened for writing. This script:

1. copies the source file byte for byte,
2. applies the additive migrations to the copy,
3. classifies every `nexus_runs` row from check links and internal key markers,
4. creates `user_asks` only for rows that are provably chat,
5. prints per-reason statistics and, when asked, keeps the migrated copy.

Running it twice is harmless: rows with a purpose, and existing associations,
are left alone.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
import sqlite3
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from assistant_api.services import ai_tasks, legacy_runs


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Report:
    total_runs: int = 0
    chat: int = 0
    commute: int = 0
    unknown: int = 0
    asks_created: int = 0
    already_classified: int = 0
    reasons: Counter = field(default_factory=Counter)
    commute_without_check: list[str] = field(default_factory=list)
    unknown_runs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total_runs": self.total_runs,
            "chat": self.chat,
            "commute": self.commute,
            "unknown": self.unknown,
            "asks_created": self.asks_created,
            "already_classified": self.already_classified,
            "reasons": dict(self.reasons),
            "commute_without_check": list(self.commute_without_check),
            "unknown_runs": list(self.unknown_runs),
        }


def _classify_copy(database: Path, *, apply: bool) -> Report:
    report = Report()
    connection = sqlite3.connect(database)
    try:
        checks = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT check_id, trigger FROM commute_checks WHERE check_id IS NOT NULL"
            )
        }
        linked = {
            row[0]
            for row in connection.execute(
                "SELECT ai_run_id FROM commute_checks WHERE ai_run_id IS NOT NULL"
            )
        }
        asks = {
            row[0]
            for row in connection.execute("SELECT local_run_id FROM user_asks")
        }
        rows = connection.execute(
            "SELECT local_run_id, user_id, idempotency_key, purpose, request_text, "
            "created_at, updated_at FROM nexus_runs ORDER BY created_at"
        ).fetchall()
        report.total_runs = len(rows)
        for (
            local_run_id,
            user_id,
            idempotency_key,
            purpose,
            request_text,
            created_at,
            updated_at,
        ) in rows:
            verdict = legacy_runs.classify(
                local_run_id=local_run_id,
                idempotency_key=idempotency_key,
                purpose=purpose,
                linked_check_ids=linked,
            )
            report.reasons[verdict.reason] += 1
            if verdict.reason.startswith("already:"):
                report.already_classified += 1
                continue
            if verdict.purpose == ai_tasks.CHAT:
                report.chat += 1
                if local_run_id in asks:
                    continue
                if apply:
                    ask_id = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO user_asks (ask_id, user_id, local_run_id, "
                        "request_text, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                        (
                            ask_id,
                            user_id,
                            local_run_id,
                            request_text,
                            created_at,
                            updated_at,
                        ),
                    )
                    connection.execute(
                        "UPDATE nexus_runs SET purpose=?, trigger=?, input_version=?, "
                        "business_ref=COALESCE(business_ref, ?) WHERE local_run_id=?",
                        (
                            ai_tasks.CHAT,
                            ai_tasks.TRIGGER_CHAT,
                            1,
                            ask_id,
                            local_run_id,
                        ),
                    )
                report.asks_created += 1
            elif verdict.purpose == ai_tasks.COMMUTE_INTERNAL:
                report.commute += 1
                check_id = verdict.check_id if verdict.check_id in checks else None
                if check_id is None:
                    report.commute_without_check.append(local_run_id)
                if apply:
                    trigger = (
                        legacy_runs.TRIGGER_BY_CHECK.get(checks[check_id])
                        if check_id is not None
                        else None
                    )
                    connection.execute(
                        "UPDATE nexus_runs SET purpose=?, trigger=COALESCE(trigger, ?), "
                        "input_version=COALESCE(input_version, 1), "
                        "business_ref=COALESCE(business_ref, ?) WHERE local_run_id=?",
                        (
                            ai_tasks.COMMUTE_INTERNAL,
                            trigger,
                            check_id,
                            local_run_id,
                        ),
                    )
            else:
                report.unknown += 1
                report.unknown_runs.append(local_run_id)
                if apply:
                    connection.execute(
                        "UPDATE nexus_runs SET purpose=? WHERE local_run_id=?",
                        (ai_tasks.LEGACY_UNKNOWN, local_run_id),
                    )
        if apply:
            connection.commit()
    finally:
        connection.close()
    return report


async def migrate_and_backfill(
    source: Path, *, output: Path | None = None, apply: bool = True
) -> tuple[Report, Path | None]:
    """Copy, migrate and classify; return the report and the kept copy (if any).

    The source file is only read: its hash is compared before and after. When
    `output` is given the migrated copy is kept there for inspection; otherwise
    it lives in a temporary directory and is deleted on return.
    """

    from scripts.migrate_sqlite import upgrade

    source_hash = _digest(source)
    if output is None:
        with tempfile.TemporaryDirectory(prefix="ai-task-migration-") as directory:
            copy = Path(directory) / "isolated.db"
            shutil.copy2(source, copy)
            await upgrade(f"sqlite+aiosqlite:///{copy}")
            report = _classify_copy(copy, apply=apply)
            assert _digest(source) == source_hash, "source database must not change"
            return report, None
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    await upgrade(f"sqlite+aiosqlite:///{output}")
    report = _classify_copy(output, apply=apply)
    assert _digest(source) == source_hash, "source database must not change"
    return report, output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="assistant-dev.db")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="classify without writing the association on the copy",
    )
    parser.add_argument(
        "--keep-copy",
        default=None,
        help="write the migrated, backfilled copy to this path for inspection",
    )
    args = parser.parse_args()
    source = Path(args.database).resolve()
    if not source.exists():
        raise SystemExit(f"source database not found: {source}")
    output = Path(args.keep_copy).resolve() if args.keep_copy else None
    source_hash = _digest(source)
    report, _copy = asyncio.run(
        migrate_and_backfill(source, output=output, apply=not args.report_only)
    )
    stats = report.as_dict()
    print("classification:")
    for key in ("total_runs", "chat", "commute", "unknown", "asks_created", "already_classified"):
        print(f"  {key}: {stats[key]}")
    print("evidence:")
    for reason, count in sorted(stats["reasons"].items()):
        print(f"  {reason}: {count}")
    if report.commute_without_check:
        print(
            "internal runs whose check id was not found (kept, no chat link): "
            f"{len(report.commute_without_check)}"
        )
    if report.unknown_runs:
        print(f"unclassifiable runs (kept): {report.unknown_runs}")
    print(f"source unchanged: {_digest(source) == source_hash}")
    print(f"migrated copy: {output if output is not None else '(temporary, deleted)'}")
    print(f"mode: {'applied' if not args.report_only else 'report only'}")


if __name__ == "__main__":
    main()

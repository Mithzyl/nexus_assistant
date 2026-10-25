"""Populate a local backend database with one persisted commute plan/advisory.

This is for backend/API integration only. It uses the explicit mock provider mode
and never contacts Apple. The App is not required to be running or connected.
Production environments are refused.

Usage:
  ASSISTANT_DATABASE_URL=sqlite+aiosqlite:///./commute-dev.db \
  ASSISTANT_ALLOW_SCHEMA_CREATE=true ASSISTANT_ENVIRONMENT=development \
  ASSISTANT_COMMUTE_PROVIDER_MODE=mock \
  ASSISTANT_PROVISION_PASSWORD='<dev password>' \
  .venv/bin/python scripts/seed_commute_dev.py --email commute@example.com

Add --reset to delete existing plans for that user before creating a fresh one.
The password is read from ASSISTANT_PROVISION_PASSWORD, never from argv.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from assistant_api.commands.seed_dev_user import provision
from assistant_api.commute.runtime import build_runtime
from assistant_api.commute.schemas import CommuteLocation, CommutePlanInput
from assistant_api.commute.services import plans as plan_service
from assistant_api.commute.services.checks import (
    claim_check,
    create_manual_check,
    execute_check,
)
from assistant_api.core.config import get_settings
from assistant_api.core.errors import AssistantError
from assistant_api.infrastructure.database import create_engine_and_factory
from assistant_api.models import Base, CommuteAdvisory, User

TIMEZONE = "Asia/Shanghai"
LOCATION_A = CommuteLocation(label="家", latitude=31.2304, longitude=121.4737)
LOCATION_B = CommuteLocation(label="公司", latitude=31.22, longitude=121.45)


def schedule() -> tuple[str, str]:
    now = datetime.now(ZoneInfo(TIMEZONE))
    arrival = now + timedelta(hours=2)
    if arrival.date() != now.date() or arrival.hour > 23:
        arrival = now.replace(hour=23, minute=59)
    check = now - timedelta(hours=1)
    if check.date() != now.date() or check.strftime("%H:%M") >= arrival.strftime("%H:%M"):
        check = now.replace(hour=0, minute=1)
    return arrival.strftime("%H:%M"), check.strftime("%H:%M")


def plan_input(name: str) -> CommutePlanInput:
    arrival, check = schedule()
    return CommutePlanInput(
        name=name,
        enabled=True,
        timezone=TIMEZONE,
        weekdays=[1, 2, 3, 4, 5, 6, 7],
        arrival_local_time=arrival,
        check_local_time=check,
        transport_mode="driving",
        buffer_minutes=10,
        rain_probability_threshold=0.4,
        rain_extra_buffer_minutes=10,
        notify_enabled=True,
        origin=LOCATION_A,
        destination=LOCATION_B,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="commute@example.com")
    parser.add_argument("--display-name", default="通勤联调用户")
    parser.add_argument("--name", default="上班通勤")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if settings.environment == "production":
        raise SystemExit("拒绝在生产环境写入联调数据。")
    if settings.commute_provider_mode != "mock":
        raise SystemExit("该种子脚本只用于 mock provider；不会被生产使用。")
    password = os.environ.get("ASSISTANT_PROVISION_PASSWORD")
    if not password:
        raise SystemExit("请通过 ASSISTANT_PROVISION_PASSWORD 提供本地账号密码（至少 8 位）。")
    if len(password) < 8:
        raise SystemExit("密码至少 8 位。")

    user_id = await provision(
        email=args.email,
        display_name=args.display_name,
        password=password,
        sample=False,
    )
    engine, factory = create_engine_and_factory(settings.database_url)
    if settings.allow_schema_create:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    runtime = build_runtime(settings)
    try:
        async with factory() as db:
            user = await db.get(User, user_id)
            assert user is not None
            existing = await plan_service.list_plans(db, user_id)
            if args.reset:
                for plan in existing:
                    await plan_service.delete_plan(db, user_id=user_id, plan_id=plan.plan_id)
                existing = []
            payload = plan_input(args.name)
            if existing:
                plan = await plan_service.update_plan(
                    db,
                    user_id=user_id,
                    plan_id=existing[0].plan_id,
                    payload=payload,
                    if_match=str(existing[0].revision),
                )
                print(f"updated plan {plan.plan_id} revision={plan.revision}")
            else:
                plan = await plan_service.create_plan(db, user_id, payload)
                print(f"created plan {plan.plan_id} revision={plan.revision}")

            check, _ = await create_manual_check(
                db,
                runtime,
                user_id=user_id,
                plan_id=plan.plan_id,
                idempotency_key=f"seed-{datetime.now(UTC).isoformat()}",
            )
            claimed = await claim_check(
                db, worker_id="seed-commute", now=datetime.now(UTC), lease_seconds=180
            )
            if claimed is not None and claimed.check_id == check.check_id:
                await execute_check(
                    db, runtime, check_id=claimed.check_id, worker_id="seed-commute"
                )
            advisory = await db.scalar(
                select(CommuteAdvisory)
                .where(CommuteAdvisory.plan_id == plan.plan_id)
                .order_by(CommuteAdvisory.generated_at.desc())
                .limit(1)
            )
            if advisory is None:
                print("check did not produce an advisory; inspect commute_checks/error", file=sys.stderr)
                raise SystemExit(2)
            print(f"advisory {advisory.advisory_id} quality={advisory.quality}")
            print(f"login email={args.email} (password from ASSISTANT_PROVISION_PASSWORD)")
    except AssistantError as exc:
        print(f"seed failed: {exc.code}: {exc.message}", file=sys.stderr)
        raise
    finally:
        await runtime.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

"""Provision a local account for development and contract testing.

Mirrors NexusPilot's `commands/provision_local_user.py` boundary: passwords are read
from an interactive prompt or an explicit environment variable, never from argv,
so they do not end up in shell history or process listings.
"""

import argparse
import asyncio
import getpass
import os

from sqlalchemy import select

from assistant_api.core.config import get_settings
from assistant_api.core.security import hash_password
from assistant_api.infrastructure.database import create_engine_and_factory
from assistant_api.models import Base, BusinessRecord, Note, ScheduleEvent, TaskItem, User
from assistant_api.models.base import utc_now


async def provision(*, email: str, display_name: str, password: str, sample: bool) -> str:
    """Create the account if absent and return its id; never reset an existing password."""

    settings = get_settings()
    if settings.environment == "production":
        raise SystemExit("拒绝在生产环境创建开发账户。")

    engine, factory = create_engine_and_factory(settings.database_url)
    try:
        if settings.allow_schema_create:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            normalized = email.strip().lower()
            user = await session.scalar(select(User).where(User.email == normalized))
            if user is None:
                user = User(
                    email=normalized,
                    display_name=display_name,
                    password_verifier=hash_password(password),
                    status="active",
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
                print(f"created user {user.user_id} <{normalized}>")
            else:
                print(f"user already exists {user.user_id} <{normalized}>; password unchanged")

            if sample:
                await _seed_sample(session, user.user_id)
            return user.user_id
    finally:
        await engine.dispose()


async def _seed_sample(session, user_id: str) -> None:
    """Insert the same small data set the contract tests assert against."""

    existing = await session.scalar(select(TaskItem).where(TaskItem.user_id == user_id))
    if existing is not None:
        print("sample data already present; skipped")
        return
    now = utc_now()
    session.add_all(
        [
            TaskItem(user_id=user_id, title="确认周末交通", status="open", due_at=now),
            TaskItem(user_id=user_id, title="交物业费", status="done", due_at=now),
            Note(
                user_id=user_id,
                title="体检报告",
                body="血压正常，建议半年复查。",
                tags_json=["健康"],
            ),
            ScheduleEvent(
                user_id=user_id,
                title="牙医复诊",
                starts_at=now,
                location="口腔医院",
                status="scheduled",
            ),
            BusinessRecord(
                user_id=user_id,
                kind="expense",
                title="午饭",
                amount=28,
                occurred_at=now,
                payload_json={"note": "示例数据"},
            ),
        ]
    )
    await session.commit()
    print("seeded sample data: 2 tasks, 1 note, 1 schedule, 1 expense")


def main() -> None:
    """Parse arguments and run one provisioning pass."""

    parser = argparse.ArgumentParser(description="Create a local Personal Assistant account")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", default="本地用户")
    parser.add_argument("--with-sample-data", action="store_true")
    args = parser.parse_args()

    password = os.environ.get("ASSISTANT_PROVISION_PASSWORD")
    if not password:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat: "):
            raise SystemExit("两次输入不一致。")
    if len(password) < 8:
        raise SystemExit("密码至少 8 位。")

    asyncio.run(
        provision(
            email=args.email,
            display_name=args.display_name,
            password=password,
            sample=args.with_sample_data,
        )
    )


if __name__ == "__main__":
    main()

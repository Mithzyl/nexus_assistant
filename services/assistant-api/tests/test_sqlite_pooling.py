"""SQLite pooling: file databases must not share one connection across sessions.

The "no such savepoint" failure came from `StaticPool` being applied to file
databases: a heartbeat commit could end a task session's savepoint, and concurrent
requests could interfere while creating the same day's commute instance.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool

from assistant_api.core.config import Settings
from assistant_api.infrastructure.database import create_engine_and_factory
from assistant_api.main import create_app

from conftest import auth, login, seed_user
from tests.commute_support import commute_plan_body


@pytest.mark.asyncio
async def test_file_sqlite_database_handles_concurrent_sessions(tmp_path):
    database = tmp_path / "file.db"
    url = f"sqlite+aiosqlite:///{database}"
    engine, factory = create_engine_and_factory(url)
    assert not isinstance(engine.pool, StaticPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE probe (value INTEGER)"))

        async def nested_writer() -> None:
            async with factory() as session:
                async with session.begin_nested():
                    await session.execute(text("INSERT INTO probe VALUES (1)"))
                    await asyncio.sleep(0.05)
                    await session.execute(text("INSERT INTO probe VALUES (2)"))

        async def concurrent_committer() -> None:
            await asyncio.sleep(0.01)
            async with factory() as session:
                await session.execute(text("INSERT INTO probe VALUES (3)"))
                await session.commit()

        await asyncio.gather(nested_writer(), concurrent_committer())

        async with factory() as session:
            count = (await session.execute(text("SELECT COUNT(*) FROM probe"))).scalar_one()
        assert count == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_in_memory_sqlite_keeps_the_shared_connection_pool():
    engine, factory = create_engine_and_factory("sqlite+aiosqlite:///:memory:")
    assert isinstance(engine.pool, StaticPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE probe (value INTEGER)"))
        async with factory() as session:
            await session.execute(text("INSERT INTO probe VALUES (7)"))
            await session.commit()
        async with factory() as session:
            value = (await session.execute(text("SELECT value FROM probe"))).scalar_one()
        assert value == 7
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_check_creation_on_a_file_database_all_succeed(tmp_path):
    settings = Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/app.db",
        nexus_base_url="http://nexus.test",
        nexus_application_token="test-application-token-32-characters",
        tool_callback_token="test-tool-callback-token-32-characters",
        allow_schema_create=True,
    )
    # Pick a timezone where it is currently daytime, so "today 23:00" is in the
    # future regardless of when the suite runs.
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(UTC)
    timezone = "Asia/Shanghai"
    for candidate in (
        "Pacific/Honolulu",
        "America/Los_Angeles",
        "America/New_York",
        "UTC",
        "Europe/London",
        "Asia/Shanghai",
        "Asia/Tokyo",
        "Australia/Sydney",
        "Pacific/Auckland",
    ):
        local_hour = now.astimezone(ZoneInfo(candidate)).hour
        if 6 <= local_hour <= 18:
            timezone = candidate
            break

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://pa") as client:
            await seed_user(app, email="concurrent@example.com", display_name="并发")
            token = await login(client, "concurrent@example.com")
            plan = (
                await client.post(
                    "/api/v1/commute-plans",
                    json=commute_plan_body(
                        timezone=timezone,
                        arrival_local_time="23:00",
                        check_local_time="20:00",
                    ),
                    headers=auth(token),
                )
            ).json()
            responses = await asyncio.gather(
                *[
                    client.post(
                        f"/api/v1/commute-plans/{plan['id']}/checks",
                        headers={
                            **auth(token),
                            "Idempotency-Key": f"concurrent-check-{index:04d}",
                        },
                    )
                    for index in range(4)
                ]
            )
            assert [response.status_code for response in responses] == [202] * 4
            check_ids = {response.json()["check_id"] for response in responses}
            assert len(check_ids) == 4

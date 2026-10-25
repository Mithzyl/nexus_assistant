"""Async engine and session factory construction for the project's SQLite DB.

Pooling is not an implementation detail here. `StaticPool` keeps exactly one
DBAPI connection for the whole process, which is required for an in-memory
database (otherwise each new connection would see an empty schema). It is wrong
for a file-backed database: the API handles concurrent requests, and each
background process runs a heartbeat loop beside its task loop. Two sessions
sharing one connection means one session's COMMIT can end the other session's
SAVEPOINT, which surfaces as `no such savepoint: sa_savepoint_1` (and as
spurious conflicts when two requests create the same day's instance).

File-backed SQLite therefore uses the normal pool, one connection per session,
with a busy timeout so concurrent writers wait instead of failing immediately.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

SQLITE_CONNECT_ARGS = {"check_same_thread": False, "timeout": 30}


def _is_memory_sqlite(database_url: str) -> bool:
    """Return True only for SQLite URLs whose schema lives in one connection."""

    return ":memory:" in database_url or "mode=memory" in database_url


def create_engine_and_factory(database_url: str):
    """Build one engine/session pair with a pool that matches the database."""

    if database_url.startswith("sqlite"):
        if _is_memory_sqlite(database_url):
            engine = create_async_engine(
                database_url,
                poolclass=StaticPool,
                connect_args=dict(SQLITE_CONNECT_ARGS),
            )
        else:
            engine = create_async_engine(
                database_url,
                connect_args=dict(SQLITE_CONNECT_ARGS),
            )
    else:
        engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Yield one session for code that must outlive a single HTTP request."""

    async with factory() as session:
        yield session

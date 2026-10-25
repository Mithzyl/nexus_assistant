"""Async engine and session factory construction for the project's SQLite DB.

SQLite needs a shared static pool for in-memory tests. No other database
dialect is currently supported by this service.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def create_engine_and_factory(database_url: str):
    """Build one engine/session pair; SQLite in-memory keeps a single connection."""

    if database_url.startswith("sqlite"):
        engine = create_async_engine(
            database_url,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Yield one session for code that must outlive a single HTTP request."""

    async with factory() as session:
        yield session

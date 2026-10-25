"""Request-scoped dependencies, resolved from the app instance rather than globals."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant_api.core.config import Settings
from assistant_api.core.errors import UnauthenticatedError
from assistant_api.core.security import digest_token
from assistant_api.models import User, UserSession
from assistant_api.models.base import as_utc
from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.nexus_client import NexusPilotClient


def get_settings(request: Request) -> Settings:
    """Return the settings this app instance was built with."""

    return request.app.state.settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """Return the shared factory for work that must outlive one request."""

    return request.app.state.session_factory


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield one transaction-scoped session and always release it."""

    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


def get_nexus_client(request: Request) -> NexusPilotClient:
    """Return the application-authenticated NexusPilot client."""

    return request.app.state.nexus_client


def get_commute_runtime(request: Request) -> CommuteRuntime:
    """Return this app instance's provider/runtime composition."""

    return request.app.state.commute_runtime


def bearer_token(request: Request) -> str | None:
    """Read one bearer token; reject other schemes instead of guessing."""

    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Resolve the local user from a live session token.

    An expired or revoked session is indistinguishable from a missing one, so a
    stolen token cannot be probed for validity.
    """

    token = bearer_token(request)
    if token is None:
        raise UnauthenticatedError()

    session = await db.scalar(
        select(UserSession).where(UserSession.token_digest == digest_token(token))
    )
    if session is None or session.revoked_at is not None:
        raise UnauthenticatedError()
    if as_utc(session.expires_at) <= datetime.now(UTC):
        raise UnauthenticatedError()

    user = await db.get(User, session.user_id)
    if user is None or user.status != "active":
        raise UnauthenticatedError()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
CommuteRuntimeDependency = Annotated[CommuteRuntime, Depends(get_commute_runtime)]
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]
NexusClient = Annotated[NexusPilotClient, Depends(get_nexus_client)]

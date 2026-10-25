"""Local account login and session lifecycle.

Personal Assistant owns this entirely. A NexusPilot account is not required,
created, or referenced anywhere in this flow.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, delete, select, update

from assistant_api.core.deps import (
    CurrentUser,
    DatabaseSession,
    SettingsDependency,
    bearer_token,
)
from assistant_api.core.errors import AssistantError, UnauthenticatedError
from assistant_api.core.security import digest_token, new_session_token, verify_password
from assistant_api.models import PushDevice, User, UserSession
from assistant_api.models.base import as_utc

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Accept only a local account; there is no provider or application id here."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    """Return the opaque token once; it is never retrievable again."""

    token: str
    expires_at: datetime
    server_time: datetime
    user_id: str


class MeResponse(BaseModel):
    """Describe the authenticated local identity for the client shell."""

    user_id: str
    email: str
    display_name: str


class RenewResponse(BaseModel):
    """Return the unchanged opaque token's new server-side deadline."""

    user: MeResponse
    expires_at: datetime
    server_time: datetime
    token_rotated: bool = False


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
) -> LoginResponse:
    """Issue a device session for a correct local password.

    The same error is returned for an unknown email and a wrong password, so the
    endpoint cannot be used to enumerate accounts.
    """

    normalized = payload.email.strip().lower()
    user = await db.scalar(select(User).where(User.email == normalized))
    if user is None or not verify_password(payload.password, user.password_verifier):
        raise UnauthenticatedError("邮箱或密码不正确。")
    if user.status != "active":
        raise UnauthenticatedError()

    token = new_session_token()
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.session_token_ttl_seconds)
    db.add(
        UserSession(
            user_id=user.user_id,
            token_digest=digest_token(token),
            expires_at=expires_at,
        )
    )
    await db.commit()
    return LoginResponse(token=token, expires_at=expires_at, server_time=now, user_id=user.user_id)


@router.post("/session/renew", response_model=RenewResponse)
async def renew_session(
    request: Request,
    db: DatabaseSession,
    settings: SettingsDependency,
) -> RenewResponse:
    """Extend one live session to server-now + TTL; never resurrect or rotate it."""

    token = bearer_token(request)
    if token is None:
        raise AssistantError("invalid_session", "登录凭据无效。", 401)
    digest = digest_token(token)
    session = await db.scalar(
        select(UserSession).where(UserSession.token_digest == digest)
    )
    if session is None:
        raise AssistantError("invalid_session", "登录凭据无效。", 401)
    now = datetime.now(UTC)
    if session.revoked_at is not None:
        raise AssistantError("session_revoked", "会话已撤销，请重新登录。", 401)
    if as_utc(session.expires_at) <= now:
        raise AssistantError("session_expired", "会话已过期，请重新登录。", 401)
    user = await db.get(User, session.user_id)
    if user is None:
        raise AssistantError("invalid_session", "登录凭据无效。", 401)
    if user.status != "active":
        raise AssistantError("account_disabled", "账号不可用。", 403)
    target = now + timedelta(seconds=settings.session_token_ttl_seconds)
    # Atomic against logout and a concurrent newer renewal: only a live row
    # changes, and the deadline never moves backwards.
    result = await db.execute(
        update(UserSession)
        .where(
            UserSession.session_id == session.session_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .values(expires_at=case((UserSession.expires_at < target, target), else_=UserSession.expires_at))
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await db.rollback()
        raise AssistantError("session_expired", "会话已失效，请重新登录。", 401)
    await db.commit()
    refreshed = await db.get(UserSession, session.session_id)
    assert refreshed is not None
    return RenewResponse(
        user=MeResponse(user_id=user.user_id, email=user.email, display_name=user.display_name),
        expires_at=refreshed.expires_at,
        server_time=now,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    db: DatabaseSession,
    user: CurrentUser,
) -> Response:
    """Revoke the presented session only; other devices stay signed in."""

    token = bearer_token(request)
    session = (
        await db.scalar(
            select(UserSession).where(
                UserSession.token_digest == digest_token(token or ""),
                UserSession.user_id == user.user_id,
            )
        )
        if token
        else None
    )
    if session is not None:
        session.revoked_at = datetime.now(UTC)
        # A logged-out session must not keep receiving the old account's
        # reminders on this installation. Other devices/sessions are untouched.
        await db.execute(
            delete(PushDevice).where(PushDevice.session_id == session.session_id)
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser) -> MeResponse:
    """Return the caller's local identity; never a NexusPilot identifier."""

    return MeResponse(
        user_id=user.user_id, email=user.email, display_name=user.display_name
    )

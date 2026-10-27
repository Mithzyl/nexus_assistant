"""Issue and resolve revocable source-scoped Shortcut credentials."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.core.errors import ConflictError, NotFoundError
from assistant_api.models import ContentSource, SyncUploadToken, User


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read(row: SyncUploadToken) -> dict:
    return {
        "token_id": row.token_id,
        "source_id": row.source_id,
        "display_name": row.display_name,
        "enabled": row.enabled,
        "last_used_at": row.last_used_at,
        "created_at": row.created_at,
        "revoked_at": row.revoked_at,
    }


async def create_token(
    db: AsyncSession, user: User, *, source_id: str, display_name: str
) -> dict:
    source = await db.scalar(
        select(ContentSource).where(
            ContentSource.source_id == source_id,
            ContentSource.user_id == user.user_id,
        )
    )
    if source is None:
        raise NotFoundError("source_not_found", "资料来源不存在。")
    if source.source_type not in ("calendar", "reminders"):
        raise ConflictError("source_not_uploadable", "该来源不能用于快捷指令上传。")
    if not source.sync_enabled:
        raise ConflictError("source_sync_disabled", "该来源已关闭上传。")
    credential = "sc_" + secrets.token_urlsafe(32)
    row = SyncUploadToken(
        user_id=user.user_id,
        source_id=source.source_id,
        display_name=display_name,
        token_digest=_digest(credential),
        enabled=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {
        **_read(row),
        "credential": credential,
        "credential_returned_once": True,
    }


async def list_tokens(db: AsyncSession, user: User) -> list[dict]:
    rows = await db.scalars(
        select(SyncUploadToken)
        .where(SyncUploadToken.user_id == user.user_id)
        .order_by(SyncUploadToken.created_at.desc())
    )
    return [_read(row) for row in rows]


async def revoke_token(db: AsyncSession, user: User, token_id: str) -> dict:
    row = await db.scalar(
        select(SyncUploadToken).where(
            SyncUploadToken.token_id == token_id,
            SyncUploadToken.user_id == user.user_id,
        )
    )
    if row is None:
        raise NotFoundError("shortcut_token_not_found", "设备上传凭据不存在。")
    row.enabled = False
    row.revoked_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return _read(row)


async def resolve_token(
    db: AsyncSession, credential: str
) -> tuple[User, SyncUploadToken]:
    from assistant_api.core.errors import UnauthenticatedError

    row = await db.scalar(
        select(SyncUploadToken).where(
            SyncUploadToken.token_digest == _digest(credential),
            SyncUploadToken.enabled.is_(True),
        )
    )
    if row is None:
        raise UnauthenticatedError("设备上传凭据无效或已撤销。")
    user = await db.get(User, row.user_id)
    if user is None or user.status != "active":
        raise UnauthenticatedError("设备上传凭据无效或已撤销。")
    row.last_used_at = datetime.now(UTC)
    return user, row


__all__ = ["create_token", "list_tokens", "resolve_token", "revoke_token"]

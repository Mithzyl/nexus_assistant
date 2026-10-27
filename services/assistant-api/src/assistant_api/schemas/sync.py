"""Minimal device-scoped credentials for manually run Shortcuts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from assistant_api.commute.schemas import UtcDateTime


class ShortcutTokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=8, max_length=36)
    display_name: str = Field(default="快捷指令上传", min_length=1, max_length=120)


class ShortcutTokenRead(BaseModel):
    token_id: str
    source_id: str
    display_name: str
    enabled: bool
    last_used_at: UtcDateTime | None = None
    created_at: UtcDateTime
    revoked_at: UtcDateTime | None = None


class ShortcutTokenCreated(ShortcutTokenRead):
    credential: str
    credential_returned_once: bool = True

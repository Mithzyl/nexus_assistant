"""Private calendar subscription management contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from assistant_api.commute.schemas import UtcDateTime


class CalendarSubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matter_ids: list[str] = Field(min_length=1, max_length=100)


class CalendarSubscriptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    matter_ids: list[str] | None = Field(default=None, min_length=0, max_length=100)


class CalendarSubscriptionRead(BaseModel):
    subscription_id: str
    enabled: bool
    matter_ids: list[str]
    last_read_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    feed_url: str | None = None
    link_returned_once: bool = False
    note: str | None = None


class CalendarSubscriptionStatus(BaseModel):
    exists: bool
    subscription: CalendarSubscriptionRead | None = None

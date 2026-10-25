"""Per-account Bark binding and explicit test contracts."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC).isoformat().replace("+00:00", "Z")


class BarkStatusRead(BaseModel):
    configured: bool
    auto_reminders_enabled: bool
    available: bool
    state: Literal["unconfigured", "disabled", "enabled"]
    base_url_host: str = "api.day.app"
    last_test_status: str | None = None
    last_test_at: datetime | None = None

    @field_serializer("last_test_at")
    def _utc(self, value: datetime | None) -> str | None:
        return _iso(value)


class BarkBindingWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Length is checked in the service so validation errors never echo the key.
    device_key: str


class BarkSettingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_reminders_enabled: bool


class BarkTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_send: Literal[True]


class BarkTestRead(BaseModel):
    request_submitted: bool = True
    status: Literal["accepted", "failed", "unknown"]
    accepted: bool
    message: str
    tested_at: datetime

    @field_serializer("tested_at")
    def _utc(self, value: datetime) -> str:
        return _iso(value) or ""

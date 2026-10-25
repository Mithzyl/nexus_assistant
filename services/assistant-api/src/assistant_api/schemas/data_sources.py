"""Contracts for local-content sources, authorization and upload batches."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

SourceType = Literal["calendar", "reminders", "notes"]
ItemKind = Literal["calendar_event", "reminder", "note"]

KIND_FOR_SOURCE: dict[str, str] = {
    "calendar": "calendar_event",
    "reminders": "reminder",
    "notes": "note",
}
FIELDS_FOR_SOURCE: dict[str, set[str]] = {
    "calendar": {"title", "start", "end", "location", "notes", "status"},
    "reminders": {"title", "due", "notes", "status"},
    "notes": {"title", "body", "tags"},
}
DEFAULT_FIELDS: dict[str, list[str]] = {
    "calendar": ["title", "start", "end", "location"],
    "reminders": ["title", "due", "status"],
    "notes": ["title", "body"],
}


class DataSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: SourceType
    device_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)
    scope_keys: list[str] = Field(default_factory=list, max_length=100)
    upload_fields: list[str] = Field(default_factory=list)
    sync_enabled: bool = True
    assistant_enabled: bool = False

    @field_validator("scope_keys")
    @classmethod
    def _scopes(cls, value: list[str]) -> list[str]:
        return _unique_strings(value, "scope_keys", 255)

    @model_validator(mode="after")
    def _fields(self):
        allowed = FIELDS_FOR_SOURCE[self.source_type]
        fields = self.upload_fields or DEFAULT_FIELDS[self.source_type]
        if not set(fields) <= allowed:
            raise ValueError("upload_fields contains a field not available for this source")
        self.upload_fields = _unique_strings(fields, "upload_fields", 64)
        return self


class DataSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    sync_enabled: bool | None = None
    assistant_enabled: bool | None = None
    upload_fields: list[str] | None = None
    scope_keys: list[str] | None = None

    @field_validator("scope_keys")
    @classmethod
    def _scopes(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _unique_strings(value, "scope_keys", 255)

    @field_validator("upload_fields")
    @classmethod
    def _fields(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _unique_strings(value, "upload_fields", 64)

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class SyncItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=255)
    scope_key: str = Field(min_length=1, max_length=255)
    version: int = Field(default=1, ge=1, le=2_147_483_647)
    updated_at: datetime
    title: str | None = Field(default=None, max_length=255)
    body: str | None = Field(default=None, max_length=100_000)
    notes: str | None = Field(default=None, max_length=20_000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    due_at: datetime | None = None
    location: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=30)


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(min_length=1, max_length=128)
    items: list[SyncItem] = Field(default_factory=list, max_length=500)
    removed_external_ids: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _disjoint(self):
        removed = set(self.removed_external_ids)
        if len(removed) != len(self.removed_external_ids):
            raise ValueError("removed_external_ids contains duplicates")
        if removed & {item.external_id for item in self.items}:
            raise ValueError("an item cannot be both upserted and removed")
        return self


class SyncResponse(BaseModel):
    batch_id: str
    created: int
    updated: int
    unchanged: int
    removed: int
    last_sync_at: datetime

    @field_serializer("last_sync_at")
    def _utc(self, value: datetime) -> str:
        return _iso_utc(value)


class DataSourceRead(BaseModel):
    source_id: str
    source_type: SourceType
    device_id: str
    display_name: str
    sync_enabled: bool
    assistant_enabled: bool
    upload_fields: list[str]
    scope_keys: list[str]
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_error_code: str | None
    item_count: int

    @field_serializer("last_sync_at")
    def _utc(self, value: datetime | None) -> str | None:
        return _iso_utc(value) if value else None


class ContentItemRead(BaseModel):
    item_id: str
    source_id: str
    source_type: SourceType
    source_display_name: str
    kind: ItemKind
    external_id: str
    scope_key: str
    version: int
    title: str
    body: str
    starts_at: datetime | None
    ends_at: datetime | None
    due_at: datetime | None
    status: str | None
    location: str | None
    tags: list[str]
    source_updated_at: datetime
    synced_at: datetime

    @field_serializer("starts_at", "ends_at", "due_at", "source_updated_at", "synced_at")
    def _utc(self, value: datetime | None) -> str | None:
        return _iso_utc(value) if value else None


class ContentListResponse(BaseModel):
    items: list[ContentItemRead]
    next_cursor: str | None = None


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _unique_strings(values: list[str], field: str, max_length: int) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = value.strip()
        if not item or len(item) > max_length:
            raise ValueError(f"{field} contains an invalid value")
        if item in cleaned:
            raise ValueError(f"{field} contains duplicates")
        cleaned.append(item)
    return cleaned

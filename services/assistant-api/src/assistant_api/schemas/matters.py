"""Request/response DTOs for matters, drafts, reminders, today and iOS sync.

Timestamps are always UTC ISO-8601 with a trailing Z. Inputs accept an ISO string
with an offset; a naive input is interpreted in the user's timezone by the
service. Nothing here reuses `source_run_id` for a record's origin.
"""


from __future__ import annotations

from assistant_api.schemas.memory import SharedMemoryDisclosure

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from assistant_api.commute.schemas import UtcDateTime
from assistant_api.commute.timeutil import ensure_aware

MatterState = Literal["active", "waiting", "archived", "deleted"]
MatterGroup = Literal["active", "waiting", "archived", "all"]
ScheduleSource = Literal["web_native", "ios_eventkit", "ios_reminders"]
SyncStatus = Literal["local_only", "pending_ios", "ios_synced"]
DraftStatus = Literal["preview", "confirmed", "cancelled"]
TodoStatus = Literal["open", "done"]
ReminderStatus = Literal["scheduled", "due", "acknowledged", "cancelled"]
CoordinateSystem = Literal["gcj02", "wgs84", "unknown"]
PlaceSearchType = Literal["place", "address"]


class Coordinate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    coordinate_system: CoordinateSystem = "unknown"
    city: str | None = Field(default=None, max_length=64)


class SchedulePayload(BaseModel):
    """Formal appointment fields; `schedule_id` means "update this one"."""

    model_config = ConfigDict(extra="forbid")

    schedule_id: str | None = None
    title: str = Field(min_length=1, max_length=255)
    starts_at: datetime
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    coordinate_system: CoordinateSystem = "unknown"
    location_city: str | None = Field(default=None, max_length=64)
    location_address: str | None = Field(default=None, max_length=255)
    location_source: str | None = Field(default=None, max_length=32)
    location_external_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _check(self) -> "SchedulePayload":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude 和 longitude 必须同时提供或同时为空。")
        if self.ends_at is not None and ensure_aware(self.ends_at) <= ensure_aware(
            self.starts_at
        ):
            raise ValueError("日程结束时间必须晚于开始时间。")
        return self


class TodoPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo_id: str | None = None
    title: str = Field(min_length=1, max_length=255)
    due_at: datetime | None = None
    reminder_at: datetime | None = None
    status: TodoStatus = "open"


class DraftPayload(BaseModel):
    """Editable proposal shown in the confirmation sheet."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255)
    goal: str | None = Field(default=None, max_length=4000)
    changes_note: str | None = Field(default=None, max_length=2000)
    next_step: str | None = Field(default=None, max_length=500)
    schedule: SchedulePayload | None = None
    todos: list[TodoPayload] = Field(default_factory=list, max_length=30)
    complete_todo_ids: list[str] = Field(default_factory=list, max_length=30)


class TodoRead(BaseModel):
    todo_id: str
    title: str
    status: TodoStatus
    position: int
    due_at: UtcDateTime | None = None
    reminder_at: UtcDateTime | None = None
    completed_at: UtcDateTime | None = None


class ProgressRead(BaseModel):
    progress_id: str
    text: str
    kind: str
    created_at: UtcDateTime


class ScheduleRead(BaseModel):
    schedule_id: str
    matter_id: str | None = None
    title: str
    starts_at: UtcDateTime
    ends_at: UtcDateTime | None = None
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    coordinate_system: CoordinateSystem = "unknown"
    location_city: str | None = None
    location_address: str | None = None
    location_source: str | None = None
    location_external_id: str | None = None
    status: str
    source: ScheduleSource | None = None
    sync_status: SyncStatus | None = None
    deleted_at: UtcDateTime | None = None


class TodayRouteRead(BaseModel):
    status: Literal["available", "unverified", "unavailable"]
    reason_code: str | None = None
    message: str | None = None
    advisory_id: str | None = None
    duration_seconds: int | None = None
    mode: Literal["driving", "walking", "transit"] | None = None
    fetched_at: UtcDateTime | None = None
    origin_basis: Literal["user_confirmed", "previous_schedule"] | None = None


class TodayScheduleRead(ScheduleRead):
    matter_title: str | None = None
    route: TodayRouteRead


class ReminderRead(BaseModel):
    reminder_id: str
    matter_id: str | None = None
    todo_id: str | None = None
    schedule_id: str | None = None
    title: str
    note: str | None = None
    trigger_at: UtcDateTime
    status: ReminderStatus
    sync_status: SyncStatus | None = None
    source: ScheduleSource | None = None
    completed_at: UtcDateTime | None = None
    acknowledged_at: UtcDateTime | None = None
    cancelled_at: UtcDateTime | None = None
    deleted_at: UtcDateTime | None = None


class MatterRead(BaseModel):
    archive_effect: dict[str, int] | None = None
    matter_id: str
    title: str
    goal: str
    status: MatterState
    version: int
    next_step: str | None = None
    last_progress_at: UtcDateTime | None = None
    latest_progress: str | None = None
    open_todos: int = 0
    created_at: UtcDateTime
    updated_at: UtcDateTime


class MatterDetailRead(MatterRead):
    schedules: list[ScheduleRead] = Field(default_factory=list)
    todos: list[TodoRead] = Field(default_factory=list)
    progress: list[ProgressRead] = Field(default_factory=list)
    reminders: list[ReminderRead] = Field(default_factory=list)


class MatterListRead(BaseModel):
    items: list[MatterRead]
    next_cursor: str | None = None


class MatterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    goal: str = Field(default="", max_length=4000)
    next_step: str | None = Field(default=None, max_length=500)


class MatterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    goal: str | None = Field(default=None, max_length=4000)
    status: Literal["active", "waiting"] | None = None
    next_step: str | None = Field(default=None, max_length=500)


class MatterVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    cancel_reminders: bool = False
    cancel_future_schedules: bool = True


class ConflictOptionRead(BaseModel):
    option_id: Literal["A", "B", "C"]
    label: str
    detail: str
    recommended: bool = False
    proposed_start: UtcDateTime | None = None
    creates_todo: str | None = None
    shift_seconds: int = 0
    target_schedule_id: str | None = None
    executable: bool = True
    unavailable_reason: str | None = None
    affected: list[dict] = Field(default_factory=list)


class ConflictItemRead(BaseModel):
    schedule_id: str | None = None
    title: str
    starts_at: UtcDateTime
    ends_at: UtcDateTime | None = None
    gap_seconds: int
    eta_seconds: int | None = None
    eta_status: Literal["available", "unverified", "not_applicable"]
    relation: Literal["previous", "next", "overlap"] = "previous"
    reason: str


class ConflictRead(BaseModel):
    has_conflict: bool
    severity: Literal["none", "tight", "overlap"] = "none"
    window: dict[str, str]
    conflicts: list[ConflictItemRead] = Field(default_factory=list)
    options: list[ConflictOptionRead] = Field(default_factory=list)
    eta_note: str | None = None


class DraftActionRead(BaseModel):
    kind: str
    role: str | None = None
    schedule_id: str | None = None
    todo_id: str | None = None
    reminder_id: str | None = None
    title: str | None = None
    starts_at: UtcDateTime | None = None
    due_at: UtcDateTime | None = None
    reminder_at: UtcDateTime | None = None
    trigger_at: UtcDateTime | None = None
    location: str | None = None
    status: str | None = None
    reason: str | None = None
    selected: bool | None = None
    cancellable: bool | None = None


class CommunicationDraftRead(BaseModel):
    reply_text: str
    option_id: str
    target_title: str
    target_starts_at: UtcDateTime | None = None
    external_change_required: bool = False
    external_change_status: Literal["not_sent", "pending_external_confirmation"]


class DraftRead(BaseModel):
    draft_id: str
    kind: Literal["create", "update"]
    status: DraftStatus
    version: int
    matter_id: str | None = None
    payload: dict
    conflicts: ConflictRead | None = None
    action_plan: list[DraftActionRead] = Field(default_factory=list)
    communication_draft: CommunicationDraftRead | None = None
    candidates: list[dict] | None = None
    ai_run_id: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class MemoryDisclosure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: str = Field(min_length=36, max_length=36)
    expected_version: int = Field(ge=1)
    confirmed_sensitive: Literal[True]


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=8, max_length=128)
    continuation_run_id: str | None = Field(default=None, min_length=8, max_length=36)
    memory_disclosures: list[MemoryDisclosure | SharedMemoryDisclosure] = Field(default_factory=list, max_length=10)


class IntakeResponse(BaseModel):
    draft: DraftRead | None = None
    replayed: bool = False
    intent: Literal["matter", "question"] = "matter"
    outcome: Literal[
        "answer", "draft", "needs_clarification", "processing", "unknown"
    ] = "draft"
    message: str | None = None
    answer: str | None = None
    clarify: str | None = None
    pending_context: dict = Field(default_factory=dict)
    local_run_id: str | None = None
    parent_run_id: str | None = None


class IntakeResultRead(BaseModel):
    confirm_status: Literal["confirmed", "unconfirmed", "unknown"] = "unknown"
    target_lifecycle: Literal["active", "archived", "deleted", "source_revoked"] | None = None
    target_title: str | None = None
    history_target_id: str | None = None
    local_run_id: str
    parent_run_id: str | None = None
    request_text: str
    intent: Literal["matter", "question"] = "matter"
    outcome: Literal[
        "answer",
        "draft",
        "needs_clarification",
        "processing",
        "unknown",
        "failed",
        "cancelled",
    ]
    status: str
    answer: str | None = None
    clarify: str | None = None
    pending_context: dict = Field(default_factory=dict)
    matter_id: str | None = None
    draft_id: str | None = None
    draft: DraftRead | None = None
    created_at: UtcDateTime
    completed_at: UtcDateTime | None = None


class MatterEditRequest(BaseModel):
    """Create an editable draft from one existing schedule or todo."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=128)
    schedule_id: str | None = None
    todo_id: str | None = None

    @model_validator(mode="after")
    def _one_target(self) -> "MatterEditRequest":
        if (self.schedule_id is None) == (self.todo_id is None):
            raise ValueError("schedule_id 和 todo_id 必须且只能提供一个。")
        return self


class ChangeRead(BaseModel):
    change_id: str
    matter_id: str
    source_matter_id: str
    source_matter_title: str | None = None
    relation: Literal["primary", "affected"] = "primary"
    affected_matter_ids: list[str] = Field(default_factory=list)
    change_type: str
    draft_id: str | None = None
    created_at: UtcDateTime
    schedule_id: str | None = None
    before: dict
    after: dict
    revertable: bool
    initial_delete_allowed: bool = False
    reason_code: str | None = None
    reason: str | None = None


class DraftPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    goal: str | None = Field(default=None, max_length=4000)
    changes_note: str | None = Field(default=None, max_length=2000)
    next_step: str | None = Field(default=None, max_length=500)
    schedule: SchedulePayload | None = None
    todos: list[TodoPayload] | None = None
    complete_todo_ids: list[str] | None = None
    cancel_schedule_ids: list[str] | None = Field(default=None, max_length=30)


class DraftSelectMatterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    matter_id: str = Field(min_length=8, max_length=36)


class DraftOptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    option_id: Literal["A", "B", "C"]


class RevertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    matter_id: str | None = None
    create_schedule: bool = True
    sync_to_ios: bool = False
    acknowledge_conflict: bool = False
    option_id: Literal["A", "B", "C"] | None = None


class ConfirmResponse(BaseModel):
    draft: DraftRead
    matter: MatterRead
    schedule: ScheduleRead | None = None
    reminders: list[ReminderRead] = Field(default_factory=list)
    change_id: str
    replayed: bool = False
    applied: bool = True
    cancelled_reminder_ids: list[str] = Field(default_factory=list)
    cancelled_schedule_ids: list[str] = Field(default_factory=list)
    notifications_retracted: bool = False
    conflict: ConflictRead | None = None
    change: dict | None = None


class RevertResponse(BaseModel):
    matter: MatterRead
    change_id: str
    reverted_at: UtcDateTime


class InitialDeleteResponse(BaseModel):
    matter_id: str
    deleted_at: UtcDateTime
    schedule_ids: list[str] = Field(default_factory=list)
    todo_ids: list[str] = Field(default_factory=list)
    reminder_ids: list[str] = Field(default_factory=list)


class TodayReminderRead(ReminderRead):
    matter_title: str | None = None


class TodayDeadlineRead(BaseModel):
    todo_id: str
    matter_id: str
    matter_title: str
    title: str
    due_at: UtcDateTime
    status: TodoStatus
    reminder_at: UtcDateTime | None = None


class TodayDraftRead(BaseModel):
    draft_id: str
    matter_id: str | None = None
    matter_title: str | None = None
    kind: Literal["create", "update"]
    version: int
    updated_at: UtcDateTime
    schedule: dict | None = None
    conflict: ConflictRead | None = None
    impact: str


class TodayChangeRead(BaseModel):
    change_id: str
    matter_id: str
    matter_title: str | None = None
    change_type: str
    created_at: UtcDateTime
    summary: str


class TodayRead(BaseModel):
    date: str
    timezone: str
    schedules: list[TodayScheduleRead]
    reminders: list[TodayReminderRead]
    pending_drafts: int
    deadlines: list[TodayDeadlineRead] = Field(default_factory=list)
    drafts: list[TodayDraftRead] = Field(default_factory=list)
    changes: list[TodayChangeRead] = Field(default_factory=list)


class EstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Coordinate | None = None
    origin_schedule_id: str | None = Field(default=None, min_length=8, max_length=36)
    destination: Coordinate
    mode: Literal["driving", "walking", "transit"] = "driving"
    depart_at: datetime | None = None
    arrive_by: datetime | None = None
    schedule_id: str | None = Field(default=None, min_length=8, max_length=36)
    origin_basis: Literal["user_confirmed", "previous_schedule"] = "user_confirmed"

    @model_validator(mode="after")
    def _one_time(self) -> "EstimateRequest":
        if self.origin is not None and self.origin_schedule_id is not None:
            raise ValueError("origin 和 origin_schedule_id 只能提供一个。")
        if self.origin is None and self.origin_schedule_id is None:
            raise ValueError("origin 或 origin_schedule_id 必须提供一个。")
        if (
            self.origin_basis == "previous_schedule"
            and self.origin_schedule_id is None
        ):
            raise ValueError("previous_schedule 起点必须提供 origin_schedule_id。")
        if self.depart_at is None and self.arrive_by is None:
            raise ValueError("depart_at 或 arrive_by 至少提供一个。")
        return self


class RoutePointRead(BaseModel):
    latitude: float
    longitude: float


class RouteEndpointRead(RoutePointRead):
    coordinate_system: CoordinateSystem
    city: str | None = None
    basis: Literal["user_confirmed", "previous_schedule", "destination_schedule"]
    schedule_id: str | None = None


class RouteSegmentRead(BaseModel):
    kind: Literal["driving", "walking", "transit"]
    label: str | None = None
    coordinate_system: CoordinateSystem
    points: list[RoutePointRead] = Field(min_length=2)


class RouteShapeRead(BaseModel):
    status: Literal["available", "partial", "missing"] = "missing"
    segments: list[RouteSegmentRead] = Field(default_factory=list)


class EstimateResponse(BaseModel):
    status: Literal["available", "unavailable"]
    provider: str
    mode: Literal["driving", "walking", "transit"]
    duration_seconds: int | None = None
    distance_meters: int | None = None
    walking_distance_meters: int | None = None
    transfer_count: int | None = None
    transfers_summary: str | None = None
    fare_cny: float | None = None
    fare_kind: str | None = None
    fare_notice: str | None = None
    fetched_at: UtcDateTime | None = None
    request_depart_at: UtcDateTime | None = None
    target_arrival_at: UtcDateTime | None = None
    source: Literal["server_provider"] = "server_provider"
    reason_code: str | None = None
    timing_basis: str | None = None
    accuracy_notice: str | None = None
    origin_basis: Literal["user_confirmed", "previous_schedule"] = "user_confirmed"
    origin_schedule_id: str | None = None
    schedule_id: str | None = None
    origin: RouteEndpointRead | None = None
    destination: RouteEndpointRead | None = None
    shape: RouteShapeRead = Field(default_factory=RouteShapeRead)


class PlaceSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=255)
    city: str | None = Field(default=None, max_length=64)
    search_type: PlaceSearchType = "place"
    limit: int = Field(default=5, ge=1, le=10)
    draft_id: str = Field(min_length=8, max_length=36)


class PlaceCandidateRead(BaseModel):
    provider: str
    provider_place_id: str
    name: str
    address: str | None = None
    city: str | None = None
    latitude: float
    longitude: float
    coordinate_system: CoordinateSystem
    source: str
    selection_token: str


class PlaceSearchResponse(BaseModel):
    query: str
    city: str | None = None
    search_type: PlaceSearchType
    ambiguous: bool
    candidates: list[PlaceCandidateRead] = Field(default_factory=list)
    reason_code: str | None = None


class PlaceSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(min_length=8, max_length=36)
    selection_token: str = Field(min_length=32, max_length=4096)


class MapPickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(min_length=8, max_length=36)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    coordinate_system: Literal["gcj02"] = "gcj02"


class MapPickLocationRead(BaseModel):
    provider: str
    provider_place_id: str | None = None
    name: str
    address: str
    city: str | None = None
    latitude: float
    longitude: float
    coordinate_system: CoordinateSystem
    source: str
    selection_token: str


class MapPickResponse(BaseModel):
    status: Literal["available", "unavailable"]
    reason_code: str | None = None
    address_notice: str | None = None
    location: MapPickLocationRead | None = None


class ScheduledRouteRead(BaseModel):
    status: Literal["available", "unverified"]
    reason_code: str | None = None
    message: str | None = None
    schedule_id: str
    provider: str | None = None
    mode: Literal["driving", "walking", "transit"] | None = None
    duration_seconds: int | None = None
    distance_meters: int | None = None
    walking_distance_meters: int | None = None
    transfer_count: int | None = None
    transfers_summary: str | None = None
    fare_cny: float | None = None
    fare_kind: str | None = None
    fare_notice: str | None = None
    origin_basis: Literal["user_confirmed", "previous_schedule"] | None = None
    origin_schedule_id: str | None = None
    fetched_at: UtcDateTime | None = None
    requested_depart_at: UtcDateTime | None = None
    target_arrival_at: UtcDateTime | None = None
    calculated_at: UtcDateTime | None = None
    timing_basis: str | None = None
    accuracy_notice: str | None = None
    origin: RouteEndpointRead | None = None
    destination: RouteEndpointRead | None = None
    shape: RouteShapeRead = Field(default_factory=RouteShapeRead)


class IosRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=255)
    scope_key: str | None = Field(default=None, max_length=255)
    last_modified_at: datetime
    deleted: bool = False
    title: str = Field(default="", max_length=255)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    due_at: datetime | None = None
    location: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=4000)
    version: int | None = Field(default=None, ge=1)


class IosSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=8, max_length=36)
    batch_id: str = Field(min_length=8, max_length=128)
    records: list[IosRecord] = Field(default_factory=list, max_length=500)
    removed_external_ids: list[str] = Field(default_factory=list, max_length=500)


class IosSnapshotResponse(BaseModel):
    batch_id: str
    source: ScheduleSource
    created: int
    updated: int
    unchanged: int
    removed: int
    last_sync_at: UtcDateTime
    replayed: bool = False


class PendingActionRead(BaseModel):
    action_id: str
    record_type: Literal["schedule", "reminder"]
    record_id: str
    operation: Literal["upsert", "delete"]
    sync_status: SyncStatus
    payload: dict
    updated_at: UtcDateTime


class PendingActionsResponse(BaseModel):
    generated_at: UtcDateTime
    items: list[PendingActionRead]
    note: str = (
        "这些记录只表示“等待 iOS 端执行”，不代表已写入苹果系统日历或提醒。"
    )


__all__ = [name for name in dir() if name[0].isupper()]

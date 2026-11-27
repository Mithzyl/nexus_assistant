"""Request/response DTOs for matters, drafts, reminders, today and iOS sync.

Timestamps are always UTC ISO-8601 with a trailing Z. Inputs accept an ISO string
with an offset; a naive input is interpreted in the user's timezone by the
service. Nothing here reuses `source_run_id` for a record's origin.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from assistant_api.commute.schemas import UtcDateTime, CommutePlanRead
from assistant_api.commute.providers.base import TransportMode
from assistant_api.commute.timeutil import ensure_aware

from assistant_api.schemas.places import (
    CoordinateSystem, PlaceSearchType, PlaceLookupRequest, PlaceLookupCandidate,
    PlaceLookupResponse, PlacePointRequest, PlacePointLocation, PlacePointResponse,
)
from assistant_api.schemas.preferences import MemorySelection, MemoryInputOptions, memory_source_constraints

MatterState = Literal["active", "waiting", "archived", "deleted"]
MatterGroup = Literal["active", "waiting", "archived", "all"]
ScheduleSource = Literal["web_native", "ios_eventkit", "ios_reminders"]
SyncStatus = Literal["local_only", "pending_ios", "ios_synced"]
DraftStatus = Literal["preview", "confirmed", "cancelled"]
TodoStatus = Literal["open", "done"]
ReminderStatus = Literal["scheduled", "due", "acknowledged", "cancelled"]


class Coordinate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    coordinate_system: CoordinateSystem = "unknown"
    city: str | None = Field(default=None, max_length=64)


class SchedulePayload(BaseModel):
    """Formal appointment fields; `schedule_id` means "update this one"."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"allOf": [
        {"oneOf": [
            {"required": ["latitude", "longitude"], "properties": {"latitude": {"type": "number"}, "longitude": {"type": "number"}}},
            {"properties": {"latitude": {"type": "null"}, "longitude": {"type": "null"}}},
        ]},
        {"not": {"required": ["reminder_at", "reminder_offset_seconds"], "properties": {"reminder_at": {"type": "string"}, "reminder_offset_seconds": {"type": "integer"}}}},
    ]})

    schedule_id: str | None = None
    title: str = Field(min_length=1, max_length=255)
    starts_at: datetime
    ends_at: datetime | None = Field(default=None, description="结束时间必须晚于starts_at；未给出结束时间时省略。")
    reminder_at: datetime | None = Field(default=None, description="可选的日程绝对提醒时间；与reminder_offset_seconds二选一，不创建待办。")
    reminder_offset_seconds: int | None = Field(default=None, ge=-31536000, le=31536000, description="明确要求日程开始前后多久提醒时提供秒数，提前为负数；修改日程时随开始时间重算。")
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
        """Require paired coordinates, ordered times and one explicitly selected reminder timing mode."""
        if self.reminder_at is not None and self.reminder_offset_seconds is not None:
            raise ValueError("日程绝对提醒与相对提醒只能选择一个。")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude 和 longitude 必须同时提供或同时为空。")
        if self.starts_at is not None and self.ends_at is not None and ensure_aware(self.ends_at) <= ensure_aware(
            self.starts_at
        ):
            raise ValueError("日程结束时间必须晚于开始时间。")
        return self


class ScheduleRelativeReminder(BaseModel):
    """An explicit dependency on a schedule time; omitted ID refers only to a same-request schedule."""
    model_config = ConfigDict(extra="forbid")
    schedule_id: str | None = Field(default=None, min_length=1, max_length=36)
    offset_seconds: int = Field(ge=-31536000, le=31536000)


class TodoPlace(BaseModel):
    """A confirmed label or address, optionally located in an explicitly named coordinate system."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"oneOf": [
        {"properties": {key: {"type": "null"} for key in ("latitude", "longitude", "coordinate_system")}},
        {"required": ["latitude", "longitude", "coordinate_system"],
         "properties": {"latitude": {"type": "number"}, "longitude": {"type": "number"},
                        "coordinate_system": {"type": "string"}}},
    ]})
    label: str = Field(min_length=1, max_length=255)
    address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=64)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    coordinate_system: Literal["gcj02", "wgs84", "bd09"] | None = None

    @model_validator(mode="after")
    def check_coordinates(self):
        """Reject partial coordinates, unspecified systems and empty names."""
        if not self.label.strip() or len({self.latitude is None, self.longitude is None,
                                          self.coordinate_system is None}) != 1:
            raise ValueError("地点需要名称；经度、纬度、坐标系必须一起提供。")
        return self


class TodoScheduleDue(ScheduleRelativeReminder):
    """Keep a deadline bound to a meeting start instead of freezing a model-computed timestamp."""
    schedule_id: str | None = Field(default=None, min_length=1, max_length=36,
        description="真实日程 ID；仅组合草稿明确引用本次一并保存的日程时可省略。原日程必须属于同一账号和事项。")
    offset_seconds: int = Field(ge=-31536000, le=31536000, strict=True,
        description="相对日程开始时间的有符号秒数，例如会前30分钟传 -1800。日程改期由后端重算。")


TODO_MEMORY_FIELDS = {"due_at", "reminder_at", "priority", "estimated_duration_minutes", "available_after", "place", "schedule_due"}
TODO_TIMING_SCHEMA = {"allOf": [
    {"not": {"required": [absolute, relative],
             "properties": {absolute: {"type": "string"}, relative: {"type": "object"}}}}
    for absolute, relative in (("due_at", "schedule_due"), ("reminder_at", "schedule_reminder"))
]}


class TodoPlanningFields(BaseModel):
    """Optional inputs for ranking, estimating available time and tracking explicit deadlines."""
    priority: Literal["low", "normal", "high", "urgent"] | None = Field(default=None,
        description="明确优先级：low/normal/high/urgent；未提供时未知，不替用户猜。")
    estimated_duration_minutes: int | None = Field(default=None, ge=1, le=1440, strict=True,
        description="预计处理耗时，单位分钟，不含路程；未知则省略。")
    available_after: datetime | None = Field(default=None,
        description="最早可执行时刻，建议传带时区偏移的 ISO 8601 时间；不是提醒或截止。")
    place: TodoPlace | None = Field(default=None, description="已确认地点，仅在 JSON 体中提供；不可猜测坐标。")
    schedule_due: TodoScheduleDue | None = Field(default=None,
        description="仅用户明确希望截止随日程变更时提供；与绝对 due_at 二选一，不创建提醒。")


class TodoPayload(TodoPlanningFields):
    """Independent todo fields plus optional, explicitly chosen deadline and reminder dependencies."""
    model_config = ConfigDict(extra="forbid", json_schema_extra=TODO_TIMING_SCHEMA)

    todo_id: str | None = None
    title: str = Field(min_length=1, max_length=255)
    due_at: datetime | None = None
    reminder_at: datetime | None = None
    status: TodoStatus = "open"
    schedule_reminder: ScheduleRelativeReminder | None = None

    @model_validator(mode="after")
    def validate_reminder_timing(self):
        """Require one timing mode for each independent clock."""
        if (self.reminder_at is not None and self.schedule_reminder is not None
                or self.due_at is not None and self.schedule_due is not None):
            raise ValueError("截止和提醒各自只能选择绝对时间或相对日程的一种。")
        return self


class DraftPayload(BaseModel):
    """Typed record changes prepared before the existing save transaction."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255)
    goal: str | None = Field(default=None, max_length=4000)
    changes_note: str | None = Field(default=None, max_length=2000)
    next_step: str | None = Field(default=None, max_length=500)
    schedule: SchedulePayload | None = None
    todos: list[TodoPayload] = Field(default_factory=list, max_length=30)
    complete_todo_ids: list[str] = Field(default_factory=list, max_length=30)


class NewSchedulePayload(SchedulePayload):
    """Describe a new schedule; existing records must use the edit-draft route."""
    schedule_id: None = None


class NewTodoPayload(TodoPayload):
    """Describe a new todo without accepting an existing todo identifier."""
    todo_id: None = None


class MemoryTodoInput(NewTodoPayload, MemoryInputOptions):
    """Accept per-field todo memory choices separately from ordinary parameters."""
    model_config = ConfigDict(extra='forbid', json_schema_extra={'allOf': [
        TodoPayload.model_config['json_schema_extra'], *memory_source_constraints(TODO_MEMORY_FIELDS)]})


class MemoryScheduleInput(NewSchedulePayload, MemoryInputOptions):
    """Allow a selected memory to supply a schedule start before formal payload validation."""
    model_config = ConfigDict(extra='forbid', json_schema_extra={'allOf': [
        SchedulePayload.model_config['json_schema_extra'],
        *memory_source_constraints({'location', 'starts_at', 'reminder_offset_seconds'})]})
    starts_at: datetime | None = None

    @model_validator(mode='after')
    def require_selected_start(self):
        """Require a real start or a model-selected start with an explicit business date."""
        if self.starts_at is None and not (self.use_preferences and self.preference_date and 'starts_at' in self.memory_defaults):
            raise ValueError('需要日程开始时间，或指定日期及本次记忆时间选择。')
        return self


class StructuredDraftRequest(DraftPayload):
    """Prepare new records for the existing atomic draft save transaction."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"oneOf": [
        {"required": ["title"], "properties": {"title": {"type": "string", "minLength": 1}, "matter_id": {"type": "null"}, "expected_matter_version": {"type": "null"}}},
        {"required": ["matter_id", "expected_matter_version"], "properties": {"matter_id": {"type": "string", "minLength": 1}, "expected_matter_version": {"type": "integer", "minimum": 1}}},
    ]})
    idempotency_key: str = Field(min_length=8, max_length=128)
    matter_id: str | None = Field(default=None, min_length=1, max_length=36, description="追加到已有事项时提供真实ID；新建事项时省略。")
    expected_matter_version: int | None = Field(default=None, ge=1, description="已有事项的当前version；新建事项时省略。")
    title: str | None = Field(default=None, min_length=1, max_length=255)
    schedule: MemoryScheduleInput | None = None
    todos: list[MemoryTodoInput] = Field(default_factory=list, max_length=30)
    complete_todo_ids: list[str] = Field(default_factory=list, max_length=0)

    @model_validator(mode="after")
    def validate_creation_target(self):
        """Require a new title or an existing matter with its observed version."""
        if self.matter_id is None:
            if not self.title or self.expected_matter_version is not None:
                raise ValueError("新建事项需要标题，不能提供原事项版本。")
        elif self.expected_matter_version is None:
            raise ValueError("追加记录需要已有事项的当前版本。")
        return self


class TodoRead(TodoPlanningFields):
    """Return optional stored inputs and the actual calculated deadline."""
    available_after: UtcDateTime | None = None
    schedule_due: TodoScheduleDue | None = Field(default=None,
        description="已保存的截止依赖；due_at 同时返回当前计算值，原日程取消时该计算值为空。")
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


class TodaySavedCommuteRouteRead(BaseModel):
    """Display the measured trip without implying it is still fresh."""

    duration_seconds: int
    mode: TransportMode | None = None
    transfers_summary: str | None = None


class TodayCommuteRead(BaseModel):
    """Link an owned daily schedule to the commute that generated it."""

    plan_id: str
    service_date: date
    origin_label: str
    destination_label: str
    saved_route: TodaySavedCommuteRouteRead | None = None


class TodayRouteRead(BaseModel):
    status: Literal["available", "unverified", "unavailable"]
    reason_code: str | None = None
    message: str | None = None
    advisory_id: str | None = None
    duration_seconds: int | None = None
    mode: TransportMode | None = None
    fetched_at: UtcDateTime | None = None
    origin_basis: Literal["user_confirmed", "previous_schedule"] | None = None
    origin_known: bool | None = None
    commute: TodayCommuteRead | None = None


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
    schedule_offset_seconds: int | None = None
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
    commute_plan_id: str | None = Field(default=None, description="该事项唯一有效通勤计划的实际ID；与matter_id是不同实体，不能互相代替。")
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
    ignore_pending_reply: bool = False
    ignore_pending_clarification: bool = False
    dismissed_reply_run_id: str | None = Field(default=None, min_length=8, max_length=36)
    memory_disclosures: list[MemoryDisclosure] = Field(default_factory=list, max_length=10)
    entry_mode: Literal["sync_chat", "async_capture"] = "sync_chat"


class IntakeStatusRequest(BaseModel):
    """Recover one task by the original owner's submission key."""

    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=128)


IntakeStage = Literal["queued", "understanding", "fetching_schema", "executing_tool",
                      "generating_reply", "completed", "awaiting_reply", "failed", "unknown", "cancelled"]


class IntakeResponse(BaseModel):
    source: Literal['chat','follow_up'] = 'chat'
    matter_id: str | None = None
    follow_up_id: str | None = None
    follow_up_execution_id: str | None = None
    status: str | None = None
    error: dict | None = None
    workflow_execution_id: str | None = None
    draft: DraftRead | None = None
    replayed: bool = False
    intent: Literal["matter", "question"] = "matter"
    outcome: Literal[
        "answer", "draft", "needs_clarification", "processing", "unknown", "failed", "cancelled"
    ] = "draft"
    message: str | None = None
    answer: str | None = None
    clarify: str | None = None
    pending_context: dict = Field(default_factory=dict)
    local_run_id: str | None = None
    parent_run_id: str | None = None
    reply_state: Literal["awaiting_reply", "resolved", "expired", "cancelled"] | None = None
    reply_expires_at: UtcDateTime | None = None
    continuation_run_id: str | None = None
    clarification_summary: str | None = None
    entry_mode: Literal["sync_chat", "async_capture"] = "sync_chat"
    stage: IntakeStage | None = None
    request_id: str | None = None
    record_id: str | None = None


class IntakeResultRead(BaseModel):
    source: Literal['chat','follow_up'] = 'chat'
    follow_up_id: str | None = None
    follow_up_execution_id: str | None = None
    confirm_status: Literal["confirmed", "unconfirmed", "unknown"] = "unknown"
    target_lifecycle: Literal["active", "archived", "deleted", "source_revoked"] | None = None
    target_title: str | None = None
    history_target_id: str | None = None
    local_run_id: str
    parent_run_id: str | None = None
    reply_state: Literal["awaiting_reply", "resolved", "expired", "cancelled"] | None = None
    reply_expires_at: UtcDateTime | None = None
    continuation_run_id: str | None = None
    clarification_summary: str | None = None
    entry_mode: Literal["sync_chat", "async_capture"] = "sync_chat"
    stage: IntakeStage | None = None
    request_id: str | None = None
    record_id: str | None = None
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
    """Open exactly one existing object; use StructuredDraftRequest for additions."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"oneOf": [
        {"required": ["schedule_id"], "properties": {"schedule_id": {"type": "string"}, "todo_id": {"type": "null"}}},
        {"required": ["todo_id"], "properties": {"todo_id": {"type": "string"}, "schedule_id": {"type": "null"}}},
    ]})

    idempotency_key: str = Field(min_length=8, max_length=128)
    schedule_id: str | None = None
    todo_id: str | None = None

    @model_validator(mode="after")
    def _one_target(self) -> "MatterEditRequest":
        """Reject missing or ambiguous existing-record selectors before opening a draft."""
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


class MemoryDraftTodoInput(MemoryTodoInput):
    """Reapply selected sources to a real todo in a combined edit draft."""
    todo_id: str | None = None


class MemoryDraftScheduleInput(MemoryScheduleInput):
    """Keep the original schedule identifier while selecting new field sources in a draft."""
    schedule_id: str | None = None


class DraftPatchRequest(BaseModel):
    """Patch formal fields or explicitly reapply memory choices without replacing record identities."""
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    goal: str | None = Field(default=None, max_length=4000)
    changes_note: str | None = Field(default=None, max_length=2000)
    next_step: str | None = Field(default=None, max_length=500)
    schedule: MemoryDraftScheduleInput | None = None
    todos: list[MemoryDraftTodoInput] | None = Field(default=None, max_length=30)
    complete_todo_ids: list[str] | None = Field(default=None, max_length=30)
    cancel_schedule_ids: list[str] | None = Field(default=None, max_length=30)

    @field_validator('schedule', 'todos', mode='before')
    @classmethod
    def accept_formal_models(cls, value):
        """Accept existing internal business models alongside JSON memory-aware input objects."""
        if isinstance(value, BaseModel):
            return value.model_dump()
        if isinstance(value, list):
            return [part.model_dump() if isinstance(part, BaseModel) else part for part in value]
        return value


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
    todos: list[TodoRead] = Field(default_factory=list)
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


class MatterDeleteRead(BaseModel):
    """Return the actual matter deletion identity and time."""
    matter_id: str
    deleted_at: UtcDateTime


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


class TodayCaptureReply(BaseModel):
    """One captured input waiting for the owner's clarification before saving."""

    local_run_id: str
    request_text: str
    question: str
    choices: list[str] = Field(default_factory=list)
    draft_id: str | None = None


class TodayRead(BaseModel):
    date: str
    timezone: str
    schedules: list[TodayScheduleRead]
    reminders: list[TodayReminderRead]
    pending_drafts: int
    deadlines: list[TodayDeadlineRead] = Field(default_factory=list)
    drafts: list[TodayDraftRead] = Field(default_factory=list)
    changes: list[TodayChangeRead] = Field(default_factory=list)
    capture_replies: list[TodayCaptureReply] = Field(default_factory=list)


class EstimateRequest(BaseModel):
    """Estimate from explicit or source-validated selected endpoints and one supplied journey time."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"allOf": [
        {"oneOf": [
            {"required": ["origin"], "properties": {"origin": {"type": "object"}, "origin_schedule_id": {"type": "null"}}},
            {"required": ["origin_schedule_id"], "properties": {"origin_schedule_id": {"type": "string"}, "origin": {"type": "null"}}},
            {'required':['memory_defaults'],'properties':{'origin':{'type':'null'},'origin_schedule_id':{'type':'null'},
                'memory_defaults':{'type':'object','required':['origin']}}},
        ]},
        {"anyOf": [{"required": [key], "properties": {key: {"type": "string"}}} for key in ("depart_at", "arrive_by")]},
        {'anyOf':[{'required':['destination'],'properties':{'destination':{'type':'object'}}},
            {'required':['memory_defaults'],'properties':{'memory_defaults':{'type':'object','required':['destination']}}}]},
        {"if": {"required": ["origin_basis"], "properties": {"origin_basis": {"const": "previous_schedule"}}}, "then": {"required": ["origin_schedule_id"], "properties": {"origin_schedule_id": {"type": "string"}}}},
        *memory_source_constraints({'origin','destination','mode'}),
    ]})

    origin: Coordinate | None = None
    origin_schedule_id: str | None = Field(default=None, min_length=8, max_length=36)
    destination: Coordinate | None = None
    mode: TransportMode = Field(default="driving", description="driving驾车、walking步行、transit公交/地铁、bicycling自行车、electrobike电动车。按route_capabilities.supported_modes选择；每段遵守用户指定方式，不支持或无路线时明确告知，不擅自替换。")
    use_preferences: bool = Field(default=True, description="允许应用本次memory_defaults选择，无需用途开关或角色绑定；false本次不使用记忆。")
    memory_defaults: dict[str, MemorySelection] = Field(default_factory=dict, max_length=3,
        description='从本任务已检索的记忆中选择origin/destination/mode，提供编号、版本、原文、拟采用值和理由。采用记忆时省略对应普通字段（包括null），两处同时提交将拒绝；origin记忆也不能同时传origin_schedule_id。不要把常用地点当当前位置；不确定用ask_user。')
    depart_at: datetime | None = None
    arrive_by: datetime | None = None
    schedule_id: str | None = Field(default=None, min_length=8, max_length=36)
    origin_basis: Literal["user_confirmed", "previous_schedule"] = "user_confirmed"

    @model_validator(mode="after")
    def _one_time(self) -> "EstimateRequest":
        """Require actual or explicitly selected endpoints, leaving source validation to the service."""
        if set(self.memory_defaults)-{'origin','destination','mode'}:
            raise ValueError('路线只支持起终点和交通方式的记忆应用。')
        if self.origin is not None and self.origin_schedule_id is not None:
            raise ValueError("origin 和 origin_schedule_id 只能提供一个。")
        if self.origin is None and self.origin_schedule_id is None and not (self.use_preferences and 'origin' in self.memory_defaults):
            raise ValueError("origin 或 origin_schedule_id 必须提供一个。")
        if self.destination is None and not (self.use_preferences and 'destination' in self.memory_defaults):
            raise ValueError('需要明确目的地，或选择本次使用的目的地记忆。')
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
    basis: Literal["user_confirmed", "previous_schedule", "destination_schedule", "memory"]
    schedule_id: str | None = None


class RouteSegmentRead(BaseModel):
    kind: TransportMode
    label: str | None = None
    coordinate_system: CoordinateSystem
    points: list[RoutePointRead] = Field(min_length=2)


class RouteShapeRead(BaseModel):
    status: Literal["available", "partial", "missing"] = "missing"
    segments: list[RouteSegmentRead] = Field(default_factory=list)


class EstimateResponse(BaseModel):
    preference_application: dict | None = None
    status: Literal["available", "unavailable"]
    provider: str
    mode: TransportMode
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
    origin_basis: Literal["user_confirmed", "previous_schedule", "memory"] = "user_confirmed"
    origin_schedule_id: str | None = None
    schedule_id: str | None = None
    origin: RouteEndpointRead | None = None
    destination: RouteEndpointRead | None = None
    shape: RouteShapeRead = Field(default_factory=RouteShapeRead)


class CommuteDayRead(BaseModel):
    """Return the saved daily schedule identity, including replay and optional route data."""
    schedule_id: str
    replayed: bool
    service_date: date
    instance_id: str | None = None
    timezone_repaired: bool | None = None
    route: EstimateResponse | None = None


class PlaceSearchRequest(PlaceLookupRequest):
    """Add a real schedule draft to the shared place query fields."""

    draft_id: str = Field(min_length=8, max_length=36)


class PlaceCandidateRead(PlaceLookupCandidate):
    """Bind a provider candidate to an authorized account and schedule draft."""

    selection_token: str


class PlaceSearchResponse(PlaceLookupResponse):
    """Preserve draft-search responses, including signed selection tokens."""

    candidates: list[PlaceCandidateRead] = Field(default_factory=list)


class PlaceSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(min_length=8, max_length=36)
    selection_token: str = Field(min_length=32, max_length=4096)


class MapPickRequest(PlacePointRequest):
    """Bind shared point input to an existing schedule draft."""

    draft_id: str = Field(min_length=8, max_length=36)


class MapPickLocationRead(PlacePointLocation):
    """Keep the draft map-point response's required address and selection token."""

    address: str
    selection_token: str


class MapPickResponse(PlacePointResponse):
    """Return a draft-bound map selection while preserving the existing response shape."""

    location: MapPickLocationRead | None = None


class ScheduledRouteRead(BaseModel):
    route_ref_id: str | None = None
    status: Literal["available", "unverified"]
    reason_code: str | None = None
    message: str | None = None
    schedule_id: str
    provider: str | None = None
    mode: TransportMode | None = None
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


# The same target rules apply to both independent resource creation endpoints.
_CREATE_TARGET_SCHEMA = {"if": {"required": ["matter_id"], "properties": {"matter_id": {"type": "string"}}},
                         "then": {"required": ["expected_matter_version"], "properties": {"expected_matter_version": {"type": "integer", "minimum": 1}}},
                         "else": {"properties": {"expected_matter_version": {"type": "null"}}}}


class RecordCreationTarget(BaseModel):
    """Resolve an existing parent by ID/version, or let the backend create the parent atomically."""
    idempotency_key: str = Field(min_length=8, max_length=128)
    matter_id: str | None = Field(default=None, min_length=1, max_length=36)
    expected_matter_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_parent_version(self):
        """An existing parent needs its current version; a new parent has no supplied version."""
        if (self.matter_id is None) != (self.expected_matter_version is None):
            raise ValueError("指定已有事项时必须同时提供当前事项版本。")
        return self


class TodoCreateRequest(RecordCreationTarget, MemoryTodoInput):
    """Create a todo without requiring any schedule; explicit relative reminders name a real schedule."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"allOf": [
        TodoPayload.model_config["json_schema_extra"], _CREATE_TARGET_SCHEMA,
        {"properties": {field: {"anyOf": [{"type": "null"}, {"type": "object", "required": ["schedule_id"],
            "properties": {"schedule_id": {"type": "string"}}}]} for field in ("schedule_reminder", "schedule_due")}},
        {"if": {"required": ["schedule_due"], "properties": {"schedule_due": {"type": "object"}}},
         "then": {"required": ["matter_id", "expected_matter_version"],
                  "properties": {"matter_id": {"type": "string"}, "expected_matter_version": {"type": "integer"}}}},
        *memory_source_constraints(TODO_MEMORY_FIELDS),
    ]})

    @model_validator(mode="after")
    def validate_relative_schedule(self):
        """Independent creation cannot refer to an imaginary same-request calendar record."""
        if any(relation and (not relation.schedule_id or not self.matter_id)
               for relation in (self.schedule_reminder, self.schedule_due)):
            raise ValueError("独立待办的相对截止或提醒需要已有日程ID及所属事项。")
        return self


class ScheduleCreateRequest(RecordCreationTarget, NewSchedulePayload):
    """Create a schedule from model-selected, nonoverlapping explicit inputs or verified memories."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"allOf": [
        SchedulePayload.model_config["json_schema_extra"], _CREATE_TARGET_SCHEMA,
        {'anyOf':[{'required':['starts_at'],'properties':{'starts_at':{'type':'string'}}},
                  {'required':['preference_date'],'properties':{'preference_date':{'type':'string'},'use_preferences':{'const':True}}}]},
        *memory_source_constraints({'location','starts_at','reminder_offset_seconds'}),
    ]})
    starts_at: datetime | None = None
    preference_date: date | None = Field(default=None, description="本次明确的具体日期。AI选择时间习惯时提供，不从习惯本身猜日期。")
    use_preferences: bool = Field(default=True, description="允许使用本次memory_defaults选择；无需预先开启用途或绑定角色。false本次禁用。")
    memory_defaults: dict[str, MemorySelection] = Field(default_factory=dict, max_length=3,
        description='AI按当前请求从已检索记忆选择location/starts_at/reminder_offset_seconds，分别提交编号、版本、原文、拟采用值和理由。采用记忆时省略对应普通字段（包括null），两处同时提交将拒绝而非自动选择来源。提醒记忆不能同时传reminder_at；地点记忆不能同时传地点、地址或坐标字段。不确定用ask_user卡片选择。')

    @model_validator(mode='after')
    def require_time_or_preference_date(self):
        """Bound selected fields and require a specific date when a memory supplies the start time."""
        if set(self.memory_defaults)-{'location','starts_at','reminder_offset_seconds'}:
            raise ValueError('日程不支持指定的记忆应用字段。')
        if self.starts_at is None and (not self.use_preferences or self.preference_date is None or 'starts_at' not in self.memory_defaults):
            raise ValueError('必须提供 starts_at，或同时提供本次具体 preference_date 和 memory_defaults.starts_at。')
        return self


class TodoPatchRequest(TodoPlanningFields, MemoryInputOptions):
    """Patch specified todo fields while keeping its identity and all unrelated calendar records."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"allOf": [{"properties": {key: {"not": {"type": "null"}} for key in ("title", "status")}}, TODO_TIMING_SCHEMA, *memory_source_constraints(TODO_MEMORY_FIELDS)]})
    idempotency_key: str = Field(min_length=8, max_length=128)
    expected_matter_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    due_at: datetime | None = None
    reminder_at: datetime | None = None
    schedule_reminder: ScheduleRelativeReminder | None = None
    status: TodoStatus | None = None

    @model_validator(mode="after")
    def validate_patch_timing(self):
        """Reject ambiguous explicit inputs before preparing an edit draft."""
        if (self.due_at is not None and self.schedule_due is not None
                or self.reminder_at is not None and self.schedule_reminder is not None):
            raise ValueError("截止和提醒各自只能选择绝对时间或相对日程的一种。")
        if self.schedule_due is not None and not self.schedule_due.schedule_id:
            raise ValueError("修改原待办的相对截止需要真实日程ID。")
        return self


class SchedulePatchRequest(MemoryInputOptions):
    """Patch only specified calendar fields; unspecified original fields remain local and unchanged."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"allOf": [{"properties": {key: {"not": {"type": "null"}} for key in ("title", "starts_at", "coordinate_system")}}, *memory_source_constraints({"location", "starts_at", "reminder_offset_seconds"})]})
    idempotency_key: str = Field(min_length=8, max_length=128)
    expected_matter_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=255)
    reminder_at: datetime | None = None
    reminder_offset_seconds: int | None = Field(default=None, ge=-31536000, le=31536000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    coordinate_system: CoordinateSystem | None = None
    location_city: str | None = Field(default=None, max_length=64)
    location_address: str | None = Field(default=None, max_length=255)
    location_source: str | None = Field(default=None, max_length=32)
    location_external_id: str | None = Field(default=None, max_length=128)


class TodoOperationRead(BaseModel):
    """Return the actual saved todo and parent version so a create does not need a verification GET."""
    matter: MatterRead
    todo: TodoRead
    reminders: list[ReminderRead] = Field(default_factory=list)
    change_id: str | None = None
    replayed: bool = False
    applied: bool = True


class ScheduleOperationRead(BaseModel):
    """Return the actual calendar result, including unapplied conflict information when required."""
    matter: MatterRead | None = None
    schedule: ScheduleRead | None = None
    reminders: list[ReminderRead] = Field(default_factory=list)
    change_id: str | None = None
    replayed: bool = False
    applied: bool = True
    conflict: ConflictRead | None = None
    preference_application: dict | None = None


class TodoListRead(BaseModel):
    """Publish the collection shape for owned todo lookup."""
    items: list[TodoRead]


class ScheduleListRead(BaseModel):
    """Publish the collection shape for owned calendar lookup."""
    items: list[ScheduleRead]


class CommuteDayState(BaseModel):
    """Publish effective state, versions and real saved records for AI decisions and result cards."""
    plan_id: str
    matter_id: str | None = None
    version: int | None = None
    revision: int
    service_date: date
    timezone: str
    status: Literal['upcoming','past','inactive','ineligible']
    target_arrival_at: UtcDateTime
    arrival_local_time: str
    schedule: ScheduleRead | None = None
    route: dict[str,Any] | None = None
    timeline_card: dict[str,Any] | None = None
    applied: bool | None = None
    replayed: bool | None = None
    change_id: str | None = None
    conflict: dict[str,Any] | None = None


class CommutePlanUpdateRead(CommutePlanRead):
    """Retain the existing plan response and include the immediately refreshed future day."""
    next_day: CommuteDayState | None = None

"""Published contracts for weekly availability and the Today recommendation."""
from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from assistant_api.commute.schemas import UtcDateTime
from assistant_api.schemas.preferences import MemoryInputOptions, memory_source_constraints

LocalMinute = Annotated[str, StringConstraints(pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')]
TargetKind = Literal['todo', 'schedule']
FeedbackAction = Literal['snooze', 'dismiss', 'resume']


class WeeklyWindow(BaseModel):
    """Describe one weekly local-time interval; a smaller end time crosses midnight."""
    model_config = ConfigDict(extra='forbid')
    weekday: int = Field(ge=1, le=7, strict=True, description='ISO 星期：周一=1，周日=7；跨天按开始日。')
    starts_local: LocalMinute
    ends_local: LocalMinute
    kind: Literal['available', 'unavailable'] = Field(
        description='available 是通常可安排的时段；unavailable 是必须扣除的休息或不可安排时段。')

    @model_validator(mode='after')
    def check_nonempty(self):
        """Reject equal endpoints because they are ambiguous between zero time and a full day."""
        if self.starts_local == self.ends_local:
            raise ValueError('起止时刻不能相同；跨午夜时结束时刻可小于开始时刻。')
        return self


class AvailabilityWrite(MemoryInputOptions):
    """Replace the recurring windows only when requested, with explicit local timezone and version."""
    model_config = ConfigDict(extra='forbid', json_schema_extra={'allOf': [
        *memory_source_constraints({'windows'}),
        {'anyOf': [
            {'required': ['windows'], 'properties': {'windows': {'type': 'array'}}},
            {'required': ['memory_defaults'], 'properties': {'memory_defaults': {
                'type': 'object', 'required': ['windows']}, 'use_preferences': {'const': True}}}
        ]}
    ]})
    timezone: str = Field(min_length=1, max_length=64, description='IANA 时区，例如 Asia/Shanghai。')
    expected_version: int = Field(ge=0, strict=True, description='先读取 availability；未配置时为0。')
    windows: list[WeeklyWindow] | None = Field(default=None, max_length=28,
        description='完整替换。空数组清除；采用已检索长期记忆时改用 memory_defaults.windows，二者不可同时提交。')

    @model_validator(mode='after')
    def check_source(self):
        """Require a real timezone and exactly one explicit source for the replacement list."""
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError('需要有效的 IANA 时区。') from exc
        if set(self.memory_defaults) - {'windows'} or self.preference_date is not None:
            raise ValueError('重复作息只支持长期 windows 来源，不能指定某一次日期。')
        if self.memory_defaults:
            if 'windows' in self.model_fields_set or not self.use_preferences:
                raise ValueError('只能从普通 windows 或本次明确选用的记忆中选择一种来源。')
        elif self.windows is None:
            raise ValueError('请提供 windows；清除使用空数组。')
        return self


class AvailabilityRead(BaseModel):
    """Return saved business windows without including source memory text."""
    version: int
    timezone: str
    windows: list[WeeklyWindow]


class ActionTarget(BaseModel):
    """Refer to an actual saved record rather than a free-text task."""
    kind: TargetKind
    record_id: str


class NextAction(ActionTarget):
    """Return a ranked suggestion, the reason and the authoritative target for existing actions."""
    title: str
    matter_id: str | None = None
    matter_version: int | None = None
    state: Literal['departure_due', 'upcoming_schedule', 'deadline_risk', 'fits_window', 'needs_duration',
                   'needs_availability', 'needs_deadline']
    reason: str
    starts_at: UtcDateTime | None = None
    commute_plan_id: str | None = None
    departure_at: UtcDateTime | None = None
    destination: str | None = None
    route_status: Literal['available', 'unavailable'] | None = None
    due_at: UtcDateTime | None = None
    window_end: UtcDateTime | None = None
    estimated_duration_minutes: int | None = None
    actions: list[Literal['view', 'complete', 'reschedule', 'snooze', 'dismiss']]


class SuppressedAction(ActionTarget):
    """Expose a still-existing target that can be restored to the recommendation list."""
    title: str
    state: Literal['snoozed', 'dismissed']
    until: UtcDateTime | None = None
    due_at: UtcDateTime | None = None
    starts_at: UtcDateTime | None = None


class NextActionRead(BaseModel):
    """Return a short-lived snapshot independently of the existing Today feed."""
    recommendation_id: str
    version: int
    calculated_at: UtcDateTime
    expires_at: UtcDateTime
    timezone: str
    data_status: Literal['ready', 'availability_missing', 'no_current_window', 'calendar_end_unknown']
    primary: NextAction | None
    alternatives: list[NextAction]
    suppressed: list[SuppressedAction]


class NextActionCommand(ActionTarget):
    """Change only this user's recommendation state; completion and rescheduling have separate APIs."""
    model_config = ConfigDict(extra='forbid', json_schema_extra={'if': {
        'required': ['action'], 'properties': {'action': {'const': 'snooze'}}},
        'then': {'required': ['until'], 'properties': {'until': {'type': 'string', 'format': 'date-time'}}},
        'else': {'properties': {'until': {'type': 'null'}}}})
    record_id: str = Field(min_length=1, max_length=36)
    recommendation_id: str = Field(min_length=1, max_length=36)
    expected_recommendation_version: int = Field(ge=1, strict=True)
    action: FeedbackAction
    until: AwareDatetime | None = Field(default=None,
        description='仅 snooze 使用未来时刻（带时区）；不会修改原截止、日程或提醒。')
    idempotency_key: str = Field(min_length=8, max_length=128)

    @model_validator(mode='after')
    def check_snooze(self):
        """Require a time for snooze and prevent using a time for other commands."""
        if (self.action == 'snooze') != (self.until is not None):
            raise ValueError('只有暂缓需要提供 until。')
        return self


class NextActionReceipt(ActionTarget):
    """Confirm the saved feedback so the client can refresh recommendations."""
    action: FeedbackAction
    until: UtcDateTime | None = None
    refresh_required: Literal[True] = True

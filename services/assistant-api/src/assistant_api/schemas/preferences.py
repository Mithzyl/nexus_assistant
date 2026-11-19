"""Validate task-selected memories; legacy preference controls remain readable without becoming a prerequisite."""
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

Purpose = Literal['schedule', 'route', 'reminder', 'todo', 'commute_plan', 'commute_draft', 'commute_quote', 'availability']
LegacyPurpose = Literal['schedule', 'route', 'reminder']
Role = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
FIELDS = {'commute_draft': {'origin_query', 'destination_query', 'city', 'arrival_local_time', 'weekdays', 'buffer_minutes', 'effective_from'},
          'commute_quote': {'mode', 'buffer_minutes', 'effective_from', 'weekdays'},
          'schedule': {'location', 'starts_at', 'reminder_offset_seconds'},
          'route': {'origin', 'destination', 'mode'}, 'reminder': {'reminder_offset_seconds'},
          'todo': {'due_at', 'reminder_at', 'priority', 'estimated_duration_minutes', 'available_after', 'place', 'schedule_due'},
          'availability': {'windows'},
          'commute_plan': {'origin', 'destination', 'transport_mode', 'weekdays', 'arrival_local_time',
                           'check_local_time', 'buffer_minutes', 'timezone', 'effective_from',
                           'rain_probability_threshold', 'rain_extra_buffer_minutes', 'notify_enabled'}}
MEMORY_INPUT_GROUPS = {
    'location': {'location', 'latitude', 'longitude', 'coordinate_system', 'location_address',
                 'location_city', 'location_source', 'location_external_id'},
    'reminder_offset_seconds': {'reminder_offset_seconds', 'reminder_at'},
    'origin': {'origin', 'origin_schedule_id'},
    'reminder_at': {'reminder_at', 'schedule_reminder'},
    'due_at': {'due_at', 'schedule_due'},
    'schedule_due': {'due_at', 'schedule_due'},
}


def memory_source_constraints(fields):
    """Describe exclusive explicit/remembered inputs in OpenAPI, including explicit null values."""
    return [{'not': {'required': [explicit, 'memory_defaults'], 'properties': {
        'memory_defaults': {'type': 'object', 'required': [field]}}}}
        for field in sorted(fields) for explicit in sorted(MEMORY_INPUT_GROUPS.get(field, {field}))]


class PreferenceBinding(BaseModel):
    """Bind an arbitrary user role to one exact confirmed memory revision."""
    model_config = ConfigDict(extra='forbid')
    entry_id: UUID
    version: int = Field(ge=1)


class PreferencePatch(BaseModel):
    """Patch controls with optimistic versioning; null removes only the named role binding."""
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=128)
    purposes: dict[LegacyPurpose, bool] = Field(default_factory=dict)
    bindings: dict[Role, PreferenceBinding | None] = Field(default_factory=dict, max_length=32)
    defaults: dict[LegacyPurpose, dict[str, Role]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def supported_fields(self):
        """Constrain business consumers, without enumerating memory categories or user roles."""
        for purpose, fields in self.defaults.items():
            if set(fields) - FIELDS[purpose]:
                raise ValueError('该用途不支持指定的默认字段。')
        return self


class PreferenceResolveRequest(BaseModel):
    """Validate memories selected for this task, without requiring configured purposes or roles."""
    model_config = ConfigDict(extra='forbid')
    purpose: Purpose
    values: dict[str, JsonValue] = Field(default_factory=dict, max_length=32)
    selections: dict[str, 'MemorySelection'] = Field(default_factory=dict, max_length=12)
    reference_date: date | None = None
    activity: str | None = Field(default=None, min_length=1, max_length=255)
    matter_id: str | None = Field(default=None, min_length=1, max_length=36)

    @model_validator(mode='after')
    def bounded_values(self):
        """Bound current business inputs and the fields a selected memory may supply."""
        import json
        if set(self.selections) - FIELDS[self.purpose]:
            raise ValueError('当前业务接口不支持这些记忆应用字段。')
        if len(json.dumps(self.values, ensure_ascii=False).encode()) > 8192:
            raise ValueError('业务输入过大。')
        return self


class PreferenceRead(BaseModel):
    """Expose retained controls with an explicit notice that new tasks need no manual configuration."""
    version: int
    purposes: dict[LegacyPurpose, bool]
    bindings: dict[str, PreferenceBinding]
    defaults: dict[LegacyPurpose, dict[str, str]]
    replayed: bool = False
    configuration_required: Literal[False] = False
    selection_mode: Literal['model'] = 'model'


class PreferenceResolution(BaseModel):
    """Publish values and field-by-field applied, explicit or unused provenance."""
    preferences_version: int | None = None
    selection_mode: Literal['model'] = 'model'
    purpose: Purpose
    timezone: str
    values: dict[str, JsonValue]
    fields: dict[str, dict[str, JsonValue]]
    application_status: Literal['resolved','applied','not_applied'] = 'resolved'
    matter_id: str | None = None
    reference_date: date | None = None


class MemoryBusinessReference(BaseModel):
    """Identify an owned business input and its observed parent/plan version, never a model-supplied snapshot."""
    model_config = ConfigDict(extra='forbid')
    kind: Literal['schedule', 'todo', 'commute_plan']
    record_id: UUID
    version: int = Field(ge=1, strict=True,
        description='schedule/todo引用所属matter.version；commute_plan引用revision。这是业务对象版本，不是记忆条目版本。')


class MemorySelection(BaseModel):
    """Keep an exact retrieved quote, source revision and proposed business value distinct from the current user text."""
    model_config = ConfigDict(extra='forbid')
    entry_id: UUID
    version: int = Field(ge=1, strict=True)
    source_quote: str = Field(min_length=1, max_length=2000)
    value: JsonValue
    reason: str = Field(min_length=1, max_length=1000)
    references: list[MemoryBusinessReference] = Field(default_factory=list, max_length=4,
        description='拟采用值结合已有日程/待办/通勤计划计算时，列出实际对象及当前事项version或计划revision；后端读取真实快照，不接受模型伪造快照。不建立自动重算依赖。')

    @model_validator(mode='after')
    def bounded_selection(self):
        """Refuse blank attribution or oversized proposed values before any memory reads."""
        import json
        if not self.source_quote.strip() or not self.reason.strip():
            raise ValueError('需要真实原文和本次采用理由。')
        if len(json.dumps(self.value,ensure_ascii=False).encode())>4096:
            raise ValueError('记忆应用值过大。')
        return self


PreferenceResolveRequest.model_rebuild()


class MemoryInputOptions(BaseModel):
    """Allow explicit per-command memory choices without adding persistent user configuration."""
    use_preferences: bool = True
    preference_date: date | None = None
    memory_defaults: dict[str, MemorySelection] = Field(default_factory=dict, max_length=12,
        description='AI按本次任务选择已检索记忆，提供entry_id/version/source_quote/value/reason及必要references。采用记忆时省略同字段普通参数，含null也不能重复。仅本次调用生效，不自动改其他记录；来源内部保存，不默认展示原文。')

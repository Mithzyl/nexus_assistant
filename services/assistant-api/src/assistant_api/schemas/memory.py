"""Owned memory commands, compatible formats and restricted current-task tools."""
from datetime import UTC, date, time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator, model_validator


class MemoryInput(BaseModel):
    """Reject identity, permission and unknown fields in memory requests."""
    model_config = ConfigDict(extra='forbid')


class PlaceMemory(MemoryInput):
    """Carry a user-confirmed place in the existing Nexus typed-memory format."""
    memory_type: Literal['place'] = 'place'
    label: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=1000)
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    coordinate_system: Literal['WGS84', 'GCJ02', 'BD09']

    @model_validator(mode='after')
    def coordinate_pair(self):
        """Keep incomplete coordinate pairs from becoming usable route origins."""
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError('经纬度必须成对提供。')
        return self


class TimedMemory(MemoryInput):
    """Require an explicit valid timezone for recurring local times."""
    timezone: str = Field(min_length=1, max_length=100)

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        """Reject unknown zones rather than silently using server local time."""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError('需要有效的 IANA 时区。') from exc
        return value


class CommuteMemory(TimedMemory):
    """Describe the four transport values already accepted by Nexus typed memory."""
    memory_type: Literal['commute_preference'] = 'commute_preference'
    origin: str = Field(min_length=1, max_length=1000)
    destination: str = Field(min_length=1, max_length=1000)
    travel_mode: Literal['walk', 'bicycle', 'transit', 'drive']
    preferred_departure: time | None = None
    weekdays: list[int] = Field(min_length=1, max_length=7)

    @field_validator('weekdays')
    @classmethod
    def valid_weekdays(cls, value):
        """Use unique ISO weekdays, without reinterpreting Sunday as zero."""
        if any(day < 1 or day > 7 for day in value) or len(set(value)) != len(value):
            raise ValueError('星期必须为不重复的 1 至 7。')
        return value


class TimePreferenceMemory(TimedMemory):
    """Preserve a preferred time without treating it as a calendar booking."""
    memory_type: Literal['time_preference'] = 'time_preference'
    activity: str = Field(min_length=1, max_length=500)
    preferred_time: time


class ExperienceMemory(MemoryInput):
    """Accept dated past experience, never a future plan disguised as history."""
    memory_type: Literal['experience'] = 'experience'
    occurred_on: date
    description: str = Field(min_length=1, max_length=2000)
    lesson: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator('occurred_on')
    @classmethod
    def not_future(cls, value):
        """Apply the existing upstream UTC-date requirement before forwarding."""
        from datetime import datetime
        if value > datetime.now(UTC).date():
            raise ValueError('过往经历不能填写未来日期。')
        return value


TypedMemory = Annotated[PlaceMemory | CommuteMemory | TimePreferenceMemory | ExperienceMemory,
                        Field(discriminator='memory_type')]


class AppMemoryData(MemoryInput):
    """Keep business extensions inside the existing encrypted Nexus envelope limits."""
    namespace: Literal['personal_assistant'] = 'personal_assistant'
    schema_version: Literal[1] = 1
    fields: dict[str, JsonValue]

    @model_validator(mode='after')
    def bounded_fields(self):
        """Reject reserved permission fields and oversized or excessively nested extensions."""
        if len(json.dumps(self.fields, ensure_ascii=False, allow_nan=False).encode()) > 8192:
            raise ValueError('业务记忆字段超过 8192 字节。')
        reserved = {'user_id', 'owner_user_id', 'application_id', 'external_user_id',
                    'project_id', 'scope', 'status', 'confirmed_sensitive', 'enabled',
                    'body_uri', 'permissions'}
        pending = [(self.fields, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 6:
                raise ValueError('业务记忆嵌套过深。')
            if isinstance(value, dict):
                if reserved.intersection(value):
                    raise ValueError('业务字段不能包含身份或权限配置。')
                pending.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
        return self


class MemoryCommand(MemoryInput):
    """Manage owned memory; confirmations come from the authenticated user's action."""
    local_run_id: str | None = Field(default=None, min_length=36, max_length=36)
    action: Literal['save', 'propose', 'correct', 'forget', 'confirm', 'disclose', 'settings', 'undo']
    key: str = Field(min_length=1, max_length=128)
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    entry_id: str | None = Field(default=None, min_length=36, max_length=36)
    expected_version: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    sensitive: bool = False
    confirmed_sensitive: bool = False
    app_data: AppMemoryData | None = None
    typed_data: TypedMemory | None = None
    confirmed_type: bool = False

    @model_validator(mode='after')
    def action_fields(self):
        """Require versioned targets and retain the genuine Run requirement only for disclosure."""
        if self.action in {'save', 'propose', 'correct'} and not (self.text or '').strip():
            raise ValueError('需要记忆正文。')
        if self.action in {'correct', 'forget', 'confirm', 'disclose', 'undo'} and (
            not self.entry_id or self.expected_version is None or self.expected_version < 1
        ):
            raise ValueError('需要真实条目及当前版本。')
        if self.action == 'settings' and (self.expected_version is None or self.enabled is None):
            raise ValueError('需要当前设置版本和启用状态。')
        if self.action == 'disclose' and not self.local_run_id:
            raise ValueError('单次披露需要关联真实运行。')
        if self.typed_data is not None and (self.action not in {'save', 'propose', 'correct'} or not self.confirmed_type):
            raise ValueError('结构化记忆需要本人确认类型。')
        return self


MemoryLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class BusinessRecallRequest(MemoryInput):
    """Search any domain; task descriptions and legacy categories never grant or restrict access."""
    purpose: str = Field(default='general', min_length=1, max_length=500)
    query: str = Field(default='', max_length=4000)
    queries: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]] = Field(default_factory=list, max_length=8)
    mode: Literal['search', 'browse'] = 'search'
    categories: list[MemoryLabel] = Field(default_factory=list, max_length=20)
    tags: list[MemoryLabel] = Field(default_factory=list, max_length=20)
    matter_id: str | None = Field(default=None, min_length=36, max_length=36)
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=5, ge=1, le=10)


class BusinessFactRequest(MemoryInput):
    """Save an exact current quote in any domain, with optional open labels and no inferred values."""
    source_quote: str = Field(min_length=2, max_length=2000)
    category: MemoryLabel | None = None
    tags: list[MemoryLabel] = Field(default_factory=list, max_length=20)

    @field_validator('tags')
    @classmethod
    def unique_tags(cls, value):
        """Preserve user-facing tag order without saving duplicate optional hints."""
        return list(dict.fromkeys(value))


class AgentMemoryCommand(MemoryInput):
    """Expose entry revisions only; trusted identity, confirmation and settings are never model parameters."""
    model_config = ConfigDict(extra='forbid', json_schema_extra={'allOf':[{
        'if':{'properties':{'action':{'const':'correct'}},'required':['action']},
        'then':{'required':['entry_id','expected_entry_version','text']}}]})
    action: Literal['save','propose','correct','forget']
    source_quote: str = Field(min_length=1,max_length=2000)
    intent: Literal['user_requested','incidental'] = 'user_requested'
    execution: Literal['before_answer','after_answer'] = 'before_answer'
    entry_id: str | None = Field(default=None,min_length=36,max_length=36)
    expected_entry_version: int | None = Field(default=None,ge=1,strict=True)
    text: str | None = Field(default=None,min_length=1,max_length=4000,
        description='保存正文。save/propose省略时逐字保存source_quote；correct必须提供纠正后的完整正文，不能只传改动片段。')
    app_data: AppMemoryData | None = None
    typed_data: TypedMemory | None = None

    @model_validator(mode='after')
    def require_target_and_text(self):
        """Require exact update targets and reject blank or meaningless deletion bodies."""
        if self.action in {'save','propose'} and self.text is None:
            self.text=self.source_quote
        if self.action in {'save','propose','correct'} and not (self.text or '').strip():
            raise ValueError('保存或纠正需要正文。')
        if self.action in {'correct','forget'}:
            if self.entry_id is None or self.expected_entry_version is None:
                raise ValueError('修改或遗忘需要条目编号与当前条目版本。')
        elif self.entry_id is not None or self.expected_entry_version is not None:
            raise ValueError('新增记忆不能指定旧条目。')
        if self.action=='forget' and any(value is not None for value in (self.text,self.app_data,self.typed_data)):
            raise ValueError('遗忘不能同时改写正文。')
        return self

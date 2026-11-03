"""Published JSON inputs for record-bound time and change follow-ups."""
from datetime import datetime
from assistant_api.commute.schemas import UtcDateTime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FollowUpModel(BaseModel):
    """Keep follow-up parameters explicit in OpenAPI."""
    model_config = ConfigDict(extra='forbid')


class RecordReference(FollowUpModel):
    """Locate an owned record through an offered read operation and optional unique selection."""
    name: str = Field(min_length=1,max_length=60)
    operation_id: str = Field(min_length=1,max_length=180)
    path_parameters: dict[str,Any] = Field(default_factory=dict)
    query_parameters: dict[str,Any] = Field(default_factory=dict)
    body: dict[str,Any] | None = Field(default=None,description='仅用于OpenAPI明确标为read的JSON查询接口；GET引用通常省略。')
    result_pointer: str = Field(default='',max_length=600)
    match: dict[str,str|int|bool] = Field(default_factory=dict,max_length=8)


class ReferenceField(FollowUpModel):
    """Read a named reference field locally using JSON Pointer."""
    reference: str = Field(min_length=1,max_length=60)
    field: str = Field(min_length=1,max_length=200)


class FollowUpTrigger(FollowUpModel):
    """Choose one absolute time, record-relative time or selected record changes."""
    model_config = ConfigDict(extra='forbid', json_schema_extra={'oneOf': [
        {'required': ['kind', 'at'], 'properties': {'kind': {'const': 'time'}, 'at': {'type': 'string', 'format': 'date-time'}, 'source': {'type': 'null'}, 'reference': {'enum': [None, '']}, 'fields': {'maxItems': 0}}},
        {'required': ['kind', 'source'], 'properties': {'kind': {'const': 'time'}, 'source': {'type': 'object'}, 'at': {'type': 'null'}, 'reference': {'enum': [None, '']}, 'fields': {'maxItems': 0}}},
        {'required': ['kind', 'reference', 'fields'], 'properties': {'kind': {'const': 'record_changed'}, 'reference': {'type': 'string', 'minLength': 1}, 'fields': {'minItems': 1}, 'at': {'type': 'null'}, 'source': {'type': 'null'}}},
    ]})
    kind: Literal['time','record_changed']
    at: datetime | None = None
    source: ReferenceField | None = None
    offset_seconds: int = Field(default=0,ge=-31536000,le=31536000)
    reference: str | None = None
    fields: list[str] = Field(default_factory=list,max_length=12)

    @model_validator(mode='after')
    def validate_source(self):
        """Require one time source or a bounded record-field change subscription."""
        if self.kind=='time':
            if (self.at is None)==(self.source is None):
                raise ValueError('请选择一个时间来源')
            if self.at and self.at.tzinfo is None:
                raise ValueError('请提供带时区的时间')
            if self.reference or self.fields:
                raise ValueError('时间触发使用at或source')
        elif not self.reference or not self.fields or self.at or self.source:
            raise ValueError('记录变化触发需要reference和fields')
        return self


class StopCondition(ReferenceField):
    """Express a directly checkable completion condition without a rule language."""
    operator: Literal['eq','ne'] = 'eq'
    value: str|int|bool|None


class FollowUpCreate(FollowUpModel):
    """Create a follow-up from explicit user instructions and existing record references."""
    idempotency_key: str = Field(min_length=8,max_length=128)
    matter_id: str = Field(min_length=1,max_length=36)
    title: str = Field(min_length=1,max_length=180)
    instruction: str = Field(min_length=1,max_length=2000)
    execution_mode: Literal['evaluate','execute'] = 'evaluate'
    references: list[RecordReference] = Field(min_length=1,max_length=12)
    trigger: FollowUpTrigger
    stop_when: list[StopCondition] = Field(default_factory=list,max_length=12)


class FollowUpPatch(FollowUpModel):
    """Change a rule using its current version, including pause and resume."""
    model_config = ConfigDict(extra='forbid', json_schema_extra={'allOf': [
        {'properties': {field: {'not': {'type': 'null'}}}}
        for field in ('title','instruction','state','execution_mode','trigger','references','stop_when')
    ]})
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None,min_length=1,max_length=180)
    instruction: str | None = Field(default=None,min_length=1,max_length=2000)
    state: Literal['enabled','paused','closed'] | None = None
    execution_mode: Literal['evaluate','execute'] | None = None
    trigger: FollowUpTrigger | None = None
    references: list[RecordReference] | None = Field(default=None,min_length=1,max_length=12)
    stop_when: list[StopCondition] | None = Field(default=None,max_length=12)


class FollowUpRead(FollowUpModel):
    """Describe a saved follow-up independently of any individual execution."""
    follow_up_id: str
    matter_id: str
    title: str
    instruction: str
    state: Literal['enabled','paused','closed']
    version: int
    execution_mode: Literal['evaluate','execute']
    references: list[RecordReference]
    trigger: FollowUpTrigger
    stop_when: list[StopCondition]
    next_trigger_at: UtcDateTime | None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class FollowUpExecutionRead(FollowUpModel):
    """Locate a follow-up's answer or question in the existing conversation API."""
    execution_id: str
    follow_up_id: str
    state: Literal['queued','running','waiting_user','succeeded','failed','unknown','skipped']
    local_run_id: str | None
    created_at: UtcDateTime

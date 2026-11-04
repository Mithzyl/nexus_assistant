"""Body-only supplement input and explicit authorship/status response fields."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from assistant_api.commute.schemas import UtcDateTime


class SupplementCreate(BaseModel):
    """Accept exact user text and explicit correction/answer intent without client-supplied authorship."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=8, max_length=128)
    parent_text_id: str | None = Field(default=None, min_length=1, max_length=36)
    relation_type: Literal["correction", "answer"] | None = None

    @model_validator(mode="after")
    def require_relation_target(self):
        """Require a parent for typed actions; untyped parent links remain compatible with older clients."""
        if self.relation_type is not None and self.parent_text_id is None:
            raise ValueError("纠正或回答必须提供所关联的原文本编号。")
        return self

    @field_validator("text")
    @classmethod
    def require_content(cls, value: str) -> str:
        """Reject whitespace-only input while retaining the user's exact original text."""
        if not value.strip():
            raise ValueError("补充内容不能为空。")
        return value


class EmptyRequest(BaseModel):
    """Reject author, acceptance identity and other client-supplied metadata."""

    model_config = ConfigDict(extra="forbid")


class SupplementRelatedTextRead(BaseModel):
    """Describe one linked text without recursively expanding the whole discussion."""

    text_id: str
    supplement_id: str
    origin: Literal["user", "assistant"]
    purpose: Literal["original", "user_revision", "summary", "suggestion", "question"]
    text: str
    parent_text_id: str | None
    relation_type: Literal["correction", "answer"] | None = None
    author_user_id: str | None
    source_run_id: str | None
    accepted_at: UtcDateTime | None
    accepted_by_user_id: str | None
    created_at: UtcDateTime


class SupplementTextRead(SupplementRelatedTextRead):
    """Return direct user corrections and answers; their presence does not imply factual resolution."""

    corrections: list[SupplementRelatedTextRead] = Field(default_factory=list)
    answers: list[SupplementRelatedTextRead] = Field(default_factory=list)
    untyped_relations: list[SupplementRelatedTextRead] = Field(default_factory=list)
    correction_count: int = 0
    answer_count: int = 0
    untyped_relation_count: int = 0
    has_corrections: bool = False
    has_answers: bool = False
    relations_truncated: bool = False


class SupplementRelationsRead(BaseModel):
    """Page related user texts independently of the original supplement's creation date."""

    text_id: str
    relation_type: Literal["correction", "answer", "related"] | None
    records: list[SupplementRelatedTextRead]
    has_more: bool
    next_before_id: str | None


class SupplementRead(BaseModel):
    """Report successful saving independently of asynchronous AI analysis."""

    supplement_id: str
    matter_id: str
    saved: bool = True
    replayed: bool = False
    ai_status: Literal["queued", "running", "succeeded", "no_supplement", "failed", "unknown"]
    classification: Literal["unknown", "explanation", "progress", "mixed"]
    analysis_attempt: int
    local_run_id: str | None
    error_code: str | None
    texts: list[SupplementTextRead]
    created_at: UtcDateTime
    updated_at: UtcDateTime


class GeneratedSupplement(BaseModel):
    """Constrain model output to passive text; it cannot request business calls or forge origins."""

    model_config = ConfigDict(extra="forbid")
    purpose: Literal["summary", "suggestion", "question"]
    text: str = Field(min_length=1, max_length=2000)


class SupplementAnalysis(BaseModel):
    """Allow no additional content without generating a meaningless acknowledgement."""

    model_config = ConfigDict(extra="forbid")
    classification: Literal["unknown", "explanation", "progress", "mixed"]
    supplements: list[GeneratedSupplement] = Field(max_length=6)

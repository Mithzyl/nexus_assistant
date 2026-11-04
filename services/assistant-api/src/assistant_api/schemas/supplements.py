"""Body-only supplement input and explicit authorship/status response fields."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from assistant_api.commute.schemas import UtcDateTime


class SupplementCreate(BaseModel):
    """Accept only user text and optional source linkage; the server assigns authorship."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=8, max_length=128)
    parent_text_id: str | None = Field(default=None, min_length=1, max_length=36)

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


class SupplementTextRead(BaseModel):
    """Expose original origin separately from any later user acceptance."""

    text_id: str
    origin: Literal["user", "assistant"]
    purpose: Literal["original", "user_revision", "summary", "suggestion", "question"]
    text: str
    parent_text_id: str | None
    author_user_id: str | None
    source_run_id: str | None
    accepted_at: UtcDateTime | None
    accepted_by_user_id: str | None
    created_at: UtcDateTime


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

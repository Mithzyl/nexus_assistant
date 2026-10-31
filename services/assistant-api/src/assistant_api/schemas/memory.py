"""Explicit receiving-entry consent, distinct from access to the App's own memory."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class SharedMemoryDisclosure(BaseModel):
    """Pin one shared grant version to the new authenticated user's Run."""
    model_config = ConfigDict(extra="forbid")
    grant_id: str = Field(min_length=36, max_length=36)
    expected_version: int = Field(ge=1)
    confirmed: Literal[True]

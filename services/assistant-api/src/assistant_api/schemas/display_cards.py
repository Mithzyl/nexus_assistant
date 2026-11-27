"""Model-authored descriptions and references for supported card templates."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from assistant_api.commute.schemas import UtcDateTime

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=36)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]


class DisplayCardInput(BaseModel):
    """Reject fields that could bypass trusted route and media resolution."""

    model_config = ConfigDict(extra="forbid")


class TimelineNodeInput(DisplayCardInput):
    """Describe a text-only step when the task is not using measured route legs."""

    title: ShortText
    category: str | None = Field(default=None, max_length=40)
    summary: str | None = Field(default=None, max_length=200)
    transition_text: str | None = Field(default=None, max_length=120)


class TimelineRouteInput(DisplayCardInput):
    """Choose a current saved route or a sequence of authored text-only steps."""

    title: ShortText
    date_label: str | None = Field(default=None, max_length=40)
    matter_id: Identifier | None = None
    route_ref_id: Identifier | None = None
    route_calculated_at: UtcDateTime | None = None
    nodes: list[TimelineNodeInput] = Field(default_factory=list, max_length=30)


class ProgressCardInput(DisplayCardInput):
    """Propose a display update without permission to mutate business records."""

    expected_version: int = Field(ge=0, description="读取主卡的version；没有主卡时用0。")
    title: ShortText
    progress_state: Literal["in_progress", "watching", "awaiting_user", "completed"]
    completed_items: list[ShortText] = Field(default_factory=list, max_length=8)
    watching_items: list[ShortText] = Field(default_factory=list, max_length=8)
    latest_changes: list[ShortText] = Field(default_factory=list, max_length=8)
    next_decisions: list[ShortText] = Field(default_factory=list, max_length=8)
    embedded_card_id: Identifier | None = None
    notify_user: bool = False


class MapRouteGroupInput(DisplayCardInput):
    """Select one measured route or the user's actual saved locations for a group."""

    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    route_ref_id: Identifier | None = None
    route_calculated_at: UtcDateTime | None = None
    schedule_ids: list[Identifier] = Field(default_factory=list, max_length=12)


class MultiRouteMapInput(DisplayCardInput):
    """Define the order and names of route groups using owned record identifiers."""

    title: ShortText
    groups: list[MapRouteGroupInput] = Field(min_length=2, max_length=6)

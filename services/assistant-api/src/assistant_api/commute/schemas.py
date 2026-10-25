"""Public request/response DTOs for the commute API.

Timestamps serialize as UTC with a trailing `Z`, IDs are opaque strings, and all
fields are snake_case so the Swift client maps them explicitly. Response models
never include another user's rows, coordinates for devices, or provider secrets.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from assistant_api.commute.timeutil import (
    InvalidCommuteTime,
    ensure_aware,
    parse_hhmm,
    validate_same_day,
    validate_timezone,
    validate_weekdays,
)

UtcDateTime = Annotated[
    datetime,
    PlainSerializer(
        lambda value: ensure_aware(value)
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        return_type=str,
        when_used="json",
    ),
]

TransportMode = Literal["driving", "walking"]
CheckStatus = Literal["queued", "running", "succeeded", "degraded", "failed", "cancelled", "unknown"]
# Phase is the current step, status is the task outcome. They are orthogonal:
# a running task has a non-terminal phase; a terminal phase ("done"/"failed"/
# "cancelled") accompanies a terminal status. Legacy rows keep NULL in both the
# new phase and analysis_mode columns instead of being back-filled.
CheckPhase = Literal[
    "queued",
    "fetching_route",
    "fetching_weather",
    "analyzing",
    "saving",
    "retry_wait",
    "done",
    "failed",
    "cancelled",
]
AdvisoryQuality = Literal["fresh", "degraded", "unavailable"]
DepartureStatus = Literal["upcoming", "leave_now", "past_due", "unknown"]
NotificationPermission = Literal["authorized", "provisional", "ephemeral", "denied", "unknown"]


class CommuteLocation(BaseModel):
    """A private place the user chose; labels are never used in lock-screen copy."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=160)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)

    @field_validator("label")
    @classmethod
    def _strip_label(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("地点名称不能为空。")
        return stripped


class CommutePlanInput(BaseModel):
    """Complete editable plan body shared by create and full update."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    timezone: str = Field(min_length=1, max_length=64)
    weekdays: list[int] = Field(min_length=1, max_length=7)
    arrival_local_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    check_local_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    transport_mode: TransportMode
    buffer_minutes: int = Field(default=10, ge=0, le=120)
    rain_probability_threshold: float = Field(default=0.4, ge=0, le=1, allow_inf_nan=False)
    rain_extra_buffer_minutes: int = Field(default=10, ge=0, le=120)
    notify_enabled: bool = True
    origin: CommuteLocation
    destination: CommuteLocation

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("计划名称不能为空。")
        return stripped

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        try:
            return validate_timezone(value)
        except InvalidCommuteTime as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, value: list[int]) -> list[int]:
        try:
            return validate_weekdays(value)
        except InvalidCommuteTime as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("arrival_local_time", "check_local_time")
    @classmethod
    def _time(cls, value: str) -> str:
        try:
            parse_hhmm(value)
        except InvalidCommuteTime as exc:
            raise ValueError(str(exc)) from exc
        return value

    @model_validator(mode="after")
    def _same_day(self) -> "CommutePlanInput":
        try:
            validate_same_day(self.check_local_time, self.arrival_local_time)
        except InvalidCommuteTime as exc:
            raise ValueError(str(exc)) from exc
        return self


class CommutePlanRead(CommutePlanInput):
    """A plan plus server-owned revision and timestamps."""

    id: str
    revision: int
    created_at: UtcDateTime
    updated_at: UtcDateTime


class SourceAttribution(BaseModel):
    text: str
    url: str


class AdvisorySource(BaseModel):
    """Where one fact came from, how fresh it is, and how Apple must be credited."""

    kind: Literal["weather", "route"]
    provider: str
    fetched_at: UtcDateTime | None = None
    valid_from: UtcDateTime | None = None
    valid_to: UtcDateTime | None = None
    freshness: Literal["fresh", "stale", "unavailable"]
    attribution: SourceAttribution | None = None
    request_depart_at: UtcDateTime | None = None
    target_arrival_at: UtcDateTime | None = None
    note: str | None = None


class WeatherSummary(BaseModel):
    """Normalized forecast facts; missing values are null, never zero."""

    forecast_start: UtcDateTime | None
    forecast_end: UtcDateTime | None
    temperature_c: float | None
    precipitation_probability_max: float | None
    precipitation_probability_known: bool
    rain_probability_threshold: float
    rain_risk: bool | None
    coverage: Literal["full", "partial", "unavailable"]
    locations: list[dict[str, Any]] = Field(default_factory=list)




class RoutePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class RouteCandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route_reference: str = Field(min_length=1, max_length=128)
    distance_meters: int | None = Field(default=None, ge=0, le=2_000_000)
    duration_seconds: int | None = Field(default=None, ge=0, le=86_400)
    duration_in_traffic_seconds: int | None = Field(default=None, ge=0, le=86_400)


class RouteSnapshotInput(BaseModel):
    """Client-provided map result; never described as Apple-signed."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["mapkit_client", "client", "stub"]
    origin: RoutePoint
    destination: RoutePoint
    transport_mode: TransportMode
    query_type: Literal["departure_time", "arrival_deadline"]
    requested_departure_at: UtcDateTime | None = None
    requested_arrival_at: UtcDateTime | None = None
    expected_departure_at: UtcDateTime | None = None
    expected_arrival_at: UtcDateTime | None = None
    queried_at: UtcDateTime
    distance_meters: int | None = Field(default=None, ge=0, le=2_000_000)
    duration_seconds: int | None = Field(default=None, ge=0, le=86_400)
    duration_in_traffic_seconds: int | None = Field(default=None, ge=0, le=86_400)
    eta_source: Literal["calculate_eta", "route"] | None = None
    eta_scope: Literal["overall", "route"] | None = None
    route_reference: str | None = Field(default=None, max_length=128)
    candidate_routes: list[RouteCandidateInput] = Field(default_factory=list, max_length=8)
    source_note: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def _shape(self):
        if (self.requested_departure_at is None) == (self.requested_arrival_at is None):
            raise ValueError("exactly one requested time is required")
        if self.query_type == "departure_time" and self.requested_departure_at is None:
            raise ValueError("departure query requires requested_departure_at")
        if self.query_type == "arrival_deadline" and self.requested_arrival_at is None:
            raise ValueError("arrival query requires requested_arrival_at")
        refs = [c.route_reference for c in self.candidate_routes]
        if len(refs) != len(set(refs)):
            raise ValueError("candidate route references must be unique")
        if self.eta_scope == "route" and not self.route_reference:
            raise ValueError("route-scoped ETA requires route_reference")
        return self


class CheckCreateRequest(BaseModel):
    """Optional map snapshot attached to an existing manual check."""

    model_config = ConfigDict(extra="forbid")
    plan_revision: int = Field(ge=1)
    route_snapshot: RouteSnapshotInput


class AiAnalysisRead(BaseModel):
    status: Literal["available", "unavailable", "rejected"]
    analysis_version: int | None = None
    route_source: Literal["client_snapshot", "server_provider", "none"] | None = None
    used_duration_seconds: int | None = None
    duration_source: Literal["duration_seconds", "duration_in_traffic_seconds", "none"]
    map_duration_seconds: int | None = None
    base_buffer_seconds: int | None = None
    additional_buffer_seconds: int | None = None
    extra_buffer_seconds: int | None = None
    recommended_departure_at: UtcDateTime | None = None
    basis: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    reason: str | None = None


class AdvisoryRead(BaseModel):
    id: str
    plan_id: str
    plan_revision: int
    local_service_date: str
    generated_at: UtcDateTime
    expires_at: UtcDateTime
    quality: AdvisoryQuality
    title: str
    summary: str
    target_arrival_at: UtcDateTime
    recommended_departure_at: UtcDateTime | None
    departure_status: DepartureStatus
    eta_seconds: int | None
    buffer_seconds: int
    weather_extra_buffer_seconds: int
    weather: WeatherSummary | None
    sources: list[AdvisorySource]
    reason_codes: list[str]
    explanation_source: Literal["template", "model"]
    ai_analysis: AiAnalysisRead | None = None
    # Read-time applicability. These are computed against the current plan and
    # clock when the record is read; the stored model output is never rewritten.
    # `latest` keeps meaning "the most recently saved advisory".
    matches_current_plan: bool = False
    is_current_service_date: bool = False
    is_expired: bool = False
    current_plan_revision: int | None = None


class PlanWeatherRead(BaseModel):
    """The latest stored weather for one plan, explicitly scoped to a date.

    This is provider weather, not an advisory input: it never stands in for the
    weather an older advisory was computed with. `matches_current_plan` is false
    once the plan revision changed, and `is_stale` is computed at read time.
    """

    plan_id: str
    plan_revision: int
    local_service_date: str
    check_id: str
    provider: str
    fetched_at: UtcDateTime | None
    valid_from: UtcDateTime | None
    valid_to: UtcDateTime | None
    quality: AdvisoryQuality
    freshness: Literal["fresh", "stale", "unavailable"]
    coverage: Literal["full", "partial", "unavailable"]
    summary: WeatherSummary | None = None
    stale_after_seconds: int
    is_stale: bool
    matches_current_plan: bool
    current_plan_revision: int


class AdvisoryListRead(BaseModel):
    items: list[AdvisoryRead]


class CheckError(BaseModel):
    code: str
    message: str
    retryable: bool


class CheckCreated(BaseModel):
    check_id: str
    status: CheckStatus


class CheckRead(BaseModel):
    check_id: str
    plan_id: str
    status: CheckStatus
    phase: CheckPhase | None = None
    analysis_mode: Literal["full", "weather_only"] | None = None
    phase_updated_at: UtcDateTime | None = None
    attempt_count: int | None = None
    next_attempt_at: UtcDateTime | None = None
    trigger: Literal["manual", "auto"]
    plan_revision: int
    advisory_id: str | None
    advisory_created: bool
    error: CheckError | None
    created_at: UtcDateTime
    finished_at: UtcDateTime | None


class PushDeviceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apns_token: str = Field(min_length=32, max_length=256, pattern=r"^[0-9a-fA-F]+$")
    notification_permission: NotificationPermission


class PushDeviceRead(BaseModel):
    installation_id: str
    notification_permission: NotificationPermission
    environment: Literal["sandbox", "production"]
    updated_at: UtcDateTime


class AdvisoryActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["opened", "skip_today"]
    installation_id: str = Field(min_length=8, max_length=128)


class AdvisoryActionRead(BaseModel):
    advisory_id: str
    action: Literal["opened", "skip_today"]
    recorded_at: UtcDateTime
    replayed: bool = False


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool


class ErrorEnvelope(BaseModel):
    error: ErrorDetail
    request_id: str


class RuntimeComponentRead(BaseModel):
    state: Literal["online", "stale", "not_seen"]
    last_seen_at: UtcDateTime | None = None


class RuntimeLatestCheckRead(BaseModel):
    check_id: str
    status: CheckStatus
    phase: CheckPhase | None = None
    phase_updated_at: UtcDateTime | None = None


class RuntimeRead(BaseModel):
    """Process liveness is not task progress; both are reported separately.

    `server_time` answers "can the server respond at all", which is distinct from
    "a background process reported in recently" and from "this task advanced".
    Clients use it to age `last_seen_at` and `phase_updated_at` on their own clock.
    """

    server_time: UtcDateTime
    scheduler: RuntimeComponentRead
    worker: RuntimeComponentRead
    stale_after_seconds: int
    queue: dict[str, int]
    latest_check: RuntimeLatestCheckRead | None = None
    last_progress_at: UtcDateTime | None = None
    route_provider_available: bool
    auto_analysis_enabled: bool = False
    auto_analysis_mode: Literal["full", "weather_only"] = "weather_only"

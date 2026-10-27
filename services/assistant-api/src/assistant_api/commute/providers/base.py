"""Provider ports and normalized values.

Business code consumes these dataclasses, never a vendor's raw JSON. A provider
can fail explicitly; it must never invent a zero probability or a successful push.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

TransportMode = Literal["driving", "walking", "transit"]
LocationRole = Literal["origin", "destination"]


class ProviderError(Exception):
    """Base failure with a stable public code and retry semantics."""

    code = "provider_unavailable"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class ProviderAuthError(ProviderError):
    code = "provider_auth_failed"
    retryable = False


class ProviderRateLimited(ProviderError):
    code = "provider_rate_limited"
    retryable = True


class ProviderTimeout(ProviderError):
    code = "provider_timeout"
    retryable = True


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"
    retryable = True


class ProviderInvalidResponse(ProviderError):
    code = "provider_invalid_response"
    retryable = True


class ProviderNoRoute(ProviderError):
    code = "route_not_found"
    retryable = False


class ProviderNotConfigured(ProviderError):
    code = "provider_not_configured"
    retryable = False


class ProviderPushError(ProviderError):
    code = "push_provider_error"
    retryable = True


@dataclass(frozen=True)
class ForecastPeriod:
    start_at: datetime
    end_at: datetime
    temperature_c: float | None
    precipitation_probability: float | None
    condition_code: str | None = None


@dataclass(frozen=True)
class LocationForecast:
    role: LocationRole
    provider: str
    fetched_at: datetime
    periods: tuple[ForecastPeriod, ...]


@dataclass(frozen=True)
class WeatherResult:
    provider: str
    fetched_at: datetime
    origin: LocationForecast
    destination: LocationForecast
    attribution_text: str
    attribution_url: str
    valid_from: datetime
    valid_to: datetime


@dataclass(frozen=True)
class EtaResult:
    provider: str
    transport_mode: TransportMode
    fetched_at: datetime
    request_depart_at: datetime
    target_arrival_at: datetime
    duration_seconds: int
    distance_meters: int | None
    response_basis: str = "departAt"
    walking_distance_meters: int | None = None
    transfer_count: int | None = None
    transfers_summary: str | None = None
    fare_cny: float | None = None
    fare_kind: str | None = None
    timing_basis: str = "provider_departure_time"


@dataclass(frozen=True)
class PlaceResult:
    provider: str
    provider_place_id: str
    name: str
    address: str | None
    city: str | None
    latitude: float
    longitude: float
    coordinate_system: str
    source: str


@dataclass(frozen=True)
class AdviceText:
    title: str
    summary: str


@dataclass(frozen=True)
class PushMessage:
    apns_id: str
    device_token: str
    topic: str
    environment: Literal["sandbox", "production"]
    payload: dict
    collapse_id: str
    expires_at: datetime


@dataclass(frozen=True)
class PushResult:
    result: Literal["accepted", "rejected", "unknown"]
    http_status: int | None = None
    error_code: str | None = None
    retryable: bool = False
    provider_timestamp: datetime | None = None


@dataclass(frozen=True)
class AdviceFacts:
    """Numbers allowed in generated copy. No coordinates or address labels."""

    transport_mode: TransportMode
    target_arrival_local: str
    recommended_departure_local: str | None
    eta_minutes: int | None
    buffer_minutes: int
    weather_extra_buffer_minutes: int
    temperature_c: float | None
    rain_probability_percent: int | None
    rain_risk: bool | None
    departure_status: Literal["upcoming", "leave_now", "past_due", "unknown"]
    weather_available: bool
    eta_available: bool
    reason_codes: list[str] = field(default_factory=list)


class WeatherProvider(Protocol):
    available: bool

    async def fetch(
        self,
        *,
        origin: tuple[float, float],
        destination: tuple[float, float],
        window_start: datetime,
        window_end: datetime,
        timezone: str,
    ) -> WeatherResult: ...


class RouteEtaProvider(Protocol):
    available: bool

    async def fetch(
        self,
        *,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: TransportMode,
        request_depart_at: datetime,
        target_arrival_at: datetime,
        origin_city: str | None = None,
        destination_city: str | None = None,
    ) -> EtaResult: ...


class PlaceProvider(Protocol):
    available: bool

    async def search(
        self,
        *,
        query: str,
        city: str | None,
        limit: int,
        search_type: Literal["place", "address"],
    ) -> list[PlaceResult]: ...


class AdviceTextProvider(Protocol):
    async def generate(self, facts: AdviceFacts) -> AdviceText | None: ...


class PushProvider(Protocol):
    async def send(self, message: PushMessage) -> PushResult: ...

    async def aclose(self) -> None: ...

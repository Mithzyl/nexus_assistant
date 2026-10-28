"""Development doubles that are explicit about being mocks.

Mock data is never enabled in production by configuration validation, and its
provider name is persisted as `mock` so a notification can never be mistaken for
a real Apple Weather/ETA/APNs result.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import floor

from assistant_api.commute.providers.base import (
    AdviceFacts,
    AdviceText,
    EtaResult,
    ForecastPeriod,
    LocationForecast,
    PlaceResult,
    ProviderUnavailable,
    PushMessage,
    PushResult,
    ReverseGeocodeResult,
    WeatherResult,
)


class MockWeatherProvider:
    provider = "mock"
    available = True

    def __init__(
        self,
        *,
        now,
        rain_probability: float = 0.2,
        fail: bool = False,
    ) -> None:
        self._now = now
        self._rain_probability = rain_probability
        self.fail = fail
        self.calls: list[dict] = []

    async def fetch(
        self,
        *,
        origin: tuple[float, float],
        destination: tuple[float, float],
        window_start: datetime,
        window_end: datetime,
        timezone: str,
    ) -> WeatherResult:
        self.calls.append(
            {
                "window_start": window_start,
                "window_end": window_end,
                "timezone": timezone,
            }
        )
        if self.fail:
            raise ProviderUnavailable("mock weather unavailable", code="mock_weather_unavailable")
        fetched_at = self._now()
        start = window_start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        start -= timedelta(hours=1)
        end = window_end.astimezone(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
        periods = []
        cursor = start
        while cursor < end:
            periods.append(
                ForecastPeriod(
                    start_at=cursor,
                    end_at=cursor + timedelta(hours=1),
                    temperature_c=22.0,
                    precipitation_probability=self._rain_probability,
                    condition_code="CLEAR",
                )
            )
            cursor += timedelta(hours=1)
        origin_forecast = LocationForecast(
            role="origin", provider=self.provider, fetched_at=fetched_at, periods=tuple(periods)
        )
        destination_forecast = LocationForecast(
            role="destination",
            provider=self.provider,
            fetched_at=fetched_at,
            periods=tuple(periods),
        )
        return WeatherResult(
            provider=self.provider,
            fetched_at=fetched_at,
            origin=origin_forecast,
            destination=destination_forecast,
            attribution_text="开发 Mock 天气数据",
            attribution_url="https://example.invalid/mock-weather",
            valid_from=periods[0].start_at,
            valid_to=periods[-1].end_at,
        )


class MockEtaProvider:
    """Return explicit mock ETA data and optional test-only route shapes."""

    provider = "mock"
    available = True

    def __init__(
        self,
        *,
        now,
        duration_seconds: int = 1800,
        fail: bool = False,
        route_segments=(),
        route_shape_status: str = "missing",
    ) -> None:
        self._now = now
        self.duration_seconds = duration_seconds
        self.fail = fail
        self.route_segments = route_segments
        self.route_shape_status = route_shape_status
        self.calls: list[dict] = []

    async def fetch(
        self,
        *,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        request_depart_at: datetime,
        target_arrival_at: datetime,
        origin_city: str | None = None,
        destination_city: str | None = None,
    ) -> EtaResult:
        self.calls.append(
            {
                "mode": mode,
                "request_depart_at": request_depart_at,
                "target_arrival_at": target_arrival_at,
            }
        )
        if self.fail:
            raise ProviderUnavailable("mock route unavailable", code="mock_route_unavailable")
        return EtaResult(
            provider=self.provider,
            transport_mode=mode,  # type: ignore[arg-type]
            fetched_at=self._now(),
            request_depart_at=request_depart_at,
            target_arrival_at=target_arrival_at,
            duration_seconds=self.duration_seconds,
            distance_meters=12_000,
            response_basis="departAt",
            route_segments=self.route_segments,
            route_shape_status=self.route_shape_status,
        )


class MockPlaceProvider:
    provider = "mock"
    available = True

    async def search(
        self,
        *,
        query: str,
        city: str | None,
        limit: int,
        search_type: str,
    ) -> list[PlaceResult]:
        """Return two visibly distinct candidates so ambiguity is exercised."""

        places = [
            PlaceResult(
                provider=self.provider,
                provider_place_id="mock-poi-1",
                name=query,
                address=f"{city or '上海'}示例路 1 号",
                city=city,
                latitude=31.2231,
                longitude=121.4456,
                coordinate_system="gcj02",
                source=f"mock_{search_type}",
            ),
            PlaceResult(
                provider=self.provider,
                provider_place_id="mock-poi-2",
                name=f"{query}（另一候选）",
                address=f"{city or '上海'}示例路 2 号",
                city=city,
                latitude=31.2304,
                longitude=121.4737,
                coordinate_system="gcj02",
                source=f"mock_{search_type}",
            ),
        ]
        return places[:limit]

    async def reverse_geocode(
        self,
        *,
        latitude: float,
        longitude: float,
    ) -> ReverseGeocodeResult:
        """Return one explicit synthetic nearby address for development."""

        return ReverseGeocodeResult(
            provider=self.provider,
            name="地图选点",
            address="上海示例路附近",
            city="上海",
            latitude=latitude,
            longitude=longitude,
            coordinate_system="gcj02",
            source="mock_regeo",
        )


class MockPushProvider:
    provider = "mock"

    def __init__(self, *, now, result: str = "accepted") -> None:
        self._now = now
        self.result = result
        self.messages: list[PushMessage] = []
        self.results: list[PushResult] = []

    async def send(self, message: PushMessage) -> PushResult:
        self.messages.append(message)
        if self.result == "rejected":
            outcome = PushResult(
                result="rejected", http_status=400, error_code="mock_rejected", retryable=False
            )
        elif self.result == "unknown":
            outcome = PushResult(result="unknown", error_code="mock_timeout", retryable=False)
        else:
            outcome = PushResult(result="accepted", http_status=200)
        self.results.append(outcome)
        return outcome

    async def aclose(self) -> None:
        return None


class TemplateOnlyAdviceTextProvider:
    """Default model port: no model available, so the deterministic template wins."""

    async def generate(self, facts: AdviceFacts) -> AdviceText | None:
        return None

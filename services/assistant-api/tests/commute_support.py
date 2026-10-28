"""Test doubles for the commute scheduler, worker and provider flow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from assistant_api.commute.providers.base import (
    AdviceFacts,
    AdviceText,
    EtaResult,
    ForecastPeriod,
    LocationForecast,
    ProviderInvalidResponse,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    PushMessage,
    PushResult,
    WeatherResult,
)
from assistant_api.commute.runtime import CommuteRuntime


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def set(self, value: datetime) -> None:
        self.value = value

    def advance(self, **kwargs) -> None:
        self.value = self.value + timedelta(**kwargs)


class FakeWeatherProvider:
    provider = "fake_weather"

    def __init__(
        self,
        *,
        now,
        probability: float | None = 0.2,
        fail: Exception | None = None,
        missing_destination: bool = False,
        fetched_offset_seconds: int = 0,
        window_hours: int = 8,
    ) -> None:
        self._now = now
        self.probability = probability
        self.fail = fail
        self.missing_destination = missing_destination
        self.fetched_offset_seconds = fetched_offset_seconds
        self.window_hours = window_hours
        self.calls: list[dict] = []

    def _periods(self, start: datetime):
        cursor = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        end = cursor + timedelta(hours=self.window_hours)
        periods = []
        while cursor < end:
            periods.append(
                ForecastPeriod(
                    start_at=cursor,
                    end_at=cursor + timedelta(hours=1),
                    temperature_c=18.0,
                    precipitation_probability=self.probability,
                )
            )
            cursor += timedelta(hours=1)
        return tuple(periods)

    async def fetch(self, *, origin, destination, window_start, window_end, timezone):
        self.calls.append(
            {"window_start": window_start, "window_end": window_end, "timezone": timezone}
        )
        if self.fail is not None:
            raise self.fail
        fetched = self._now() - timedelta(seconds=self.fetched_offset_seconds)
        origin_periods = self._periods(window_start)
        destination_periods = self._periods(window_start) if not self.missing_destination else tuple()
        return WeatherResult(
            provider=self.provider,
            fetched_at=fetched,
            origin=LocationForecast(
                role="origin", provider=self.provider, fetched_at=fetched, periods=origin_periods
            ),
            destination=LocationForecast(
                role="destination",
                provider=self.provider,
                fetched_at=fetched,
                periods=destination_periods,
            ),
            attribution_text="测试天气",
            attribution_url="https://example.invalid/weather",
            valid_from=origin_periods[0].start_at,
            valid_to=origin_periods[-1].end_at,
        )


class FakeEtaProvider:
    """Return deterministic ETA data while allowing shape-specific tests."""

    provider = "fake_eta"

    def __init__(
        self,
        *,
        now,
        duration_seconds: int = 1800,
        fail: Exception | None = None,
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
        origin,
        destination,
        mode,
        request_depart_at,
        target_arrival_at,
        origin_city=None,
        destination_city=None,
    ):
        self.calls.append(
            {
                "mode": mode,
                "request_depart_at": request_depart_at,
                "target_arrival_at": target_arrival_at,
            }
        )
        if self.fail is not None:
            raise self.fail
        return EtaResult(
            provider=self.provider,
            transport_mode=mode,
            fetched_at=self._now(),
            request_depart_at=request_depart_at,
            target_arrival_at=target_arrival_at,
            duration_seconds=self.duration_seconds,
            distance_meters=10_000,
            route_segments=self.route_segments,
            route_shape_status=self.route_shape_status,
        )


class FakeAdviceTextProvider:
    def __init__(self, response: AdviceText | None = None, *, delay_seconds: float = 0.0) -> None:
        self.response = response
        self.delay_seconds = delay_seconds
        self.calls: list[AdviceFacts] = []

    async def generate(self, facts: AdviceFacts) -> AdviceText | None:
        import asyncio

        self.calls.append(facts)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return self.response


class FakeBark:
    configured = True

    def __init__(self, status: str = "accepted", message: str = "accepted") -> None:
        self.status = status
        self.message = message
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, *, title: str, body: str, group: str = "PA"):
        from assistant_api.commute.services.bark import BarkDelivery
        self.sent.append((title, body, group))
        return BarkDelivery(self.status, self.message)

    async def aclose(self) -> None:
        return None


class FakePushProvider:
    provider = "fake_apns"

    def __init__(self, results: list[PushResult] | None = None) -> None:
        self.results = list(results or [PushResult(result="accepted", http_status=200)])
        self.messages: list[PushMessage] = []

    async def send(self, message: PushMessage) -> PushResult:
        self.messages.append(message)
        if len(self.results) == 1:
            return self.results[0]
        return self.results.pop(0)

    async def aclose(self) -> None:
        return None


def build_test_runtime(
    settings,
    *,
    clock: FixedClock,
    weather: FakeWeatherProvider | None = None,
    eta: FakeEtaProvider | None = None,
    advice_text=None,
    push=None,
    bark=None,
) -> CommuteRuntime:
    from assistant_api.commute.providers.mock import TemplateOnlyAdviceTextProvider

    return CommuteRuntime(
        settings=settings,
        now=clock,
        weather=weather or FakeWeatherProvider(now=clock),
        eta=eta or FakeEtaProvider(now=clock),
        advice_text=advice_text or TemplateOnlyAdviceTextProvider(),
        push=push or FakePushProvider(),
        provider_mode="mock",
        bark=bark or FakeBark(),
    )


def commute_plan_body(**overrides):
    body = {
        "name": "上班通勤",
        "enabled": True,
        "timezone": "Asia/Shanghai",
        "weekdays": [1, 2, 3, 4, 5],
        "arrival_local_time": "09:00",
        "check_local_time": "07:30",
        "transport_mode": "driving",
        "buffer_minutes": 10,
        "rain_probability_threshold": 0.4,
        "rain_extra_buffer_minutes": 10,
        "notify_enabled": True,
        "origin": {"label": "家", "latitude": 31.2304, "longitude": 121.4737},
        "destination": {"label": "公司", "latitude": 31.2200, "longitude": 121.4500},
    }
    body.update(overrides)
    return body


def error_response(status_code: int, code: str = "provider_unavailable") -> Exception:
    return ProviderUnavailable("simulated", code=code)

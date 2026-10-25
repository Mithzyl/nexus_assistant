"""Open-Meteo forecast adapter (no API key)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from assistant_api.commute.providers.base import (
    ForecastPeriod,
    LocationForecast,
    ProviderInvalidResponse,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    WeatherResult,
)
from assistant_api.commute.timeutil import ensure_aware

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
ATTRIBUTION_TEXT = "天气数据由 Open-Meteo 提供"
ATTRIBUTION_URL = "https://open-meteo.com/"


class OpenMeteoWeatherProvider:
    provider = "open_meteo"
    available = True

    def __init__(self, *, timeout_seconds: float, now, base_url: str = OPEN_METEO_URL,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._now = now
        self._base_url = base_url
        self._client = httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False,
                                         trust_env=False, transport=transport)

    async def _fetch_location(self, latitude: float, longitude: float, timezone: str,
                              role: str) -> LocationForecast:
        try:
            response = await self._client.get(
                self._base_url,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "hourly": "temperature_2m,precipitation_probability,weather_code",
                    "timezone": timezone,
                    "forecast_days": 2,
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("Open-Meteo timed out", code="open_meteo_timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Open-Meteo request failed", code="open_meteo_unavailable") from exc
        if response.status_code == 429:
            raise ProviderRateLimited("Open-Meteo rate limited", code="open_meteo_rate_limited")
        if response.status_code >= 500:
            raise ProviderUnavailable("Open-Meteo unavailable", code="open_meteo_unavailable")
        if response.status_code != 200:
            raise ProviderInvalidResponse("Open-Meteo rejected the location",
                                          code="open_meteo_invalid_response", retryable=False)
        try:
            body = response.json()
            hourly = body["hourly"]
            times = hourly["time"]
            temps = hourly["temperature_2m"]
            probs = hourly["precipitation_probability"]
            codes = hourly.get("weather_code", [None] * len(times))
            zone = ZoneInfo(timezone)
            periods = tuple(
                ForecastPeriod(
                    start_at=datetime.fromisoformat(t).replace(tzinfo=zone).astimezone(UTC),
                    end_at=(datetime.fromisoformat(t).replace(tzinfo=zone) + timedelta(hours=1)).astimezone(UTC),
                    temperature_c=float(temp) if temp is not None else None,
                    precipitation_probability=(float(prob) / 100 if prob is not None else None),
                    condition_code=str(code) if code is not None else None,
                )
                for t, temp, prob, code in zip(times, temps, probs, codes)
            )
            if not periods:
                raise KeyError("hourly.time")
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ProviderInvalidResponse("Open-Meteo response did not contain hourly forecasts",
                                          code="open_meteo_invalid_response") from exc
        return LocationForecast(role=role, provider=self.provider,
                                fetched_at=ensure_aware(self._now()), periods=periods)

    async def fetch(self, *, origin, destination, window_start, window_end, timezone):
        origin_forecast, destination_forecast = await asyncio.gather(
            self._fetch_location(origin[0], origin[1], timezone, "origin"),
            self._fetch_location(destination[0], destination[1], timezone, "destination"),
        )
        periods = origin_forecast.periods + destination_forecast.periods
        return WeatherResult(
            provider=self.provider,
            fetched_at=ensure_aware(self._now()),
            origin=origin_forecast,
            destination=destination_forecast,
            attribution_text=ATTRIBUTION_TEXT,
            attribution_url=ATTRIBUTION_URL,
            valid_from=min(p.start_at for p in periods),
            valid_to=max(p.end_at for p in periods),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

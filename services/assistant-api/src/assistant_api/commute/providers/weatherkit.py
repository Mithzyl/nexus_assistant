"""WeatherKit REST adapter.

WeatherKit uses a short-lived ES256 JWT (team id, service id, key id) that is
cached only in memory. Business code sees normalized hourly periods, never the
vendor response. A missing forecast probability stays None.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
import jwt

from assistant_api.commute.providers.base import (
    ForecastPeriod,
    LocationForecast,
    ProviderAuthError,
    ProviderInvalidResponse,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    WeatherResult,
)
from assistant_api.commute.timeutil import ensure_aware


def _read_private_key(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProviderAuthError(
            "WeatherKit signing key is not readable", code="weatherkit_key_unreadable"
        ) from exc


class WeatherKitProvider:
    provider = "weatherkit"
    available = True

    def __init__(
        self,
        *,
        team_id: str,
        key_id: str,
        service_id: str,
        private_key_path: Path,
        base_url: str,
        language: str,
        timeout_seconds: float,
        now,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._team_id = team_id
        self._key_id = key_id
        self._service_id = service_id
        self._key_path = private_key_path
        self._base_url = base_url.rstrip("/")
        self._language = language
        self._now = now
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds, transport=transport, follow_redirects=False
        )
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    def _bearer(self) -> str:
        now = ensure_aware(self._now()).astimezone(UTC)
        if self._token and self._token_expires_at and now < self._token_expires_at:
            return self._token
        issued = int(now.timestamp())
        payload = {
            "iss": self._team_id,
            "iat": issued,
            "exp": issued + 3000,
            "sub": self._service_id,
        }
        try:
            token = jwt.encode(
                payload,
                _read_private_key(self._key_path),
                algorithm="ES256",
                headers={"kid": self._key_id},
            )
        except Exception as exc:  # noqa: BLE001 - key parsing errors are configuration
            raise ProviderAuthError(
                "WeatherKit JWT signing failed", code="weatherkit_auth_failed"
            ) from exc
        self._token = token
        self._token_expires_at = now + timedelta(seconds=3000)
        return token

    async def _fetch_location(
        self, latitude: float, longitude: float, timezone: str
    ) -> LocationForecast:
        url = (
            f"{self._base_url}/api/v1/weather/{quote(self._language, safe='')}"
            f"/{latitude:.6f}/{longitude:.6f}"
        )
        try:
            response = await self._client.get(
                url,
                params={"timezone": timezone},
                headers={"Authorization": f"Bearer {self._bearer()}"},
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("WeatherKit timed out", code="weatherkit_timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                "WeatherKit request failed", code="weatherkit_unavailable"
            ) from exc

        if response.status_code in (401, 403):
            raise ProviderAuthError(
                "WeatherKit rejected the credential", code="weatherkit_auth_failed"
            )
        if response.status_code == 429:
            retry_after = _retry_after(response)
            raise ProviderRateLimited(
                "WeatherKit rate limited",
                code="weatherkit_rate_limited",
                retry_after_seconds=retry_after,
            )
        if response.status_code >= 500:
            raise ProviderUnavailable(
                "WeatherKit is unavailable", code="weatherkit_unavailable"
            )
        if response.status_code != 200:
            raise ProviderInvalidResponse(
                "WeatherKit rejected the location",
                code="weatherkit_invalid_response",
                retryable=False,
            )
        try:
            body = response.json()
            hours = body["forecastHourly"]["hours"]
            periods = tuple(_parse_hour(entry) for entry in hours)
            if not periods:
                raise KeyError("hours")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(
                "WeatherKit response did not contain hourly forecasts",
                code="weatherkit_invalid_response",
            ) from exc
        fetched_at = ensure_aware(self._now())
        return LocationForecast(
            role="origin",
            provider=self.provider,
            fetched_at=fetched_at,
            periods=periods,
        )

    async def fetch(
        self,
        *,
        origin: tuple[float, float],
        destination: tuple[float, float],
        window_start: datetime,
        window_end: datetime,
        timezone: str,
    ) -> WeatherResult:
        origin_forecast, destination_forecast = await asyncio.gather(
            self._fetch_location(origin[0], origin[1], timezone),
            self._fetch_location(destination[0], destination[1], timezone),
        )
        origin_forecast = LocationForecast(
            role="origin",
            provider=origin_forecast.provider,
            fetched_at=origin_forecast.fetched_at,
            periods=origin_forecast.periods,
        )
        destination_forecast = LocationForecast(
            role="destination",
            provider=destination_forecast.provider,
            fetched_at=destination_forecast.fetched_at,
            periods=destination_forecast.periods,
        )
        periods = origin_forecast.periods + destination_forecast.periods
        return WeatherResult(
            provider=self.provider,
            fetched_at=ensure_aware(self._now()),
            origin=origin_forecast,
            destination=destination_forecast,
            attribution_text="天气数据由 Apple Weather 提供",
            attribution_url="https://developer.apple.com/weatherkit/data-source-attribution/",
            valid_from=min(period.start_at for period in periods),
            valid_to=max(period.end_at for period in periods),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _parse_hour(entry: dict) -> ForecastPeriod:
    start = datetime.fromisoformat(str(entry["forecastStart"]).replace("Z", "+00:00"))
    end_raw = entry.get("forecastEnd")
    end = (
        datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
        if end_raw
        else start + timedelta(hours=1)
    )
    probability = entry.get("precipitationChance")
    if probability is not None:
        probability = float(probability)
        if not 0 <= probability <= 1:
            probability = None
    temperature = entry.get("temperature")
    if temperature is not None:
        temperature = float(temperature)
    return ForecastPeriod(
        start_at=ensure_aware(start),
        end_at=ensure_aware(end),
        temperature_c=temperature,
        precipitation_probability=probability,
        condition_code=entry.get("conditionCode"),
    )


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None

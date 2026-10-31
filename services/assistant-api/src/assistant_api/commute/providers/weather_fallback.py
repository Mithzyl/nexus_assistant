"""Ordered weather fallback; AMap daily forecasts never invent hourly probabilities."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import asyncio
import logging

from .base import (ForecastPeriod, LocationForecast, WeatherResult, ProviderError,
                   ProviderInvalidResponse)

logger = logging.getLogger(__name__)


def covers(result, start, end):
    if result is None:
        return False
    for location in (result.origin, result.destination):
        cursor = start
        for p in sorted(location.periods, key=lambda p: p.start_at):
            if p.end_at <= cursor:
                continue
            if p.start_at > cursor:
                break
            if p.temperature_c is None and p.precipitation_probability is None and not p.condition_code:
                break
            cursor = max(cursor, p.end_at)
        if cursor < end:
            return False
    return True


class AmapWeatherProvider:
    available = True
    provider = "amap"

    def __init__(self, amap, now):
        self.amap, self.now = amap, now

    async def fetch(self, *, origin, destination, window_start, window_end, timezone):
        # Request-scoped deduplication: two endpoints in one district share one forecast.
        forecasts = {}
        locations = []
        zone = ZoneInfo("Asia/Shanghai")
        try:
            for role, point in (("origin", origin), ("destination", destination)):
                geo = await self.amap._get("/v3/geocode/regeo", {
                    "location": f"{point[1]},{point[0]}", "extensions": "base"})
                adcode = geo["regeocode"]["addressComponent"]["adcode"]
                if not adcode:
                    raise ValueError("missing adcode")
                if adcode not in forecasts:
                    data = await self.amap._get("/v3/weather/weatherInfo", {
                        "city": adcode, "extensions": "all", "output": "JSON"})
                    forecasts[adcode] = next(f for f in data["forecasts"] if f["adcode"] == adcode)
                periods = []
                for cast in forecasts[adcode]["casts"]:
                    start = datetime.fromisoformat(cast["date"]).replace(tzinfo=zone)
                    conditions = [cast.get("dayweather"), cast.get("nightweather")]
                    if not all(isinstance(c, str) and c.strip() for c in conditions):
                        continue
                    # Daily conditions only: day/night temperatures are extrema, not hourly values.
                    periods.append(ForecastPeriod(start, start + timedelta(days=1), None, None,
                                                  "高德按日预报：白天" + conditions[0] + "；夜间" + conditions[1]))
                locations.append(LocationForecast(role, self.provider, self.now(), tuple(periods)))
            periods = locations[0].periods + locations[1].periods
            result = WeatherResult(self.provider, self.now(), *locations,
                                   "天气由高德提供（按日预报，无逐小时气温及降雨概率）",
                                   "https://lbs.amap.com/api/webservice/guide/api/weatherinfo",
                                   min(p.start_at for p in periods), max(p.end_at for p in periods))
        except (KeyError, TypeError, ValueError, StopIteration) as exc:
            raise ProviderInvalidResponse("AMap weather is empty or invalid", code="amap_weather_invalid_response") from exc
        if not covers(result, window_start, window_end):
            raise ProviderInvalidResponse("AMap forecast does not cover target", code="amap_weather_out_of_range", retryable=False)
        return result


class FallbackWeatherProvider:
    available = True

    def __init__(self, primary, fallback, timeout_seconds):
        self.primary, self.fallback = primary, fallback
        self.timeout_seconds = timeout_seconds
        self.total_timeout_seconds = timeout_seconds * 4 + 1

    async def fetch(self, **kwargs):
        try:
            result = await asyncio.wait_for(self.primary.fetch(**kwargs), self.timeout_seconds)
            if covers(result, kwargs["window_start"], kwargs["window_end"]):
                return result
            reason = "weather_empty_or_uncovered"
        except (ProviderError, TimeoutError) as exc:
            reason = getattr(exc, "code", "weather_timeout")
        logger.info("Weather fallback to AMap: %s", reason)
        # Two reverse-geocodes and up to two district forecasts, bounded as a whole.
        return await asyncio.wait_for(self.fallback.fetch(**kwargs), self.timeout_seconds * 3)

    async def aclose(self):
        await self.primary.aclose()
        # AMap client is shared with runtime.eta/places, which owns its lifecycle.

"""AMap place lookup and route ETA adapter.

The API key stays in server settings. AMap coordinates are GCJ-02, so the
adapter rejects unrelated coordinate systems instead of silently mixing them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from assistant_api.commute.providers.base import (
    EtaResult,
    PlaceResult,
    ProviderInvalidResponse,
    ProviderNoRoute,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from assistant_api.commute.timeutil import ensure_aware


class AmapProvider:
    """Call AMap's place, geocode and direction APIs with normalized results."""

    provider = "amap"
    available = True
    coordinate_system = "gcj02"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://restapi.amap.com",
        timeout_seconds: float = 10,
        now=None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._now = now or (lambda: datetime.now(UTC))
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
        )
        self._cache: dict[tuple, tuple[datetime, EtaResult]] = {}

    async def _get(self, path: str, params: dict) -> dict:
        try:
            response = await self._client.get(
                f"{self._base_url}{path}", params={**params, "key": self._api_key}
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("AMap request timed out", code="amap_timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("AMap request failed", code="amap_unavailable") from exc
        if response.status_code == 429:
            raise ProviderRateLimited("AMap rate limited", code="amap_rate_limited")
        if response.status_code >= 500:
            raise ProviderUnavailable("AMap is unavailable", code="amap_unavailable")
        if response.status_code != 200:
            raise ProviderInvalidResponse(
                "AMap returned an unexpected status", code="amap_invalid_response"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderInvalidResponse(
                "AMap returned invalid JSON", code="amap_invalid_response"
            ) from exc
        if str(body.get("status")) != "1":
            code = "amap_rate_limited" if "LIMIT" in str(body.get("infocode", "")) else "amap_invalid_response"
            raise ProviderInvalidResponse("AMap returned an error", code=code)
        return body

    async def search(
        self,
        *,
        query: str,
        city: str | None,
        limit: int,
        search_type: str,
    ) -> list[PlaceResult]:
        """Return every candidate; callers must not assume the first one is exact."""

        if search_type == "address":
            body = await self._get(
                "/v3/geocode/geo",
                {"address": query, "city": city or ""},
            )
            rows = body.get("geocodes") or []
            results = []
            for row in rows[:limit]:
                location = str(row.get("location") or "")
                parsed = self._parse_location(location)
                if parsed is None:
                    continue
                results.append(
                    PlaceResult(
                        provider=self.provider,
                        provider_place_id=str(row.get("adcode") or location),
                        name=str(row.get("formatted_address") or query),
                        address=str(row.get("formatted_address") or "") or None,
                        city=str(row.get("city") or "") or None,
                        latitude=parsed[0],
                        longitude=parsed[1],
                        coordinate_system=self.coordinate_system,
                        source="amap_geocode",
                    )
                )
            return results

        body = await self._get(
            "/v3/place/text",
            {
                "keywords": query,
                "city": city or "",
                "citylimit": "true" if city else "false",
                "offset": max(1, min(limit, 25)),
                "page": 1,
                "extensions": "base",
            },
        )
        results = []
        for row in (body.get("pois") or [])[:limit]:
            parsed = self._parse_location(str(row.get("location") or ""))
            if parsed is None:
                continue
            results.append(
                PlaceResult(
                    provider=self.provider,
                    provider_place_id=str(row.get("id") or row.get("name") or ""),
                    name=str(row.get("name") or query),
                    address=str(row.get("address") or "") or None,
                    city=str(row.get("cityname") or "") or None,
                    latitude=parsed[0],
                    longitude=parsed[1],
                    coordinate_system=self.coordinate_system,
                    source="amap_place",
                )
            )
        return results

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
        if mode not in ("driving", "transit"):
            raise ProviderNoRoute(
                "AMap supports driving and public transit here",
                code="unsupported_transport_mode",
            )
        if mode == "transit" and not origin_city:
            raise ProviderNoRoute(
                "A city is required for public-transit routing",
                code="transit_city_required",
            )
        cache_key = (
            mode,
            round(origin[0], 5),
            round(origin[1], 5),
            round(destination[0], 5),
            round(destination[1], 5),
            origin_city,
            destination_city,
            ensure_aware(request_depart_at).astimezone(UTC).strftime("%Y-%m-%dT%H:%M"),
            ensure_aware(target_arrival_at).astimezone(UTC).strftime("%Y-%m-%dT%H:%M"),
        )
        cached = self._cache.get(cache_key)
        now = ensure_aware(self._now())
        if cached is not None and now - cached[0] < timedelta(minutes=3):
            return cached[1]

        params = {
            "origin": self._coord(origin),
            "destination": self._coord(destination),
            "extensions": "base",
        }
        if mode == "driving":
            body = await self._get("/v3/direction/driving", {**params, "strategy": 0})
            path = ((body.get("route") or {}).get("paths") or [None])[0]
            if not isinstance(path, dict):
                raise ProviderNoRoute("AMap returned no driving route", code="route_not_found")
            duration = self._positive_int(path.get("duration"))
            distance = self._positive_int(path.get("distance"))
            taxi_cost = self._positive_float((body.get("route") or {}).get("taxi_cost"))
            result = self._result(
                mode=mode,
                request_depart_at=request_depart_at,
                target_arrival_at=target_arrival_at,
                duration=duration,
                distance=distance,
                fare=taxi_cost,
                fare_kind=(
                    "amap_taxi_cost_estimate" if taxi_cost is not None else None
                ),
            )
        else:
            local = ensure_aware(request_depart_at).astimezone(
                ZoneInfo("Asia/Shanghai")
            )
            body = await self._get(
                "/v3/direction/transit/integrated",
                {
                    **params,
                    "city": origin_city,
                    "cityd": destination_city or origin_city,
                    "date": local.strftime("%Y-%m-%d"),
                    "time": local.strftime("%H:%M"),
                    "strategy": 0,
                },
            )
            route = body.get("route") or {}
            transit = (route.get("transits") or [None])[0]
            if not isinstance(transit, dict):
                raise ProviderNoRoute("AMap returned no transit route", code="route_not_found")
            segments = transit.get("segments") or []
            walking = sum(
                self._positive_int((segment.get("walking") or {}).get("distance")) or 0
                for segment in segments
                if isinstance(segment, dict)
            )
            lines = []
            for segment in segments:
                bus = segment.get("bus") or {}
                for line in bus.get("buslines") or []:
                    name = line.get("name")
                    if name:
                        lines.append(str(name))
            result = self._result(
                mode=mode,
                request_depart_at=request_depart_at,
                target_arrival_at=target_arrival_at,
                duration=self._positive_int(transit.get("duration")),
                distance=self._positive_int(transit.get("distance")),
                walking_distance=walking or None,
                transfer_count=max(0, len(lines) - 1) if lines else None,
                transfers_summary=" → ".join(lines[:5]) or None,
                fare=self._positive_float(transit.get("cost")),
                fare_kind=(
                    "amap_transit_fare"
                    if self._positive_float(transit.get("cost")) is not None
                    else None
                ),
            )
        self._cache[cache_key] = (now, result)
        return result

    def _result(
        self,
        *,
        mode: str,
        request_depart_at: datetime,
        target_arrival_at: datetime,
        duration: int | None,
        distance: int | None,
        walking_distance: int | None = None,
        transfer_count: int | None = None,
        transfers_summary: str | None = None,
        fare: float | None = None,
        fare_kind: str | None = None,
    ) -> EtaResult:
        if duration is None:
            raise ProviderNoRoute("AMap returned no usable duration", code="route_not_found")
        basis = "provider_departure_time"
        if ensure_aware(request_depart_at) > ensure_aware(self._now()) + timedelta(hours=2):
            basis = "planned_time_not_live_forecast"
        return EtaResult(
            provider=self.provider,
            transport_mode=mode,  # type: ignore[arg-type]
            fetched_at=ensure_aware(self._now()),
            request_depart_at=ensure_aware(request_depart_at),
            target_arrival_at=ensure_aware(target_arrival_at),
            duration_seconds=duration,
            distance_meters=distance,
            response_basis="departAt",
            walking_distance_meters=walking_distance,
            transfer_count=transfer_count,
            transfers_summary=transfers_summary,
            fare_cny=fare,
            fare_kind=fare_kind,
            timing_basis=basis,
        )

    @staticmethod
    def _parse_location(value: str) -> tuple[float, float] | None:
        try:
            longitude, latitude = (float(item) for item in value.split(",", 1))
        except (TypeError, ValueError):
            return None
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return None
        return latitude, longitude

    @staticmethod
    def _coord(value: tuple[float, float]) -> str:
        return f"{value[1]:.6f},{value[0]:.6f}"

    @staticmethod
    def _positive_int(value) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _positive_float(value) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    async def aclose(self) -> None:
        await self._client.aclose()

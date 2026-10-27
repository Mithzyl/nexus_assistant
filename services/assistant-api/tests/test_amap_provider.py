"""AMap adapters keep places, transit details and fees explicit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from assistant_api.commute.providers.amap import AmapProvider
from assistant_api.services.commute_estimate import estimate

NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_amap_place_and_transit_normalization_is_cached():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.url.params["key"] == "server-key"
        if request.url.path == "/v3/place/text":
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "pois": [
                        {
                            "id": "B001",
                            "name": "静安寺",
                            "address": "南京西路",
                            "cityname": "上海",
                            "location": "121.4456,31.2231",
                        },
                        {
                            "id": "B002",
                            "name": "静安寺另一入口",
                            "address": "常德路",
                            "cityname": "上海",
                            "location": "121.4460,31.2240",
                        },
                    ],
                },
            )
        if request.url.path == "/v3/direction/transit/integrated":
            assert request.url.params["city"] == "上海"
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "route": {
                        "transits": [
                            {
                                "duration": "1500",
                                "distance": "9000",
                                "cost": "4.0",
                                "segments": [
                                    {
                                        "walking": {"distance": "300"},
                                        "bus": {
                                            "buslines": [
                                                {"name": "地铁2号线"},
                                                {"name": "地铁4号线"},
                                            ]
                                        },
                                    }
                                ],
                            }
                        ]
                    },
                },
            )
        if request.url.path == "/v3/direction/driving":
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "route": {
                        "taxi_cost": "35.0",
                        "paths": [{"duration": "1200", "distance": "8500"}],
                    },
                },
            )
        return httpx.Response(404)

    provider = AmapProvider(
        api_key="server-key",
        now=lambda: NOW,
        transport=httpx.MockTransport(handler),
    )
    places = await provider.search(
        query="静安寺", city="上海", limit=5, search_type="place"
    )
    assert len(places) == 2
    assert places[0].coordinate_system == "gcj02"
    assert places[0].provider_place_id == "B001"

    depart = NOW + timedelta(days=2)
    first = await provider.fetch(
        origin=(31.2231, 121.4456),
        destination=(31.2304, 121.4737),
        mode="transit",
        request_depart_at=depart,
        target_arrival_at=depart + timedelta(minutes=30),
        origin_city="上海",
        destination_city="上海",
    )
    second = await provider.fetch(
        origin=(31.2231, 121.4456),
        destination=(31.2304, 121.4737),
        mode="transit",
        request_depart_at=depart,
        target_arrival_at=depart + timedelta(minutes=30),
        origin_city="上海",
        destination_city="上海",
    )
    assert first == second
    assert first.duration_seconds == 1500
    assert first.walking_distance_meters == 300
    assert first.transfer_count == 1
    assert first.fare_cny == 4.0
    assert first.fare_kind == "amap_transit_fare"
    assert first.timing_basis == "planned_time_not_live_forecast"
    assert paths.count("/v3/direction/transit/integrated") == 1

    driving = await provider.fetch(
        origin=(31.2231, 121.4456),
        destination=(31.2304, 121.4737),
        mode="driving",
        request_depart_at=NOW + timedelta(minutes=20),
        target_arrival_at=NOW + timedelta(minutes=50),
    )
    assert driving.duration_seconds == 1200
    assert driving.fare_cny == 35.0
    assert driving.fare_kind == "amap_taxi_cost_estimate"
    assert driving.timing_basis == "provider_departure_time"
    await provider.aclose()


@pytest.mark.asyncio
async def test_amap_route_rejects_mixed_coordinate_systems_before_provider_call():
    called = False

    class Eta:
        provider = "amap"
        available = True

        async def fetch(self, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("provider must not be called")

    runtime = SimpleNamespace(eta=Eta(), now=lambda: NOW)
    result = await estimate(
        runtime,
        origin=(31.2, 121.4),
        destination=(31.3, 121.5),
        mode="driving",
        depart_at=NOW,
        arrive_by=None,
        origin_coordinate_system="wgs84",
        destination_coordinate_system="gcj02",
    )
    assert result["status"] == "unavailable"
    assert result["reason_code"] == "coordinate_system_mismatch"
    assert called is False

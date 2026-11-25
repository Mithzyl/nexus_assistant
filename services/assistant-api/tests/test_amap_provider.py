"""AMap adapters keep places, transit details and fees explicit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from assistant_api.commute.providers.amap import AmapProvider
from assistant_api.commute.schemas import CommuteLocation
from assistant_api.commute.services.plans import route_locations
from assistant_api.services.commute_estimate import estimate

NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


async def test_plan_gps_coordinates_convert_only_for_amap():
    """Keep the plan GPS value and use the converted point for the provider request."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Confirm the provider receives the original GPS point for conversion."""
        requests.append(request.url.path)
        assert request.url.params["coordsys"] == "gps"
        assert request.url.params["locations"] == "109.400000,24.300000"
        return httpx.Response(200, json={"status": "1", "locations": "109.404000,24.303000"})

    provider = AmapProvider(api_key="test", transport=httpx.MockTransport(handler))
    try:
        gps = CommuteLocation(label="GPS", latitude=24.30, longitude=109.40, coordinate_system="wgs84")
        amap = CommuteLocation(label="候选点", latitude=24.33, longitude=109.43, coordinate_system="gcj02", city="柳州")
        origin, destination = await route_locations(SimpleNamespace(eta=provider), gps, amap)
        assert gps.coordinate_system == "wgs84" and gps.longitude == 109.40
        assert (origin.latitude, origin.longitude, origin.coordinate_system) == (24.303, 109.404, "gcj02")
        assert destination == amap
        assert requests == ["/v3/assistant/coordinate/convert"]
    finally:
        await provider.aclose()


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
            assert request.url.params["extensions"] == "all"
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
                                        "walking": {
                                            "distance": "300",
                                            "steps": [
                                                {
                                                    "polyline": (
                                                        "121.4456,31.2231;"
                                                        "121.4460,31.2235"
                                                    )
                                                }
                                            ],
                                        },
                                        "bus": {
                                            "buslines": [
                                                {
                                                    "name": "地铁2号线",
                                                    "polyline": (
                                                        "121.4460,31.2235;"
                                                        "121.4737,31.2304"
                                                    ),
                                                },
                                                {
                                                    "name": "地铁4号线",
                                                    "polyline": (
                                                        "121.4460,31.2235;"
                                                        "121.4737,31.2304"
                                                    ),
                                                },
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
            assert request.url.params["extensions"] == "all"
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "route": {
                        "taxi_cost": "35.0",
                        "paths": [
                            {
                                "duration": "1200",
                                "distance": "8500",
                                "steps": [
                                    {
                                        "polyline": (
                                            "121.4456,31.2231;"
                                            "121.4500,31.2260;"
                                            "121.4737,31.2304"
                                        )
                                    }
                                ],
                            }
                        ],
                    },
                },
            )
        if request.url.path == "/v3/geocode/regeo":
            assert request.url.params["location"] == "121.445600,31.223100"
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "regeocode": {
                        "formatted_address": "上海市静安区南京西路",
                        "addressComponent": {
                            "city": "上海市",
                            "building": {"name": ""},
                        },
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
    assert first.route_shape_status == "available"
    assert [segment.kind for segment in first.route_segments] == [
        "walking",
        "transit",
    ]
    assert first.route_segments[1].label == "地铁2号线"
    assert first.route_segments[0].points[0].longitude == 121.4456
    assert {segment.coordinate_system for segment in first.route_segments} == {
        "gcj02"
    }
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
    assert driving.route_shape_status == "available"
    assert driving.route_segments[0].kind == "driving"
    assert len(driving.route_segments[0].points) == 3
    assert driving.route_segments[0].coordinate_system == "gcj02"

    map_point = await provider.reverse_geocode(
        latitude=31.2231,
        longitude=121.4456,
    )
    assert map_point.name == "地图选点"
    assert map_point.address == "上海市静安区南京西路"
    assert map_point.coordinate_system == "gcj02"
    assert map_point.source == "amap_regeo"
    await provider.aclose()


@pytest.mark.asyncio
async def test_amap_single_point_route_shape_is_reported_missing():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/direction/driving"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "route": {
                    "paths": [
                        {
                            "duration": "600",
                            "distance": "3000",
                            "steps": [
                                {"polyline": "121.4456,31.2231"},
                            ],
                        }
                    ]
                },
            },
        )

    provider = AmapProvider(
        api_key="server-key",
        now=lambda: NOW,
        transport=httpx.MockTransport(handler),
    )
    result = await provider.fetch(
        origin=(31.2231, 121.4456),
        destination=(31.2304, 121.4737),
        mode="driving",
        request_depart_at=NOW,
        target_arrival_at=NOW + timedelta(minutes=20),
    )
    assert result.route_segments == ()
    assert result.route_shape_status == "missing"
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

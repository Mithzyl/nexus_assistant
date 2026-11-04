"""Exercise draft-free lookup using the isolated database and mock place provider only."""

import pytest
from sqlalchemy import func, select

from assistant_api.commute.providers.mock import MockPlaceProvider
from assistant_api.models import MatterDraft
from assistant_api.schemas.matters import MapPickResponse, PlaceSearchResponse
from assistant_api.services import commute_places
from conftest import auth


@pytest.mark.asyncio
async def test_lookup_uses_body_without_creating_or_requiring_a_draft(harness):
    """A logged-in commute form can search before any business draft exists."""
    harness.app.state.commute_runtime.places = MockPlaceProvider()
    response = await harness.client.post(
        "/api/v1/places/search",
        json={"query": "示例路", "city": "上海", "search_type": "address", "limit": 2},
        headers=auth(harness.token_a),
    )
    assert response.status_code == 200
    assert response.request.url.query == b""
    body = response.json()
    assert body["ambiguous"] is True
    assert len(body["candidates"]) == 2
    assert body["candidates"][0]["source"] == "mock_address"
    assert all("selection_token" not in candidate for candidate in body["candidates"])
    async with harness.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterDraft)) == 0


@pytest.mark.asyncio
async def test_map_point_lookup_does_not_require_a_draft(harness):
    """Reverse lookup preserves the selected GCJ-02 point without issuing a draft token."""
    harness.app.state.commute_runtime.places = MockPlaceProvider()
    response = await harness.client.post(
        "/api/v1/places/map-pick",
        json={"latitude": 31.23, "longitude": 121.47, "coordinate_system": "gcj02"},
        headers=auth(harness.token_a),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "available"
    assert body["location"]["latitude"] == 31.23
    assert "selection_token" not in body["location"]


@pytest.mark.asyncio
async def test_lookup_requires_login_and_rejects_url_parameters(harness):
    """Lookup uses normal authentication and accepts query conditions only in JSON."""
    body = {"query": "示例路"}
    anonymous = await harness.client.post("/api/v1/places/search", json=body)
    assert anonymous.status_code == 401
    rejected = await harness.client.post(
        "/api/v1/places/search?query=address-must-not-be-in-url",
        json=body, headers=auth(harness.token_a),
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "unexpected_query_parameters"


@pytest.mark.asyncio
async def test_shared_lookup_does_not_bypass_existing_draft_checks(harness):
    """The legacy draft endpoint still rejects an absent draft instead of acting generically."""
    response = await harness.client.post(
        "/api/v1/commute/places/search",
        json={"query": "示例路", "draft_id": "missing-draft"},
        headers=auth(harness.token_a),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_unconfigured_place_service_is_not_an_empty_success(harness):
    """Unavailable providers return an explicit reason, distinct from no matching places."""
    harness.app.state.commute_runtime.places = None
    response = await harness.client.post(
        "/api/v1/places/search", json={"query": "示例路"}, headers=auth(harness.token_a),
    )
    assert response.status_code == 200
    assert response.json()["reason_code"] == "amap_not_configured"


@pytest.mark.asyncio
async def test_draft_wrappers_keep_signed_candidate_response_shapes(harness):
    """Shared lookup still supplies the legacy draft wrappers' required selection tokens."""
    runtime = harness.app.state.commute_runtime
    runtime.places = MockPlaceProvider()
    context = {"secret": "isolated-place-selection-secret", "user_id": harness.user_a, "draft_id": "isolated-draft"}
    searched = await commute_places.search_places(
        runtime, **context, query="示例路", city="上海", search_type="place", limit=2,
    )
    search = PlaceSearchResponse.model_validate(searched)
    assert search.candidates[0].selection_token
    picked = await commute_places.map_pick_place(
        runtime, **context, latitude=31.23, longitude=121.47, coordinate_system="gcj02",
    )
    point = MapPickResponse.model_validate(picked)
    assert point.location.selection_token
    assert point.location.latitude == 31.23

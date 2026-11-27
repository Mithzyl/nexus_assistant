"""Exercise saved route groups and account-scoped image uploads in disposable databases."""
from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image

from assistant_api.models import User
from assistant_api.services import image_attachments, schedule_routes
from assistant_api.services.api_operations import OperationCatalog
from conftest import auth
from test_display_cards import answer, call, fixed_agent, intake, measured_commute, resolve


def sample_photo():
    """Make a real, oversized-for-thumbnail image with private metadata."""
    exif = Image.Exif()
    exif[315] = "private test author"
    with BytesIO() as stream:
        Image.new("RGB", (1200, 800), "#adcabb").save(stream, "JPEG", exif=exif)
        return stream.getvalue()


async def create_matter(h, token, title):
    """Create a visible matter used for attachment ownership checks."""
    response = await h.client.post("/api/v1/matters", headers=auth(token), json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_owned_images_are_uploaded_previewed_linked_and_deleted(harness):
    """Serve distinct private variants and block cross-account list, preview, change and delete."""
    h = harness
    route = "/api/v1/image-attachments"
    headers = auth(h.token_a)
    foreign = auth(h.token_b)
    media = {"content-type": "application/octet-stream"}
    matter = await create_matter(h, h.token_a, "周末资料")
    mid = matter["matter_id"]
    foreign_mid = (await create_matter(h, h.token_b, "另一个账号"))["matter_id"]
    original = sample_photo()
    denied = await h.client.post(route, headers={**headers, **media, "X-Matter-Id": foreign_mid}, content=original)
    assert denied.status_code == 404
    response = await h.client.post(route, headers={**headers, **media, "X-Matter-Id": mid}, content=original)
    assert response.status_code == 201, response.text
    info = response.json()
    assert (info["matter_id"], info["width"], info["height"], info["media_type"]) == (mid, 1200, 800, "image/jpeg")
    assert "original" not in info and "thumbnail" not in info
    assert (await h.client.get(route, params={"matter_id": mid}, headers=headers)).json() == [info]
    assert (await h.client.get(route, headers=foreign)).json() == []
    assert (await h.client.get(route, params={"matter_id": mid}, headers=foreign)).status_code == 404

    for path in (info["original_path"], info["thumbnail_path"]):
        assert (await h.client.get("/api/v1" + path)).status_code == 401
        unauthorized = await h.client.get("/api/v1" + path, headers=foreign)
        assert unauthorized.status_code == 404
    thumb = await h.client.get("/api/v1" + info["thumbnail_path"], headers=headers)
    assert thumb.headers["content-type"] == "image/png"
    assert thumb.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in thumb.headers["cache-control"]
    with Image.open(BytesIO(thumb.content)) as resized:
        assert resized.size == (480, 320)
        assert resized.getexif().get(315) is None
    full = await h.client.get("/api/v1" + info["original_path"], headers=headers)
    assert full.headers["content-type"] == "image/jpeg"
    assert full.content == original
    image = route + "/" + info["attachment_id"]
    assert (await h.client.patch(image, headers=foreign, json={"caption": "越权"})).status_code == 404
    assert (await h.client.delete(image, headers=foreign)).status_code == 404
    assert (await h.client.patch(image, headers=headers, json={"matter_id": foreign_mid})).status_code == 404
    assert (await h.client.patch(image, headers=headers, json={"caption": "出发资料"})).json()["caption"] == "出发资料"
    assert (await h.client.delete(image, headers=headers)).status_code == 204
    assert (await h.client.get("/api/v1" + info["original_path"], headers=headers)).status_code == 404
    assert all(not item.path.startswith(route) for item in OperationCatalog(h.app).operations.values())
    assert h.fake.calls == []


@pytest.mark.asyncio
async def test_image_upload_rejects_non_image_and_bounded_inputs(harness, monkeypatch):
    """Validate the bytes, size and dimensions rather than believing the request's MIME claim."""
    h = harness
    headers = {**auth(h.token_a), "content-type": "application/octet-stream"}
    target = "/api/v1/image-attachments"
    invalid = await h.client.post(target, headers=headers, content=b'<svg onload="alert(1)"></svg>')
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "image_invalid"
    with BytesIO() as stream:
        Image.new("1", (6001, 1)).save(stream, "PNG")
        rejected = await h.client.post(target, headers=headers, content=stream.getvalue())
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "image_dimensions"
    monkeypatch.setattr(image_attachments, "MAX_UPLOAD_BYTES", 32)
    rejected = await h.client.post(target, headers=headers, content=b"x" * 33)
    assert rejected.status_code == 413
    assert (await h.client.get(target, headers=auth(h.token_a))).json() == []


async def save_sample_map(h):
    """Save two distinct measured route groups and one real location-only group in a single Agent."""
    plan, day = await measured_commute(h)
    headers = auth(h.token_a)
    route1 = day["route"]
    schedule = await h.client.post("/api/v1/schedules", headers=headers, json={
        "idempotency_key": "multi-map-second-schedule", "title": "公园午餐",
        "location": "柳江公园", "starts_at": "2026-11-01T12:00:00+08:00", "ends_at": "2026-11-01T12:30:00+08:00",
        "latitude": 24.315, "longitude": 109.425, "coordinate_system": "gcj02",
    })
    assert schedule.status_code == 201, schedule.text
    schedule_id = schedule.json()["schedule"]["schedule_id"]
    route2 = deepcopy(route1)
    route2["legs"] = [{"kind": "walking", "duration_seconds": 720, "distance_meters": 900}]
    route2["distance_meters"], route2["duration_seconds"] = 900, 720
    route2["shape"] = {"status": "available", "segments": [{"kind": "walking", "label": "步行",
        "coordinate_system": "gcj02", "points": [{"latitude": 24.314, "longitude": 109.419},
                                                 {"latitude": 24.315, "longitude": 109.425}]}]}
    route2["origin"] = {**route2["origin"], "latitude": 24.314, "longitude": 109.419}
    route2["destination"] = {**route2["destination"], "latitude": 24.315, "longitude": 109.425}
    async with h.app.state.session_factory() as db:
        user = await db.get(User, h.user_a)
        await schedule_routes.save_for_schedule(db, user, schedule_id=schedule_id, result=route2)
    other_route = await h.client.get("/api/v1/commute/routes/" + schedule_id, headers=headers)
    assert other_route.status_code == 200, other_route.text
    route2 = other_route.json()
    groups = [
        {"label": "周六上班", "route_ref_id": route1["route_ref_id"], "route_calculated_at": route1["calculated_at"]},
        {"label": "周日公园", "route_ref_id": route2["route_ref_id"], "route_calculated_at": route2["calculated_at"]},
        {"label": "只看地点", "schedule_ids": [schedule_id]},
    ]
    bad = [groups[0], {"label": "无权路线", "schedule_ids": ["unknown-schedule-id"]}]
    replies = fixed_agent(h, [
        resolve("save_multi_route_map_card"),
        call("save_multi_route_map_card", {"title": "周末路线", "groups": bad}),
        call("save_multi_route_map_card", {"title": "周末路线", "groups": [
            {**groups[0], "polyline_points": [[1, 2], [2, 3]]}, groups[1],
        ]}),
        call("save_multi_route_map_card", {"title": "周末路线", "groups": groups}),
        lambda results: answer("路线已整理。", cards=[{"card_id": results[-1]["result"]["data"]["card_id"]}]),
    ])
    result = await intake(h, "把周六上班和周日公园路线分组显示", "map-groups-conversation")
    assert [value.get("execution_status") for value in replies[1:4]] == ["failed", "failed", "succeeded"], replies
    assert replies[1]["error"]["code"] == "card_place_unavailable"
    assert "groups" not in replies[3]["result"]["data"]
    card = result["pending_context"]["cards"][0]
    assert card["card_type"] == "multi_route_map"
    return card


@pytest.mark.asyncio
async def test_saved_multiroute_card_uses_selected_owned_geometry(commute_harness):
    """Keep route groups, marker-only nodes and old-client fields on a single saved result."""
    card = await save_sample_map(commute_harness)
    first, second, places = card["payload"]["groups"]
    assert first["distance_meters"] == 8400 and second["distance_meters"] == 900
    assert first["shape"]["segments"][1]["points"][-1]["longitude"] == 109.411
    assert second["shape"]["segments"][0]["points"][-1]["longitude"] == 109.425
    assert first["blocks"][-1]["items"][0]["title"] == "步行 8 分钟到中山路站"
    assert second["blocks"][-1]["items"][0]["title"] == "步行 12 分钟"
    assert places["shape"]["segments"] == [] and places["distance_meters"] is None
    assert places["blocks"][-1]["items"][0]["title"] == "公园午餐"
    assert places["markers"][0]["label"] == "柳江公园"
    assert all(key in card for key in ("kind", "title", "blocks", "status"))

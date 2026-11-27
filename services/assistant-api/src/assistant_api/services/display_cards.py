"""Build old-client-readable cards from owned data and save versioned progress."""
from datetime import UTC, datetime
from math import isfinite

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from assistant_api.commute.providers.base import TRANSPORT_LABELS, transit_line_label
from assistant_api.core.errors import ConflictError, InvalidRequestError, NotFoundError
from assistant_api.models import DisplayCard, Matter, ScheduleEvent, ScheduleRouteResult
from assistant_api.models.base import as_utc
from assistant_api.schemas.display_cards import MultiRouteMapInput, ProgressCardInput, TimelineRouteInput

PROGRESS_SECTIONS = {
    "completed_items": "已完成",
    "watching_items": "正在关注",
    "latest_changes": "新变化",
    "next_decisions": "等你拍板",
}


def _minutes(seconds):
    """Format measured trip duration, rounding up partial minutes."""
    return f"{(seconds + 59) // 60} 分钟" if seconds else None


def timeline_content(title, *, route=None, nodes=(), date_label=None, detail_href=None):
    """Build a readable route time line and keep only provider geometry for the map."""
    route = route or {}
    legs = route.get("legs") or route.get("shape", {}).get("segments") or []
    items = []
    for index, leg in enumerate(legs[:30]):
        kind = leg.get("kind")
        label = leg.get("label")
        seconds = _minutes(leg.get("duration_seconds"))
        meters = leg.get("distance_meters")
        departure = leg.get("departure_stop_name")
        arrival = leg.get("arrival_stop_name")
        if kind == "walking":
            next_leg = legs[index + 1] if index + 1 < len(legs) else {}
            next_stop = next_leg.get("departure_stop_name")
            heading = "步行" + (f" {seconds}" if seconds else "") + (f"到{next_stop}" if next_stop else "")
            description = f"{meters} 米" if meters else None
        elif kind == "transit":
            line = transit_line_label(label) if label else "公交或轨道交通"
            heading = f"乘 {line}" + (f"，在{arrival}下车" if arrival else "")
            description = " · ".join(filter(None, [f"{departure}上车" if departure else None, seconds]))
        else:
            heading = TRANSPORT_LABELS.get(kind, "前往目的地")
            description = seconds
        items.append({"title": heading[:180], "description": description or None})
    if not items:
        items = list(nodes)
    if not items and route:
        items = [{"title": TRANSPORT_LABELS.get(route.get("mode"), "前往目的地")}]
    blocks = []
    totals = " · ".join(filter(None, [
        f"全程约 {_minutes(route['duration_seconds'])}" if route.get("duration_seconds") else None,
        f"{route['distance_meters'] / 1000:g} 公里" if route.get("distance_meters") else None,
    ]))
    if totals:
        blocks.append({"type": "text", "text": totals})
    if items:
        blocks.append({"type": "timeline", "items": items})
    return {
        "kind": "info", "status": "info", "title": title, "subtitle": date_label,
        "blocks": blocks, "card_type": "timeline_route",
        "payload": {k: v for k, v in {
            "shape": route.get("shape"), "origin": route.get("origin"),
            "destination": route.get("destination"), "detail_href": detail_href,
        }.items() if v is not None},
    }


def published_card(row):
    """Attach server-owned identity and version to a saved presentation."""
    return {**row.content_json, "card_id": row.card_id, "version": row.version,
            "source_mode": row.source_mode, "matter_id": row.matter_id,
            "updated_at": as_utc(row.updated_at).isoformat()}


async def owned_route_snapshot(db, user, route_ref_id, route_calculated_at, matter_id):
    """Resolve only a checked route version from an owned, active schedule."""
    from assistant_api.services.schedule_routes import validate_saved_route

    row = await db.scalar(select(ScheduleRouteResult).where(
        ScheduleRouteResult.route_result_id == route_ref_id, ScheduleRouteResult.user_id == user.user_id))
    schedule = await db.scalar(select(ScheduleEvent).where(
        ScheduleEvent.schedule_id == row.schedule_id, ScheduleEvent.user_id == user.user_id,
        ScheduleEvent.deleted_at.is_(None), ScheduleEvent.status == "scheduled")) if row else None
    if not schedule or (matter_id and schedule.matter_id != matter_id):
        raise NotFoundError("card_route_unavailable", "找不到这张卡可使用的路线。")
    if as_utc(row.calculated_at) != route_calculated_at:
        raise ConflictError("card_route_changed", "路线已更新，请重新读取。")
    route, _ = await validate_saved_route(db, user, schedule, row)
    if route is None:
        raise ConflictError("card_route_changed", "路线已失效，请重新读取或查路线。")
    return route, schedule


async def save_timeline(db, user, run, body: TimelineRouteInput, creation_key):
    """Save a bounded immutable snapshot after checking current route ownership and revision."""
    if run is None or run.user_id != user.user_id:
        raise NotFoundError("card_run_unavailable", "当前对话不可用。")
    if bool(body.route_ref_id) == bool(body.nodes) or bool(body.route_ref_id) != bool(body.route_calculated_at):
        raise InvalidRequestError("card_route_invalid", "请选择一条带测算时间的已保存路线，或填写行程步骤。")
    existing = await db.scalar(select(DisplayCard).where(
        DisplayCard.user_id == user.user_id, DisplayCard.source_run_id == run.local_run_id,
        DisplayCard.creation_key == creation_key))
    if existing:
        return existing
    bound_matter = (run.context_json or {}).get("api_flow", {}).get("matter_id")
    matter_id = body.matter_id or bound_matter
    route = None
    if body.route_ref_id:
        route, schedule = await owned_route_snapshot(db, user, body.route_ref_id, body.route_calculated_at, matter_id)
        matter_id = schedule.matter_id
    if matter_id:
        matter = await db.get(Matter, matter_id)
        if not matter or matter.user_id != user.user_id or matter.deleted_at or (bound_matter and matter_id != bound_matter):
            raise NotFoundError("card_matter_unavailable", "找不到这张卡可使用的事项。")
    nodes = [{"title": n.title, "category": n.category, "description": n.summary[:60] if n.summary else None,
              "connector": n.transition_text} for n in body.nodes]
    card = DisplayCard(
        user_id=user.user_id, matter_id=matter_id, source_run_id=run.local_run_id, creation_key=creation_key,
        source_mode="chat", card_type="timeline_route",
        content_json=timeline_content(body.title, route=route, nodes=nodes, date_label=body.date_label,
                                      detail_href=f"/matters/{matter_id}" if matter_id else None),
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return card


def route_marker(value, label):
    """Accept only finite provider or saved schedule points in the supported range."""
    if not isinstance(value, dict):
        return None
    latitude, longitude = value.get("latitude"), value.get("longitude")
    if (type(latitude) not in (int, float) or type(longitude) not in (int, float)
            or not isfinite(latitude) or not isfinite(longitude)
            or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
        return None
    return {"latitude": latitude, "longitude": longitude, "label": label,
            "coordinate_system": value.get("coordinate_system") or "unknown"}


def map_shape(route):
    """Bound snapshot geometry and preserve the endpoints of every retained segment."""
    segments = []
    for segment in (route.get("shape") or {}).get("segments", [])[:30]:
        points = segment.get("points") or []
        stride = max(1, (len(points) + 298) // 299)
        sampled = points[::stride]
        if sampled and sampled[-1] != points[-1]:
            sampled.append(points[-1])
        if len(sampled) >= 2:
            segments.append({**segment, "points": sampled})
    return {"status": (route.get("shape") or {}).get("status", "missing"), "segments": segments}


async def map_route_group(db, user, group, matter_id, position):
    """Build a grouped map and a matching text time line from the same owned records."""
    if bool(group.route_ref_id) == bool(group.schedule_ids) or bool(group.route_ref_id) != bool(group.route_calculated_at):
        raise InvalidRequestError("card_route_invalid", "每组请选择一条已保存的路线，或选择已有日程地点。")
    if group.route_ref_id:
        route, schedule = await owned_route_snapshot(db, user, group.route_ref_id, group.route_calculated_at, matter_id)
        timeline = timeline_content(group.label, route=route)
        markers = [route_marker(route.get("origin"), "出发点"),
                   route_marker(route.get("destination"), schedule.location or "目的地")]
        shape = map_shape(route)
        distance = route.get("distance_meters")
        transport_mode = route.get("mode")
    else:
        ids = list(dict.fromkeys(group.schedule_ids))
        rows = await db.scalars(select(ScheduleEvent).where(
            ScheduleEvent.schedule_id.in_(ids), ScheduleEvent.user_id == user.user_id,
            ScheduleEvent.deleted_at.is_(None), ScheduleEvent.status == "scheduled"))
        by_id = {row.schedule_id: row for row in rows}
        if len(by_id) != len(ids) or (matter_id and any(row.matter_id != matter_id for row in by_id.values())):
            raise NotFoundError("card_place_unavailable", "找不到这组可使用的日程地点。")
        schedules = [by_id[identifier] for identifier in ids]
        markers = [route_marker({"latitude": row.latitude, "longitude": row.longitude,
                                  "coordinate_system": row.coordinate_system}, row.location or row.title)
                   for row in schedules]
        timeline = timeline_content(group.label, nodes=[{"title": row.title, "description": row.location}
                                                        for row in schedules])
        shape = {"status": "missing", "segments": []}
        distance = transport_mode = None
    return {"group_id": str(position), "label": group.label, "distance_meters": distance,
            "transport_mode": transport_mode, "shape": shape,
            "markers": [marker for marker in markers if marker], "blocks": timeline["blocks"]}


async def save_multi_route_map(db, user, run, body: MultiRouteMapInput, creation_key):
    """Save route groups as a historical card and retain readable blocks for older clients."""
    if run is None or run.user_id != user.user_id:
        raise NotFoundError("card_run_unavailable", "当前对话不可用。")
    existing = await db.scalar(select(DisplayCard).where(
        DisplayCard.user_id == user.user_id, DisplayCard.source_run_id == run.local_run_id,
        DisplayCard.creation_key == creation_key))
    if existing:
        return existing
    matter_id = ((run.context_json or {}).get("api_flow") or {}).get("matter_id")
    labels = {group.label for group in body.groups}
    if len(labels) != len(body.groups):
        raise InvalidRequestError("card_group_labels_duplicate", "每组路线需要不同的名称。")
    groups = [await map_route_group(db, user, group, matter_id, index)
              for index, group in enumerate(body.groups)]
    blocks = []
    for group in groups:
        summary = next((block["text"] for block in group["blocks"] if block["type"] == "text"), None)
        blocks.append({"type": "text", "text": group["label"] + (f" · {summary}" if summary else "")})
        blocks.extend(block for block in group["blocks"] if block["type"] == "timeline")
    card = DisplayCard(
        user_id=user.user_id, matter_id=matter_id, source_run_id=run.local_run_id, creation_key=creation_key,
        source_mode="chat", card_type="multi_route_map",
        content_json={"kind": "info", "status": "info", "title": body.title, "blocks": blocks,
                      "card_type": "multi_route_map", "payload": {"groups": groups}},
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return card


async def current_progress(db, user_id, matter_id):
    """Read the current progress card in the owned matter's unique slot."""
    return await db.scalar(select(DisplayCard).where(
        DisplayCard.user_id == user_id, DisplayCard.progress_matter_id == matter_id))


async def progress_content(db, user_id, matter_id, body: ProgressCardInput):
    """Build four display sections and resolve an optional same-matter time line."""
    sections = {key: list(dict.fromkeys(getattr(body, key))) for key in PROGRESS_SECTIONS}
    blocks = [{"type": "fields", "items": [{"label": label, "value": "、".join(sections[key])}]}
              for key, label in PROGRESS_SECTIONS.items() if sections[key]]
    embedded = None
    if body.embedded_card_id:
        card = await db.scalar(select(DisplayCard).where(
            DisplayCard.card_id == body.embedded_card_id, DisplayCard.user_id == user_id,
            DisplayCard.matter_id == matter_id, DisplayCard.card_type.in_(("timeline_route", "multi_route_map"))))
        if card is None:
            raise NotFoundError("card_child_unavailable", "找不到该事项可使用的行程卡。")
        embedded = published_card(card)
        blocks.extend(embedded["blocks"])
    if not blocks:
        raise InvalidRequestError("card_progress_empty", "请至少填写一项进展。")
    return {"kind": "info", "status": "info", "title": body.title, "blocks": blocks,
            "card_type": "long_task_progress",
            "payload": {**sections, "progress_state": body.progress_state, "embedded_card": embedded}}


async def apply_progress(db, run, *, source_mode):
    """Save a validated observation after successful completion, with atomic version checking."""
    flow = (run.context_json or {}).get("api_flow") or {}
    proposal = flow.get("progress_proposal")
    matter_id = flow.get("matter_id")
    if not proposal or not matter_id or run.status != "succeeded":
        return None
    matter = await db.get(Matter, matter_id, populate_existing=True)
    if not matter or matter.user_id != run.user_id or matter.deleted_at or matter.status not in ("active", "waiting"):
        return "matter_unavailable"
    body = ProgressCardInput.model_validate(proposal)
    current = await current_progress(db, run.user_id, matter_id)
    if current and current.source_run_id == run.local_run_id:
        return "saved"
    if (current.version if current else 0) != body.expected_version:
        return "version_changed"
    try:
        content = await progress_content(db, run.user_id, matter_id, body)
    except (NotFoundError, InvalidRequestError):
        return "references_changed"
    if current and current.content_json == content:
        return "unchanged"
    now = datetime.now(UTC)
    previous = current.content_json["payload"] if current else {}
    changes = any(set(content["payload"][key]) - set(previous.get(key, []))
                  for key in ("latest_changes", "next_decisions"))
    notify_at = now if source_mode == "scheduled_follow_up" and body.notify_user and changes else None
    values = dict(
        source_run_id=run.local_run_id, source_mode=source_mode, version=body.expected_version + 1,
        content_json=content, notify_at=notify_at, updated_at=now,
    )
    if current:
        changed = await db.execute(update(DisplayCard).where(
            DisplayCard.card_id == current.card_id, DisplayCard.version == body.expected_version).values(**values))
        return "saved" if changed.rowcount == 1 else "version_changed"
    try:
        async with db.begin_nested():
            db.add(DisplayCard(user_id=run.user_id, matter_id=matter_id, progress_matter_id=matter_id,
                               card_type="long_task_progress", **values))
            await db.flush()
    except IntegrityError:
        if await current_progress(db, run.user_id, matter_id):
            return "version_changed"
        raise
    return "saved"

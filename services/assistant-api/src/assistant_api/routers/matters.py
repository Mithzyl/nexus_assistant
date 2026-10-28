"""Matter, draft, today, reminder and estimate endpoints.

Route order matters: literal paths (`/matters/intake`, `/matters/drafts/...`) are
declared before parameterised ones. Every route scopes by the authenticated user.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Header, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.core.deps import (
    CommuteRuntimeDependency,
    CurrentUser,
    DatabaseSession,
    NexusClient,
    SettingsDependency,
)
from assistant_api.schemas import matters as schemas
from assistant_api.core.errors import InvalidRequestError, NotFoundError
from assistant_api.models import ScheduleEvent
from assistant_api.services import commute_estimate, commute_places, matters, schedule_routes

router = APIRouter(prefix="/api/v1", tags=["matters"])


class EmptyBody(BaseModel):
    pass


def _intake_response(
    *,
    result: dict,
    response: Response,
) -> dict:
    outcome = result["outcome"]
    if outcome in ("answer", "needs_clarification"):
        response.status_code = 200
    elif outcome == "draft":
        response.status_code = 201
    return {
        "draft": result.get("draft"),
        "replayed": bool(result.get("replayed")),
        "intent": result.get("intent", "matter"),
        "outcome": outcome,
        "message": (
            "这句话更像问题或闲聊，已直接回答，不会创建事项。"
            if outcome == "answer"
            else "还需要补充信息后才能形成待确认安排。"
            if outcome == "needs_clarification"
            else None
        ),
        "answer": result.get("answer"),
        "clarify": result.get("clarify"),
        "pending_context": result.get("pending_context") or {},
        "local_run_id": result["local_run_id"],
        "parent_run_id": result.get("parent_run_id"),
    }


@router.get("/matters/intake", response_model=list[schemas.IntakeResultRead])
async def list_intake_results(
    db: DatabaseSession,
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=50),
) -> list[dict]:
    """List the caller's unified input operations, including unfinished runs."""

    return await matters.list_intake_results(db, user, limit=limit)


@router.get("/matters/intake/{local_run_id}", response_model=schemas.IntakeResultRead)
async def get_intake_result(
    local_run_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    """Reload one answer, clarification, draft or unresolved processing state."""

    return await matters.get_intake_result(db, user, local_run_id)


@router.post("/matters/intake", response_model=schemas.IntakeResponse, status_code=201)
async def intake(
    request: Request,
    payload: schemas.IntakeRequest,
    db: DatabaseSession,
    client: NexusClient,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
    response: Response,
) -> dict:
    result = await matters.intake(
        db,
        client,
        settings,
        request.app.state.session_factory,
        runtime,
        user=user,
        text=payload.text,
        idempotency_key=payload.idempotency_key,
        continuation_run_id=payload.continuation_run_id,
    )
    return _intake_response(result=result, response=response)


@router.post(
    "/matters/{matter_id}/intake",
    response_model=schemas.IntakeResponse,
    status_code=201,
)
async def intake_for_matter(
    matter_id: str,
    request: Request,
    payload: schemas.IntakeRequest,
    db: DatabaseSession,
    client: NexusClient,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
    response: Response,
) -> dict:
    result = await matters.intake(
        db,
        client,
        settings,
        request.app.state.session_factory,
        runtime,
        user=user,
        text=payload.text,
        idempotency_key=payload.idempotency_key,
        matter_id=matter_id,
        continuation_run_id=payload.continuation_run_id,
    )
    return _intake_response(result=result, response=response)


@router.post(
    "/matters/{matter_id}/drafts",
    response_model=schemas.DraftRead,
    status_code=201,
)
async def create_edit_draft(
    matter_id: str,
    payload: schemas.MatterEditRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    return await matters.create_edit_draft(
        db, runtime, settings, user, matter_id, payload
    )


@router.get("/matters/{matter_id}/changes", response_model=list[schemas.ChangeRead])
async def list_changes(
    matter_id: str, db: DatabaseSession, user: CurrentUser
) -> list[dict]:
    return await matters.list_changes(db, user, matter_id)


@router.get("/matters/drafts", response_model=list[schemas.DraftRead])
async def list_drafts(
    db: DatabaseSession,
    user: CurrentUser,
    draft_status: str | None = Query(default=None, alias="status"),
    matter_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[dict]:
    return await matters.list_drafts(
        db, user, status=draft_status, matter_id=matter_id, limit=limit
    )


@router.get("/matters/drafts/{draft_id}", response_model=schemas.DraftRead)
async def get_draft(
    draft_id: str,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    return await matters.get_draft(db, runtime, settings, user, draft_id)


@router.post(
    "/matters/drafts/{draft_id}/select-matter", response_model=schemas.DraftRead
)
async def select_matter(
    draft_id: str,
    payload: schemas.DraftSelectMatterRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    return await matters.select_matter(
        db, runtime, settings, user, draft_id, payload
    )


@router.post(
    "/matters/drafts/{draft_id}/apply-option", response_model=schemas.DraftRead
)
async def apply_option(
    draft_id: str,
    payload: schemas.DraftOptionRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    return await matters.apply_option(
        db, runtime, settings, user, draft_id, payload
    )


@router.patch("/matters/drafts/{draft_id}", response_model=schemas.DraftRead)
async def patch_draft(
    draft_id: str,
    payload: schemas.DraftPatchRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    return await matters.patch_draft(db, runtime, settings, user, draft_id, payload)


@router.post(
    "/matters/drafts/{draft_id}/confirm",
    response_model=schemas.ConfirmResponse,
)
async def confirm_draft(
    draft_id: str,
    payload: schemas.ConfirmRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
) -> dict:
    return await matters.confirm_draft(
        db, runtime, settings, user, draft_id, payload, idempotency_key
    )


@router.post("/matters/drafts/{draft_id}/cancel", response_model=schemas.DraftRead)
async def cancel_draft(
    draft_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    return await matters.cancel_draft(db, user, draft_id)


@router.get("/matters", response_model=schemas.MatterListRead)
async def list_matters(
    db: DatabaseSession,
    user: CurrentUser,
    group: schemas.MatterGroup = "all",
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None),
) -> dict:
    offset = 0
    if cursor:
        try:
            offset = max(0, int(cursor))
        except ValueError:
            offset = 0
    items, next_cursor = await matters.list_matters(
        db, user, group=group, limit=limit, offset=offset
    )
    return {"items": items, "next_cursor": next_cursor}


@router.post("/matters", response_model=schemas.MatterRead, status_code=201)
async def create_matter(
    payload: schemas.MatterCreate, db: DatabaseSession, user: CurrentUser
) -> dict:
    return await matters.create_matter(db, user, payload)


@router.get("/matters/{matter_id}", response_model=schemas.MatterDetailRead)
async def get_matter(
    matter_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    return await matters.get_matter_detail(db, user, matter_id)


@router.patch("/matters/{matter_id}", response_model=schemas.MatterRead)
async def update_matter(
    matter_id: str,
    payload: schemas.MatterUpdate,
    db: DatabaseSession,
    user: CurrentUser,
) -> dict:
    return await matters.update_matter(db, user, matter_id, payload)


@router.post("/matters/{matter_id}/archive", response_model=schemas.MatterRead)
async def archive_matter(
    matter_id: str,
    payload: schemas.MatterVersionRequest,
    db: DatabaseSession,
    user: CurrentUser,
) -> dict:
    return await matters.set_status(
        db,
        user,
        matter_id,
        status="archived",
        expected_version=payload.expected_version,
        cancel_reminders=payload.cancel_reminders,
    )


@router.post("/matters/{matter_id}/reopen", response_model=schemas.MatterRead)
async def reopen_matter(
    matter_id: str,
    payload: schemas.MatterVersionRequest,
    db: DatabaseSession,
    user: CurrentUser,
) -> dict:
    return await matters.set_status(
        db,
        user,
        matter_id,
        status="active",
        expected_version=payload.expected_version,
        cancel_reminders=False,
    )


@router.delete("/matters/{matter_id}")
async def delete_matter(
    matter_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    return await matters.delete_matter(db, user, matter_id)


@router.delete(
    "/matters/{matter_id}/initial",
    response_model=schemas.InitialDeleteResponse,
)
async def initial_delete_matter(
    matter_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    """Delete one untouched initial create and only records created with it."""

    return await matters.initial_delete_matter(db, user, matter_id)


@router.post("/matters/{matter_id}/todos/{todo_id}/complete", response_model=schemas.TodoRead)
async def complete_todo(
    matter_id: str,
    todo_id: str,
    db: DatabaseSession,
    user: CurrentUser,
    cancel_reminders: bool = Query(default=False),
) -> dict:
    return await matters.complete_todo(
        db, user, matter_id, todo_id, done=True, cancel_reminders=cancel_reminders
    )


@router.post("/matters/{matter_id}/todos/{todo_id}/reopen", response_model=schemas.TodoRead)
async def reopen_todo(
    matter_id: str, todo_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    return await matters.complete_todo(db, user, matter_id, todo_id, done=False)


@router.post(
    "/matters/{matter_id}/changes/{change_id}/revert",
    response_model=schemas.RevertResponse,
)
async def revert_change(
    matter_id: str,
    change_id: str,
    payload: schemas.RevertRequest,
    db: DatabaseSession,
    user: CurrentUser,
) -> dict:
    return await matters.revert_change(
        db, user, matter_id, change_id, expected_version=payload.expected_version
    )


@router.get("/today", response_model=schemas.TodayRead)
async def today(
    db: DatabaseSession,
    settings: SettingsDependency,
    user: CurrentUser,
    day: date | None = Query(default=None, alias="date"),
) -> dict:
    timezone = await matters.user_timezone(db, user, settings.matter_default_timezone)
    from zoneinfo import ZoneInfo

    target = day or datetime.now(ZoneInfo(timezone)).date()
    return await matters.today_view(db, user, day=target, timezone=timezone)


@router.get("/reminders", response_model=list[schemas.ReminderRead])
async def list_reminders(
    db: DatabaseSession,
    user: CurrentUser,
    starts_at: datetime | None = Query(default=None, alias="from"),
    ends_at: datetime | None = Query(default=None, alias="to"),
    reminder_status: str | None = Query(default=None, alias="status"),
) -> list[dict]:
    return await matters.list_reminders(
        db, user, starts_at=starts_at, ends_at=ends_at, status=reminder_status
    )


@router.post(
    "/reminders/{reminder_id}/acknowledge",
    response_model=schemas.ReminderRead,
)
async def acknowledge_reminder(
    reminder_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    """Mark a due reminder as seen; repeated calls return the same state."""

    return await matters.acknowledge_reminder(db, user, reminder_id)


@router.post("/commute/places/search", response_model=schemas.PlaceSearchResponse)
async def commute_place_search(
    payload: schemas.PlaceSearchRequest,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    settings: SettingsDependency,
    user: CurrentUser,
) -> dict:
    """Search places or addresses; multiple results remain explicit candidates."""

    await matters.validate_place_draft(db, user, payload.draft_id)
    return await commute_places.search_places(
        runtime,
        secret=settings.tool_callback_token.get_secret_value(),
        user_id=user.user_id,
        draft_id=payload.draft_id,
        query=payload.query,
        city=payload.city,
        search_type=payload.search_type,
        limit=payload.limit,
    )


@router.post("/commute/places/map-pick", response_model=schemas.MapPickResponse)
async def commute_place_map_pick(
    payload: schemas.MapPickRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    """Reverse geocode one explicit GCJ-02 point for the current draft."""

    return await matters.create_map_pick(
        db,
        runtime,
        settings,
        user,
        draft_id=payload.draft_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        coordinate_system=payload.coordinate_system,
    )


@router.post("/commute/places/confirm", response_model=schemas.DraftRead)
async def commute_place_confirm(
    payload: schemas.PlaceSelectionRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    """Apply a signed candidate to a preview and recalculate conflicts."""

    return await matters.apply_place_selection(
        db,
        runtime,
        settings,
        user,
        draft_id=payload.draft_id,
        selection_token=payload.selection_token,
        secret=settings.tool_callback_token.get_secret_value(),
    )


@router.post("/commute/estimate", response_model=schemas.EstimateResponse)
async def commute_estimate_endpoint(
    payload: schemas.EstimateRequest,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    """POST is this implementation's choice (the plan did not specify a method)."""

    origin = payload.origin
    origin_basis = payload.origin_basis
    origin_schedule_fingerprint = None
    if payload.origin_schedule_id:
        previous = await db.scalar(
            select(ScheduleEvent).where(
                ScheduleEvent.schedule_id == payload.origin_schedule_id,
                ScheduleEvent.user_id == user.user_id,
                ScheduleEvent.deleted_at.is_(None),
            )
        )
        if previous is None:
            raise NotFoundError("schedule_not_found", "起点日程不存在。")
        if previous.latitude is None or previous.longitude is None:
            raise InvalidRequestError(
                "origin_coordinates_missing", "上一条日程没有可用坐标，请补充地点。"
            )
        origin = schemas.Coordinate(
            latitude=previous.latitude,
            longitude=previous.longitude,
            coordinate_system=previous.coordinate_system or "unknown",
            city=previous.location_city,
        )
        origin_basis = "previous_schedule"
        origin_schedule_fingerprint = schedule_routes.schedule_fingerprint(previous)
    assert origin is not None

    result = await commute_estimate.estimate(
        runtime,
        origin=(origin.latitude, origin.longitude),
        destination=(payload.destination.latitude, payload.destination.longitude),
        mode=payload.mode,
        depart_at=payload.depart_at,
        arrive_by=payload.arrive_by,
        origin_city=origin.city,
        destination_city=payload.destination.city,
        origin_coordinate_system=origin.coordinate_system,
        destination_coordinate_system=payload.destination.coordinate_system,
        origin_basis=origin_basis,
        origin_schedule_id=payload.origin_schedule_id,
        schedule_id=payload.schedule_id,
    )
    if payload.schedule_id and result["status"] == "available":
        if origin_schedule_fingerprint is not None:
            result["origin_schedule_fingerprint"] = origin_schedule_fingerprint
        stored = await schedule_routes.save_for_schedule(
            db,
            user,
            schedule_id=payload.schedule_id,
            result=result,
        )
        return {**result, **stored}
    return result


@router.get(
    "/commute/routes/{schedule_id}",
    response_model=schemas.ScheduledRouteRead,
)
async def commute_schedule_route(
    schedule_id: str,
    db: DatabaseSession,
    user: CurrentUser,
) -> dict:
    """Read one saved route without measuring again or mutating the schedule."""

    return await schedule_routes.detail_for_schedule(db, user, schedule_id)


__all__ = ["router"]

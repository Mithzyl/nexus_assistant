"""Matter, draft, today, reminder and estimate endpoints.

Route order matters: literal paths (`/matters/intake`, `/matters/drafts/...`) are
declared before parameterised ones. Every route scopes by the authenticated user.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Header, Query, Request, status
from pydantic import BaseModel

from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.core.deps import (
    CommuteRuntimeDependency,
    CurrentUser,
    DatabaseSession,
    NexusClient,
    SettingsDependency,
)
from assistant_api.schemas import matters as schemas
from assistant_api.services import commute_estimate, matters

router = APIRouter(prefix="/api/v1", tags=["matters"])


class EmptyBody(BaseModel):
    pass


@router.post("/matters/intake", response_model=schemas.IntakeResponse, status_code=201)
async def intake(
    request: Request,
    payload: schemas.IntakeRequest,
    db: DatabaseSession,
    client: NexusClient,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    draft, replayed = await matters.intake(
        db,
        client,
        settings,
        request.app.state.session_factory,
        runtime,
        user=user,
        text=payload.text,
        idempotency_key=payload.idempotency_key,
    )
    return {"draft": draft, "replayed": replayed}


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
) -> dict:
    draft, replayed = await matters.intake(
        db,
        client,
        settings,
        request.app.state.session_factory,
        runtime,
        user=user,
        text=payload.text,
        idempotency_key=payload.idempotency_key,
        matter_id=matter_id,
    )
    return {"draft": draft, "replayed": replayed}


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


@router.post("/commute/estimate", response_model=schemas.EstimateResponse)
async def commute_estimate_endpoint(
    payload: schemas.EstimateRequest,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    """POST is this implementation's choice (the plan did not specify a method)."""

    return await commute_estimate.estimate(
        runtime,
        origin=(payload.origin.latitude, payload.origin.longitude),
        destination=(payload.destination.latitude, payload.destination.longitude),
        mode=payload.mode,
        depart_at=payload.depart_at,
        arrive_by=payload.arrive_by,
    )


__all__ = ["router"]

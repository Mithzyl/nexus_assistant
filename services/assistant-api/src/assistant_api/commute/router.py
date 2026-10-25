"""/api/v1 commute routes. Identity always comes from the session, never a body."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Header, Path, Query, Request, Response, status
from sqlalchemy import delete, select

from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.commute.schemas import (
    CheckCreateRequest,
    AdvisoryActionInput,
    AdvisoryActionRead,
    AdvisoryRead,
    CheckCreated,
    CheckRead,
    CommutePlanInput,
    CommutePlanRead,
    PlanWeatherRead,
    PushDeviceInput,
    PushDeviceRead,
    RuntimeRead,
)
from assistant_api.commute.heartbeat import runtime_snapshot
from assistant_api.commute.serializers import (
    advisory_read,
    check_read,
    plan_read,
    plan_weather_read,
)
from assistant_api.commute.services import actions as action_service
from assistant_api.commute.services import checks as check_service
from assistant_api.commute.services import plans as plan_service
from assistant_api.commute.services.scheduling import latest_advisory
from assistant_api.commute.timeutil import ensure_aware, local_today
from assistant_api.core.deps import (
    CommuteRuntimeDependency,
    CurrentUser,
    DatabaseSession,
    SettingsDependency,
    bearer_token,
)
from assistant_api.core.errors import ConflictError, ForbiddenError, NotFoundError
from assistant_api.core.security import digest_token
from assistant_api.models import (
    CommuteAdvisory,
    CommuteCheck,
    CommuteInstance,
    CommutePlan,
    CommuteWeatherSnapshot,
    PushDevice,
    UserSession,
)

router = APIRouter(prefix="/api/v1", tags=["commute"])

IdempotencyKey = Header(alias="Idempotency-Key", min_length=8, max_length=128)
IfMatch = Header(default=None, alias="If-Match")


def _etag(revision: int) -> str:
    return f'"{revision}"'


@router.get("/commute/runtime", response_model=RuntimeRead)
async def get_commute_runtime(
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    settings: SettingsDependency,
    user: CurrentUser,
) -> RuntimeRead:
    now = ensure_aware(runtime.now())
    snapshot = await runtime_snapshot(
        db,
        user_id=user.user_id,
        now=now,
        stale_seconds=settings.commute_heartbeat_stale_seconds,
    )
    snapshot["server_time"] = now
    route_available = bool(getattr(runtime.eta, "available", False))
    snapshot["route_provider_available"] = route_available
    snapshot["auto_analysis_enabled"] = settings.commute_auto_ai_enabled
    snapshot["auto_analysis_mode"] = (
        "full"
        if settings.commute_auto_ai_enabled and route_available
        else "weather_only"
    )
    return RuntimeRead.model_validate(snapshot)


@router.get("/commute-plans", response_model=list[CommutePlanRead])
async def list_commute_plans(
    db: DatabaseSession, user: CurrentUser
) -> list[CommutePlanRead]:
    return [plan_read(plan) for plan in await plan_service.list_plans(db, user.user_id)]


@router.post(
    "/commute-plans",
    response_model=CommutePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_commute_plan(
    payload: CommutePlanInput,
    response: Response,
    db: DatabaseSession,
    user: CurrentUser,
) -> CommutePlanRead:
    plan = await plan_service.create_plan(db, user.user_id, payload)
    response.headers["ETag"] = _etag(plan.revision)
    return plan_read(plan)


@router.get("/commute-plans/{plan_id}", response_model=CommutePlanRead)
async def get_commute_plan(
    plan_id: str,
    response: Response,
    db: DatabaseSession,
    user: CurrentUser,
) -> CommutePlanRead:
    plan = await plan_service.get_plan(db, user.user_id, plan_id)
    response.headers["ETag"] = _etag(plan.revision)
    return plan_read(plan)


@router.put("/commute-plans/{plan_id}", response_model=CommutePlanRead)
async def update_commute_plan(
    plan_id: str,
    payload: CommutePlanInput,
    response: Response,
    db: DatabaseSession,
    user: CurrentUser,
    if_match: str | None = IfMatch,
) -> CommutePlanRead:
    plan = await plan_service.update_plan(
        db,
        user_id=user.user_id,
        plan_id=plan_id,
        payload=payload,
        if_match=if_match,
    )
    response.headers["ETag"] = _etag(plan.revision)
    return plan_read(plan)


@router.delete("/commute-plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_commute_plan(
    plan_id: str, db: DatabaseSession, user: CurrentUser
) -> Response:
    await plan_service.delete_plan(db, user_id=user.user_id, plan_id=plan_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/commute-plans/{plan_id}/checks",
    response_model=CheckCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_check(
    plan_id: str,
    response: Response,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
    idempotency_key: str = IdempotencyKey,
    payload: CheckCreateRequest | None = Body(default=None),
) -> CheckCreated:
    created, replayed = await check_service.create_manual_check(
        db,
        runtime,
        user_id=user.user_id,
        plan_id=plan_id,
        idempotency_key=idempotency_key,
        request=payload,
    )
    response.headers["X-Idempotent-Replay"] = "true" if replayed else "false"
    return created


@router.get("/checks/{check_id}", response_model=CheckRead)
async def get_check(
    check_id: str, db: DatabaseSession, user: CurrentUser
) -> CheckRead:
    check = await check_service.get_check(db, user_id=user.user_id, check_id=check_id)
    return check_read(check)


@router.get("/commute-plans/{plan_id}/weather", response_model=PlanWeatherRead)
async def get_plan_weather(
    plan_id: str,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    settings: SettingsDependency,
    user: CurrentUser,
    local_service_date: date | None = Query(default=None),
) -> PlanWeatherRead:
    """Return the latest stored weather for one plan and one service date.

    The authoritative link is `commute_weather_snapshots.check_id`; the check
    carries the plan revision and its instance carries the service date.
    `commute_checks.weather_snapshot_id` is never written and is not consulted.
    A missing snapshot is reported as such instead of borrowing another day or a
    different plan location.
    """

    plan = await plan_service.get_plan(db, user.user_id, plan_id)
    now = ensure_aware(runtime.now())
    day = local_service_date or local_today(now, plan.timezone)
    row = (
        await db.execute(
            select(CommuteWeatherSnapshot, CommuteCheck)
            .join(CommuteCheck, CommuteCheck.check_id == CommuteWeatherSnapshot.check_id)
            .join(CommuteInstance, CommuteInstance.instance_id == CommuteCheck.instance_id)
            .where(
                CommuteWeatherSnapshot.user_id == user.user_id,
                CommuteCheck.plan_id == plan.plan_id,
                CommuteInstance.local_service_date == day,
                CommuteWeatherSnapshot.quality != "unavailable",
            )
            .order_by(
                CommuteWeatherSnapshot.fetched_at.desc(),
                CommuteWeatherSnapshot.created_at.desc(),
            )
            .limit(1)
        )
    ).first()
    if row is None:
        raise NotFoundError("NO_WEATHER", "该计划当天没有可用的天气记录。")
    snapshot, check = row
    return plan_weather_read(
        snapshot,
        check,
        plan,
        local_service_date=day,
        now=now,
        stale_after_seconds=settings.commute_weather_freshness_seconds,
    )


@router.get("/commute-plans/{plan_id}/advisories/latest", response_model=AdvisoryRead)
async def get_latest_advisory(
    plan_id: str,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> AdvisoryRead:
    """The most recently saved advisory, with read-time applicability flags.

    "Latest" keeps its existing meaning; it is not silently redefined as "the
    advisory that is currently valid". A client decides how to label a record
    whose plan revision, service date or expiry no longer matches.
    """

    plan = await plan_service.get_plan(db, user.user_id, plan_id)
    advisory = await latest_advisory(db, user_id=user.user_id, plan_id=plan_id)
    if advisory is None:
        raise NotFoundError("NO_ADVISORY", "暂无通勤建议。")
    return advisory_read(advisory, plan=plan, now=ensure_aware(runtime.now()))


@router.get("/advisories/{advisory_id}", response_model=AdvisoryRead)
async def get_advisory(
    advisory_id: str,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> AdvisoryRead:
    advisory = await db.scalar(
        select(CommuteAdvisory).where(
            CommuteAdvisory.advisory_id == advisory_id,
            CommuteAdvisory.user_id == user.user_id,
        )
    )
    if advisory is None:
        raise NotFoundError("advisory_not_found", "通勤建议不存在。")
    plan = await db.scalar(
        select(CommutePlan).where(
            CommutePlan.plan_id == advisory.plan_id,
            CommutePlan.user_id == user.user_id,
        )
    )
    return advisory_read(
        advisory, plan=plan, now=ensure_aware(runtime.now())
    )


@router.put("/push-devices/{installation_id}", response_model=PushDeviceRead)
async def register_push_device(
    installation_id: Annotated[str, Path(min_length=8, max_length=128)],
    payload: PushDeviceInput,
    request: Request,
    db: DatabaseSession,
    settings: SettingsDependency,
    user: CurrentUser,
) -> PushDeviceRead:
    """Bind this installation's current APNs token to the session's user.

    The client never chooses topic or environment. A binding held by a *live*
    session of another account is not stolen; the App must log out first, which
    deletes that session's device rows.
    """

    now = datetime.now(UTC)
    token = bearer_token(request) or ""
    session = await db.scalar(
        select(UserSession).where(
            UserSession.token_digest == digest_token(token),
            UserSession.user_id == user.user_id,
            UserSession.revoked_at.is_(None),
        )
    )
    # Reject a second live account binding for the same install unless the old
    # session is already revoked/expired. This prevents notification crossover.
    others = list(
        (
            await db.scalars(
                select(PushDevice).where(
                    PushDevice.installation_id == installation_id,
                    PushDevice.user_id != user.user_id,
                )
            )
        ).all()
    )
    for other in others:
        other_session = (
            await db.scalar(
                select(UserSession).where(UserSession.session_id == other.session_id)
            )
            if other.session_id
            else None
        )
        live = (
            other_session is not None
            and other_session.revoked_at is None
            and ensure_aware(other_session.expires_at) > now
        )
        if live:
            raise ConflictError(
                "installation_bound_other_user",
                "该安装实例仍绑定其他账号，请先退出旧账号。",
            )
        await db.delete(other)

    device = await db.scalar(
        select(PushDevice).where(
            PushDevice.user_id == user.user_id,
            PushDevice.installation_id == installation_id,
        )
    )
    token_value = payload.apns_token.lower()
    topic = settings.commute_apns_topic or "com.example.assistant"
    if device is None:
        device = PushDevice(
            user_id=user.user_id,
            session_id=session.session_id if session else None,
            installation_id=installation_id,
            apns_token=token_value,
            notification_permission=payload.notification_permission,
            environment=settings.commute_apns_environment,
            topic=topic,
            token_updated_at=now,
            last_registered_at=now,
        )
        db.add(device)
    else:
        if device.apns_token != token_value:
            device.token_updated_at = now
        device.apns_token = token_value
        device.notification_permission = payload.notification_permission
        device.environment = settings.commute_apns_environment
        device.topic = topic
        device.session_id = session.session_id if session else device.session_id
        device.last_registered_at = now
    await db.commit()
    await db.refresh(device)
    return PushDeviceRead(
        installation_id=device.installation_id,
        notification_permission=device.notification_permission,
        environment=device.environment,
        updated_at=device.updated_at,
    )


@router.delete("/push-devices/{installation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_push_device(
    installation_id: Annotated[str, Path(min_length=8, max_length=128)],
    db: DatabaseSession,
    user: CurrentUser,
) -> Response:
    await db.execute(
        delete(PushDevice).where(
            PushDevice.user_id == user.user_id,
            PushDevice.installation_id == installation_id,
        )
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/advisories/{advisory_id}/actions", response_model=AdvisoryActionRead)
async def advisory_action(
    advisory_id: str,
    payload: AdvisoryActionInput,
    response: Response,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
    idempotency_key: str = IdempotencyKey,
) -> AdvisoryActionRead:
    record, replayed = await action_service.record_action(
        db,
        user_id=user.user_id,
        advisory_id=advisory_id,
        payload=payload,
        idempotency_key=idempotency_key,
        now=ensure_aware(runtime.now()),
    )
    response.headers["X-Idempotent-Replay"] = "true" if replayed else "false"
    return AdvisoryActionRead(**{**record, "replayed": replayed})

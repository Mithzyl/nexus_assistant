"""Plan CRUD, optimistic concurrency and deletion cleanup."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute.schemas import CommuteLocation, CommutePlanInput
from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.core.errors import AssistantError, ConflictError, InvalidRequestError, NotFoundError
from assistant_api.models import (
    AdvisoryAction,
    CommuteAdvisory,
    CommuteCheck,
    CommuteEtaSnapshot,
    CommuteIdempotencyKey,
    CommuteInstance,
    CommutePlan,
    CommuteWeatherSnapshot,
    NotificationOutbox,
    PushAttempt,
)

IF_MATCH_RE = re.compile(r'^(?:W/)?"?(\d+)"?$')


class PreconditionRequiredError(AssistantError):
    def __init__(self) -> None:
        super().__init__(
            "precondition_required",
            "修改计划必须携带 If-Match: 当前 revision。",
            428,
        )


def parse_if_match(header: str | None) -> int:
    if not header:
        raise PreconditionRequiredError()
    match = IF_MATCH_RE.fullmatch(header.strip())
    if match is None:
        raise InvalidRequestError("if_match_invalid", "If-Match 必须是当前 revision。")
    revision = int(match.group(1))
    if revision < 1:
        raise InvalidRequestError("if_match_invalid", "If-Match 必须是当前 revision。")
    return revision


def _values(payload: CommutePlanInput) -> dict:
    """Map the editable plan and place metadata to the existing column names."""
    return {
        "name": payload.name,
        "effective_from": payload.effective_from,
        "enabled": payload.enabled,
        "timezone": payload.timezone,
        "weekdays_json": payload.weekdays,
        "arrival_local_time": payload.arrival_local_time,
        "check_local_time": payload.check_local_time,
        "transport_mode": payload.transport_mode,
        "buffer_minutes": payload.buffer_minutes,
        "rain_probability_threshold": payload.rain_probability_threshold,
        "rain_extra_buffer_minutes": payload.rain_extra_buffer_minutes,
        "notify_enabled": payload.notify_enabled,
        "origin_label": payload.origin.label,
        "origin_latitude": payload.origin.latitude,
        "origin_longitude": payload.origin.longitude,
        "origin_city": payload.origin.city,
        "origin_coordinate_system": payload.origin.coordinate_system,
        "destination_label": payload.destination.label,
        "destination_latitude": payload.destination.latitude,
        "destination_longitude": payload.destination.longitude,
        "destination_city": payload.destination.city,
        "destination_coordinate_system": payload.destination.coordinate_system,
    }


async def route_locations(
    runtime: CommuteRuntime, origin: CommuteLocation, destination: CommuteLocation,
) -> tuple[CommuteLocation, CommuteLocation]:
    """Prepare provider coordinates while keeping the saved original points unchanged."""
    points = (origin, destination)
    if runtime.eta.provider != "amap":
        return points
    gps = [(point.latitude, point.longitude) for point in points if point.coordinate_system == "wgs84"]
    converted = iter(await runtime.eta.convert_gps(gps) if gps else [])

    def route_point(point: CommuteLocation) -> CommuteLocation:
        """Preserve the legacy GCJ-02 assumption and replace only explicit GPS points."""
        if point.coordinate_system == "wgs84":
            latitude, longitude = next(converted)
            return point.model_copy(update={"latitude": latitude, "longitude": longitude, "coordinate_system": "gcj02"})
        return point.model_copy(update={"coordinate_system": "gcj02"})

    return route_point(origin), route_point(destination)


async def list_plans(db: AsyncSession, user_id: str) -> list[CommutePlan]:
    return list(
        (
            await db.scalars(
                select(CommutePlan)
                .where(CommutePlan.user_id == user_id)
                .order_by(CommutePlan.created_at.desc())
            )
        ).all()
    )


async def get_plan(db: AsyncSession, user_id: str, plan_id: str) -> CommutePlan:
    plan = await db.scalar(
        select(CommutePlan).where(
            CommutePlan.plan_id == plan_id, CommutePlan.user_id == user_id
        )
    )
    if plan is None:
        raise NotFoundError("plan_not_found", "通勤计划不存在。")
    return plan


async def create_plan(db: AsyncSession, user_id: str, payload: CommutePlanInput, *, memory_application=None) -> CommutePlan:
    """Save a recurring configuration and its selected memory provenance in one transaction."""
    from assistant_api.models import User
    from assistant_api.services import memory_applications
    user = await db.get(User, user_id)
    if memory_application:
        await memory_applications.verify_receipt(db, user, memory_application['receipt'])
    plan = CommutePlan(user_id=user_id, revision=1, **_values(payload))
    db.add(plan)
    await db.flush()
    if memory_application:
        await memory_applications.persist(db, user, memory_application, command_key=f'plan:{plan.plan_id}:1',
            target_id=plan.plan_id, target_revision=1, actual=payload.model_dump(mode='json'))
    await db.commit()
    await db.refresh(plan)
    return plan


async def update_plan(
    db: AsyncSession,
    *,
    user_id: str,
    plan_id: str,
    payload: CommutePlanInput,
    if_match: str | None,
    memory_application=None,
) -> CommutePlan:
    """Change only the owned current revision and atomically retain any explicit memory reapplication."""
    from assistant_api.models import User
    from assistant_api.services import memory_applications
    user = await db.get(User, user_id)
    if memory_application:
        await memory_applications.verify_receipt(db, user, memory_application['receipt'])
    expected = parse_if_match(if_match)
    current = await get_plan(db, user_id, plan_id)
    if memory_application:
        from assistant_api.commute.serializers import plan_read
        previous = plan_read(current).model_dump(mode='json')
        memory_application = {**memory_application, 'previous_values': {
            field: previous.get(field) for field in memory_application['receipt']['fields']}}
    now = datetime.now(UTC)
    values = _values(payload)
    for endpoint, point in (("origin", payload.origin), ("destination", payload.destination)):
        if (point.latitude, point.longitude) == (
            getattr(current, f"{endpoint}_latitude"), getattr(current, f"{endpoint}_longitude"),
        ):
            # Older clients omit metadata when editing an unchanged point.
            for field in ("city", "coordinate_system"):
                if field not in point.model_fields_set:
                    values.pop(f"{endpoint}_{field}")
    if "effective_from" not in payload.model_fields_set:
        values.pop("effective_from", None)
    values.update({"revision": expected + 1, "updated_at": now})
    result = await db.execute(
        update(CommutePlan)
        .where(
            CommutePlan.plan_id == plan_id,
            CommutePlan.user_id == user_id,
            CommutePlan.revision == expected,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        await db.rollback()
        exists = await db.scalar(
            select(CommutePlan.plan_id).where(
                CommutePlan.plan_id == plan_id, CommutePlan.user_id == user_id
            )
        )
        if exists is None:
            raise NotFoundError("plan_not_found", "通勤计划不存在。")
        raise ConflictError(
            "revision_conflict",
            "计划已被其他设备修改，请刷新后重试。",
        )
    plan = await db.scalar(
        select(CommutePlan).where(
            CommutePlan.plan_id == plan_id, CommutePlan.user_id == user_id
        )
    )
    assert plan is not None
    await _cancel_stale_work(db, plan, now=now)
    if memory_application:
        await memory_applications.persist(db, user, memory_application, command_key=f'plan:{plan.plan_id}:{plan.revision}',
            target_id=plan.plan_id, target_revision=plan.revision, actual=payload.model_dump(mode='json'))
    await db.commit()
    await db.refresh(plan)
    return plan


async def _cancel_stale_work(db: AsyncSession, plan: CommutePlan, *, now: datetime) -> None:
    """Stop queued tasks and unsent pushes for a changed revision or disabled plan.

    Already accepted notifications are not recalled; no API can promise that.
    """

    await db.execute(
        update(CommuteCheck)
        .where(
            CommuteCheck.plan_id == plan.plan_id,
            CommuteCheck.status == "queued",
        )
        .values(
            status="cancelled",
            phase="cancelled",
            phase_updated_at=now,
            next_attempt_at=None,
            error_code="plan_revision_superseded",
            error_message="计划已修改，任务取消。",
            finished_at=now,
            lease_owner=None,
            lease_expires_at=None,
        )
    )
    await db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.plan_id == plan.plan_id,
            NotificationOutbox.status == "pending",
        )
        .values(status="cancelled", last_error_code="plan_revision_superseded")
    )
    if not plan.enabled:
        await db.execute(
            update(CommuteInstance)
            .where(
                CommuteInstance.plan_id == plan.plan_id,
                CommuteInstance.status == "active",
            )
            .values(status="closed")
        )


async def delete_plan(db: AsyncSession, *, user_id: str, plan_id: str) -> None:
    plan = await get_plan(db, user_id, plan_id)
    check_ids = list(
        (
            await db.scalars(
                select(CommuteCheck.check_id).where(CommuteCheck.plan_id == plan.plan_id)
            )
        ).all()
    )
    outbox_ids = list(
        (
            await db.scalars(
                select(NotificationOutbox.outbox_id).where(
                    NotificationOutbox.plan_id == plan.plan_id
                )
            )
        ).all()
    )
    advisory_ids = list(
        (
            await db.scalars(
                select(CommuteAdvisory.advisory_id).where(
                    CommuteAdvisory.plan_id == plan.plan_id
                )
            )
        ).all()
    )
    if outbox_ids:
        await db.execute(delete(PushAttempt).where(PushAttempt.outbox_id.in_(outbox_ids)))
        await db.execute(
            delete(NotificationOutbox).where(NotificationOutbox.outbox_id.in_(outbox_ids))
        )
    if advisory_ids:
        await db.execute(
            delete(AdvisoryAction).where(AdvisoryAction.advisory_id.in_(advisory_ids))
        )
        action_scopes = [
            f"POST /api/v1/advisories/{advisory_id}/actions"
            for advisory_id in advisory_ids
        ]
        await db.execute(
            delete(CommuteIdempotencyKey).where(
                CommuteIdempotencyKey.user_id == user_id,
                CommuteIdempotencyKey.scope.in_(action_scopes),
            )
        )
    if check_ids:
        await db.execute(
            delete(CommuteWeatherSnapshot).where(
                CommuteWeatherSnapshot.check_id.in_(check_ids)
            )
        )
        await db.execute(
            delete(CommuteEtaSnapshot).where(CommuteEtaSnapshot.check_id.in_(check_ids))
        )
    await db.execute(delete(CommuteAdvisory).where(CommuteAdvisory.plan_id == plan.plan_id))
    await db.execute(delete(CommuteCheck).where(CommuteCheck.plan_id == plan.plan_id))
    await db.execute(
        delete(CommuteInstance).where(CommuteInstance.plan_id == plan.plan_id)
    )
    await db.execute(
        delete(CommuteIdempotencyKey).where(
            CommuteIdempotencyKey.user_id == user_id,
            CommuteIdempotencyKey.scope.like(f"%{plan.plan_id}%"),
        )
    )
    await db.delete(plan)
    await db.commit()

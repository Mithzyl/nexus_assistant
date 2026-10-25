"""Manual check creation and the check execution worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute import constants
from assistant_api.commute.advice import (
    build_advisory_values,
    material_change,
    notification_body,
    validate_model_text,
)
from assistant_api.commute.providers.base import (
    AdviceText,
    ProviderError,
    ProviderNotConfigured,
    ProviderTimeout,
)
from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.commute.schemas import CheckCreateRequest, CheckCreated, RouteSnapshotInput
from assistant_api.commute.serializers import check_read
from assistant_api.commute.services.ai import analyze as analyze_commute
from assistant_api.commute.services.plans import get_plan
from assistant_api.commute.services.scheduling import ensure_instance, latest_advisory
from assistant_api.commute.timeutil import ensure_aware, local_today, target_arrival_for
from assistant_api.core.errors import AssistantError, ConflictError, NotFoundError
from assistant_api.models import (
    CommuteAdvisory,
    CommuteCheck,
    CommuteEtaSnapshot,
    CommuteIdempotencyKey,
    CommuteInstance,
    CommutePlan,
    CommuteWeatherSnapshot,
    NotificationOutbox,
)
from assistant_api.models.base import new_id

CHECK_SCOPE = "POST /api/v1/commute-plans/{plan_id}/checks"

def _validate_route_snapshot(
    plan: CommutePlan, snapshot: RouteSnapshotInput, *, now: datetime
) -> None:
    """Bind client facts to this plan and reject stale or contradictory input."""

    if abs(snapshot.origin.latitude - plan.origin_latitude) > 0.01 or abs(
        snapshot.origin.longitude - plan.origin_longitude
    ) > 0.01:
        raise AssistantError("route_snapshot_origin_mismatch", "路线起点与计划不一致。", 422)
    if abs(snapshot.destination.latitude - plan.destination_latitude) > 0.01 or abs(
        snapshot.destination.longitude - plan.destination_longitude
    ) > 0.01:
        raise AssistantError("route_snapshot_destination_mismatch", "路线终点与计划不一致。", 422)
    if snapshot.transport_mode != plan.transport_mode:
        raise AssistantError("route_snapshot_mode_mismatch", "路线方式与计划不一致。", 422)
    queried = ensure_aware(snapshot.queried_at)
    if queried > now + timedelta(seconds=120):
        raise AssistantError("route_snapshot_clock_skew", "路线查询时间超出允许范围。", 422)
    if queried < now - timedelta(minutes=15):
        raise AssistantError("route_snapshot_stale", "路线快照已过期，请重新查询。", 422)
    requested = snapshot.requested_departure_at or snapshot.requested_arrival_at
    assert requested is not None
    if ensure_aware(requested) < now - timedelta(days=1):
        raise AssistantError("route_snapshot_window_invalid", "路线快照时间不在当前通勤窗口。", 422)
    if snapshot.query_type == "arrival_deadline" and snapshot.requested_departure_at is not None:
        raise AssistantError("route_snapshot_query_invalid", "到达查询不能同时给出出发时间。", 422)
    if snapshot.query_type == "departure_time" and snapshot.requested_arrival_at is not None:
        raise AssistantError("route_snapshot_query_invalid", "出发查询不能同时给出到达时间。", 422)



def _ai_analysis_for_check(check: CommuteCheck, snapshot: dict | None) -> dict:
    """AI is not silently replaced by the template; expose why it did not run."""
    if snapshot is None:
        return {"status": "unavailable", "duration_source": "none",
                "reason": "route_provider_unconfigured"}
    duration = snapshot.get("duration_in_traffic_seconds") or snapshot.get("duration_seconds")
    source = "duration_in_traffic_seconds" if snapshot.get("duration_in_traffic_seconds") is not None else (
        "duration_seconds" if snapshot.get("duration_seconds") is not None else "none")
    return {"status": "unavailable", "used_duration_seconds": duration,
            "duration_source": source, "reason": "ai_analysis_unavailable"}


def canonical_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


async def create_manual_check(
    db: AsyncSession,
    runtime: CommuteRuntime,
    *,
    user_id: str,
    plan_id: str,
    idempotency_key: str,
    request: CheckCreateRequest | None = None,
) -> tuple[CheckCreated, bool]:
    """Queue a user refresh; replay is user+plan scoped and never consumes quota."""

    scope = CHECK_SCOPE.format(plan_id=plan_id)
    request_payload = request.model_dump(mode="json") if request is not None else {}
    request_hash = canonical_hash(request_payload)
    existing_key = await db.scalar(
        select(CommuteIdempotencyKey).where(
            CommuteIdempotencyKey.user_id == user_id,
            CommuteIdempotencyKey.scope == scope,
            CommuteIdempotencyKey.key == idempotency_key,
        )
    )
    if existing_key is not None:
        if existing_key.request_hash != request_hash:
            raise ConflictError(
                "idempotency_key_reused", "同一 Idempotency-Key 不能用于不同请求。"
            )
        check = await db.scalar(
            select(CommuteCheck).where(
                CommuteCheck.check_id == existing_key.resource_id,
                CommuteCheck.user_id == user_id,
            )
        )
        status = check.status if check is not None else existing_key.response_json["status"]
        return CheckCreated(check_id=existing_key.resource_id, status=status), True

    plan = await get_plan(db, user_id, plan_id)
    if request is not None and request.plan_revision != plan.revision:
        raise ConflictError("plan_revision_conflict", "计划已更新，请刷新后重新查询。")
    if not plan.enabled:
        raise ConflictError("plan_disabled", "计划已关闭，请先启用后再刷新。")
    now = ensure_aware(runtime.now())
    route_snapshot = request.route_snapshot if request is not None else None
    route_snapshot_json = (
        route_snapshot.model_dump(mode="json") if route_snapshot is not None else None
    )
    if route_snapshot is not None:
        _validate_route_snapshot(plan, route_snapshot, now=now)
    day = local_today(now, plan.timezone)
    target = target_arrival_for(day, plan.arrival_local_time, plan.timezone)
    if now >= target:
        raise ConflictError(
            "check_window_closed", "今天的目标到达时间已过，不再生成通勤建议。"
        )
    instance = await ensure_instance(db, plan, now=now, allow_non_scheduled_day=True)
    if instance is None:
        raise ConflictError("check_window_closed", "今天没有可执行的通勤窗口。")
    if instance.status == "skipped":
        raise ConflictError("skipped_today", "今天已跳过该计划。")

    check = CommuteCheck(
        check_id=new_id(),
        user_id=user_id,
        plan_id=plan.plan_id,
        instance_id=instance.instance_id,
        plan_revision=plan.revision,
        trigger="manual",
        analysis_mode="full",
        phase="queued",
        phase_updated_at=now,
        status="queued",
        next_attempt_at=None,
        route_snapshot_json=route_snapshot_json,
        route_snapshot_hash=(
            canonical_hash(route_snapshot_json) if route_snapshot_json is not None else None
        ),
        created_at=now,
        updated_at=now,
    )
    response = {"check_id": check.check_id, "status": "queued"}
    db.add(check)
    db.add(
        CommuteIdempotencyKey(
            user_id=user_id,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            resource_id=check.check_id,
            response_json=response,
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing_key = await db.scalar(
            select(CommuteIdempotencyKey).where(
                CommuteIdempotencyKey.user_id == user_id,
                CommuteIdempotencyKey.scope == scope,
                CommuteIdempotencyKey.key == idempotency_key,
            )
        )
        if existing_key is None:
            raise
        if existing_key.request_hash != request_hash:
            raise ConflictError(
                "idempotency_key_reused", "同一 Idempotency-Key 不能用于不同请求。"
            )
        check = await db.scalar(
            select(CommuteCheck).where(CommuteCheck.check_id == existing_key.resource_id)
        )
        status = check.status if check is not None else existing_key.response_json["status"]
        return CheckCreated(check_id=existing_key.resource_id, status=status), True
    return CheckCreated(check_id=check.check_id, status="queued"), False


async def get_check(db: AsyncSession, *, user_id: str, check_id: str) -> CommuteCheck:
    check = await db.scalar(
        select(CommuteCheck).where(
            CommuteCheck.check_id == check_id, CommuteCheck.user_id == user_id
        )
    )
    if check is None:
        raise NotFoundError("check_not_found", "检查任务不存在。")
    return check


async def claim_check(
    db: AsyncSession, *, worker_id: str, now: datetime, lease_seconds: int
) -> CommuteCheck | None:
    """Atomically claim one runnable check, including an expired lease."""

    candidates = list(
        (
            await db.scalars(
                select(CommuteCheck.check_id)
                .where(
                    (
                        (CommuteCheck.status == "queued")
                        & (
                            (CommuteCheck.next_attempt_at.is_(None))
                            | (CommuteCheck.next_attempt_at <= now)
                        )
                    )
                    | (
                        (CommuteCheck.status == "running")
                        & (CommuteCheck.lease_expires_at.is_not(None))
                        & (CommuteCheck.lease_expires_at < now)
                    )
                )
                .order_by(CommuteCheck.created_at)
                .limit(10)
            )
        ).all()
    )
    for check_id in candidates:
        result = await db.execute(
            update(CommuteCheck)
            .where(
                CommuteCheck.check_id == check_id,
                (
                    (CommuteCheck.status == "queued")
                    | (
                        (CommuteCheck.status == "running")
                        & (CommuteCheck.lease_expires_at < now)
                    )
                ),
            )
            .values(
                status="running",
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                attempt_count=CommuteCheck.attempt_count + 1,
            )
        )
        if result.rowcount == 1:
            await db.commit()
            return await db.scalar(
                select(CommuteCheck).where(CommuteCheck.check_id == check_id)
            )
    await db.rollback()
    return None


def backoff_seconds(
    attempt: int,
    *,
    base: float,
    maximum: float,
    retry_after: float | None = None,
) -> float:
    """Exponential backoff with jitter; Retry-After is a lower bound."""

    delay = min(maximum, base * (2 ** max(0, attempt - 1)))
    delay = delay * random.uniform(0.6, 1.4)
    if retry_after is not None:
        delay = max(delay, retry_after)
    return min(maximum, max(0.0, delay))


async def _call_eta(runtime: CommuteRuntime, plan: CommutePlan, *, request_depart_at, target):
    if not getattr(runtime.eta, "available", True):
        return None, ProviderNotConfigured("route provider is not configured", code="route_provider_unconfigured")
    try:
        return await asyncio.wait_for(
            runtime.eta.fetch(
                origin=(plan.origin_latitude, plan.origin_longitude),
                destination=(plan.destination_latitude, plan.destination_longitude),
                mode=plan.transport_mode,  # type: ignore[arg-type]
                request_depart_at=request_depart_at,
                target_arrival_at=target,
            ),
            timeout=runtime.settings.commute_http_timeout_seconds,
        ), None
    except TimeoutError as exc:
        return None, ProviderTimeout("route provider timed out", code="provider_timeout")
    except ProviderError as exc:
        return None, exc


async def _call_weather(
    runtime: CommuteRuntime,
    plan: CommutePlan,
    *,
    window_start,
    window_end,
):
    try:
        return await asyncio.wait_for(
            runtime.weather.fetch(
                origin=(plan.origin_latitude, plan.origin_longitude),
                destination=(plan.destination_latitude, plan.destination_longitude),
                window_start=window_start,
                window_end=window_end,
                timezone=plan.timezone,
            ),
            timeout=runtime.settings.commute_http_timeout_seconds,
        ), None
    except TimeoutError as exc:
        return None, ProviderTimeout("weather provider timed out", code="provider_timeout")
    except ProviderError as exc:
        return None, exc


def _planned_basis(
    *, now: datetime, target: datetime, plan: CommutePlan, previous: CommuteAdvisory | None
) -> tuple[datetime, str]:
    if previous is not None and previous.recommended_departure_at is not None:
        candidate = ensure_aware(previous.recommended_departure_at)
        if candidate > now:
            return candidate, "previous_advisory"
    basis = target - timedelta(minutes=plan.buffer_minutes)
    if basis > now:
        return basis, "target_arrival_minus_buffer"
    return now, "now_after_planned_basis"


async def _retry_or_finish(
    db: AsyncSession,
    check: CommuteCheck,
    runtime: CommuteRuntime,
    *,
    worker_id: str,
    now: datetime,
    target: datetime,
    error: ProviderError,
) -> None:
    delay = backoff_seconds(
        check.attempt_count,
        base=runtime.settings.commute_provider_backoff_base_seconds,
        maximum=runtime.settings.commute_provider_backoff_max_seconds,
        retry_after=error.retry_after_seconds,
    )
    if (
        error.retryable
        and check.attempt_count < runtime.settings.commute_provider_max_attempts
        and now + timedelta(seconds=delay) < target
    ):
        values = {
            "status": "queued",
            "phase": "retry_wait",
            "phase_updated_at": now,
            "next_attempt_at": now + timedelta(seconds=delay),
            "lease_owner": None,
            "lease_expires_at": None,
            "error_code": error.code,
            "error_message": "供应商暂时不可用，已安排重试。",
            "updated_at": now,
        }
    else:
        values = {
            "status": "failed",
            "phase": "failed",
            "phase_updated_at": now,
            "next_attempt_at": None,
            "advisory_id": None,
            "finished_at": now,
            "lease_owner": None,
            "lease_expires_at": None,
            "error_code": error.code,
            "error_message": "供应商不可用或重试已用尽。",
            "updated_at": now,
        }
    saved = await db.execute(
        update(CommuteCheck)
        .where(
            CommuteCheck.check_id == check.check_id,
            CommuteCheck.status == "running",
            CommuteCheck.lease_owner == worker_id,
            CommuteCheck.lease_expires_at.is_not(None),
            CommuteCheck.lease_expires_at > now,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if saved.rowcount != 1:
        await db.rollback()
        await db.refresh(check)
        return
    await db.commit()
    await db.refresh(check)


async def _set_phase(
    db: AsyncSession, runtime: CommuteRuntime, *, check: CommuteCheck,
    worker_id: str, phase: str,
) -> bool:
    """Persist a real step, renew the lease, and prove ownership atomically."""

    now = ensure_aware(runtime.now())
    result = await db.execute(
        update(CommuteCheck)
        .where(
            CommuteCheck.check_id == check.check_id,
            CommuteCheck.status == "running",
            CommuteCheck.lease_owner == worker_id,
        )
        .values(
            phase=phase,
            phase_updated_at=now,
            lease_expires_at=now + timedelta(seconds=runtime.settings.commute_job_lease_seconds),
        )
    )
    if result.rowcount != 1:
        await db.rollback()
        await db.refresh(check)
        return False
    await db.commit()
    await db.refresh(check)
    return True


async def _execute_weather_only(
    db: AsyncSession, runtime: CommuteRuntime, *, check: CommuteCheck,
    plan: CommutePlan, target: datetime, worker_id: str,
) -> CommuteCheck:
    """Refresh weather only; never create an advisory or touch the latest one."""

    now = ensure_aware(runtime.now())
    if not await _set_phase(db, runtime, check=check, worker_id=worker_id, phase="fetching_weather"):
        return check
    result, error = await _call_weather(runtime, plan, window_start=now, window_end=target)
    if error is not None and error.retryable:
        await _retry_or_finish(
            db, check, runtime, worker_id=worker_id, now=now, target=target, error=error
        )
        return check
    if not await _set_phase(db, runtime, check=check, worker_id=worker_id, phase="saving"):
        return check
    next_status = "succeeded"
    next_error_code: str | None = None
    next_error_message: str | None = None
    if result is None:
        next_status = "failed"
        next_error_code = error.code if error else "weather_unavailable"
        next_error_message = "天气暂不可用。"
    else:
        from assistant_api.commute.advice import evaluate_weather
        weather = evaluate_weather(
            result, window_start=now, window_end=target, now=now, plan=plan,
            freshness_seconds=runtime.settings.commute_weather_freshness_seconds,
        )
        db.add(CommuteWeatherSnapshot(
            user_id=check.user_id, check_id=check.check_id, provider=weather.provider,
            fetched_at=weather.fetched_at, valid_from=weather.valid_from,
            valid_to=weather.valid_to, quality=weather.quality,
            freshness="fresh" if weather.available else "unavailable",
            summary_json=weather.summary or {},
        ))
        if not weather.available:
            next_status = "failed"
            next_error_code = "weather_unavailable"
            next_error_message = "天气暂不可用。"
    saved = await db.execute(
        update(CommuteCheck)
        .where(
            CommuteCheck.check_id == check.check_id,
            CommuteCheck.status == "running",
            CommuteCheck.lease_owner == worker_id,
            CommuteCheck.lease_expires_at.is_not(None),
            CommuteCheck.lease_expires_at > now,
        )
        .values(
            status=next_status,
            phase=("failed" if next_status == "failed" else "done"),
            phase_updated_at=now,
            error_code=next_error_code,
            error_message=next_error_message,
            finished_at=now,
            next_attempt_at=None,
            advisory_id=None,
            advisory_created=False,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if saved.rowcount != 1:
        await db.rollback()
        return await db.scalar(
            select(CommuteCheck).where(CommuteCheck.check_id == check.check_id)
            .execution_options(populate_existing=True)
        )
    await db.commit()
    await db.refresh(check)
    return check


async def execute_check(
    db: AsyncSession,
    runtime: CommuteRuntime,
    *,
    check_id: str,
    worker_id: str,
) -> CommuteCheck | None:
    """Execute one claimed check; revalidate plan state before every durable fact."""

    check = await db.scalar(select(CommuteCheck).where(CommuteCheck.check_id == check_id))
    if check is None or check.status != "running" or check.lease_owner != worker_id:
        return check
    now = ensure_aware(runtime.now())
    plan = await db.scalar(select(CommutePlan).where(CommutePlan.plan_id == check.plan_id))
    instance = await db.scalar(
        select(CommuteInstance).where(CommuteInstance.instance_id == check.instance_id)
    )

    async def cancel(code: str, message: str) -> CommuteCheck:
        cancelled = await db.execute(
            update(CommuteCheck)
            .where(
                CommuteCheck.check_id == check_id,
                CommuteCheck.status == "running",
                CommuteCheck.lease_owner == worker_id,
            )
            .values(
                status="cancelled",
                phase="cancelled",
                phase_updated_at=now,
                error_code=code,
                error_message=message,
                finished_at=now,
                next_attempt_at=None,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if cancelled.rowcount != 1:
            await db.rollback()
            return await db.scalar(
                select(CommuteCheck)
                .where(CommuteCheck.check_id == check_id)
                .execution_options(populate_existing=True)
            )
        await db.commit()
        await db.refresh(check)
        return check

    if plan is None:
        return await cancel("plan_deleted", "计划已删除，任务取消。")
    if plan.revision != check.plan_revision:
        return await cancel("plan_revision_superseded", "计划已修改，任务取消。")
    if not plan.enabled:
        return await cancel("plan_disabled", "计划已关闭，任务取消。")
    if instance is None:
        return await cancel("instance_missing", "当天执行实例已不存在。")
    if instance.status == "skipped":
        return await cancel("skipped_today", "今天已跳过该计划。")
    target = ensure_aware(instance.target_arrival_at)
    if now >= target:
        instance.status = "expired"
        return await cancel("target_arrival_passed", "目标到达时间已过，任务取消。")
    if instance.status != "active":
        return await cancel("instance_closed", "当天的检查窗口已结束。")

    if getattr(check, "analysis_mode", None) == "weather_only":
        return await _execute_weather_only(
            db, runtime, check=check, plan=plan, target=target, worker_id=worker_id,
        )

    previous = await latest_advisory(
        db,
        user_id=check.user_id,
        plan_id=check.plan_id,
        local_service_date=instance.local_service_date,
    )
    if check.trigger == "auto" and previous is not None and previous.recommended_departure_at is not None:
        departure = ensure_aware(previous.recommended_departure_at)
        if now >= departure:
            instance.status = "closed"
            return await cancel("departure_passed", "建议出发时间已过，自动检查停止。")
    if not await _set_phase(db, runtime, check=check, worker_id=worker_id, phase="fetching_route"):
        return check
    request_depart, basis = _planned_basis(
        now=now, target=target, plan=plan, previous=previous
    )

    eta_result, eta_error = await _call_eta(
        runtime, plan, request_depart_at=request_depart, target=target
    )
    if eta_error is not None and eta_error.retryable:
        await _retry_or_finish(
            db, check, runtime, worker_id=worker_id, now=now, target=target, error=eta_error
        )
        return check

    eta_seconds = eta_result.duration_seconds if eta_result else None
    if eta_seconds:
        provisional = (
            target
            - timedelta(seconds=eta_seconds)
            - timedelta(minutes=plan.buffer_minutes)
        )
        window_start = provisional - timedelta(
            minutes=plan.rain_extra_buffer_minutes
        )
    else:
        window_start = now
    window_end = target
    if not await _set_phase(db, runtime, check=check, worker_id=worker_id, phase="fetching_weather"):
        return check
    weather_result, weather_error = await _call_weather(
        runtime, plan, window_start=window_start, window_end=window_end
    )
    if weather_error is not None and weather_error.retryable:
        await _retry_or_finish(
            db, check, runtime, worker_id=worker_id, now=now, target=target, error=weather_error
        )
        return check

    from assistant_api.commute.advice import evaluate_eta, evaluate_weather

    eta = evaluate_eta(
        eta_result,
        now=now,
        freshness_seconds=runtime.settings.commute_eta_freshness_seconds,
    )
    weather = evaluate_weather(
        weather_result,
        window_start=window_start,
        window_end=window_end,
        now=now,
        plan=plan,
        freshness_seconds=runtime.settings.commute_weather_freshness_seconds,
    )

    # Persist provider summaries even when the advisory itself is unchanged.
    weather_snapshot = CommuteWeatherSnapshot(
        user_id=check.user_id,
        check_id=check.check_id,
        provider=weather.provider,
        fetched_at=weather.fetched_at,
        valid_from=weather.valid_from,
        valid_to=weather.valid_to,
        quality=weather.quality,
        freshness=(
            "stale"
            if "WEATHER_STALE" in weather.reason_codes
            else ("fresh" if weather.available else "unavailable")
        ),
        summary_json=weather.summary or {},
    )
    eta_snapshot = CommuteEtaSnapshot(
        user_id=check.user_id,
        check_id=check.check_id,
        provider=eta.provider,
        transport_mode=plan.transport_mode,
        fetched_at=eta.fetched_at,
        request_depart_at=ensure_aware(request_depart),
        target_arrival_at=target,
        duration_seconds=eta.duration_seconds,
        distance_meters=eta.distance_meters,
        freshness=eta.freshness,
        response_basis=basis,
        summary_json={
            "request_basis": basis,
            "response_basis": eta.response_basis,
            "duration_seconds": eta.duration_seconds,
        },
    )
    db.add_all([weather_snapshot, eta_snapshot])
    await db.flush()

    route_source = "none"
    if check.route_snapshot_json is not None:
        route_source = check.route_snapshot_json.get("source", "client_snapshot")
        if route_source not in {"client_snapshot", "server_provider"}:
            route_source = "client_snapshot"
    elif eta_result is not None and getattr(runtime.eta, "available", True):
        route_source = "server_provider"
        server_snapshot = {
            "source": "server_provider",
            "provider": eta_result.provider,
            "origin": {"latitude": plan.origin_latitude, "longitude": plan.origin_longitude},
            "destination": {"latitude": plan.destination_latitude, "longitude": plan.destination_longitude},
            "transport_mode": plan.transport_mode,
            "query_type": "departure_time",
            "requested_departure_at": ensure_aware(request_depart).isoformat(),
            "requested_arrival_at": None,
            "expected_departure_at": ensure_aware(request_depart).isoformat(),
            "expected_arrival_at": ensure_aware(target).isoformat(),
            "queried_at": ensure_aware(eta_result.fetched_at).isoformat(),
            "distance_meters": eta_result.distance_meters,
            "duration_seconds": eta_result.duration_seconds,
            "duration_in_traffic_seconds": None,
            "eta_source": "route",
            "eta_scope": "overall",
            "route_reference": None,
            "candidate_routes": [],
            "source_note": "server-side route provider; not Apple-signed",
        }
        check.route_snapshot_json = server_snapshot
        check.route_snapshot_hash = canonical_hash(server_snapshot)

    client_snapshot_stale = False
    if route_source == "client_snapshot" and check.route_snapshot_json:
        try:
            queried_at = ensure_aware(
                datetime.fromisoformat(str(check.route_snapshot_json["queried_at"]).replace("Z", "+00:00"))
            )
            client_snapshot_stale = queried_at < now - timedelta(minutes=15)
        except (KeyError, TypeError, ValueError):
            client_snapshot_stale = True

    values = build_advisory_values(
        plan=plan,
        instance=instance,
        now=now,
        eta=eta,
        weather=weather,
        previous=previous,
    )
    should_create = check.trigger == "manual" or previous is None or material_change(
        previous, values
    )
    if not await _set_phase(db, runtime, check=check, worker_id=worker_id, phase="analyzing"):
        return check
    ai_result: dict | None = None
    no_route_reason = (
        "route_provider_unconfigured"
        if not getattr(runtime.eta, "available", True)
        else "route_provider_unavailable"
    )
    if route_source != "none" and not client_snapshot_stale:
        values["quality"] = "fresh" if weather.quality == "fresh" else "degraded"
    elif client_snapshot_stale:
        values["quality"] = "degraded"
    if route_source != "none":
        code = "ROUTE_CLIENT_SNAPSHOT" if route_source == "client_snapshot" else "ROUTE_SERVER_PROVIDER"
        if not any(x.get("code") == code for x in values["reason_codes"]):
            values["reason_codes"].append({"code": code})
    if client_snapshot_stale and not any(x.get("code") == "ROUTE_SNAPSHOT_STALE" for x in values["reason_codes"]):
        values["reason_codes"].append({"code": "ROUTE_SNAPSHOT_STALE"})
    if should_create and client_snapshot_stale:
        ai_result = {"status": "unavailable", "route_source": "client_snapshot",
                     "duration_source": "none", "reason": "route_snapshot_stale"}
    elif should_create:
        ai_result = await analyze_commute(
            db, runtime, check=check, plan=plan,
            snapshot=check.route_snapshot_json, weather=values["weather"],
            target=values["target_arrival_at"], route_source=route_source,
            no_route_reason=no_route_reason,
        )
        if ai_result.get("status") == "available":
            used = int(ai_result["used_duration_seconds"])
            extra = int(ai_result["extra_buffer_seconds"])
            departure = ensure_aware(
                datetime.fromisoformat(ai_result["recommended_departure_at"].replace("Z", "+00:00"))
            )
            values["eta_seconds"] = used
            # `extra` is the AI's total buffer. The advisory schema keeps the
            # fixed plan buffer separate from the weather-specific portion.
            fixed_buffer = plan.buffer_minutes * 60
            values["weather_extra_buffer_seconds"] = max(0, extra - fixed_buffer)
            values["recommended_departure_at"] = departure
            values["departure_status"] = (
                "upcoming" if departure > now else
                "leave_now" if departure >= now - timedelta(minutes=5) else "past_due"
            )
            if not any(x.get("code") == "AI_ANALYSIS_AVAILABLE" for x in values["reason_codes"]):
                values["reason_codes"].append({"code": "AI_ANALYSIS_AVAILABLE"})
            # A late old-plan workflow cannot overwrite the new revision.
            await db.refresh(plan)
            if plan.revision != check.plan_revision:
                return await cancel("plan_revision_superseded", "计划已修改，旧分析结果丢弃。")
        if ai_result.get("status") == "available":
            should_create = True
    if not await _set_phase(db, runtime, check=check, worker_id=worker_id, phase="saving"):
        return check
    created_advisory: CommuteAdvisory | None = None
    if should_create:
        title = values["template"].title
        summary = values["template"].summary
        explanation_source = "template"
        candidate: AdviceText | None = None
        try:
            candidate = await asyncio.wait_for(
                runtime.advice_text.generate(values["facts"]),
                timeout=runtime.settings.commute_advice_model_timeout_seconds,
            )
        except (TimeoutError, ProviderError, Exception):  # noqa: BLE001 - fallback by design
            candidate = None
        validated = validate_model_text(candidate, values["template"])
        if validated is not None:
            title, summary, explanation_source = (
                validated.title,
                validated.summary,
                "model",
            )
        if ai_result is not None and ai_result.get("status") == "available":
            title = ai_result["title"]
            summary = ai_result["summary"]
            explanation_source = "model"
            ai_result = {k: v for k, v in ai_result.items() if k not in {"title", "summary"}}
        created_advisory = CommuteAdvisory(
            advisory_id=new_id(),
            user_id=check.user_id,
            plan_id=plan.plan_id,
            check_id=check.check_id,
            plan_revision=check.plan_revision,
            local_service_date=instance.local_service_date,
            generated_at=now,
            expires_at=values["expires_at"],
            quality=values["quality"],
            title=title,
            summary=summary,
            target_arrival_at=values["target_arrival_at"],
            recommended_departure_at=values["recommended_departure_at"],
            departure_status=values["departure_status"],
            eta_seconds=values["eta_seconds"],
            buffer_seconds=values["buffer_seconds"],
            weather_extra_buffer_seconds=values["weather_extra_buffer_seconds"],
            weather_json=values["weather"],
            sources_json=values["sources"],
            reason_codes_json=values["reason_codes"],
            explanation_source=explanation_source,
            ai_analysis_json=ai_result if ai_result is not None else _ai_analysis_for_check(check, check.route_snapshot_json),
        )
        db.add(created_advisory)
        await db.flush()
        instance.last_advisory_id = created_advisory.advisory_id
    next_advisory_id = (
        created_advisory.advisory_id
        if created_advisory is not None
        else (previous.advisory_id if previous is not None else None)
    )
    advisory_created = created_advisory is not None

    next_status = "succeeded"
    next_error_code: str | None = None
    next_error_message: str | None = None
    if values["quality"] == "unavailable":
        next_status = "failed"
        next_error_code = "providers_unavailable"
        next_error_message = "天气和路线数据均不可用。"
    elif values["quality"] == "degraded":
        next_status = "degraded"
    if client_snapshot_stale:
        next_status = "failed"
        next_error_code = "route_snapshot_stale"
        next_error_message = "路线快照已过期，请重新查询。"

    # Last state check before saving an advisory and before creating an outbox.
    await db.refresh(plan)
    await db.refresh(instance)
    if (
        plan.revision != check.plan_revision
        or not plan.enabled
        or instance.status != "active"
        or ensure_aware(instance.target_arrival_at) <= now
    ):
        await db.rollback()
        # Discard this executor's unsaved work, but only cancel the row while
        # this worker still owns it. A later worker's progress is never touched.
        await db.execute(
            update(CommuteCheck)
            .where(
                CommuteCheck.check_id == check_id,
                CommuteCheck.status == "running",
                CommuteCheck.lease_owner == worker_id,
            )
            .values(
                status="cancelled",
                phase="cancelled",
                phase_updated_at=now,
                error_code="plan_state_changed",
                error_message="保存建议前计划状态已变化，任务取消。",
                finished_at=now,
                next_attempt_at=None,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        return await db.scalar(select(CommuteCheck).where(CommuteCheck.check_id == check_id))

    if check.trigger == "auto" and plan.notify_enabled:
        notification_advisory = created_advisory
        notification_values = values
        if (
            notification_advisory is None
            and instance.auto_reminder_count == 0
            and previous is not None
            and values["quality"] != "unavailable"
        ):
            # A manual refresh before the first automatic check must not consume
            # the automatic first reminder. Notify against the latest advisory
            # even though this check produced no new advisory row. Only the first
            # automatic slot does this, so it cannot become a duplicate stream.
            notification_advisory = previous
            notification_values = {
                "title": previous.title,
                "summary": previous.summary,
                "departure_status": previous.departure_status,
                "quality": previous.quality,
                "recommended_departure_at": previous.recommended_departure_at,
            }
        if notification_advisory is not None:
            await _maybe_enqueue_outbox(
                db,
                runtime,
                plan=plan,
                instance=instance,
                advisory=notification_advisory,
                values=notification_values,
                now=now,
            )

    # Every durable result is guarded by one conditional UPDATE: the advisory,
    # snapshots, outbox and terminal status become visible only if this worker
    # still holds an unexpired lease. A stale executor cannot overwrite the
    # successor's row, and its transaction is rolled back wholesale.
    saved = await db.execute(
        update(CommuteCheck)
        .where(
            CommuteCheck.check_id == check_id,
            CommuteCheck.status == "running",
            CommuteCheck.lease_owner == worker_id,
            CommuteCheck.lease_expires_at.is_not(None),
            CommuteCheck.lease_expires_at > now,
        )
        .values(
            status=next_status,
            phase=("failed" if next_status == "failed" else "done"),
            phase_updated_at=now,
            error_code=next_error_code,
            error_message=next_error_message,
            finished_at=now,
            next_attempt_at=None,
            advisory_id=next_advisory_id,
            advisory_created=advisory_created,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if saved.rowcount != 1:
        await db.rollback()
        return await db.scalar(
            select(CommuteCheck)
            .where(CommuteCheck.check_id == check_id)
            .execution_options(populate_existing=True)
        )
    await db.commit()
    await db.refresh(check)
    return check


async def _maybe_enqueue_outbox(
    db: AsyncSession,
    runtime: CommuteRuntime,
    *,
    plan: CommutePlan,
    instance: CommuteInstance,
    advisory: CommuteAdvisory,
    values: dict,
    now: datetime,
) -> None:
    max_reminders = runtime.settings.commute_max_auto_reminders_per_day
    if instance.auto_reminder_count >= max_reminders:
        return
    if values["quality"] == "unavailable" and instance.auto_reminder_count >= 1:
        # At most one short "data unavailable" notice per plan/day, and it counts
        # toward the same logical reminder cap as normal advisories.
        return
    if instance.last_auto_reminder_at is not None:
        last = ensure_aware(instance.last_auto_reminder_at)
        if now - last < timedelta(
            minutes=runtime.settings.commute_min_reminder_gap_minutes
        ):
            return
    sequence = instance.auto_reminder_count + 1
    title, body = notification_body(values)
    payload = {
        "aps": {
            "alert": {"title": title, "body": body},
            "sound": "default",
            "thread-id": f"commute-{plan.plan_id}",
        },
        "type": "commute_advisory",
        "advisory_id": advisory.advisory_id,
        "plan_id": plan.plan_id,
        "local_service_date": instance.local_service_date.isoformat(),
        "schema_version": constants.ADVISORY_SCHEMA_VERSION,
    }
    collapse_id = (
        "commute-"
        + hashlib.sha256(
            f"{plan.plan_id}:{instance.local_service_date.isoformat()}".encode()
        ).hexdigest()[:40]
    )
    outbox = NotificationOutbox(
        outbox_id=str(uuid.uuid4()),
        user_id=plan.user_id,
        plan_id=plan.plan_id,
        instance_id=instance.instance_id,
        advisory_id=advisory.advisory_id,
        local_service_date=instance.local_service_date,
        reminder_sequence=sequence,
        status="pending",
        payload_json=payload,
        collapse_id=collapse_id,
        next_attempt_at=now,
        expires_at=ensure_aware(instance.target_arrival_at),
    )
    try:
        async with db.begin_nested():
            db.add(outbox)
            await db.flush()
    except IntegrityError:
        return
    instance.auto_reminder_count = sequence
    instance.last_auto_reminder_at = now

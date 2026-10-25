"""Advisory actions: open audit and skip-today cancellation."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute.schemas import AdvisoryActionInput
from assistant_api.commute.services.scheduling import cancel_pending_checks
from assistant_api.commute.timeutil import ensure_aware
from assistant_api.core.errors import ConflictError, ForbiddenError, NotFoundError
from assistant_api.models import (
    AdvisoryAction,
    CommuteAdvisory,
    CommuteIdempotencyKey,
    CommuteInstance,
    PushDevice,
)


def action_hash(payload: AdvisoryActionInput) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "action": payload.action,
                "installation_id": payload.installation_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def record_action(
    db: AsyncSession,
    *,
    user_id: str,
    advisory_id: str,
    payload: AdvisoryActionInput,
    idempotency_key: str,
    now: datetime,
) -> tuple[dict, bool]:
    scope = f"POST /api/v1/advisories/{advisory_id}/actions"
    request_hash = action_hash(payload)
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
        return existing_key.response_json, True

    advisory = await db.scalar(
        select(CommuteAdvisory).where(
            CommuteAdvisory.advisory_id == advisory_id,
            CommuteAdvisory.user_id == user_id,
        )
    )
    if advisory is None:
        raise NotFoundError("advisory_not_found", "通勤建议不存在。")
    device = await db.scalar(
        select(PushDevice).where(
            PushDevice.user_id == user_id,
            PushDevice.installation_id == payload.installation_id,
        )
    )
    if device is None:
        # opened/skip_today are user interactions, not APNs sends. They must work
        # even when notification permission (and therefore token registration)
        # was denied. An installation bound to another account is still refused.
        other_owner = await db.scalar(
            select(PushDevice.device_id).where(
                PushDevice.installation_id == payload.installation_id,
                PushDevice.user_id != user_id,
            )
        )
        if other_owner is not None:
            raise ForbiddenError(
                "device_not_bound", "该安装实例不属于当前账号。"
            )

    existing_action = await db.scalar(
        select(AdvisoryAction).where(
            AdvisoryAction.advisory_id == advisory_id,
            AdvisoryAction.action == payload.action,
            AdvisoryAction.installation_id == payload.installation_id,
        )
    )
    if existing_action is not None:
        response = {
            "advisory_id": advisory_id,
            "action": payload.action,
            "recorded_at": ensure_aware(existing_action.created_at)
            .astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "replayed": True,
        }
        # Still bind this Idempotency-Key to the replayed action, so reusing the
        # key with a different body later is a conflict rather than a surprise.
        db.add(
            CommuteIdempotencyKey(
                user_id=user_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                resource_id=existing_action.action_id,
                response_json=response,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            stored = await db.scalar(
                select(CommuteIdempotencyKey).where(
                    CommuteIdempotencyKey.user_id == user_id,
                    CommuteIdempotencyKey.scope == scope,
                    CommuteIdempotencyKey.key == idempotency_key,
                )
            )
            if stored is not None and stored.request_hash != request_hash:
                raise ConflictError(
                    "idempotency_key_reused", "同一 Idempotency-Key 不能用于不同请求。"
                )
        return response, True

    instance = await db.scalar(
        select(CommuteInstance).where(
            CommuteInstance.plan_id == advisory.plan_id,
            CommuteInstance.local_service_date == advisory.local_service_date,
        )
    )
    if payload.action == "skip_today":
        if instance is None:
            raise ConflictError("instance_not_found", "当天执行实例不存在。")
        if ensure_aware(instance.target_arrival_at) <= now:
            raise ConflictError("advisory_expired", "该建议已过目标到达时间。")
        if instance.status == "skipped":
            pass
        else:
            instance.status = "skipped"
            instance.skip_today_at = now
            await cancel_pending_checks(
                db,
                instance_id=instance.instance_id,
                code="skipped_today",
                now=now,
            )

    action = AdvisoryAction(
        user_id=user_id,
        advisory_id=advisory.advisory_id,
        plan_id=advisory.plan_id,
        instance_id=instance.instance_id if instance else None,
        installation_id=payload.installation_id,
        action=payload.action,
    )
    db.add(action)
    await db.flush()
    response = {
        "advisory_id": advisory_id,
        "action": payload.action,
        "recorded_at": ensure_aware(action.created_at)
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "replayed": False,
    }
    db.add(
        CommuteIdempotencyKey(
            user_id=user_id,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
            resource_id=action.action_id,
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
        return existing_key.response_json, True
    return response, False

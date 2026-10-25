"""Outbox delivery worker for APNs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute.providers.base import (
    ProviderAuthError,
    ProviderError,
    PushMessage,
)
from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.commute.services.bark import BarkNotifier
from assistant_api.commute.timeutil import ensure_aware
from assistant_api.models import (
    BarkReminderSetting,
    CommuteAdvisory,
    CommuteInstance,
    CommutePlan,
    NotificationOutbox,
    PushAttempt,
    PushDevice,
)
from assistant_api.models.base import utc_now

ELIGIBLE_PERMISSIONS = {"authorized", "provisional", "ephemeral"}
INVALID_TOKEN_REASONS = {"Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic"}
APNS_MAX_PAYLOAD_BYTES = 4096


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _backoff(attempt: int, base: float = 1.0, maximum: float = 30.0) -> float:
    delay = min(maximum, base * (2 ** max(0, attempt - 1)))
    return min(maximum, delay * random.uniform(0.6, 1.4))


async def claim_outbox(
    db: AsyncSession, *, worker_id: str, now: datetime, lease_seconds: int
) -> NotificationOutbox | None:
    candidates = list(
        (
            await db.scalars(
                select(NotificationOutbox.outbox_id)
                .where(
                    (NotificationOutbox.status == "pending")
                    & (NotificationOutbox.next_attempt_at <= now)
                )
                .order_by(NotificationOutbox.next_attempt_at)
                .limit(10)
            )
        ).all()
    )
    for outbox_id in candidates:
        row = await db.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.outbox_id == outbox_id
            )
        )
        if row is None:
            continue
        if ensure_aware(row.expires_at) <= now:
            row.status = "expired"
            row.last_error_code = "notification_expired"
            row.lease_owner = None
            row.lease_expires_at = None
            await db.commit()
            db.expunge(row)
            continue
        result = await db.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.outbox_id == outbox_id,
                NotificationOutbox.status == "pending",
            )
            .values(
                status="sending",
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                attempt_count=NotificationOutbox.attempt_count + 1,
            )
        )
        if result.rowcount == 1:
            await db.commit()
            return await db.scalar(
                select(NotificationOutbox).where(
                    NotificationOutbox.outbox_id == outbox_id
                )
            )
    await db.rollback()
    return None


async def expire_abandoned_deliveries(
    db: AsyncSession, *, now: datetime
) -> int:
    """Finalize a crashed in-flight send as unknown; never resend it blindly.

    The process may have died after Bark accepted the request but before the
    local commit. A new worker cannot distinguish that from a pre-send crash, so
    the only safe outcome is an explicit unknown (at-most-once locally).
    """

    result = await db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.status == "sending",
            NotificationOutbox.lease_expires_at.is_not(None),
            NotificationOutbox.lease_expires_at < now,
        )
        .values(
            status="unknown",
            last_error_code="delivery_lease_expired_unknown",
            lease_owner=None,
            lease_expires_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount or 0


def _record_bark_attempt(
    db: AsyncSession, outbox: NotificationOutbox, key: str,
    *, result: str, now: datetime, error_code: str | None = None,
) -> None:
    db.add(PushAttempt(
        outbox_id=outbox.outbox_id,
        user_id=outbox.user_id,
        device_id=None,
        token_digest=token_digest(key or "runtime-bark"),
        provider="bark",
        result=result,
        error_code=error_code,
        retryable=False,
        attempt_at=now,
    ))


async def deliver_outbox(
    db: AsyncSession, runtime: CommuteRuntime, *, outbox_id: str, worker_id: str
) -> NotificationOutbox | None:
    outbox = await db.scalar(
        select(NotificationOutbox).where(NotificationOutbox.outbox_id == outbox_id)
    )
    if outbox is None or outbox.status != "sending" or outbox.lease_owner != worker_id:
        return outbox
    now = ensure_aware(runtime.now())
    plan = await db.scalar(select(CommutePlan).where(CommutePlan.plan_id == outbox.plan_id))
    instance = await db.scalar(
        select(CommuteInstance).where(CommuteInstance.instance_id == outbox.instance_id)
    )
    advisory = await db.scalar(
        select(CommuteAdvisory).where(CommuteAdvisory.advisory_id == outbox.advisory_id)
    )

    async def finish_early(status: str, code: str) -> NotificationOutbox:
        """Persist an early terminal state only while this worker owns the claim."""

        saved = await db.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.outbox_id == outbox.outbox_id,
                NotificationOutbox.status == "sending",
                NotificationOutbox.lease_owner == worker_id,
            )
            .values(
                status=status,
                last_error_code=code,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if saved.rowcount != 1:
            await db.rollback()
            return await db.scalar(
                select(NotificationOutbox)
                .where(NotificationOutbox.outbox_id == outbox.outbox_id)
                .execution_options(populate_existing=True)
            )
        await db.commit()
        await db.refresh(outbox)
        return outbox

    if plan is None or advisory is None or instance is None:
        return await finish_early("cancelled", "plan_or_advisory_deleted")
    if plan.revision != advisory.plan_revision:
        return await finish_early("cancelled", "plan_revision_superseded")
    if not plan.enabled:
        return await finish_early("cancelled", "plan_disabled")
    if instance.status == "skipped":
        return await finish_early("cancelled", "skipped_today")
    if ensure_aware(outbox.expires_at) <= now:
        return await finish_early("expired", "notification_expired")

    raw = json.dumps(outbox.payload_json, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > APNS_MAX_PAYLOAD_BYTES:
        return await finish_early("failed", "payload_too_large")

    # This deployment has no APNs credentials. Bark is the only notification
    # channel and is bound to the outbox owner. No key, or auto reminders off,
    # means no send at all. The APNs block below is intentionally unreachable.
    row = await db.get(BarkReminderSetting, outbox.user_id)
    if row is None or not row.auto_reminders_enabled:
        return await finish_early("cancelled", "bark_not_enabled")
    bark = runtime.bark
    owns_notifier = False
    if bark is None:
        if not row.device_key:
            return await finish_early("cancelled", "bark_not_configured")
        bark = BarkNotifier(row.device_key, timeout_seconds=runtime.settings.bark_timeout_seconds)
        owns_notifier = True
    if not bark.configured:
        return await finish_early("cancelled", "bark_not_enabled")

    alert = (outbox.payload_json.get("aps") or {}).get("alert") or {}
    # End the read transaction before the network wait; SQLite must never stay
    # locked while Bark is slow or unreachable.
    await db.commit()
    delivery = None
    send_error: str | None = None
    try:
        delivery = await bark.send(
            title=str(alert.get("title") or "PA 通勤提醒"),
            body=str(alert.get("body") or "通勤建议已更新。"),
        )
    except Exception:  # noqa: BLE001 - an uncertain send must not be retried blindly
        send_error = "bark_send_unknown"
    finally:
        if owns_notifier:
            await bark.aclose()
    if delivery is not None and delivery.status == "accepted":
        next_status = "accepted_by_bark"
        last_error = None
        attempt_result = "accepted"
        attempt_error = None
        accepted_at = now
    elif delivery is None or delivery.status == "unknown":
        next_status = "unknown"
        last_error = send_error or "bark_unknown"
        attempt_result = "unknown"
        attempt_error = last_error
        accepted_at = None
    else:
        next_status = "failed"
        last_error = (delivery.error_code or "bark_failed") if delivery is not None else "bark_failed"
        attempt_result = "rejected"
        attempt_error = last_error
        accepted_at = None

    # The send already happened. Save the outcome only if this worker still
    # owns the outbox lease; otherwise a successor's row wins and this result is
    # deliberately dropped (the remote delivery may still have occurred).
    saved = await db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.outbox_id == outbox.outbox_id,
            NotificationOutbox.status == "sending",
            NotificationOutbox.lease_owner == worker_id,
            NotificationOutbox.lease_expires_at.is_not(None),
            NotificationOutbox.lease_expires_at > now,
        )
        .values(
            status=next_status,
            last_error_code=last_error,
            accepted_at=accepted_at,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if saved.rowcount != 1:
        await db.rollback()
        return await db.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.outbox_id == outbox.outbox_id
            )
        )
    _record_bark_attempt(
        db, outbox, row.device_key, result=attempt_result, now=now,
        error_code=attempt_error,
    )
    await db.commit()
    await db.refresh(outbox)
    return outbox

    devices = list(
        (
            await db.scalars(
                select(PushDevice).where(
                    PushDevice.user_id == outbox.user_id,
                    PushDevice.notification_permission.in_(ELIGIBLE_PERMISSIONS),
                )
            )
        ).all()
    )
    if not devices:
        outbox.status = "cancelled"
        outbox.last_error_code = "no_eligible_device"
        outbox.lease_owner = None
        outbox.lease_expires_at = None
        await db.commit()
        return outbox

    accepted = False
    any_unknown = False
    retryable_failure = False
    permanent_failure = False
    last_code: str | None = None
    provider_error: InvalidToken | None = None

    for device in devices:
        if device.user_id != outbox.user_id:
            # Defense in depth; the query above is already user-scoped.
            continue
        token = device.apns_token
        message = PushMessage(
            apns_id=outbox.outbox_id,
            device_token=token,
            topic=(
                runtime.settings.commute_apns_topic
                or device.topic
                or "com.example.assistant"
            ),
            environment=runtime.settings.commute_apns_environment,
            payload=outbox.payload_json,
            collapse_id=outbox.collapse_id,
            expires_at=ensure_aware(outbox.expires_at),
        )
        try:
            result = await asyncio.wait_for(
                runtime.push.send(message),
                timeout=runtime.settings.commute_apns_timeout_seconds + 2,
            )
        except ProviderAuthError as exc:
            outbox.status = "failed"
            outbox.last_error_code = exc.code
            outbox.lease_owner = None
            outbox.lease_expires_at = None
            await db.commit()
            return outbox
        except ProviderError as exc:
            last_code = exc.code
            result = None
            provider_error = InvalidToken(token, device.device_id, exc.code, None, exc.retryable)
            permanent_failure = not exc.retryable
        except TimeoutError:
            last_code = "push_timeout"
            provider_error = InvalidToken(token, device.device_id, "push_timeout", None, False)
        except Exception:  # noqa: BLE001 - one bad provider result must not crash the worker
            last_code = "push_provider_error"
            provider_error = InvalidToken(token, device.device_id, "push_provider_error", None, True)

        if result is None:
            unknown_outcome = bool(
                provider_error and provider_error.code.endswith("timeout")
            )
            if unknown_outcome:
                any_unknown = True
            attempt = PushAttempt(
                outbox_id=outbox.outbox_id,
                user_id=outbox.user_id,
                device_id=device.device_id,
                token_digest=token_digest(token),
                provider=getattr(runtime.push, "provider", runtime.provider_mode),
                result="unknown" if unknown_outcome else "rejected",
                error_code=provider_error.code if provider_error else last_code,
                retryable=bool(provider_error and provider_error.retryable),
                attempt_at=now,
            )
            db.add(attempt)
            if provider_error and provider_error.retryable:
                retryable_failure = True
            else:
                permanent_failure = True
            continue

        attempt = PushAttempt(
            outbox_id=outbox.outbox_id,
            user_id=outbox.user_id,
            device_id=device.device_id,
            token_digest=token_digest(token),
            provider=getattr(runtime.push, "provider", runtime.provider_mode),
            result=result.result,
            http_status=result.http_status,
            error_code=result.error_code,
            retryable=result.retryable,
            provider_timestamp=result.provider_timestamp,
            attempt_at=now,
        )
        db.add(attempt)
        if result.result == "accepted":
            accepted = True
        elif result.result == "unknown":
            any_unknown = True
        elif result.retryable:
            retryable_failure = True
        else:
            permanent_failure = True
        if result.error_code:
            last_code = result.error_code
        if result.result == "rejected" and result.error_code in INVALID_TOKEN_REASONS:
            await _maybe_remove_stale_token(db, device, token, result.provider_timestamp)

    if accepted:
        outbox.status = "accepted_by_apns"
        outbox.accepted_at = now
        outbox.last_error_code = None
    elif any_unknown:
        # APNs timeout has an unknown outcome. We do not retry automatically:
        # that is the bounded behavior that avoids an unbounded duplicate storm.
        outbox.status = "unknown"
        outbox.last_error_code = last_code or "apns_unknown"
    elif retryable_failure and outbox.attempt_count < runtime.settings.commute_outbox_max_attempts:
        delay = _backoff(outbox.attempt_count)
        if now + timedelta(seconds=delay) < ensure_aware(outbox.expires_at):
            outbox.status = "pending"
            outbox.next_attempt_at = now + timedelta(seconds=delay)
            outbox.last_error_code = last_code
        else:
            outbox.status = "failed"
            outbox.last_error_code = last_code or "notification_expired"
    else:
        outbox.status = "failed"
        outbox.last_error_code = last_code or "push_failed"
    outbox.lease_owner = None
    outbox.lease_expires_at = None
    await db.commit()
    return outbox


class InvalidToken:
    __slots__ = ("token", "device_id", "code", "timestamp", "retryable")

    def __init__(self, token, device_id, code, timestamp, retryable):
        self.token = token
        self.device_id = device_id
        self.code = code
        self.timestamp = timestamp
        self.retryable = retryable


async def _maybe_remove_stale_token(
    db: AsyncSession,
    device: PushDevice,
    sent_token: str,
    response_timestamp: datetime | None,
) -> None:
    """Only delete when the invalid-token response predates the newest registration.

    A late 410 for an old token must never remove a token the app has since rotated.
    """

    current = await db.scalar(
        select(PushDevice).where(PushDevice.device_id == device.device_id)
    )
    if current is None or current.apns_token != sent_token:
        return
    registered = ensure_aware(current.token_updated_at)
    if response_timestamp is not None:
        if registered > ensure_aware(response_timestamp):
            return
    else:
        # Without an APNs timestamp we cannot safely distinguish an old response
        # from a fresh one, so we keep the binding and let the next registration
        # decide. This trades a redundant send for data safety.
        return
    await db.delete(current)

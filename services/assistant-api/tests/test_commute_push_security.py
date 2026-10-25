"""Push binding, APNs result handling, idempotency and cross-user isolation."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from assistant_api.commute.providers.base import PushResult
from assistant_api.commute.services.push import _maybe_remove_stale_token
from assistant_api.models import (
    BarkReminderSetting,
    CommuteAdvisory,
    NotificationOutbox,
    PushAttempt,
    PushDevice,
)
from assistant_api.commute.timeutil import ensure_aware
from tests.commute_flow import create_plan, deliver_one_outbox, run_full_auto_cycle
from tests.test_commute_worker_flow import auth, register_device


async def full_flow(h):
    await register_device(h, h.token_a)
    plan = await create_plan(h, h.token_a)
    check, outbox = await run_full_auto_cycle(h)
    return plan, check, outbox


@pytest.mark.asyncio
async def test_multi_device_one_logical_reminder(commute_harness):
    h = commute_harness
    await register_device(h, h.token_a, installation_id="install-1")
    await register_device(h, h.token_a, installation_id="install-2")
    await create_plan(h, h.token_a)
    check, outbox = await run_full_auto_cycle(h)
    assert outbox is not None and outbox.status == "accepted_by_bark"
    assert len(h.bark.sent) == 1
    assert h.push.messages == []
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(NotificationOutbox)) == 1
        advisory = await db.scalar(select(CommuteAdvisory))
    assert advisory is not None
    # One sequence consumed, not one per device.
    assert check.trigger == "auto"


@pytest.mark.asyncio
async def test_no_bark_channel_cancels_without_calling_apns(commute_harness):
    h = commute_harness
    h.bark.configured = False
    await register_device(h, h.token_a, permission="denied")
    await create_plan(h, h.token_a)
    _, outbox = await run_full_auto_cycle(h)
    assert outbox is not None and outbox.status == "cancelled"
    assert outbox.last_error_code == "bark_not_enabled"
    assert h.push.messages == []
    assert h.bark.sent == []


@pytest.mark.asyncio
async def test_bark_unknown_is_recorded_once_without_blind_retry(commute_harness):
    h = commute_harness
    h.bark.status = "unknown"
    await register_device(h, h.token_a)
    await create_plan(h, h.token_a)
    _, outbox = await run_full_auto_cycle(h)
    assert outbox is not None and outbox.status == "unknown"
    assert outbox.last_error_code == "bark_unknown"
    assert h.push.messages == []

    # No automatic second delivery of an unknown outcome.
    from tests.commute_flow import deliver_one_outbox

    assert await deliver_one_outbox(h) is None
    assert len(h.bark.sent) == 1


@pytest.mark.asyncio
async def test_invalid_token_response_does_not_delete_rotated_token(commute_harness):
    h = commute_harness
    await register_device(h, h.token_a, installation_id="rotate-1")
    h.push.results = [
        PushResult(result="rejected", http_status=410, error_code="Unregistered")
    ]
    async with h.app.state.session_factory() as db:
        device = await db.scalar(
            select(PushDevice).where(PushDevice.installation_id == "rotate-1")
        )
        old_token = device.apns_token
        old_time = ensure_aware(device.token_updated_at)
        # A late APNs response predates the newest registration.
        await _maybe_remove_stale_token(
            db, device, old_token, old_time - timedelta(minutes=5)
        )
        await db.commit()
        assert (
            await db.scalar(
                select(PushDevice).where(PushDevice.installation_id == "rotate-1")
            )
        ) is not None

        # A response at/after registration with the still-current token removes it.
        await _maybe_remove_stale_token(db, device, old_token, old_time)
        await db.commit()
        assert (
            await db.scalar(
                select(PushDevice).where(PushDevice.installation_id == "rotate-1")
            )
        ) is None


@pytest.mark.asyncio
async def test_logout_removes_binding_and_blocks_cross_account_takeover(commute_harness):
    h = commute_harness
    await register_device(h, h.token_a, installation_id="switch-1")

    # While A's session is live, B may not claim the same install.
    conflict = await h.client.put(
        "/api/v1/push-devices/switch-1",
        json={"apns_token": "b" * 64, "notification_permission": "authorized"},
        headers=auth(h.token_b),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "installation_bound_other_user"

    logout = await h.client.post("/api/v1/auth/logout", headers=auth(h.token_a))
    assert logout.status_code == 204
    async with h.app.state.session_factory() as db:
        assert (
            await db.scalar(
                select(PushDevice).where(PushDevice.installation_id == "switch-1")
            )
        ) is None

    claimed = await h.client.put(
        "/api/v1/push-devices/switch-1",
        json={"apns_token": "b" * 64, "notification_permission": "authorized"},
        headers=auth(h.token_b),
    )
    assert claimed.status_code == 200
    assert claimed.json()["environment"] == "sandbox"


@pytest.mark.asyncio
async def test_client_cannot_choose_apns_environment_or_spoof_user(commute_harness):
    h = commute_harness
    response = await h.client.put(
        "/api/v1/push-devices/inst-1",
        json={
            "apns_token": "a" * 64,
            "notification_permission": "authorized",
            "environment": "production",
        },
        headers=auth(h.token_a),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_advisory_actions_are_idempotent_and_cross_user_hidden(commute_harness):
    h = commute_harness
    plan, check, _ = await full_flow(h)
    advisory_id = check.advisory_id
    assert advisory_id is not None

    hidden = await h.client.get(
        f"/api/v1/advisories/{advisory_id}", headers=auth(h.token_b)
    )
    assert hidden.status_code == 404
    hidden_action = await h.client.post(
        f"/api/v1/advisories/{advisory_id}/actions",
        json={"action": "opened", "installation_id": "install-1"},
        headers=auth(h.token_b, {"Idempotency-Key": "other-user-action"}),
    )
    assert hidden_action.status_code == 404

    opened = await h.client.post(
        f"/api/v1/advisories/{advisory_id}/actions",
        json={"action": "opened", "installation_id": "install-1"},
        headers=auth(h.token_a, {"Idempotency-Key": "open-key-1"}),
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["replayed"] is False

    replay = await h.client.post(
        f"/api/v1/advisories/{advisory_id}/actions",
        json={"action": "opened", "installation_id": "install-1"},
        headers=auth(h.token_a, {"Idempotency-Key": "open-key-1"}),
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True

    reused = await h.client.post(
        f"/api/v1/advisories/{advisory_id}/actions",
        json={"action": "skip_today", "installation_id": "install-1"},
        headers=auth(h.token_a, {"Idempotency-Key": "open-key-1"}),
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_key_reused"

    # An unbound install can still report its own action (e.g. notification
    # permission denied), but another account's binding is refused.
    unbound = await h.client.post(
        f"/api/v1/advisories/{advisory_id}/actions",
        json={"action": "opened", "installation_id": "unbound-install"},
        headers=auth(h.token_a, {"Idempotency-Key": "unbound-action"}),
    )
    assert unbound.status_code == 200
    await h.client.put(
        "/api/v1/push-devices/b-other-install",
        json={"apns_token": "c" * 64, "notification_permission": "authorized"},
        headers=auth(h.token_b),
    )
    foreign = await h.client.post(
        f"/api/v1/advisories/{advisory_id}/actions",
        json={"action": "opened", "installation_id": "b-other-install"},
        headers=auth(h.token_a, {"Idempotency-Key": "foreign-action"}),
    )
    assert foreign.status_code == 403
    assert foreign.json()["error"]["code"] == "device_not_bound"

    detail = await h.client.get(
        f"/api/v1/advisories/{advisory_id}", headers=auth(h.token_a)
    )
    assert detail.status_code == 200
    serialized = str(detail.json())
    assert "31.2304" not in serialized and "121.4737" not in serialized
    assert '"家"' not in serialized and '"公司"' not in serialized


@pytest.mark.asyncio
async def test_replayed_action_also_binds_its_idempotency_key(commute_harness):
    h = commute_harness
    _, check, _ = await full_flow(h)
    advisory_id = check.advisory_id
    payload = {"action": "opened", "installation_id": "install-1"}
    first = await h.client.post(
        f"/api/v1/advisories/{advisory_id}/actions",
        json=payload,
        headers=auth(h.token_a, {"Idempotency-Key": "open-first-key"}),
    )
    assert first.status_code == 200 and first.json()["replayed"] is False
    second = await h.client.post(
        f"/api/v1/advisories/{advisory_id}/actions",
        json=payload,
        headers=auth(h.token_a, {"Idempotency-Key": "open-second-key"}),
    )
    assert second.status_code == 200 and second.json()["replayed"] is True
    conflict = await h.client.post(
        f"/api/v1/advisories/{advisory_id}/actions",
        json={"action": "skip_today", "installation_id": "install-1"},
        headers=auth(h.token_a, {"Idempotency-Key": "open-second-key"}),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"


@pytest.mark.asyncio
async def test_enabled_bark_owner_replaces_apns_delivery(commute_harness):
    h = commute_harness
    await register_device(h, h.token_a)

    class FakeBark:
        configured = True
        def __init__(self): self.sent = []
        async def send(self, *, title, body, group="PA"):
            self.sent.append((title, body))
            from assistant_api.commute.services.bark import BarkDelivery
            return BarkDelivery("accepted", "accepted")
        async def aclose(self): return None

    fake = FakeBark()
    h.runtime.bark = fake
    plan = await create_plan(h, h.token_a)
    _, outbox = await run_full_auto_cycle(h)
    assert outbox is not None and outbox.status == "accepted_by_bark"
    assert fake.sent and outbox.plan_id in outbox.payload_json["aps"]["thread-id"]
    assert h.push.messages == []


@pytest.mark.asyncio
async def test_bark_failed_is_recorded_without_apns_fallback(commute_harness):
    h = commute_harness
    h.bark.status = "failed"
    await register_device(h, h.token_a)
    await create_plan(h, h.token_a)
    _, outbox = await run_full_auto_cycle(h)
    assert outbox is not None and outbox.status == "failed"
    assert outbox.last_error_code == "bark_failed"
    assert h.push.messages == []
    assert len(h.bark.sent) == 1


@pytest.mark.asyncio
async def test_disabling_auto_reminders_cancels_an_already_pending_outbox(commute_harness):
    h = commute_harness
    from tests.commute_flow import deliver_one_outbox, execute_one_check, scheduler_run
    from assistant_api.models import BarkReminderSetting

    await create_plan(h, h.token_a)
    await scheduler_run(h)
    assert await execute_one_check(h) is not None
    async with h.app.state.session_factory() as db:
        setting = await db.get(BarkReminderSetting, h.user_a)
        setting.auto_reminders_enabled = False
        await db.commit()
    outbox = await deliver_one_outbox(h)
    assert outbox is not None and outbox.status == "cancelled"
    assert outbox.last_error_code == "bark_not_enabled"
    assert h.bark.sent == []
    assert h.push.messages == []




@pytest.mark.asyncio
async def test_bark_attempt_is_recorded_without_an_apns_device(commute_harness):
    h = commute_harness
    h.bark.status = "accepted"
    await create_plan(h, h.token_a)  # no PushDevice registration at all
    _, outbox = await run_full_auto_cycle(h)
    assert outbox is not None and outbox.status == "accepted_by_bark"
    async with h.app.state.session_factory() as db:
        attempt = await db.scalar(
            select(PushAttempt).where(PushAttempt.outbox_id == outbox.outbox_id)
        )
        assert attempt is not None
        assert attempt.provider == "bark"
        assert attempt.result == "accepted"
        assert attempt.device_id is None
        assert attempt.token_digest
        assert await db.scalar(select(func.count()).select_from(PushDevice)) == 0


@pytest.mark.asyncio
async def test_non_owner_cannot_deliver_a_claimed_outbox(commute_harness):
    """Delivery checks the current claim before any network send."""

    h = commute_harness
    await create_plan(h, h.token_a)
    from assistant_api.commute.services.push import claim_outbox
    from tests.commute_flow import execute_one_check, scheduler_run

    await scheduler_run(h)
    await execute_one_check(h)
    async with h.app.state.session_factory() as db:
        claimed = await claim_outbox(
            db, worker_id="worker-a", now=h.clock(), lease_seconds=180
        )
        assert claimed is not None
        outbox_id = claimed.outbox_id
    from assistant_api.commute.services.push import deliver_outbox

    async with h.app.state.session_factory() as db:
        result = await deliver_outbox(
            db, h.runtime, outbox_id=outbox_id, worker_id="worker-b"
        )
    assert result is not None and result.status == "sending"
    assert h.bark.sent == []


@pytest.mark.asyncio
async def test_abandoned_in_flight_send_becomes_unknown_without_resend(commute_harness):
    """A crash after the send but before the commit cannot be retried safely."""

    h = commute_harness
    await create_plan(h, h.token_a)
    from assistant_api.commute.services.push import (
        claim_outbox,
        expire_abandoned_deliveries,
    )
    from tests.commute_flow import execute_one_check, scheduler_run

    await scheduler_run(h)
    await execute_one_check(h)
    async with h.app.state.session_factory() as db:
        claimed = await claim_outbox(
            db, worker_id="worker-a", now=h.clock(), lease_seconds=180
        )
        assert claimed is not None
        claimed.lease_expires_at = h.clock() - timedelta(seconds=1)
        await db.commit()
        outbox_id = claimed.outbox_id
    async with h.app.state.session_factory() as db:
        assert await expire_abandoned_deliveries(db, now=h.clock()) == 1
        row = await db.get(NotificationOutbox, outbox_id)
        assert row.status == "unknown"
        assert row.last_error_code == "delivery_lease_expired_unknown"
    assert h.bark.sent == []
    async with h.app.state.session_factory() as db:
        assert await claim_outbox(
            db, worker_id="worker-b", now=h.clock(), lease_seconds=180
        ) is None

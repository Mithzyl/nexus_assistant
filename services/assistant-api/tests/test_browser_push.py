"""Verify browser delivery timing and repeat handling with real isolated business storage."""
import base64
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from conftest import auth


def subscription():
    """Return a correctly encoded subscription without making a browser or network request."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    public = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
    return {'endpoint': 'https://fcm.googleapis.com/fcm/send/isolated',
            'keys': {'p256dh': encode(public), 'auth': encode(b'0123456789abcdef')}}


async def test_due_todo_schedule_and_commute_send_once(harness):
    """Only due active records reach this account's subscription, and each occurrence sends once."""
    from assistant_api.models import Matter, MatterTodo, ScheduleEvent, CommutePlan
    from assistant_api.services import browser_push
    h = harness
    h.settings.web_push_enabled = True
    h.settings.web_push_public_key = 'test-public-key'
    h.settings.web_push_private_key_path = '/test-only-key'
    async def sender(info, payload):
        """Capture the actual notification payload after queue and ownership checks."""
        sent.append(payload)
    sent = []
    h.app.state.web_push_sender = sender
    now = datetime.now(UTC)
    response = await h.client.post('/api/v1/browser-notifications/subscriptions', headers=auth(h.token_a), json=subscription())
    assert response.status_code == 201, response.text
    async with h.app.state.session_factory() as db:
        for kind in ('todo', 'schedule', 'commute'):
            matter = Matter(user_id=h.user_a, title=kind, status='active')
            db.add(matter); await db.flush()
            at = now + timedelta(seconds=10)
            if kind == 'todo':
                db.add(MatterTodo(user_id=h.user_a, matter_id=matter.matter_id, title='到期待办', due_at=at, status='open'))
            else:
                db.add(ScheduleEvent(user_id=h.user_a, matter_id=matter.matter_id, title=kind, starts_at=at, status='scheduled'))
                if kind == 'commute':
                    db.add(CommutePlan(user_id=h.user_a, matter_id=matter.matter_id, name='通勤', timezone='Asia/Shanghai',
                        enabled=True, notify_enabled=True, origin_latitude=31.2, origin_longitude=121.4,
                        destination_latitude=31.3, destination_longitude=121.5, transport_mode='driving',
                        arrival_local_time='09:00', check_local_time='08:00', origin_label='起点', destination_label='终点',
                        weekdays_json=[1,2,3,4,5], buffer_minutes=10))
        await db.commit()
    await browser_push.tick(h.app, now=now + timedelta(seconds=5))
    assert sent == []
    await browser_push.tick(h.app, now=now + timedelta(seconds=15))
    assert {item['kind'] for item in sent} == {'todo', 'schedule', 'commute'}
    await browser_push.tick(h.app, now=now + timedelta(seconds=16))
    assert len(sent) == 3


async def test_cancelled_reminder_and_disabled_subscription_do_not_send(harness):
    """Cancelling an event or disabling its browser subscription prevents notification writes."""
    from assistant_api.models import Matter, Reminder
    from assistant_api.services import browser_push
    h = harness
    h.settings.web_push_enabled = True
    h.settings.web_push_public_key = 'test-public-key'
    h.settings.web_push_private_key_path = '/test-only-key'
    sent = []
    async def sender(info, payload):
        """Record unexpected delivery so cancellation is checked against real dispatch."""
        sent.append(payload)
    h.app.state.web_push_sender = sender
    data = subscription()
    saved = await h.client.post('/api/v1/browser-notifications/subscriptions', headers=auth(h.token_a), json=data)
    assert saved.status_code == 201
    now = datetime.now(UTC)
    async with h.app.state.session_factory() as db:
        m = Matter(user_id=h.user_a, title='取消场景', status='active'); db.add(m); await db.flush()
        db.add(Reminder(user_id=h.user_a, matter_id=m.matter_id, title='已取消',
                        trigger_at=now + timedelta(seconds=2), status='cancelled'))
        await db.commit()
    await browser_push.tick(h.app, now=now + timedelta(seconds=3))
    assert sent == []
    disabled = await h.client.delete('/api/v1/browser-notifications/subscriptions/' + saved.json()['subscription_id'], headers=auth(h.token_a))
    assert disabled.status_code == 200
    await browser_push.tick(h.app, now=now + timedelta(seconds=5))
    assert sent == []


async def test_unknown_send_is_not_replayed_and_display_receipt_is_account_scoped(harness):
    """An uncertain HTTP outcome cannot duplicate a push or let another account acknowledge it."""
    from assistant_api.models import MatterTodo, Matter
    from assistant_api.models.browser_push import BrowserPushDelivery
    from assistant_api.services import browser_push
    h = harness
    h.settings.web_push_enabled = True
    h.settings.web_push_public_key = 'test-public-key'
    h.settings.web_push_private_key_path = '/test-only-key'
    attempts = []
    async def sender(info, payload):
        """Model a connection loss after dispatch, not a confirmed business failure."""
        attempts.append(payload)
        raise TimeoutError('unknown outcome')
    h.app.state.web_push_sender = sender
    response = await h.client.post('/api/v1/browser-notifications/subscriptions', headers=auth(h.token_a), json=subscription())
    assert response.status_code == 201
    now = datetime.now(UTC)
    async with h.app.state.session_factory() as db:
        matter = Matter(user_id=h.user_a,title='未知推送',status='active'); db.add(matter); await db.flush()
        db.add(MatterTodo(user_id=h.user_a,matter_id=matter.matter_id,title='不应重复',
                         due_at=now+timedelta(seconds=2),status='open'))
        await db.commit()
    await browser_push.tick(h.app,now=now+timedelta(seconds=3))
    await browser_push.tick(h.app,now=now+timedelta(seconds=4))
    assert len(attempts) == 1
    async with h.app.state.session_factory() as db:
        delivery = await db.scalar(select(BrowserPushDelivery))
        assert delivery.status == 'unknown'
        delivery_id = delivery.delivery_id
    denied = await h.client.post('/api/v1/browser-notifications/receipts/'+delivery_id, headers=auth(h.token_b))
    assert denied.status_code == 404
    acknowledged = await h.client.post('/api/v1/browser-notifications/receipts/'+delivery_id, headers=auth(h.token_a))
    assert acknowledged.status_code == 200
    assert acknowledged.json()['status'] == 'displayed'

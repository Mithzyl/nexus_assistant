"""Authenticated Web Push settings; capability URLs and browser keys never reach AI."""
import base64
from datetime import UTC, datetime
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from assistant_api.core.deps import CurrentUser, DatabaseSession, SettingsDependency
from assistant_api.models.browser_push import BrowserPushSubscription, BrowserPushDelivery

router = APIRouter(prefix='/api/v1/browser-notifications', tags=['browser-notifications'])
PRIVATE = {'x-assistant': {'enabled': False, 'reason': '浏览器通知订阅由用户授权，能力凭据不能交给AI。'}}


class SubscriptionKeys(BaseModel):
    """Bound the browser's encryption material before storing its push capability."""
    model_config = ConfigDict(extra='forbid')
    p256dh: str = Field(min_length=80, max_length=100)
    auth: str = Field(min_length=20, max_length=32)

    @field_validator('p256dh', 'auth')
    @classmethod
    def validate_key(cls, value, info):
        """Require valid Web Push key lengths and an actual P-256 public point."""
        from cryptography.hazmat.primitives.asymmetric import ec
        decoded = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
        if info.field_name == 'p256dh':
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded)
        elif len(decoded) != 16:
            raise ValueError('Invalid browser authentication key')
        return value


class SubscriptionWrite(BaseModel):
    """Accept only supported browser push services, never arbitrary callback destinations."""
    model_config = ConfigDict(extra='forbid')
    endpoint: str = Field(max_length=2048)
    keys: SubscriptionKeys

    @field_validator('endpoint')
    @classmethod
    def validate_endpoint(cls, value):
        """Reject local or credential-bearing URLs before any HTTP transmission."""
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.hostname not in {
            'fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com'}
            or parsed.username or parsed.password or parsed.fragment or parsed.port not in {None, 443}):
            raise ValueError('Unsupported browser push service')
        return value


@router.get('/config', openapi_extra=PRIVATE)
async def configuration(user: CurrentUser, db: DatabaseSession, settings: SettingsDependency):
    """Expose only the public application-server key and actual configuration availability."""
    configured = bool(settings.web_push_enabled and settings.web_push_public_key and settings.web_push_private_key_path)
    ids = list(await db.scalars(select(BrowserPushSubscription.subscription_id).where(
        BrowserPushSubscription.user_id == user.user_id, BrowserPushSubscription.enabled.is_(True))))
    return {'configured': configured, 'public_key': settings.web_push_public_key if configured else None, 'subscription_ids': ids}


@router.post('/subscriptions', status_code=201, openapi_extra=PRIVATE)
async def subscribe(payload: SubscriptionWrite, user: CurrentUser, db: DatabaseSession, settings: SettingsDependency):
    """Bind one browser capability to its authenticated account with an explicit enabled time."""
    if not settings.web_push_enabled or not settings.web_push_public_key or not settings.web_push_private_key_path:
        raise HTTPException(503, '浏览器推送尚未配置。')
    row = await db.scalar(select(BrowserPushSubscription).where(BrowserPushSubscription.endpoint == payload.endpoint))
    now = datetime.now(UTC)
    if row is None:
        row = BrowserPushSubscription(user_id=user.user_id, endpoint=payload.endpoint,
            keys_json=payload.keys.model_dump(), enabled=True, enabled_at=now)
        db.add(row)
    else:
        if row.user_id != user.user_id or not row.enabled: row.enabled_at = now
        row.user_id = user.user_id
        row.keys_json = payload.keys.model_dump()
        row.enabled = True
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, '订阅正在更新，请重试。') from exc
    return {'subscription_id': row.subscription_id, 'enabled': row.enabled}


@router.delete('/subscriptions/{subscription_id}', openapi_extra=PRIVATE)
async def unsubscribe(subscription_id: str, user: CurrentUser, db: DatabaseSession):
    """Disable only the current account's subscription; retain its delivery history."""
    row = await db.get(BrowserPushSubscription, subscription_id)
    if not row or row.user_id != user.user_id: raise HTTPException(404, '订阅不存在。')
    row.enabled = False
    await db.commit()
    return {'enabled': False}


@router.post('/receipts/{delivery_id}', openapi_extra=PRIVATE)
async def displayed(delivery_id: str, user: CurrentUser, db: DatabaseSession):
    """Record browser display even after an uncertain send; receipts never trigger a resend."""
    row = await db.get(BrowserPushDelivery, delivery_id)
    if not row or row.user_id != user.user_id: raise HTTPException(404, '通知不存在。')
    if row.status in {'sending', 'accepted', 'unknown', 'displayed'}:
        row.status = 'displayed'
        row.displayed_at = row.displayed_at or datetime.now(UTC)
        await db.commit()
    return {'status': row.status}

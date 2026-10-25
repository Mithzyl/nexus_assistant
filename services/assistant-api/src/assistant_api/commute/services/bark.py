"""Per-account Bark binding, sender and explicit test. Keys never leave the DB."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.core.errors import ConflictError, InvalidRequestError, NotFoundError
from assistant_api.models import BarkReminderSetting
from assistant_api.models.base import utc_now
from assistant_api.schemas.bark import BarkStatusRead, BarkTestRead

BARK_HOST = "https://api.day.app"


@dataclass(frozen=True)
class BarkDelivery:
    status: str
    message: str
    error_code: str | None = None


class BarkNotifier:
    def __init__(self, device_key: str, *, timeout_seconds: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None,
                 base_url: str = BARK_HOST) -> None:
        self._key = device_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False,
                                         trust_env=False, transport=transport)

    @property
    def configured(self) -> bool:
        return bool(self._key)

    async def send(self, *, title: str, body: str, group: str = "PA") -> BarkDelivery:
        try:
            response = await self._client.post(
                f"{self._base_url}/{quote(self._key, safe='')}",
                json={"title": title, "body": body, "group": group},
            )
        except (httpx.TimeoutException, httpx.RequestError):
            return BarkDelivery("unknown", "发送结果暂不确定，请稍后重试。", "bark_network_unknown")
        if response.status_code == 429 or response.status_code >= 500:
            return BarkDelivery("unknown", "Bark 服务暂时异常，结果暂不确定。", "bark_http_unknown")
        if not 200 <= response.status_code < 300:
            return BarkDelivery("failed", "Bark 拒绝了这次请求。", "bark_rejected")
        try:
            payload = response.json()
        except ValueError:
            return BarkDelivery("failed", "Bark 返回了无法识别的响应。", "bark_response_invalid")
        if isinstance(payload, dict) and payload.get("code") == 200:
            return BarkDelivery("accepted", "Bark 已接受这次通知请求。")
        return BarkDelivery("failed", "Bark 未接受这次通知请求。", "bark_not_accepted")

    async def aclose(self) -> None:
        await self._client.aclose()


async def _row(db: AsyncSession, user_id: str, *, create: bool = False) -> BarkReminderSetting | None:
    row = await db.get(BarkReminderSetting, user_id)
    if row is None and create:
        row = BarkReminderSetting(user_id=user_id, auto_reminders_enabled=False)
        db.add(row)
        await db.flush()
    return row


def _status(row: BarkReminderSetting | None) -> BarkStatusRead:
    configured = bool(row and row.device_key)
    enabled = bool(configured and row and row.auto_reminders_enabled)
    state = "enabled" if enabled else ("disabled" if configured else "unconfigured")
    return BarkStatusRead(
        configured=configured,
        auto_reminders_enabled=enabled,
        available=configured,
        state=state,
        last_test_status=row.last_test_status if row else None,
        last_test_at=row.last_test_at if row else None,
    )


async def status(db: AsyncSession, user_id: str) -> BarkStatusRead:
    return _status(await _row(db, user_id))


async def bind(db: AsyncSession, user_id: str, device_key: str) -> BarkStatusRead:
    key = device_key.strip()
    if len(key) < 8:
        raise InvalidRequestError("bark_key_invalid", "Bark Device Key 不完整。")
    if len(key) > 512:
        raise InvalidRequestError("bark_key_invalid", "Bark Device Key 过长。")
    row = await _row(db, user_id, create=True)
    assert row is not None
    row.device_key = key
    # Saving a key never enables timed reminders or sends a message.
    row.auto_reminders_enabled = False
    await db.commit()
    return await status(db, user_id)


async def unbind(db: AsyncSession, user_id: str) -> BarkStatusRead:
    row = await _row(db, user_id)
    if row is None or not row.device_key:
        raise NotFoundError("bark_not_configured", "当前账号尚未绑定 Bark。")
    row.device_key = None
    row.auto_reminders_enabled = False
    row.last_test_status = None
    row.last_test_at = None
    await db.commit()
    return await status(db, user_id)


async def set_auto_reminders(db: AsyncSession, user_id: str, enabled: bool) -> BarkStatusRead:
    row = await _row(db, user_id)
    if row is None or not row.device_key:
        raise ConflictError("bark_not_configured", "请先绑定 Bark Key。")
    row.auto_reminders_enabled = enabled
    await db.commit()
    return await status(db, user_id)


async def send_test(db: AsyncSession, user_id: str, transport=None) -> BarkTestRead:
    row = await _row(db, user_id)
    if row is None or not row.device_key:
        raise ConflictError("bark_not_configured", "请先绑定 Bark Key。")
    notifier = BarkNotifier(row.device_key, transport=transport)
    try:
        delivery = await notifier.send(
            title="PA 通勤通知测试",
            body="这是一条由你主动触发的 Bark 测试通知。",
        )
    finally:
        await notifier.aclose()
    now = utc_now()
    row.last_test_status = delivery.status
    row.last_test_at = now
    await db.commit()
    return BarkTestRead(
        status=delivery.status,
        accepted=delivery.status == "accepted",
        message=delivery.message,
        tested_at=now,
    )

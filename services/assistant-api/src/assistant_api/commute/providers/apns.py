"""APNs alert provider over HTTP/2.

Topic and environment come from server settings only. The client connection is
reused, the provider token is refreshed well before Apple's one-hour limit, and
only a token digest is ever persisted by callers. `accepted` means APNs accepted
the request, never that the user received or read it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
import jwt

from assistant_api.commute.providers.base import (
    ProviderAuthError,
    ProviderPushError,
    PushMessage,
    PushResult,
)
from assistant_api.commute.timeutil import ensure_aware

SANDBOX_HOST = "https://api.sandbox.push.apple.com"
PRODUCTION_HOST = "https://api.push.apple.com"


class ApnsPushProvider:
    provider = "apns"

    def __init__(
        self,
        *,
        team_id: str,
        key_id: str,
        private_key_path: Path,
        topic: str,
        environment: Literal["sandbox", "production"],
        now,
        timeout_seconds: float,
        host: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._team_id = team_id
        self._key_id = key_id
        self._key_path = private_key_path
        self.topic = topic
        self.environment = environment
        self._now = now
        self._client = httpx.AsyncClient(
            http2=True,
            base_url=host or (SANDBOX_HOST if environment == "sandbox" else PRODUCTION_HOST),
            timeout=timeout_seconds,
            transport=transport,
        )
        self._jwt: str | None = None
        self._jwt_refresh_at: datetime | None = None

    def _bearer(self) -> str:
        now = ensure_aware(self._now()).astimezone(UTC)
        if self._jwt and self._jwt_refresh_at and now < self._jwt_refresh_at:
            return self._jwt
        try:
            key = self._key_path.read_text(encoding="utf-8")
            token = jwt.encode(
                {"iss": self._team_id, "iat": int(now.timestamp())},
                key,
                algorithm="ES256",
                headers={"kid": self._key_id},
            )
        except Exception as exc:  # noqa: BLE001 - configuration failures
            raise ProviderAuthError("APNs signing failed", code="apns_auth_failed") from exc
        self._jwt = token
        # Refresh after 50 minutes; Apple rejects provider tokens older than 1h.
        self._jwt_refresh_at = now + timedelta(minutes=50)
        return token

    async def send(self, message: PushMessage, *, _retried_auth: bool = False) -> PushResult:
        if message.topic != self.topic or message.environment != self.environment:
            raise ProviderAuthError(
                "APNs topic/environment must come from server configuration",
                code="apns_configuration_mismatch",
            )
        headers = {
            "authorization": f"bearer {self._bearer()}",
            "apns-topic": self.topic,
            "apns-push-type": "alert",
            "apns-priority": "10",
            "apns-id": message.apns_id,
            "apns-collapse-id": message.collapse_id,
            "apns-expiration": str(int(ensure_aware(message.expires_at).timestamp())),
            "content-type": "application/json",
        }
        try:
            response = await self._client.post(
                f"/3/device/{message.device_token}", headers=headers, json=message.payload
            )
        except httpx.TimeoutException:
            return PushResult(result="unknown", error_code="apns_timeout")
        except httpx.HTTPError:
            return PushResult(result="unknown", error_code="apns_connection_error")

        if response.status_code == 200:
            return PushResult(result="accepted", http_status=200)
        reason, timestamp = _reason_and_timestamp(response)
        if response.status_code == 410 or reason in ("Unregistered", "BadDeviceToken"):
            return PushResult(
                result="rejected",
                http_status=response.status_code,
                error_code=reason or "apns_unregistered",
                retryable=False,
                provider_timestamp=timestamp,
            )
        if response.status_code in (401, 403):
            if (
                response.status_code == 403
                and reason in ("ExpiredProviderToken", "InvalidProviderToken")
                and not _retried_auth
            ):
                # One bounded retry with a freshly minted provider JWT. A bad
                # key/topic remains permanent and is not retried forever.
                self._jwt = None
                self._jwt_refresh_at = None
                return await self.send(message, _retried_auth=True)
            return PushResult(
                result="rejected",
                http_status=response.status_code,
                error_code=reason or "apns_auth_failed",
                retryable=False,
            )
        if response.status_code == 429 or response.status_code >= 500:
            return PushResult(
                result="rejected",
                http_status=response.status_code,
                error_code=reason or "apns_unavailable",
                retryable=True,
            )
        return PushResult(
            result="rejected",
            http_status=response.status_code,
            error_code=reason or "apns_rejected",
            retryable=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _reason_and_timestamp(response: httpx.Response) -> tuple[str | None, datetime | None]:
    reason: str | None = None
    timestamp: datetime | None = None
    try:
        body = response.json()
        reason = body.get("reason")
        raw = body.get("timestamp")
        if isinstance(raw, (int, float)):
            timestamp = datetime.fromtimestamp(raw / 1000, tz=UTC)
    except ValueError:
        pass
    if timestamp is None:
        raw_header = response.headers.get("timestamp")
        if raw_header and raw_header.isdigit():
            timestamp = datetime.fromtimestamp(int(raw_header) / 1000, tz=UTC)
    return reason, timestamp

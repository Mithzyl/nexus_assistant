"""Optional model-powered wording through a controlled HTTP endpoint.

The payload contains only normalized facts (no coordinates, labels, user id or
device data). Any timeout, malformed response or non-200 result falls back to
the template in the caller; this provider is never on the critical path.
"""

from __future__ import annotations

import httpx

from assistant_api.commute.providers.base import AdviceFacts, AdviceText


class HttpAdviceTextProvider:
    def __init__(
        self,
        *,
        url: str,
        token: str | None,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url
        self._token = token
        self._timeout = timeout_seconds
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds, transport=transport, follow_redirects=False
        )

    async def generate(self, facts: AdviceFacts) -> AdviceText | None:
        headers = {"content-type": "application/json"}
        if self._token:
            headers["authorization"] = f"Bearer {self._token}"
        payload = {
            "task": "commute_advisory_wording",
            "schema_version": 1,
            "facts": {
                "transport_mode": facts.transport_mode,
                "target_arrival_local": facts.target_arrival_local,
                "recommended_departure_local": facts.recommended_departure_local,
                "eta_minutes": facts.eta_minutes,
                "buffer_minutes": facts.buffer_minutes,
                "weather_extra_buffer_minutes": facts.weather_extra_buffer_minutes,
                "temperature_c": facts.temperature_c,
                "rain_probability_percent": facts.rain_probability_percent,
                "rain_risk": facts.rain_risk,
                "departure_status": facts.departure_status,
                "weather_available": facts.weather_available,
                "eta_available": facts.eta_available,
                "reason_codes": facts.reason_codes,
            },
            "constraints": [
                "只输出 title 和 summary，不新增任何数字",
                "不得添加天气预警、路况事件或未提供的事实",
                "锁屏文案不得包含地址或坐标",
            ],
        }
        try:
            response = await self._client.post(self._url, json=payload, headers=headers)
            if response.status_code != 200:
                return None
            body = response.json()
            title = body["title"]
            summary = body["summary"]
            if not isinstance(title, str) or not isinstance(summary, str):
                return None
            return AdviceText(title=title, summary=summary)
        except (httpx.HTTPError, TimeoutError, KeyError, TypeError, ValueError):
            return None

    async def aclose(self) -> None:
        await self._client.aclose()

"""Apple Maps Server API ETA adapter.

Maps uses its own JWT -> access-token exchange. It is deliberately separate from
the WeatherKit signer: separate keys, claims, caches, and error codes. There is
no transport-mode fallback; no route means the caller must see unavailable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt

from assistant_api.commute.providers.base import (
    EtaResult,
    ProviderAuthError,
    ProviderInvalidResponse,
    ProviderNoRoute,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from assistant_api.commute.timeutil import ensure_aware

TRANSPORT_TYPE = {"driving": "AUTOMOBILE", "walking": "WALKING"}


class MapsEtaProvider:
    provider = "apple_maps_server_api"
    available = True

    def __init__(
        self,
        *,
        team_id: str,
        key_id: str,
        maps_id: str,
        private_key_path: Path,
        base_url: str,
        timeout_seconds: float,
        now,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._team_id = team_id
        self._key_id = key_id
        self._maps_id = maps_id
        self._key_path = private_key_path
        self._base_url = base_url.rstrip("/")
        self._now = now
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds, transport=transport, follow_redirects=False
        )
        self._access_token: str | None = None
        self._access_expires_at: datetime | None = None

    def _client_jwt(self) -> str:
        now = ensure_aware(self._now()).astimezone(UTC)
        issued = int(now.timestamp())
        try:
            key = self._key_path.read_text(encoding="utf-8")
            return jwt.encode(
                {"iss": self._team_id, "iat": issued, "exp": issued + 1800, "sub": self._maps_id},
                key,
                algorithm="ES256",
                headers={"kid": self._key_id},
            )
        except Exception as exc:  # noqa: BLE001 - configuration failures
            raise ProviderAuthError(
                "Maps token signing failed", code="maps_auth_failed"
            ) from exc

    async def _get_access_token(self) -> str:
        now = ensure_aware(self._now()).astimezone(UTC)
        if self._access_token and self._access_expires_at and now < self._access_expires_at:
            return self._access_token
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/token",
                headers={"Authorization": f"Bearer {self._client_jwt()}"},
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("Maps token request timed out", code="maps_timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                "Maps token request failed", code="maps_unavailable"
            ) from exc
        if response.status_code in (401, 403):
            raise ProviderAuthError(
                "Maps rejected the credential", code="maps_auth_failed"
            )
        if response.status_code == 429:
            raise ProviderRateLimited(
                "Maps rate limited", code="maps_rate_limited",
                retry_after_seconds=_retry_after(response),
            )
        if response.status_code >= 500:
            raise ProviderUnavailable("Maps is unavailable", code="maps_unavailable")
        if response.status_code != 200:
            raise ProviderInvalidResponse(
                "Maps token exchange failed", code="maps_invalid_response", retryable=False
            )
        try:
            body = response.json()
            token = body["accessToken"]
            expires = int(body.get("expiresInSeconds", 1800))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(
                "Maps token response was malformed", code="maps_invalid_response"
            ) from exc
        self._access_token = token
        self._access_expires_at = now + timedelta(seconds=max(60, expires - 60))
        return token

    async def _request_eta(
        self,
        *,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        request_depart_at: datetime,
        target_arrival_at: datetime,
        refresh_token: bool,
    ) -> EtaResult:
        if refresh_token:
            self._access_token = None
        token = await self._get_access_token()
        params = {
            "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
            "destination": f"{destination[0]:.6f},{destination[1]:.6f}",
            "transportType": TRANSPORT_TYPE.get(mode, ""),
            "departAt": ensure_aware(request_depart_at).astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        if not params["transportType"]:
            raise ProviderNoRoute("Unsupported transport mode", code="unsupported_transport_mode")
        try:
            response = await self._client.get(
                f"{self._base_url}/v1/eta",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("Maps ETA timed out", code="maps_timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Maps ETA request failed", code="maps_unavailable") from exc

        if response.status_code == 401:
            if not refresh_token:
                return await self._request_eta(
                    origin=origin,
                    destination=destination,
                    mode=mode,
                    request_depart_at=request_depart_at,
                    target_arrival_at=target_arrival_at,
                    refresh_token=True,
                )
            raise ProviderAuthError("Maps access token was rejected", code="maps_auth_failed")
        if response.status_code == 429:
            raise ProviderRateLimited(
                "Maps ETA rate limited",
                code="maps_rate_limited",
                retry_after_seconds=_retry_after(response),
            )
        if response.status_code >= 500:
            raise ProviderUnavailable("Maps ETA is unavailable", code="maps_unavailable")
        if response.status_code in (400, 404, 422):
            raise ProviderNoRoute(
                "No route for this origin, destination or mode",
                code="route_not_found",
            )
        if response.status_code != 200:
            raise ProviderInvalidResponse(
                "Maps ETA returned an unexpected status", code="maps_invalid_response"
            )
        try:
            body = response.json()
            etas = body.get("etats") or body.get("etas") or body.get("routes") or []
            if not etas:
                raise KeyError("etats")
            chosen = next(
                (item for item in etas if item.get("transportType") in (None, params["transportType"])),
                etas[0],
            )
            duration = chosen.get("durationSeconds") or chosen.get("expectedTravelTimeSeconds")
            if duration is None or int(duration) <= 0:
                raise KeyError("durationSeconds")
            distance = chosen.get("distanceMeters")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderNoRoute(
                "Maps returned no usable route duration", code="route_not_found"
            ) from exc
        return EtaResult(
            provider=self.provider,
            transport_mode=mode,  # type: ignore[arg-type]
            fetched_at=ensure_aware(self._now()),
            request_depart_at=ensure_aware(request_depart_at),
            target_arrival_at=ensure_aware(target_arrival_at),
            duration_seconds=int(duration),
            distance_meters=int(distance) if distance is not None else None,
            response_basis="departAt",
        )

    async def fetch(
        self,
        *,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        request_depart_at: datetime,
        target_arrival_at: datetime,
    ) -> EtaResult:
        return await self._request_eta(
            origin=origin,
            destination=destination,
            mode=mode,
            request_depart_at=request_depart_at,
            target_arrival_at=target_arrival_at,
            refresh_token=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None

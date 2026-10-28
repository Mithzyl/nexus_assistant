"""Place lookup shared by the Web client and schedule location editing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from assistant_api.commute.providers.base import ProviderError
from assistant_api.commute.runtime import CommuteRuntime

GCJ02_CHINA_BOUNDS = (73.0, 3.0, 136.0, 54.0)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_selection(
    candidate: dict,
    secret: str,
    *,
    user_id: str,
    draft_id: str,
    selection_kind: str,
) -> str:
    """Sign one short-lived place selection bound to its account and draft."""

    payload = {
        **candidate,
        "selection_kind": selection_kind,
        "user_id": user_id,
        "draft_id": draft_id,
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + 900,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(signature)}"


def verify_selection(token: str, secret: str) -> dict:
    try:
        raw_part, signature_part = token.split(".", 1)
        raw = _unb64(raw_part)
        signature = _unb64(signature_part)
    except (ValueError, TypeError) as exc:
        raise ValueError("place selection token is malformed") from exc
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("place selection token signature is invalid")
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("place selection token payload is invalid") from exc
    if int(value.get("expires_at") or 0) < int(time.time()):
        raise ValueError("place selection token has expired")
    return value


async def search_places(
    runtime: CommuteRuntime,
    *,
    secret: str,
    user_id: str,
    draft_id: str,
    query: str,
    city: str | None,
    search_type: str,
    limit: int,
) -> dict:
    """Return normalized candidates without treating the first result as exact."""

    if runtime.places is None or not getattr(runtime.places, "available", False):
        return {
            "query": query,
            "city": city,
            "search_type": search_type,
            "ambiguous": False,
            "candidates": [],
            "reason_code": "amap_not_configured",
        }
    try:
        rows = await runtime.places.search(
            query=query,
            city=city,
            limit=limit,
            search_type=search_type,
        )
    except ProviderError as exc:
        return {
            "query": query,
            "city": city,
            "search_type": search_type,
            "ambiguous": False,
            "candidates": [],
            "reason_code": exc.code,
        }
    except Exception:  # noqa: BLE001 - provider boundary
        return {
            "query": query,
            "city": city,
            "search_type": search_type,
            "ambiguous": False,
            "candidates": [],
            "reason_code": "place_provider_error",
        }
    candidates = [
        {
            "provider": row.provider,
            "provider_place_id": row.provider_place_id,
            "name": row.name,
            "address": row.address,
            "city": row.city,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "coordinate_system": row.coordinate_system,
            "source": row.source,
        }
        for row in rows
    ]
    for candidate in candidates:
        candidate["selection_token"] = sign_selection(
            candidate,
            secret,
            user_id=user_id,
            draft_id=draft_id,
            selection_kind="place_candidate",
        )
    return {
        "query": query,
        "city": city,
        "search_type": search_type,
        "ambiguous": len(candidates) > 1,
        "candidates": candidates,
        "reason_code": None if candidates else "place_not_found",
    }


async def map_pick_place(
    runtime: CommuteRuntime,
    *,
    secret: str,
    user_id: str,
    draft_id: str,
    latitude: float,
    longitude: float,
    coordinate_system: str,
) -> dict:
    """Reverse geocode one explicit map point without treating it as a POI."""

    if coordinate_system != "gcj02":
        return {
            "status": "unavailable",
            "reason_code": "map_pick_coordinate_system_unsupported",
            "location": None,
        }
    min_longitude, min_latitude, max_longitude, max_latitude = GCJ02_CHINA_BOUNDS
    if not (
        min_latitude <= latitude <= max_latitude
        and min_longitude <= longitude <= max_longitude
    ):
        return {
            "status": "unavailable",
            "reason_code": "map_pick_coordinate_out_of_bounds",
            "location": None,
        }
    if runtime.places is None or not getattr(runtime.places, "available", False):
        return {
            "status": "unavailable",
            "reason_code": "amap_not_configured",
            "location": None,
        }
    try:
        location = await runtime.places.reverse_geocode(
            latitude=latitude,
            longitude=longitude,
        )
    except ProviderError as exc:
        return {
            "status": "unavailable",
            "reason_code": exc.code,
            "location": None,
        }
    except Exception:  # noqa: BLE001 - provider boundary
        return {
            "status": "unavailable",
            "reason_code": "place_provider_error",
            "location": None,
        }
    candidate = {
        "provider": location.provider,
        "provider_place_id": None,
        "name": location.name,
        "address": location.address,
        "city": location.city,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "coordinate_system": location.coordinate_system,
        "source": location.source,
    }
    candidate["selection_token"] = sign_selection(
        candidate,
        secret,
        user_id=user_id,
        draft_id=draft_id,
        selection_kind="map_pick",
    )
    return {
        "status": "available",
        "reason_code": None,
        "address_notice": "地址为地图坐标附近的反查结果，请核对门牌或建筑。",
        "location": candidate,
    }


__all__ = [
    "map_pick_place",
    "search_places",
    "sign_selection",
    "verify_selection",
]

"""Place lookup shared by the Web client and schedule location editing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from assistant_api.commute.runtime import CommuteRuntime

from assistant_api.services import place_queries


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
    runtime: CommuteRuntime, *, secret: str, user_id: str, draft_id: str,
    query: str, city: str | None, search_type: str, limit: int,
) -> dict:
    """Wrap shared search results with existing account-and-draft-bound selection tokens."""
    result = await place_queries.search_places(
        runtime, query=query, city=city, search_type=search_type, limit=limit,
    )
    for candidate in result["candidates"]:
        candidate["selection_token"] = sign_selection(
            candidate, secret, user_id=user_id, draft_id=draft_id,
            selection_kind="place_candidate",
        )
    return result


async def map_pick_place(
    runtime: CommuteRuntime, *, secret: str, user_id: str, draft_id: str,
    latitude: float, longitude: float, coordinate_system: str,
) -> dict:
    """Bind shared reverse-lookup output to the original schedule draft selection flow."""
    result = await place_queries.map_pick_place(
        runtime, latitude=latitude, longitude=longitude,
        coordinate_system=coordinate_system,
    )
    if result.get("location"):
        candidate = result["location"]
        candidate["selection_token"] = sign_selection(
            candidate, secret, user_id=user_id, draft_id=draft_id, selection_kind="map_pick",
        )
    return result


__all__ = [
    "map_pick_place",
    "search_places",
    "sign_selection",
    "verify_selection",
]

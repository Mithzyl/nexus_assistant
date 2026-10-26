"""Standalone commute estimate shared by the web client and conflict checks.

This is deliberately not a long-term commute plan: it takes two coordinates and a
time, asks the configured RouteEtaProvider, and reports the provider's real state.
No number is invented when the provider is missing or fails.
"""

from __future__ import annotations

from datetime import UTC, datetime

from assistant_api.commute.providers.base import ProviderError
from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.commute.timeutil import ensure_aware

ESTIMATE_SOURCE = "server_provider"


def _unavailable(mode: str, reason: str) -> dict:
    return {
        "status": "unavailable",
        "provider": "unavailable",
        "mode": mode,
        "duration_seconds": None,
        "distance_meters": None,
        "fetched_at": None,
        "request_depart_at": None,
        "target_arrival_at": None,
        "source": ESTIMATE_SOURCE,
        "reason_code": reason,
    }


async def estimate(
    runtime: CommuteRuntime,
    *,
    origin: tuple[float, float],
    destination: tuple[float, float],
    mode: str,
    depart_at: datetime | None,
    arrive_by: datetime | None,
) -> dict:
    """Return one provider measurement or an explicit unavailable state."""

    if not getattr(runtime.eta, "available", False):
        return _unavailable(mode, "route_provider_unavailable")
    noon = ensure_aware(runtime.now())
    depart = ensure_aware(depart_at) if depart_at is not None else noon
    target = ensure_aware(arrive_by) if arrive_by is not None else depart
    try:
        result = await runtime.eta.fetch(
            origin=origin,
            destination=destination,
            mode=mode,  # type: ignore[arg-type]
            request_depart_at=depart,
            target_arrival_at=target,
        )
    except ProviderError as exc:
        return _unavailable(mode, exc.code)
    except Exception:  # noqa: BLE001 - provider boundary must not leak details
        return _unavailable(mode, "route_provider_error")
    return {
        "status": "available",
        "provider": result.provider,
        "mode": result.transport_mode,
        "duration_seconds": int(result.duration_seconds),
        "distance_meters": result.distance_meters,
        "fetched_at": ensure_aware(result.fetched_at).astimezone(UTC),
        "request_depart_at": ensure_aware(result.request_depart_at).astimezone(UTC),
        "target_arrival_at": ensure_aware(result.target_arrival_at).astimezone(UTC),
        "source": ESTIMATE_SOURCE,
        "reason_code": None,
    }


__all__ = ["estimate", "ESTIMATE_SOURCE"]

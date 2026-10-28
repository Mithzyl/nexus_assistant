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


def _unavailable(
    mode: str,
    reason: str,
    *,
    origin_basis: str = "user_confirmed",
    origin_schedule_id: str | None = None,
    schedule_id: str | None = None,
) -> dict:
    return {
        "status": "unavailable",
        "provider": "unavailable",
        "mode": mode,
        "duration_seconds": None,
        "distance_meters": None,
        "walking_distance_meters": None,
        "transfer_count": None,
        "transfers_summary": None,
        "fare_cny": None,
        "fare_kind": None,
        "fare_notice": None,
        "fetched_at": None,
        "request_depart_at": None,
        "target_arrival_at": None,
        "source": ESTIMATE_SOURCE,
        "reason_code": reason,
        "timing_basis": None,
        "accuracy_notice": None,
        "origin_basis": origin_basis,
        "origin_schedule_id": origin_schedule_id,
        "schedule_id": schedule_id,
    }


async def estimate(
    runtime: CommuteRuntime,
    *,
    origin: tuple[float, float],
    destination: tuple[float, float],
    mode: str,
    depart_at: datetime | None,
    arrive_by: datetime | None,
    origin_city: str | None = None,
    destination_city: str | None = None,
    origin_coordinate_system: str | None = None,
    destination_coordinate_system: str | None = None,
    origin_basis: str = "user_confirmed",
    origin_schedule_id: str | None = None,
    schedule_id: str | None = None,
) -> dict:
    """Return one provider measurement or an explicit unavailable state."""

    if getattr(runtime.eta, "provider", None) == "amap" and (
        origin_coordinate_system != "gcj02"
        or destination_coordinate_system != "gcj02"
    ):
        return _unavailable(
            mode,
            "coordinate_system_mismatch",
            origin_basis=origin_basis,
            origin_schedule_id=origin_schedule_id,
            schedule_id=schedule_id,
        )
    if not getattr(runtime.eta, "available", False):
        return _unavailable(
            mode,
            "route_provider_unavailable",
            origin_basis=origin_basis,
            origin_schedule_id=origin_schedule_id,
            schedule_id=schedule_id,
        )
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
            origin_city=origin_city,
            destination_city=destination_city,
        )
    except ProviderError as exc:
        return _unavailable(
            mode,
            exc.code,
            origin_basis=origin_basis,
            origin_schedule_id=origin_schedule_id,
            schedule_id=schedule_id,
        )
    except Exception:  # noqa: BLE001 - provider boundary must not leak details
        return _unavailable(
            mode,
            "route_provider_error",
            origin_basis=origin_basis,
            origin_schedule_id=origin_schedule_id,
            schedule_id=schedule_id,
        )
    notice = (
        "未来时刻按计划时间请求，不等同于实时路况预测。"
        if result.timing_basis == "planned_time_not_live_forecast"
        else None
    )
    return {
        "status": "available",
        "provider": result.provider,
        "mode": result.transport_mode,
        "duration_seconds": int(result.duration_seconds),
        "distance_meters": result.distance_meters,
        "walking_distance_meters": result.walking_distance_meters,
        "transfer_count": result.transfer_count,
        "transfers_summary": result.transfers_summary,
        "fare_cny": result.fare_cny,
        "fare_kind": result.fare_kind,
        "fare_notice": (
            "AMap taxi_cost 是路线返回的费用估计，不等同实时出租车计价。"
            if result.fare_kind == "amap_taxi_cost_estimate"
            else None
        ),
        "fetched_at": ensure_aware(result.fetched_at).astimezone(UTC),
        "request_depart_at": ensure_aware(result.request_depart_at).astimezone(UTC),
        "target_arrival_at": ensure_aware(result.target_arrival_at).astimezone(UTC),
        "source": ESTIMATE_SOURCE,
        "reason_code": None,
        "timing_basis": result.timing_basis,
        "accuracy_notice": notice,
        "origin_basis": origin_basis,
        "origin_schedule_id": origin_schedule_id,
        "schedule_id": schedule_id,
    }


__all__ = ["estimate", "ESTIMATE_SOURCE"]

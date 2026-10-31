"""ORM-to-DTO conversion with UTC `Z` timestamps via the schemas.

Applicability flags are computed at read time from the current plan and clock.
They never rewrite what the model produced: an old advisory keeps its own
`weather_json` and sources even after newer weather arrives.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from assistant_api.commute.schemas import (
    AdvisoryRead,
    AdvisorySource,
    AiAnalysisRead,
    CheckRead,
    CommuteLocation,
    CommutePlanRead,
    PlanWeatherRead,
    WeatherSummary,
)
from assistant_api.commute.timeutil import ensure_aware, local_today
from assistant_api.models import (
    CommuteAdvisory,
    CommuteCheck,
    CommutePlan,
    CommuteWeatherSnapshot,
)


def plan_read(plan: CommutePlan) -> CommutePlanRead:
    return CommutePlanRead(
        id=plan.plan_id,
        effective_from=plan.effective_from,
        matter_id=plan.matter_id,
        first_schedule_id=plan.first_schedule_id,
        revision=plan.revision,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        name=plan.name,
        enabled=plan.enabled,
        timezone=plan.timezone,
        weekdays=list(plan.weekdays_json or []),
        arrival_local_time=plan.arrival_local_time,
        check_local_time=plan.check_local_time,
        transport_mode=plan.transport_mode,
        buffer_minutes=plan.buffer_minutes,
        rain_probability_threshold=plan.rain_probability_threshold,
        rain_extra_buffer_minutes=plan.rain_extra_buffer_minutes,
        notify_enabled=plan.notify_enabled,
        origin=CommuteLocation(
            city=plan.origin_city,
            coordinate_system=plan.origin_coordinate_system,
            label=plan.origin_label,
            latitude=plan.origin_latitude,
            longitude=plan.origin_longitude,
        ),
        destination=CommuteLocation(
            city=plan.destination_city,
            coordinate_system=plan.destination_coordinate_system,
            label=plan.destination_label,
            latitude=plan.destination_latitude,
            longitude=plan.destination_longitude,
        ),
    )


def advisory_read(
    advisory: CommuteAdvisory,
    *,
    plan: CommutePlan | None = None,
    now: datetime | None = None,
) -> AdvisoryRead:
    weather = advisory.weather_json
    summary = WeatherSummary(**weather) if weather else None
    current_plan_revision = plan.revision if plan is not None else None
    matches_current_plan = (
        plan is not None and advisory.plan_revision == plan.revision
    )
    is_current_service_date = False
    if plan is not None and now is not None:
        is_current_service_date = (
            local_today(ensure_aware(now), plan.timezone)
            == advisory.local_service_date
        )
    is_expired = False
    if now is not None:
        is_expired = ensure_aware(advisory.expires_at) <= ensure_aware(now)
    return AdvisoryRead(
        id=advisory.advisory_id,
        plan_id=advisory.plan_id,
        plan_revision=advisory.plan_revision,
        local_service_date=advisory.local_service_date.isoformat(),
        generated_at=advisory.generated_at,
        expires_at=advisory.expires_at,
        quality=advisory.quality,
        title=advisory.title,
        summary=advisory.summary,
        target_arrival_at=advisory.target_arrival_at,
        recommended_departure_at=advisory.recommended_departure_at,
        departure_status=advisory.departure_status,
        eta_seconds=advisory.eta_seconds,
        buffer_seconds=advisory.buffer_seconds,
        weather_extra_buffer_seconds=advisory.weather_extra_buffer_seconds,
        weather=summary,
        sources=[AdvisorySource(**item) for item in (advisory.sources_json or [])],
        reason_codes=[
            item["code"] for item in (advisory.reason_codes_json or [])
        ],
        explanation_source=advisory.explanation_source,
        ai_analysis=(
            AiAnalysisRead(**advisory.ai_analysis_json)
            if advisory.ai_analysis_json is not None
            else None
        ),
        matches_current_plan=matches_current_plan,
        is_current_service_date=is_current_service_date,
        is_expired=is_expired,
        current_plan_revision=current_plan_revision,
    )


def plan_weather_read(
    snapshot: CommuteWeatherSnapshot,
    check: CommuteCheck,
    plan: CommutePlan,
    *,
    local_service_date,
    now: datetime,
    stale_after_seconds: int,
) -> PlanWeatherRead:
    """Publish one stored snapshot; the date comes from the check's instance."""

    now = ensure_aware(now)
    summary = dict(snapshot.summary_json or {})
    is_stale = False
    if snapshot.fetched_at is None:
        is_stale = True
    elif ensure_aware(snapshot.fetched_at) < now - timedelta(
        seconds=stale_after_seconds
    ):
        is_stale = True
    if snapshot.valid_to is not None and ensure_aware(snapshot.valid_to) < now:
        is_stale = True
    coverage = summary.get("coverage") or "unavailable"
    if coverage not in {"full", "partial", "unavailable"}:
        coverage = "unavailable"
    return PlanWeatherRead(
        plan_id=check.plan_id,
        plan_revision=check.plan_revision,
        local_service_date=local_service_date.isoformat(),
        check_id=check.check_id,
        provider=snapshot.provider,
        fetched_at=snapshot.fetched_at,
        valid_from=snapshot.valid_from,
        valid_to=snapshot.valid_to,
        quality=snapshot.quality if snapshot.quality in {
            "fresh", "degraded", "unavailable"
        } else "unavailable",
        freshness=snapshot.freshness if snapshot.freshness in {
            "fresh", "stale", "unavailable"
        } else "unavailable",
        coverage=coverage,
        summary=WeatherSummary(**summary) if summary else None,
        stale_after_seconds=stale_after_seconds,
        is_stale=is_stale,
        matches_current_plan=check.plan_revision == plan.revision,
        current_plan_revision=plan.revision,
    )


def check_read(check: CommuteCheck) -> CheckRead:
    error = None
    if check.error_code:
        error = {
            "code": check.error_code,
            "message": check.error_message or "检查未能完成。",
            "retryable": check.error_code
            in {"provider_unavailable", "provider_timeout", "provider_rate_limited", "check_retry_pending"},
        }
    return CheckRead(
        check_id=check.check_id,
        plan_id=check.plan_id,
        status=check.status,
        phase=check.phase,
        analysis_mode=check.analysis_mode,
        phase_updated_at=check.phase_updated_at,
        attempt_count=check.attempt_count,
        next_attempt_at=(check.next_attempt_at if check.status == "queued" else None),
        trigger=check.trigger,
        plan_revision=check.plan_revision,
        advisory_id=check.advisory_id,
        advisory_created=check.advisory_created,
        error=error,
        created_at=check.created_at,
        finished_at=check.finished_at,
    )

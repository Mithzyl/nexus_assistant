"""Nexus-backed commute analysis with strict, non-fabricating validation.

The internal task is created through the shared AI task service: the run row, the
`check.ai_run_id` link and the input are committed together before any network
call. This module owns the commute-only parts — the prompt, the fixed input for a
submitted task, and the arithmetic check of the model's JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.commute.timeutil import ensure_aware
from assistant_api.models import CommuteCheck, CommutePlan, NexusRun, User
from assistant_api.nexus_client import NexusError, NexusTimeoutError
from assistant_api.services import ai_tasks, run_service

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "unknown"}


@dataclass
class AiFailure(Exception):
    status: str
    reason: str


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise AiFailure("rejected", "ai_output_invalid_json") from None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            raise AiFailure("rejected", "ai_output_invalid_json") from None
    if not isinstance(value, dict):
        raise AiFailure("rejected", "ai_output_not_object")
    return value


def _prompt(plan: CommutePlan, check: CommuteCheck, snapshot: dict, weather: dict, target: datetime) -> str:
    """Only normalized facts and snapshot data, never labels or secrets."""
    context = {
        "task": "commute_analysis",
        "schema_version": 1,
        "plan_revision": plan.revision,
        "timezone": plan.timezone,
        "target_arrival_at": ensure_aware(target).isoformat(),
        "transport_mode": plan.transport_mode,
        "buffer_minutes": plan.buffer_minutes,
        "rain_extra_buffer_minutes": plan.rain_extra_buffer_minutes,
        "route_snapshot": snapshot,
        "weather": weather,
        "requirements": {
            "output": "single JSON object, no markdown",
            "fields": {
                "title": "short Chinese title",
                "summary": "one or two Chinese sentences using only supplied facts",
                "used_duration_seconds": "must equal one of the snapshot durations",
                "duration_source": "duration_seconds|duration_in_traffic_seconds",
                "base_buffer_seconds": "fixed plan buffer in seconds",
                "additional_buffer_seconds": "model-recommended extra beyond the fixed buffer",
                "extra_buffer_seconds": "base + additional, non-negative integer",
                "recommended_departure_at": "UTC ISO8601",
                "basis": ["short supplied fact"],
                "missing_information": ["short missing fact"],
            },
            "rules": [
                "Do not invent roads, traffic incidents, weather alerts, or guarantees.",
                "Use only supplied ETA, weather, target arrival and preferences.",
                "If duration_in_traffic_seconds is present, use it as used_duration_seconds and duration_source=duration_in_traffic_seconds; do not use the base duration when a traffic value exists.",
                "The used duration must include traffic exactly once.",
                "recommended_departure_at = target_arrival_at - used_duration_seconds - extra_buffer_seconds.",
            ],
        },
    }
    return "请作为通勤分析器，只输出严格 JSON。输入：\n" + json.dumps(
        context, ensure_ascii=False, separators=(",", ":")
    )


def _validate(
    result: dict, *, plan: CommutePlan, snapshot: dict, target: datetime,
    route_source: str = "client_snapshot",
) -> dict:
    duration = result.get("used_duration_seconds")
    source = result.get("duration_source")
    extra = result.get("extra_buffer_seconds")
    departure_raw = result.get("recommended_departure_at")
    if not isinstance(duration, int) or duration < 0 or duration > 86_400:
        raise AiFailure("rejected", "ai_duration_invalid")
    traffic = snapshot.get("duration_in_traffic_seconds")
    if traffic is not None and source not in ("duration_in_traffic_seconds", "duration_seconds"):
        raise AiFailure("rejected", "ai_duration_source_invalid")
    if traffic is not None:
        # A valid traffic ETA is authoritative; equal numeric values must not
        # relabel the base duration as the source.
        if duration != traffic:
            raise AiFailure("rejected", "ai_duration_not_from_snapshot")
        source = "duration_in_traffic_seconds"
        expected = traffic
    elif source == "duration_seconds":
        expected = snapshot.get("duration_seconds")
    else:
        raise AiFailure("rejected", "ai_duration_source_invalid")
    if expected is None or duration != expected:
        raise AiFailure("rejected", "ai_duration_not_from_snapshot")
    base = getattr(plan, "buffer_minutes", 10) * 60
    additional = result.get("additional_buffer_seconds")
    if additional is None:
        additional = max(0, extra - base) if isinstance(extra, int) else None
    if not isinstance(extra, int) or not 0 <= extra <= 7_200:
        raise AiFailure("rejected", "ai_extra_buffer_invalid")
    if not isinstance(additional, int) or additional < 0 or base + additional != extra:
        raise AiFailure("rejected", "ai_extra_buffer_invalid")
    try:
        departure = datetime.fromisoformat(str(departure_raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise AiFailure("rejected", "ai_departure_invalid") from None
    departure = ensure_aware(departure).astimezone(UTC)
    calculated = ensure_aware(target).astimezone(UTC) - timedelta(seconds=duration + extra)
    if abs((departure - calculated).total_seconds()) > 60:
        raise AiFailure("rejected", "ai_departure_arithmetic_invalid")
    title = result.get("title")
    summary = result.get("summary")
    if not isinstance(title, str) or not title.strip() or not isinstance(summary, str) or not summary.strip():
        raise AiFailure("rejected", "ai_text_invalid")
    ref = snapshot.get("route_reference")
    if ref and result.get("route_reference") not in (None, ref):
        raise AiFailure("rejected", "ai_route_reference_invalid")
    basis = result.get("basis", [])
    missing = result.get("missing_information", [])
    if not isinstance(basis, list) or not all(isinstance(x, str) for x in basis):
        raise AiFailure("rejected", "ai_basis_invalid")
    if not isinstance(missing, list) or not all(isinstance(x, str) for x in missing):
        raise AiFailure("rejected", "ai_missing_invalid")
    return {
        "status": "available",
        "analysis_version": 2,
        "route_source": route_source,
        "used_duration_seconds": duration,
        "duration_source": source,
        "map_duration_seconds": expected,
        "base_buffer_seconds": base,
        "additional_buffer_seconds": additional,
        "extra_buffer_seconds": extra,
        "recommended_departure_at": departure.isoformat().replace("+00:00", "Z"),
        "basis": basis[:20],
        "missing_information": missing[:20],
        "title": title.strip()[:200],
        "summary": summary.strip(),
    }


def commute_input(context: dict[str, Any]) -> str:
    """Build the internal instruction for one already-persisted check."""

    return _prompt(
        context["plan"],
        context["check"],
        context["snapshot"],
        context.get("weather") or {},
        context["target"],
    )


def commute_result(text: str | None, context: dict[str, Any]) -> dict:
    """Parse and arithmetically check the model answer for this exact input."""

    if not text:
        raise ai_tasks.AiTaskResultRejected("ai_result_empty")
    try:
        parsed = _extract_json(text)
    except AiFailure as exc:
        raise ai_tasks.AiTaskResultRejected(exc.reason) from None
    try:
        return _validate(
            parsed,
            plan=context["plan"],
            snapshot=context["snapshot"],
            target=context["target"],
            route_source=context.get("route_source", "client_snapshot"),
        )
    except AiFailure as exc:
        raise ai_tasks.AiTaskResultRejected(exc.reason) from None


COMMUTE_SPEC = ai_tasks.register(
    ai_tasks.AiTaskSpec(
        purpose=ai_tasks.COMMUTE_INTERNAL,
        input_version=1,
        # One check is one submitted task: a retry reuses the stored input even
        # when the route snapshot summary was recomputed.
        strict_input_match=False,
        prepare_input=commute_input,
        validate_result=commute_result,
    )
)


def task_key(check: CommuteCheck) -> str:
    """Server-internal key: one submitted analysis per check and input version."""

    return f"commute:{check.check_id}:v{COMMUTE_SPEC.input_version}"


def _unavailable(route_source: str, reason: str) -> dict:
    return {
        "status": "unavailable",
        "route_source": route_source,
        "duration_source": "none",
        "reason": reason,
    }


def _unknown(route_source: str, reason: str) -> dict:
    return {
        "status": "unknown",
        "route_source": route_source,
        "duration_source": "none",
        "reason": reason,
    }


def _failed(route_source: str, reason: str) -> dict:
    return {
        "status": "failed",
        "route_source": route_source,
        "duration_source": "none",
        "reason": reason,
    }


async def _linked_run(db: AsyncSession, *, user: User, check: CommuteCheck) -> NexusRun | None:
    """Find the run already submitted for this check, by link or by legacy marker."""

    if check.ai_run_id:
        row = await db.get(NexusRun, check.ai_run_id)
        if row is not None and row.user_id == user.user_id:
            return row
    row = await db.scalar(
        select(NexusRun)
        .where(
            NexusRun.user_id == user.user_id,
            NexusRun.purpose == ai_tasks.COMMUTE_INTERNAL,
            NexusRun.business_ref == check.check_id,
        )
        .order_by(NexusRun.created_at)
        .limit(1)
    )
    if row is not None:
        return row
    # Runs written before this service existed used a per-snapshot key. They are
    # still the same submitted task, so they are adopted instead of duplicated.
    return await db.scalar(
        select(NexusRun)
        .where(
            NexusRun.user_id == user.user_id,
            NexusRun.idempotency_key.like(f"commute-ai:{check.check_id}:%"),
        )
        .order_by(NexusRun.created_at)
        .limit(1)
    )


async def _resolve_run(
    db: AsyncSession,
    *,
    user: User,
    check: CommuteCheck,
    context: dict[str, Any],
    trigger: str,
) -> tuple[NexusRun, bool]:
    """Return the task for this check, creating and linking it in one commit."""

    row = await _linked_run(db, user=user, check=check)
    if row is not None:
        if check.ai_run_id != row.local_run_id:
            # Explicit adoption write: the link survives a later failure, timeout
            # or unknown result, and is never left to a phase update.
            check.ai_run_id = row.local_run_id
            await db.commit()
        return row, False
    row, replayed = await run_service.create_ai_task(
        db,
        user=user,
        purpose=ai_tasks.COMMUTE_INTERNAL,
        trigger=trigger,
        idempotency_key=task_key(check),
        context=context,
        business_ref=check.check_id,
        attach=lambda created_row: setattr(check, "ai_run_id", created_row.local_run_id),
    )
    if check.ai_run_id != row.local_run_id:
        check.ai_run_id = row.local_run_id
        await db.commit()
    return row, not replayed


async def analyze(
    db: AsyncSession,
    runtime: CommuteRuntime,
    *,
    check: CommuteCheck,
    plan: CommutePlan,
    snapshot: dict | None,
    weather: dict | None,
    target: datetime,
    route_source: str = "client_snapshot",
    no_route_reason: str = "route_provider_unconfigured",
) -> dict:
    """Run one durable Nexus workflow or reuse the exact prior attempt."""

    if snapshot is None:
        return _unavailable("none", no_route_reason)
    if runtime.nexus is None:
        return _unavailable(route_source, "ai_nexus_not_configured")
    user = await db.get(User, check.user_id)
    if user is None:
        return _unavailable(route_source, "account_missing")
    context = {
        "check": check,
        "plan": plan,
        "snapshot": snapshot,
        "weather": weather or {},
        "target": target,
        "route_source": route_source,
    }
    trigger = (
        ai_tasks.TRIGGER_MANUAL
        if check.trigger == "manual"
        else ai_tasks.TRIGGER_SCHEDULE
    )
    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    try:
        row, created = await _resolve_run(
            db, user=user, check=check, context=context, trigger=trigger
        )
        if created:
            row = await run_service.launch_ai_task(
                db, runtime.nexus, runtime.settings, factory, user=user, row=row
            )
        else:
            row = await run_service.resume_ai_task(
                db, runtime.nexus, runtime.settings, factory, user=user, row=row
            )
        if not row.nexus_run_id:
            # The remote run may have been accepted before the local mapping was
            # committed. Recreating it blindly would spend a second call.
            return _unknown(route_source, "ai_run_mapping_unknown")
        if row.status not in TERMINAL_STATUSES:
            row, timed_out = await run_service.wait_terminal(
                db,
                runtime.nexus,
                user=user,
                row=row,
                timeout_seconds=runtime.settings.commute_ai_timeout_seconds,
            )
            if timed_out and row.status not in TERMINAL_STATUSES:
                return _unavailable(route_source, "ai_timeout")
        if row.status == "unknown":
            return _unknown(route_source, "ai_result_unknown")
        if row.status != "succeeded":
            return _failed(route_source, row.error_code or "ai_workflow_failed")
        try:
            return ai_tasks.spec_for(ai_tasks.COMMUTE_INTERNAL).validate_result(
                run_service.answer_text(row), context
            )
        except ai_tasks.AiTaskResultRejected as exc:
            return {"status": "rejected", "duration_source": "none", "reason": exc.reason}
    except NexusTimeoutError:
        return _unknown(route_source, "ai_upstream_timeout")
    except (ai_tasks.AiTaskConfigurationError, NexusError):
        return _unavailable(route_source, "ai_upstream_error")
    except Exception:  # noqa: BLE001 - one check must not crash the worker
        return _unavailable(route_source, "ai_upstream_error")

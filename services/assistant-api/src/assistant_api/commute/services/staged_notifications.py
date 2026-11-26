"""Prepare one Bark notification per commute phase from saved, attributable facts."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from assistant_api.commute.timeutil import ensure_aware
from assistant_api.core.observability import emit_event
from assistant_api.models import CommuteAdvisory, CommuteCheck, CommuteInstance, CommutePlan, Matter, NotificationOutbox, User
from assistant_api.models.bark import BarkReminderSetting
from assistant_api.commute.services.scheduling import ensure_instance
from assistant_api.commute.services.notification_content import instant, message, verified_boarding, weather_facts

# Reserve stable sequence values on the existing unique (plan, day, sequence)
# key. Legacy, unstaged records used sequences 1 and 2.
PHASES = {"evening": 101, "morning": 102, "recheck": 103}
UNSENT = ("pending", "cancelled")


def decision(plan, day, stage, reason):
    """Log the scheduling decision without exposing trip content or device credentials."""
    emit_event("commute_notification_decision", plan_id=plan.plan_id, service_date=day.isoformat(),
               stage=stage, reason=reason)


def phase_start(plan, day, stage):
    """Use the service date and plan zone to compute the relevant wall-clock time."""
    clock = (plan.evening_notice_local_time or "22:00") if stage == "evening" else plan.check_local_time
    local_day = day - timedelta(days=1) if stage == "evening" else day
    return datetime.combine(local_day, time.fromisoformat(clock), ZoneInfo(plan.timezone)).astimezone(UTC)


def phase_expiry(plan, instance, stage):
    """Expire previews at local midnight and morning work by the target arrival."""
    if stage == "evening":
        return datetime.combine(instance.local_service_date, time.min, ZoneInfo(plan.timezone)).astimezone(UTC)
    return ensure_aware(instance.target_arrival_at)


async def enqueue_due(db, settings, *, now):
    """Schedule durable work under the commute scheduler lease, without calling providers."""
    if not settings.bark_web_origin:
        return 0
    plans = (await db.scalars(select(CommutePlan).join(
        BarkReminderSetting, BarkReminderSetting.user_id == CommutePlan.user_id,
    ).join(User, User.user_id == CommutePlan.user_id).where(
        CommutePlan.enabled.is_(True), CommutePlan.notify_enabled.is_(True), User.status == "active",
        BarkReminderSetting.auto_reminders_enabled.is_(True), BarkReminderSetting.device_key.is_not(None),
        BarkReminderSetting.device_key != "",
    ))).all()
    interval = timedelta(minutes=settings.commute_check_interval_minutes)
    count = 0
    for plan in plans:
        day = now.astimezone(ZoneInfo(plan.timezone)).date()
        tomorrow = day + timedelta(days=1)
        for service_date, stage in ((tomorrow, "evening"), (day, "morning"), (day, "recheck")):
            due = phase_start(plan, service_date, stage)
            if stage == "recheck":
                due += interval
            if now < due:
                continue
            instance = await ensure_instance(db, plan, now=now, service_date=service_date)
            if instance is None or instance.status != "active" or now >= phase_expiry(plan, instance, stage):
                continue
            prior = await db.scalar(select(NotificationOutbox).where(
                NotificationOutbox.instance_id == instance.instance_id,
                NotificationOutbox.notification_stage == stage,
            ))
            if prior and (prior.status not in UNSENT or prior.attempt_count > 0
                          or (prior.status == "pending" and stage != "recheck")):
                continue
            busy = await db.scalar(select(CommuteCheck.check_id).where(
                CommuteCheck.instance_id == instance.instance_id, CommuteCheck.notification_stage.is_not(None),
                CommuteCheck.status.in_(("queued", "running")),
            ).limit(1))
            if busy:
                continue
            slot = int((now - due) // interval) if stage == "recheck" else 0
            key = f"commute-bark:{plan.plan_id}:{service_date}:{stage}:{plan.revision}:{slot}"
            if await db.scalar(select(CommuteCheck.check_id).where(
                CommuteCheck.user_id == plan.user_id, CommuteCheck.idempotency_key == key,
            )):
                continue
            try:
                async with db.begin_nested():
                    db.add(CommuteCheck(user_id=plan.user_id, plan_id=plan.plan_id,
                        instance_id=instance.instance_id, plan_revision=plan.revision,
                        trigger="auto", notification_stage=stage, idempotency_key=key,
                        phase="queued", phase_updated_at=now, status="queued", created_at=now))
                    await db.flush()
            except IntegrityError:
                continue
            count += 1
    return count


def usable(snapshot, plan, instance, now):
    """Only use a future departure for the current plan and current arrival target."""
    if not snapshot or snapshot.get("plan_revision") != plan.revision:
        return False
    departure = instant(snapshot.get("departure_at"))
    return (snapshot.get("service_date") == instance.local_service_date.isoformat()
            and instant(snapshot.get("target_arrival_at")) == ensure_aware(instance.target_arrival_at)
            and departure is not None and departure > now)


def significant_change(previous, current, threshold):
    """Compare actions with what Bark actually accepted, including first usable recovery."""
    if not previous:
        return "first_usable_plan"
    if instant(previous["departure_at"]) - instant(current["departure_at"]) >= timedelta(minutes=threshold):
        return "departure_earlier"
    old_weather, new_weather = previous.get("weather") or {}, current.get("weather") or {}
    if new_weather.get("severe") and not old_weather.get("severe"):
        return "severe_weather"
    if new_weather.get("rain_risk") is True and old_weather.get("rain_risk") is not True:
        return "new_rain_risk"
    old, new = previous.get("boarding") or {}, current.get("boarding") or {}
    def line_key(boarding):
        """Distinguish lines and directions without depending on route geometry."""
        return boarding.get("line_id") or boarding.get("line_name"), boarding.get("direction"), boarding.get("boarding_stop_id")
    if old.get("line_name") and new.get("line_name") and line_key(old) != line_key(new):
        return "boarding_changed"
    old_prediction = instant(old.get("predicted_boarding_at"))
    new_prediction = instant(new.get("predicted_boarding_at"))
    if (old.get("trip_id") and old.get("trip_id") == new.get("trip_id")
            and old_prediction and new_prediction
            and (old_prediction - new_prediction >= timedelta(minutes=5)
                 or new_prediction > instant(current["target_arrival_at"]))):
        return "boarding_time_changed"
    return None


async def last_accepted(db, instance):
    """Read the immutable last accepted usable plan for comparisons."""
    rows = (await db.scalars(select(NotificationOutbox).where(
        NotificationOutbox.instance_id == instance.instance_id,
        NotificationOutbox.status == "accepted_by_bark", NotificationOutbox.notification_stage.is_not(None),
    ).order_by(NotificationOutbox.accepted_at.desc(), NotificationOutbox.reminder_sequence.desc()))).all()
    return next((row.payload_json["notification_snapshot"] for row in rows
                 if (row.payload_json.get("notification_snapshot") or {}).get("departure_at")), None)


async def queue_stage(db, settings, plan, instance, stage, snapshot, *, now, check_id=None, reason=None):
    """Insert or replace an unsent phase and freeze the facts once it has been claimed."""
    day = instance.local_service_date
    if (not plan.enabled or not plan.notify_enabled or instance.status != "active"
            or now >= phase_expiry(plan, instance, stage)):
        decision(plan, day, stage, "plan_skipped_disabled_or_expired")
        return
    binding = await db.get(BarkReminderSetting, plan.user_id)
    if not binding or not binding.auto_reminders_enabled or not binding.device_key:
        decision(plan, day, stage, "bark_not_enabled")
        return
    row = await db.scalar(select(NotificationOutbox).where(
        NotificationOutbox.plan_id == plan.plan_id, NotificationOutbox.local_service_date == day,
        NotificationOutbox.reminder_sequence == PHASES[stage],
    ))
    if row and (row.status not in UNSENT or row.attempt_count > 0):
        decision(plan, day, stage, "stage_already_claimed")
        return
    previous = None
    if stage == "recheck":
        confirming = await db.scalar(select(NotificationOutbox.outbox_id).where(
            NotificationOutbox.instance_id == instance.instance_id,
            NotificationOutbox.notification_stage == "morning",
            NotificationOutbox.status.in_(("pending", "sending")),
        ))
        if confirming:
            decision(plan, day, stage, "morning_confirmation_pending")
            return
        previous = await last_accepted(db, instance)
        if not usable(snapshot, plan, instance, now):
            decision(plan, day, stage, "no_usable_route")
            return
        reason = significant_change(previous, snapshot, max(10, settings.commute_departure_earlier_threshold_minutes))
        if not reason:
            if row and row.status == "pending":
                row.status, row.last_error_code = "cancelled", "no_material_change"
            decision(plan, day, stage, "no_material_change")
            return
    elif not usable(snapshot, plan, instance, now):
        if stage == "evening":
            decision(plan, day, stage, "no_usable_route")
            return
        snapshot = {"target_arrival_at": ensure_aware(instance.target_arrival_at).isoformat(),
                    "plan_revision": plan.revision, "service_date": day.isoformat()}
        reason = "no_usable_route"
    title, body = message(plan, stage, snapshot, reason, previous)
    if len(title) > 15 or len("".join(body.splitlines()[:2])) > 40 or len(body) > 100:
        raise ValueError("Commute notification text exceeds its layout budget")
    payload = {"aps": {"alert": {"title": title, "body": body}}, "type": "commute_advisory",
               "plan_id": plan.plan_id, "local_service_date": day.isoformat(), "reason": reason,
               "previous_departure_at": previous.get("departure_at") if previous else None,
               "level": (plan.evening_notice_level or "passive") if stage == "evening"
               else "timeSensitive" if stage == "recheck" else "active",
               "notification_snapshot": snapshot}
    expiry = phase_expiry(plan, instance, stage)
    if snapshot.get("departure_at"):
        expiry = min(expiry, instant(snapshot["departure_at"]))
    advisory = await db.get(CommuteAdvisory, row.advisory_id) if row else CommuteAdvisory(
        user_id=plan.user_id, plan_id=plan.plan_id, check_id=check_id, local_service_date=day,
    )
    advisory.plan_revision, advisory.generated_at = plan.revision, now
    advisory.expires_at = ensure_aware(instance.target_arrival_at)
    advisory.title, advisory.summary = title, body
    advisory.target_arrival_at = ensure_aware(instance.target_arrival_at)
    advisory.recommended_departure_at = instant(snapshot.get("departure_at"))
    advisory.quality = "fresh" if snapshot.get("departure_at") else "unavailable"
    advisory.departure_status = "upcoming" if snapshot.get("departure_at") else "unknown"
    advisory.eta_seconds = snapshot.get("duration_seconds")
    advisory.buffer_seconds = snapshot.get("buffer_seconds", 0)
    weather = snapshot.get("weather") or {}
    advisory.weather_extra_buffer_seconds = weather.get("extra_buffer_seconds", 0)
    advisory.weather_json = weather.get("summary")
    advisory.reason_codes_json = [{"code": reason}] if reason else []
    if not row:
        try:
            async with db.begin_nested():
                db.add(advisory)
                await db.flush()
                row = NotificationOutbox(user_id=plan.user_id, plan_id=plan.plan_id,
                    instance_id=instance.instance_id, advisory_id=advisory.advisory_id,
                    local_service_date=day, reminder_sequence=PHASES[stage], notification_stage=stage,
                    status="pending", payload_json=payload, collapse_id=f"{day}:{stage}:{plan.plan_id}",
                    expires_at=expiry, next_attempt_at=now)
                db.add(row)
                await db.flush()
        except IntegrityError:
            decision(plan, day, stage, "stage_already_exists")
            return
    else:
        row.payload_json, row.expires_at, row.next_attempt_at = payload, expiry, now
        row.status, row.last_error_code = "pending", None
    decision(plan, day, stage, "queued" if not reason else f"queued_{reason}")


def payload_for_delivery(payload, plan, stage, *, day, now):
    """Recheck source freshness at dispatch and preserve exactly what was transmitted."""
    snapshot = dict(payload["notification_snapshot"])
    boarding = snapshot.get("boarding")
    if boarding:
        snapshot["boarding"] = verified_boarding(boarding, plan=plan, day=day, now=now)
    weather = snapshot.get("weather") or {}
    if weather.get("valid_until") and instant(weather["valid_until"]) <= now:
        snapshot["weather"] = None
    previous_at = payload.get("previous_departure_at")
    title, body = message(plan, stage, snapshot, payload.get("reason"),
                          {"departure_at": previous_at} if previous_at else None)
    return {**payload, "notification_snapshot": snapshot,
            "aps": {"alert": {"title": title, "body": body}}}


async def save_snapshot(db, settings, plan, instance, schedule, route, *, weather=None, check=None, now):
    """Save notification facts and refresh pending content inside the route transaction."""
    previous = instance.notification_snapshot_json or {}
    target = ensure_aware(instance.target_arrival_at).isoformat()
    instance.plan_revision = plan.revision
    if weather is None and previous.get("target_arrival_at") == target and previous.get("plan_revision") == plan.revision:
        candidate = previous.get("weather") or {}
        valid_to = instant(candidate.get("valid_until"))
        if valid_to and valid_to > max(now, instant(target)):
            weather = candidate
    snapshot = {
        "service_date": instance.local_service_date.isoformat(), "plan_revision": plan.revision,
        "target_arrival_at": target, "departure_at": ensure_aware(schedule.starts_at).isoformat(),
        "schedule_id": schedule.schedule_id, "saved_at": now.isoformat(),
        "duration_seconds": route["duration_seconds"], "mode": route.get("mode") or plan.transport_mode,
        "buffer_seconds": max(0, int((instant(target) - ensure_aware(schedule.starts_at)).total_seconds())
                              - route["duration_seconds"]),
        "boarding": verified_boarding(route.get("transit_boarding"), plan=plan,
                                      day=instance.local_service_date, now=now), "weather": weather,
    }
    instance.notification_snapshot_json = snapshot
    pending_stages = set((await db.scalars(select(NotificationOutbox.notification_stage).where(
        NotificationOutbox.instance_id == instance.instance_id, NotificationOutbox.status == "pending",
        NotificationOutbox.notification_stage.is_not(None),
    ))).all())
    for stage in sorted(pending_stages - ({check.notification_stage} if check else set())):
        if usable(snapshot, plan, instance, now):
            await queue_stage(db, settings, plan, instance, stage, snapshot, now=now)
        else:
            await db.execute(update(NotificationOutbox).where(
                NotificationOutbox.instance_id == instance.instance_id, NotificationOutbox.notification_stage == stage,
                NotificationOutbox.status == "pending",
            ).values(status="cancelled", last_error_code="route_superseded"))
    if check:
        await queue_stage(db, settings, plan, instance, check.notification_stage, snapshot, now=now, check_id=check.check_id)


async def execute_notification_check(db, runtime, *, check_id, worker_id):
    """Refresh a claimed notification phase and select a labelled fallback on known failure."""
    from assistant_api.commute.schemas import CommuteDayRefresh
    from assistant_api.commute.services.checks import _call_weather
    from assistant_api.core.errors import AssistantError
    from assistant_api.services.commute_day_commands import change_day

    now = ensure_aware(runtime.now())
    check = await db.get(CommuteCheck, check_id)
    if not check or check.status != "running" or check.lease_owner != worker_id:
        return
    plan = await db.get(CommutePlan, check.plan_id)
    instance = await db.get(CommuteInstance, check.instance_id)
    user = await db.get(User, check.user_id)
    stage = check.notification_stage
    code = None
    if (not plan or not instance or not user or user.status != "active" or not plan.enabled
            or not plan.notify_enabled or plan.revision != check.plan_revision or instance.status != "active"
            or now >= phase_expiry(plan, instance, stage)):
        code = "plan_skipped_disabled_or_expired"
    else:
        binding = await db.get(BarkReminderSetting, user.user_id)
        if not binding or not binding.auto_reminders_enabled or not binding.device_key:
            code = "bark_not_enabled"
    if code is None:
        if plan.matter_id is None:
            matter = Matter(user_id=plan.user_id, title=plan.name, goal="按通勤计划安排出行")
            db.add(matter)
            await db.flush()
            linked = await db.execute(update(CommutePlan).where(
                CommutePlan.plan_id == plan.plan_id, CommutePlan.matter_id.is_(None),
                CommutePlan.enabled.is_(True), CommutePlan.revision == check.plan_revision,
            ).values(matter_id=matter.matter_id))
            if linked.rowcount == 1:
                await db.commit()
                await db.refresh(plan)
            else:
                await db.rollback()
                return
        target = ensure_aware(instance.target_arrival_at)
        previous_departure = instant((instance.notification_snapshot_json or {}).get("departure_at"))
        start = max(now, min(target - timedelta(hours=2), previous_departure or target))
        await db.commit()
        result, _ = await _call_weather(runtime, plan, window_start=start, window_end=target)
        weather = weather_facts(result, plan=plan, start=start, target=target, now=now, settings=runtime.settings)
        try:
            outcome = await change_day(db, runtime, runtime.settings, user, plan.plan_id,
                instance.local_service_date, CommuteDayRefresh(service_date=instance.local_service_date,
                    idempotency_key=check.idempotency_key), refresh=True, notification_check=check,
                notification_weather=weather)
            if not outcome["applied"]:
                code = "commute_conflict"
        except AssistantError as exc:
            code = exc.code
            await db.rollback()
            check = await db.get(CommuteCheck, check_id, populate_existing=True)
            plan = await db.get(CommutePlan, check.plan_id, populate_existing=True)
            instance = await db.get(CommuteInstance, check.instance_id, populate_existing=True)
        now = ensure_aware(runtime.now())
        if code and stage == "morning" and plan and instance and plan.revision == check.plan_revision:
            previous = instance.notification_snapshot_json
            reason = "no_usable_route"
            if usable(previous, plan, instance, now):
                saved = instant(previous["saved_at"])
                if saved >= phase_start(plan, instance.local_service_date, "morning"):
                    reason = "current_morning_plan"
                elif (saved >= phase_start(plan, instance.local_service_date, "evening")
                      and saved.astimezone(ZoneInfo(plan.timezone)).date() == instance.local_service_date - timedelta(days=1)):
                    reason = "evening_fallback"
            await queue_stage(db, runtime.settings, plan, instance, stage,
                              previous if reason != "no_usable_route" else None,
                              now=now, check_id=check_id, reason=reason)
    if plan and instance and code:
        decision(plan, instance.local_service_date, stage, code)
    await db.execute(update(CommuteCheck).where(
        CommuteCheck.check_id == check_id, CommuteCheck.status == "running",
        CommuteCheck.lease_owner == worker_id,
    ).values(status="failed" if code else "succeeded", phase="failed" if code else "done", error_code=code,
             finished_at=now, phase_updated_at=now, lease_owner=None, lease_expires_at=None))
    await db.commit()

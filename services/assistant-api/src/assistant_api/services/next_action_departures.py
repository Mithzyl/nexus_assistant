"""Read eligible, previously saved departures without requesting fresh route estimates."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select

from assistant_api.commute.timeutil import ensure_aware
from assistant_api.models import CommuteInstance, CommutePlan, ScheduleRouteResult
from assistant_api.services.schedule_routes import validate_saved_route


@dataclass(frozen=True)
class SavedDeparture:
    """Return a validated departure time plus the owning commute plan when one exists."""

    departure_at: datetime | None = None
    commute_plan_id: str | None = None



def _same_point(point, latitude, longitude, coordinate_system):
    """Require a saved route endpoint to correspond to the authoritative destination."""
    return (isinstance(point, dict) and latitude is not None and longitude is not None
            and type(point.get('latitude')) in (int,float) and type(point.get('longitude')) in (int,float)
            and point.get('latitude') == latitude and point.get('longitude') == longitude
            and point.get('coordinate_system') == coordinate_system)


def _departure(schedule, plan, instance, route):
    """Use the measured commute start as saved, or subtract duration from a matching calendar arrival."""
    duration = route.get('duration_seconds')
    target = route.get('target_arrival_at')
    if (route.get('status') != 'available' or type(duration) is not int or not 0 < duration <= 86400
            or not isinstance(target, str)):
        return None
    try:
        arrival = datetime.fromisoformat(target.replace('Z', '+00:00'))
    except ValueError:
        return None
    if arrival.tzinfo is None or not _same_point(route.get('destination'), schedule.latitude, schedule.longitude,
                                                schedule.coordinate_system):
        return None
    start = ensure_aware(schedule.starts_at)
    if plan is None:
        return (start-timedelta(seconds=duration), arrival) if arrival==start else None
    if (not plan.enabled or plan.matter_id != schedule.matter_id or route.get('mode') != plan.transport_mode
            or not _same_point(route.get('origin'), plan.origin_latitude, plan.origin_longitude, 'gcj02')
            or not _same_point(route.get('destination'),plan.destination_latitude,plan.destination_longitude,'gcj02')):
        return None
    local = arrival.astimezone(ZoneInfo(plan.timezone))
    if instance:
        if (instance.plan_revision != plan.revision or instance.status != 'active'
                or instance.skip_today_at or ensure_aware(instance.target_arrival_at) != arrival):
            return None
    elif (schedule.schedule_id != plan.first_schedule_id
          or local.strftime('%H:%M') != plan.arrival_local_time):
        return None
    if ((plan.effective_from and local.date() < plan.effective_from)
            or local.isoweekday() not in plan.weekdays_json
            or not schedule.ends_at or ensure_aware(schedule.ends_at) != arrival-timedelta(minutes=plan.buffer_minutes)
            or start != ensure_aware(schedule.ends_at)-timedelta(seconds=duration)):
        return None
    return start, arrival


async def saved_departures(db, user, schedules, now):
    """Read only this account's earned departure candidates and reuse route freshness checks."""
    scoped = {s.source_scope_key.removeprefix('commute:') for s in schedules
              if (s.source_scope_key or '').startswith('commute:')}
    ids = [s.schedule_id for s in schedules]
    if not ids:
        return {}
    routes = {r.schedule_id:r for r in await db.scalars(select(ScheduleRouteResult).where(
        ScheduleRouteResult.user_id==user.user_id,ScheduleRouteResult.schedule_id.in_(ids)))}
    plans = list(await db.scalars(select(CommutePlan).where(CommutePlan.user_id==user.user_id,
        or_(CommutePlan.first_schedule_id.in_(ids),CommutePlan.plan_id.in_(scoped)))))
    first = {p.first_schedule_id:p for p in plans}
    plans_by_id = {p.plan_id:p for p in plans}
    instances = {(item.plan_id,item.local_service_date):item for item in await db.scalars(select(CommuteInstance).where(
        CommuteInstance.user_id==user.user_id,CommuteInstance.plan_id.in_(plans_by_id),
        CommuteInstance.target_arrival_at>=now,CommuteInstance.target_arrival_at<now+timedelta(days=2)))} if plans else {}
    result = {}
    for schedule in schedules:
        plan = first.get(schedule.schedule_id)
        scope = schedule.source_scope_key or ''
        if scope.startswith('commute:'):
            plan = plans_by_id.get(scope.removeprefix('commute:'))
            if plan is None:
                continue
        if plan and plan.matter_id != schedule.matter_id:
            continue
        if plan:
            result[schedule.schedule_id] = SavedDeparture(commute_plan_id=plan.plan_id)
        row = routes.get(schedule.schedule_id)
        if row is None:
            continue
        route, _ = await validate_saved_route(db,user,schedule,row,at=now)
        if route is None:
            continue
        instance = None
        if plan:
            try:
                zone = ZoneInfo(plan.timezone)
            except (ValueError, ZoneInfoNotFoundError):
                continue
            # A buffered arrival can be on the next local date.
            arrival_basis = (ensure_aware(schedule.ends_at)+timedelta(minutes=plan.buffer_minutes)
                             if schedule.ends_at else ensure_aware(schedule.starts_at))
            instance = instances.get((plan.plan_id,arrival_basis.astimezone(zone).date()))
        times = _departure(schedule,plan,instance,route)
        if times and now < times[1]:
            result[schedule.schedule_id] = SavedDeparture(times[0],plan.plan_id if plan else None)
    return result

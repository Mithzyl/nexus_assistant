/**
 * Commute-day client and local-date helpers.
 *
 * The three day endpoints take the local service date in a JSON body, not in
 * the path or query. `service_date` must be the calendar date in the plan's own
 * time zone, so it is derived with `Intl` and never by slicing a UTC instant.
 */

import {
  apiRequest,
  pa,
  type CommuteDayChange,
  type CommuteDayQuery,
  type CommuteDayRefresh,
  type CommuteDayState,
  type CommutePlan,
} from "./api";

function planPath(planId: string): string {
  return `/commute-plans/${encodeURIComponent(planId)}`;
}

/** Read one local service date. This is a read-only POST and never saves business data. */
export function queryCommuteDay(
  planId: string,
  serviceDate: string,
  signal?: AbortSignal,
): Promise<CommuteDayState> {
  const body: CommuteDayQuery = { service_date: serviceDate };
  return apiRequest<CommuteDayState>(pa(`${planPath(planId)}/days/query`), {
    method: "POST",
    body,
    signal,
  });
}

/** Change one day's arrival time; the server recomputes start, end and the route. */
export function updateCommuteDay(
  planId: string,
  input: CommuteDayChange,
): Promise<CommuteDayState> {
  return apiRequest<CommuteDayState>(pa(`${planPath(planId)}/days`), {
    method: "PATCH",
    body: input,
  });
}

/** Recompute and persist one future day without touching the long-term plan. */
export function refreshCommuteDay(
  planId: string,
  input: CommuteDayRefresh,
): Promise<CommuteDayState> {
  return apiRequest<CommuteDayState>(pa(`${planPath(planId)}/days/refresh`), {
    method: "POST",
    body: input,
  });
}

/**
 * Return the calendar date of `now` in `timeZone` as `YYYY-MM-DD`.
 *
 * An invalid zone falls back to the device's local date instead of a UTC slice,
 * so the value still means "today where the user is" rather than a shifted day.
 */
export function serviceDateInTimeZone(now: Date, timeZone: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(now);
    const value = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
    return `${value("year")}-${value("month")}-${value("day")}`;
  } catch {
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
    return local.toISOString().slice(0, 10);
  }
}

/** Shift a `YYYY-MM-DD` calendar date by whole days without time-zone drift. */
export function addServiceDays(serviceDate: string, days: number): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(serviceDate);
  if (!match) return serviceDate;
  const shifted = new Date(
    Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + days),
  );
  return shifted.toISOString().slice(0, 10);
}

/** ISO weekday (Monday = 1 … Sunday = 7) for a plain calendar date. */
export function serviceWeekday(serviceDate: string): number {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(serviceDate);
  if (!match) return 0;
  const day = new Date(
    Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])),
  ).getUTCDay();
  return day === 0 ? 7 : day;
}

/** `HH:mm` of `now` in `timeZone`, used only to skip a day that already started. */
export function localTimeInTimeZone(now: Date, timeZone: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(now);
    const value = (type: string) => parts.find((part) => part.type === type)?.value ?? "00";
    return `${value("hour")}:${value("minute")}`;
  } catch {
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
    return local.toISOString().slice(11, 16);
  }
}

/**
 * Pick the next date the plan is configured to run, starting from today locally.
 *
 * When today matches a configured weekday but its arrival time has already
 * passed, the search starts tomorrow so the panel does not open on a past day.
 * This only chooses which date to ask about; the server still decides whether
 * that day is upcoming, past, inactive or ineligible.
 */
export function nextServiceDate(
  plan: Pick<CommutePlan, "timezone" | "weekdays" | "effective_from" | "arrival_local_time">,
  now: Date,
): string {
  const today = serviceDateInTimeZone(now, plan.timezone);
  const nowLocalTime = localTimeInTimeZone(now, plan.timezone);
  const effectiveFrom = plan.effective_from ? String(plan.effective_from).slice(0, 10) : null;
  const weekdays = plan.weekdays ?? [];
  for (let offset = 0; offset < 14; offset += 1) {
    const candidate = addServiceDays(today, offset);
    if (effectiveFrom && candidate < effectiveFrom) continue;
    if (offset === 0 && nowLocalTime >= plan.arrival_local_time) continue;
    if (weekdays.length === 0 || weekdays.includes(serviceWeekday(candidate))) {
      return candidate;
    }
  }
  return today;
}

/** Format a UTC instant in the plan's time zone, e.g. `11月4日 09:50`. */
export function formatInTimeZone(
  value: string | null | undefined,
  timeZone: string,
  withDate = true,
): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone,
      ...(withDate ? { month: "long", day: "numeric" } : {}),
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(date);
  } catch {
    return date.toLocaleString("zh-CN", { hour12: false });
  }
}

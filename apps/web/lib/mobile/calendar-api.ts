/** Private calendar subscription management through the same-origin PA proxy. */

import {
  apiRequest,
  pa,
  type CalendarSubscription,
  type CalendarSubscriptionStatus,
} from "./api";

export function getCalendarSubscription(signal?: AbortSignal): Promise<CalendarSubscriptionStatus> {
  return apiRequest<CalendarSubscriptionStatus>(pa("/calendar-subscription"), { signal });
}

export function createCalendarSubscription(
  matterIds: string[],
): Promise<CalendarSubscription> {
  return apiRequest<CalendarSubscription>(pa("/calendar-subscription"), {
    method: "POST",
    body: { matter_ids: matterIds },
  });
}

export function rotateCalendarSubscription(
  matterIds: string[],
): Promise<CalendarSubscription> {
  return apiRequest<CalendarSubscription>(pa("/calendar-subscription/rotate"), {
    method: "POST",
    body: { matter_ids: matterIds },
  });
}

export function updateCalendarSubscription(input: {
  enabled?: boolean;
  matter_ids?: string[];
}): Promise<CalendarSubscription> {
  return apiRequest<CalendarSubscription>(pa("/calendar-subscription"), {
    method: "PATCH",
    body: input,
  });
}

export function revokeCalendarSubscription(): Promise<CalendarSubscription> {
  return apiRequest<CalendarSubscription>(pa("/calendar-subscription"), {
    method: "DELETE",
  });
}

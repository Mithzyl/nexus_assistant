import { apiRequest, pa, type TodoOperation } from "./api";

export interface ActionTarget {
  kind: "todo" | "schedule";
  record_id: string;
}

export interface TodayAction extends ActionTarget {
  title: string;
  matter_id: string | null;
  matter_version: number | null;
  state: string;
  reason: string;
  commute_plan_id: string | null;
  departure_at: string | null;
  destination: string | null;
  actions: ("view" | "complete" | "reschedule" | "snooze" | "dismiss")[];
}

export interface NextActions {
  recommendation_id: string;
  version: number;
  expires_at: string;
  timezone: string;
  data_status: "ready" | "availability_missing" | "no_current_window" | "calendar_end_unknown";
  primary: TodayAction | null;
  alternatives: TodayAction[];
  suppressed: (ActionTarget & { title: string; state: "snoozed" | "dismissed" })[];
}

export type FeedbackAction = "snooze" | "dismiss" | "resume";

/** Refresh the recommendation separately so a failure cannot hide the timeline. */
export function getNextActions(signal: AbortSignal) {
  return apiRequest<NextActions>(pa("/next-actions/query"), { method: "POST", signal });
}

/** Apply a recommendation-only change against the response that supplied the target. */
export function sendNextActionFeedback(snapshot: NextActions, target: ActionTarget, action: FeedbackAction, key: string, until?: string) {
  return apiRequest<{ refresh_required: true }>(pa("/next-actions/commands"), {
    method: "POST", body: {
      recommendation_id: snapshot.recommendation_id,
      expected_recommendation_version: snapshot.version, kind: target.kind, record_id: target.record_id,
      action, idempotency_key: key, ...(until ? { until } : {}),
    },
  });
}

/** Complete the real todo against the parent version that the recommendation observed. */
export async function completeRecommendedTodo(id: string, key: string, version: number | null) {
  // The recommendation already carries the observed parent version. Require it
  // so an old button cannot silently update a record edited elsewhere.
  if (version === null) throw new Error("待办版本不可用，请刷新后再试。");
  return apiRequest<TodoOperation>(pa(`/todos/${encodeURIComponent(id)}`), {
    method: "PATCH", body: { status: "done", expected_matter_version: version, idempotency_key: key },
  });
}

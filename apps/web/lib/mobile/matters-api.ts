/** Real matter/draft/today client. All paths go through the same-origin proxy. */

import {
  apiRequest,
  ApiError,
  type WorkflowError,
  pa,
  type DraftConfirmInput,
  type DraftConfirmResponse,
  type DraftPatchInput,
  type FollowUpExecutionRead,
  type FollowUpPatchInput,
  type FollowUpRead,
  type InitialDeleteResponse,
  type MatterDetail,
  type MatterSupplement,
  type SupplementCreateInput,
  type SupplementRelationFilter,
  type SupplementRelationsPage,
  type MatterChange,
  type MatterDraft,
  type MatterEditInput,
  type MatterGroup,
  type MatterIntakeResponse,
  type MatterIntakeResult,
  type MatterListResponse,
  type MatterReminder,
  type MatterSummary,
  type MatterTodo,
  type MatterUpdateInput,
  type MatterVersionInput,
  type MapPickResponse,
  type RevertResponse,
  type ScheduleCreateInput,
  type ScheduleOperation,
  type TodoCreateInput,
  type TodoOperation,
  type PlaceSearchResponse,
  type RouteEstimate,
  type ScheduledRoute,
  type TodayView,
} from "./api";

function id(value: string): string {
  return encodeURIComponent(value);
}

export function listMatters(
  group: MatterGroup,
  cursor?: string | null,
  signal?: AbortSignal,
): Promise<MatterListResponse> {
  const query = new URLSearchParams({ group, limit: "20" });
  if (cursor) query.set("cursor", cursor);
  return apiRequest<MatterListResponse>(pa(`/matters?${query.toString()}`), { signal });
}

export function getMatter(matterId: string, signal?: AbortSignal): Promise<MatterDetail> {
  return apiRequest<MatterDetail>(pa(`/matters/${id(matterId)}`), { signal });
}

export function listMatterChanges(
  matterId: string,
  signal?: AbortSignal,
): Promise<MatterChange[]> {
  return apiRequest<MatterChange[]>(pa(`/matters/${id(matterId)}/changes`), { signal });
}

/** Open an existing schedule or todo as a structured server preview. */
export function createMatterEditDraft(
  matterId: string,
  input: MatterEditInput,
): Promise<MatterDraft> {
  return apiRequest<MatterDraft>(pa(`/matters/${id(matterId)}/drafts`), {
    method: "POST",
    body: input,
  });
}

export function updateMatter(matterId: string, input: MatterUpdateInput): Promise<MatterSummary> {
  return apiRequest<MatterSummary>(pa(`/matters/${id(matterId)}`), {
    method: "PATCH",
    body: input,
  });
}

export function archiveMatter(matterId: string, input: MatterVersionInput): Promise<MatterSummary> {
  return apiRequest<MatterSummary>(pa(`/matters/${id(matterId)}/archive`), {
    method: "POST",
    body: { cancel_reminders: false, ...input },
  });
}

export function reopenMatter(matterId: string, input: MatterVersionInput): Promise<MatterSummary> {
  return apiRequest<MatterSummary>(pa(`/matters/${id(matterId)}/reopen`), {
    method: "POST",
    body: input,
  });
}

export function deleteMatter(matterId: string): Promise<unknown> {
  return apiRequest<unknown>(pa(`/matters/${id(matterId)}`), { method: "DELETE" });
}

/** Delete one untouched initial create; the server re-checks eligibility and rolls back on conflict. */
export function initialDeleteMatter(matterId: string): Promise<InitialDeleteResponse> {
  return apiRequest<InitialDeleteResponse>(pa(`/matters/${id(matterId)}/initial`), {
    method: "DELETE",
  });
}

/** Mark one due reminder as seen; repeated calls return the same acknowledged state. */
export function acknowledgeReminder(reminderId: string): Promise<MatterReminder> {
  return apiRequest<MatterReminder>(pa(`/reminders/${id(reminderId)}/acknowledge`), {
    method: "POST",
  });
}

export function completeMatterTodo(matterId: string, todoId: string): Promise<MatterTodo> {
  return apiRequest<MatterTodo>(pa(`/matters/${id(matterId)}/todos/${id(todoId)}/complete`), {
    method: "POST",
  });
}

export function reopenMatterTodo(matterId: string, todoId: string): Promise<MatterTodo> {
  return apiRequest<MatterTodo>(pa(`/matters/${id(matterId)}/todos/${id(todoId)}/reopen`), {
    method: "POST",
  });
}

/**
 * Create one todo in a single business call.
 *
 * Pass `matter_id` with its current `expected_matter_version` to add the todo to
 * an existing matter; omit both to let the server create the owning matter.
 */
export function createTodo(input: TodoCreateInput): Promise<TodoOperation> {
  return apiRequest<TodoOperation>(pa("/todos"), { method: "POST", body: input });
}

/**
 * Create one schedule without creating a todo.
 *
 * The server rejects changes to time or place on commute-managed schedules;
 * those belong to the commute-day endpoints.
 */
export function createSchedule(input: ScheduleCreateInput): Promise<ScheduleOperation> {
  return apiRequest<ScheduleOperation>(pa("/schedules"), { method: "POST", body: input });
}

/**
 * Save one supplement before the AI analysis runs.
 *
 * The caller keeps the returned supplement and polls it separately; this call
 * never waits for model output and never changes schedules or todos.
 */
export function saveMatterSupplement(
  matterId: string,
  input: SupplementCreateInput,
): Promise<MatterSupplement> {
  return apiRequest<MatterSupplement>(pa(`/matters/${id(matterId)}/supplements`), {
    method: "POST",
    body: input,
  });
}

/** Read supplements newest-first; `beforeId` pages further back. */
export function listMatterSupplements(
  matterId: string,
  options: { limit?: number; beforeId?: string | null } = {},
  signal?: AbortSignal,
): Promise<MatterSupplement[]> {
  const query = new URLSearchParams({ limit: String(options.limit ?? 20) });
  if (options.beforeId) query.set("before_id", options.beforeId);
  return apiRequest<MatterSupplement[]>(
    pa(`/matters/${id(matterId)}/supplements?${query.toString()}`),
    { signal },
  );
}

/** Read one supplement and its analysis state without starting new model work. */
export function getMatterSupplement(
  matterId: string,
  supplementId: string,
  signal?: AbortSignal,
): Promise<MatterSupplement> {
  return apiRequest<MatterSupplement>(
    pa(`/matters/${id(matterId)}/supplements/${id(supplementId)}`),
    { signal },
  );
}

/** Record that the user accepted one assistant-written text; does not execute it. */
export function acceptSupplementText(
  matterId: string,
  supplementId: string,
  textId: string,
): Promise<MatterSupplement> {
  return apiRequest<MatterSupplement>(
    pa(`/matters/${id(matterId)}/supplements/${id(supplementId)}/texts/${id(textId)}/accept`),
    { method: "POST", body: {} },
  );
}

/** Queue one explicit retry after a definite failure; unknown results cannot retry. */
export function retrySupplementAnalysis(
  matterId: string,
  supplementId: string,
): Promise<MatterSupplement> {
  return apiRequest<MatterSupplement>(
    pa(`/matters/${id(matterId)}/supplements/${id(supplementId)}/retry`),
    { method: "POST", body: {} },
  );
}

/**
 * Read one page of corrections, answers or legacy links for a single text.
 *
 * `beforeId` is the last relation text id, not a supplement id; reuse the same
 * filter when following `next_before_id`. This read never starts AI work.
 */
export function listSupplementTextRelations(
  matterId: string,
  supplementId: string,
  textId: string,
  options: {
    relationType?: SupplementRelationFilter | null;
    limit?: number;
    beforeId?: string | null;
  } = {},
  signal?: AbortSignal,
): Promise<SupplementRelationsPage> {
  const query = new URLSearchParams({ limit: String(options.limit ?? 20) });
  if (options.relationType) query.set("relation_type", options.relationType);
  if (options.beforeId) query.set("before_id", options.beforeId);
  return apiRequest<SupplementRelationsPage>(
    pa(
      `/matters/${id(matterId)}/supplements/${id(supplementId)}/texts/${id(textId)}/relations?${query.toString()}`,
    ),
    { signal },
  );
}

export function getToday(date?: string, signal?: AbortSignal): Promise<TodayView> {
  const query = date ? `?date=${encodeURIComponent(date)}` : "";
  return apiRequest<TodayView>(pa(`/today${query}`), { signal });
}

export function listDrafts(
  status: "preview" | "confirmed" | "cancelled" = "preview",
  matterId?: string | null,
  signal?: AbortSignal,
): Promise<MatterDraft[]> {
  const query = new URLSearchParams({ status, limit: "20" });
  if (matterId) query.set("matter_id", matterId);
  return apiRequest<MatterDraft[]>(pa(`/matters/drafts?${query.toString()}`), { signal });
}

export function getDraft(draftId: string, signal?: AbortSignal): Promise<MatterDraft> {
  return apiRequest<MatterDraft>(pa(`/matters/drafts/${id(draftId)}`), { signal });
}

export function patchDraft(draftId: string, input: DraftPatchInput): Promise<MatterDraft> {
  return apiRequest<MatterDraft>(pa(`/matters/drafts/${id(draftId)}`), {
    method: "PATCH",
    body: input,
  });
}

export function selectDraftMatter(
  draftId: string,
  expectedVersion: number,
  matterId: string,
): Promise<MatterDraft> {
  return apiRequest<MatterDraft>(pa(`/matters/drafts/${id(draftId)}/select-matter`), {
    method: "POST",
    body: { expected_version: expectedVersion, matter_id: matterId },
  });
}

export function applyDraftOption(
  draftId: string,
  expectedVersion: number,
  optionId: "A" | "B" | "C",
): Promise<MatterDraft> {
  return apiRequest<MatterDraft>(pa(`/matters/drafts/${id(draftId)}/apply-option`), {
    method: "POST",
    body: { expected_version: expectedVersion, option_id: optionId },
  });
}

export function confirmDraft(
  draftId: string,
  input: DraftConfirmInput,
  idempotencyKey: string,
): Promise<DraftConfirmResponse> {
  return apiRequest<DraftConfirmResponse>(pa(`/matters/drafts/${id(draftId)}/confirm`), {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: input,
  });
}

export function cancelDraft(draftId: string): Promise<MatterDraft> {
  return apiRequest<MatterDraft>(pa(`/matters/drafts/${id(draftId)}/cancel`), { method: "POST" });
}

export function intakeMatter(
  text: string,
  idempotencyKey: string,
  continuationRunId?: string | null,
  memoryDisclosures: import("@/components/mobile/account-memory").SelectedMemory[] = [],
): Promise<MatterIntakeResponse> {
  return apiRequest<MatterIntakeResponse>(pa("/matters/intake"), {
    method: "POST",
    body: {
      text,
      idempotency_key: idempotencyKey,
      memory_disclosures: memoryDisclosures,
      ...(continuationRunId ? { continuation_run_id: continuationRunId } : {}),
    },
  });
}

/** Persist captured text before returning; the Assistant worker continues independently. */
export function submitCapture(text: string, idempotencyKey: string): Promise<MatterIntakeResponse> {
  return apiRequest<MatterIntakeResponse>(pa("/matters/intake"), {
    method: "POST",
    body: { text, idempotency_key: idempotencyKey, entry_mode: "async_capture" },
  });
}

/** Read progress for the original submission without resending its input or starting work. */
export function getIntakeStatus(idempotencyKey: string, signal?: AbortSignal): Promise<MatterIntakeResult> {
  return apiRequest<MatterIntakeResult>(pa("/matters/intake/status"), {
    method: "POST",
    body: { idempotency_key: idempotencyKey },
    signal,
  });
}

/** Persist dismissal for this account so refresh cannot reopen the same question. */
export function dismissIntakeReply(localRunId: string): Promise<void> {
  return apiRequest<void>(pa(`/matters/intake/${id(localRunId)}/dismiss-reply`), { method: "POST" });
}

export function listIntakeResults(limit = 20, signal?: AbortSignal): Promise<MatterIntakeResult[]> {
  return apiRequest<MatterIntakeResult[]>(pa(`/matters/intake?limit=${limit}`), { signal });
}

export function listFollowUps(
  matterId?: string | null,
  state?: "enabled" | "paused" | "closed" | null,
  limit = 20,
  signal?: AbortSignal,
): Promise<FollowUpRead[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (matterId) query.set("matter_id", matterId);
  if (state) query.set("state", state);
  return apiRequest<FollowUpRead[]>(pa(`/follow-ups?${query.toString()}`), { signal });
}

export function getFollowUp(
  followUpId: string,
  signal?: AbortSignal,
): Promise<FollowUpRead> {
  return apiRequest<FollowUpRead>(pa(`/follow-ups/${id(followUpId)}`), { signal });
}

export function patchFollowUp(
  followUpId: string,
  input: FollowUpPatchInput,
): Promise<FollowUpRead> {
  return apiRequest<FollowUpRead>(pa(`/follow-ups/${id(followUpId)}`), {
    method: "PATCH",
    body: input,
  });
}

export function listFollowUpExecutions(
  followUpId: string,
  limit = 20,
  signal?: AbortSignal,
): Promise<FollowUpExecutionRead[]> {
  return apiRequest<FollowUpExecutionRead[]>(
    pa(`/follow-ups/${id(followUpId)}/executions?limit=${limit}`),
    { signal },
  );
}

export function getIntakeResult(
  localRunId: string,
  signal?: AbortSignal,
): Promise<MatterIntakeResult> {
  return apiRequest<MatterIntakeResult>(pa(`/matters/intake/${id(localRunId)}`), { signal });
}

export function searchPlaces(
  query: string,
  city: string | null,
  searchType: "place" | "address" = "place",
  draftId?: string | null,
): Promise<PlaceSearchResponse> {
  return apiRequest<PlaceSearchResponse>(pa("/commute/places/search"), {
    method: "POST",
    body: {
      query,
      ...(city?.trim() ? { city: city.trim() } : {}),
      search_type: searchType,
      limit: 5,
      ...(draftId ? { draft_id: draftId } : {}),
    },
  });
}

/** Reverse geocode one explicit map center for the current server draft. */
export function pickMapLocation(
  draftId: string,
  latitude: number,
  longitude: number,
): Promise<MapPickResponse> {
  return apiRequest<MapPickResponse>(pa("/commute/places/map-pick"), {
    method: "POST",
    body: {
      draft_id: draftId,
      latitude,
      longitude,
      coordinate_system: "gcj02",
    },
  });
}

export function confirmPlaceSelection(
  draftId: string,
  selectionToken: string,
): Promise<MatterDraft> {
  return apiRequest<MatterDraft>(pa("/commute/places/confirm"), {
    method: "POST",
    body: { draft_id: draftId, selection_token: selectionToken },
  });
}

export function estimateRoute(input: {
  origin?: { latitude: number; longitude: number; coordinate_system: string; city?: string | null };
  origin_schedule_id?: string;
  destination: { latitude: number; longitude: number; coordinate_system: string; city?: string | null };
  mode: import("./api").CommuteTransportMode;
  depart_at?: string;
  arrive_by?: string;
  schedule_id?: string;
  origin_basis?: "user_confirmed" | "previous_schedule";
}): Promise<RouteEstimate> {
  return apiRequest<RouteEstimate>(pa("/commute/estimate"), {
    method: "POST",
    body: input,
  });
}

/** Read one persisted route shape without triggering a new route estimate. */
export function getScheduleRoute(
  scheduleId: string,
  signal?: AbortSignal,
): Promise<ScheduledRoute> {
  return apiRequest<ScheduledRoute>(
    pa(`/commute/routes/${id(scheduleId)}`),
    { signal },
  );
}

export function intakeMatterProgress(
  matterId: string,
  text: string,
  idempotencyKey: string,
  continuationRunId?: string | null,
): Promise<MatterIntakeResponse> {
  return apiRequest<MatterIntakeResponse>(pa(`/matters/${id(matterId)}/intake`), {
    method: "POST",
    body: {
      text,
      idempotency_key: idempotencyKey,
      ...(continuationRunId ? { continuation_run_id: continuationRunId } : {}),
    },
  });
}

export function revertMatterChange(
  matterId: string,
  changeId: string,
  expectedVersion: number,
): Promise<RevertResponse> {
  return apiRequest<RevertResponse>(
    pa(`/matters/${matterId}/changes/${id(changeId)}/revert`),
    { method: "POST", body: { expected_version: expectedVersion } },
  );
}

/** Turn a draft confirmation into a brief result for the toast. */
export function confirmSummary(response: DraftConfirmResponse): string {
  if (!response.applied) return "这次没保存，请检查草稿";
  const conflict = response.conflict?.has_conflict ? "，留意时间冲突" : "";
  if (!response.schedule) return `已更新${conflict}`;
  const title = Array.from(response.schedule.title.trim().replace(/\s+/g, " "));
  if (title.length === 0) return `日程已更新${conflict}`;
  const available = 18 - "日程「」已更新".length - conflict.length;
  const shortTitle = title.length > available ? title.slice(0, available - 1).join("") + "…" : title.join("");
  return `日程「${shortTitle}」已更新${conflict}`;
}

export const TODAY_REFRESH_EVENT = "pa:today-refresh";

/** Ask the mounted Today screen to reread server state after a confirmed edit. */
export function requestTodayRefresh(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(TODAY_REFRESH_EVENT));
  }
}

export function newOperationKey(prefix: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`.slice(0, 128);
}

export class IntakeStreamError extends ApiError {
  localRunId: string | null;
  workflowExecutionId: string | null;
  outcome: string;
  workflowError: WorkflowError;
  pendingContext?: import("./api").IntakePendingContext;
  constructor(data: {pending_context?: import("./api").IntakePendingContext; error: WorkflowError; local_run_id?: string | null; workflow_execution_id?: string | null; outcome?: string}) {
    const e = data.error;
    super(e.outcome_is_known ? 502 : 504, e.error_code, e.public_message, e.is_retryable);
    this.workflowError = e;
    this.pendingContext = data.pending_context;
    this.localRunId = data.local_run_id ?? null;
    this.workflowExecutionId = data.workflow_execution_id ?? null;
    this.outcome = data.outcome ?? (e.outcome_is_known ? "failed" : "unknown");
  }
}

function interruptedStream(): IntakeStreamError {
  return new IntakeStreamError({error:{error_code:"assistant_stream_interrupted",error_type:"transport_error",
    public_message:"连接中断了，还没拿到结果。内容留着，稍后再核对。",
    is_retryable:false,outcome_is_known:false,details_reference_id:null}});
}

/** Read one SSE frame; comment lines such as `: heartbeat` carry no event. */
function readSseFrame(frame: string): { type: string; data: string } | null {
  let type = "message";
  const data: string[] = [];
  for (const rawLine of frame.split("\n")) {
    const line = rawLine.replace(/\r$/, "");
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") type = value;
    if (field === "data") data.push(value);
  }
  return data.length > 0 ? { type, data: data.join("\n") } : null;
}

/**
 * Stream one intake turn and return its persisted result.
 *
 * `answer` frames carry the whole public answer so far and replace the draft
 * text; `accepted` only means the request arrived and never renders as a
 * business result. A broken stream keeps the caller's input and idempotency key
 * so the same run can be recovered instead of dispatched again.
 */
export async function streamIntakeMatter(text: string, idempotencyKey: string,
  continuationRunId: string | null | undefined,
  memoryDisclosures: import("@/components/mobile/account-memory").SelectedMemory[],
  onAnswer: (text: string) => void, ignorePendingReply = false, dismissedReplyRunId?: string | null): Promise<MatterIntakeResponse> {
  const response = await fetch(pa("/matters/intake/stream"), {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ text, idempotency_key: idempotencyKey, entry_mode: "sync_chat",
      continuation_run_id: continuationRunId, memory_disclosures: memoryDisclosures,
      ...(dismissedReplyRunId
        ? { ignore_pending_clarification: true, dismissed_reply_run_id: dismissedReplyRunId }
        : ignorePendingReply ? { ignore_pending_reply: true } : {}) }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, body?.error?.code ?? "assistant_connection_failed",
      body?.error?.message ?? "暂时连不上助手，内容先留着。", body?.error?.retryable === true);
  }
  if (!response.body) throw interruptedStream();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = readSseFrame(frame);
        if (!parsed) continue;
        let data: Record<string, unknown>;
        try {
          data = JSON.parse(parsed.data);
        } catch {
          continue;
        }
        if (parsed.type === "accepted") continue;
        if (parsed.type === "answer") {
          onAnswer(String(data.text ?? ""));
          continue;
        }
        if (parsed.type === "result") return data as unknown as MatterIntakeResponse;
        if (parsed.type === "error") {
          if (data.error) throw new IntakeStreamError(data as { error: WorkflowError });
          throw interruptedStream();
        }
      }
      if (done) throw interruptedStream();
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw interruptedStream();
  } finally { reader.releaseLock(); }
}

/** Real matter/draft/today client. All paths go through the same-origin proxy. */

import {
  apiRequest,
  pa,
  type DraftConfirmInput,
  type DraftConfirmResponse,
  type DraftPatchInput,
  type MatterDetail,
  type MatterChange,
  type MatterDraft,
  type MatterEditInput,
  type MatterGroup,
  type MatterIntakeResponse,
  type MatterListResponse,
  type MatterSummary,
  type MatterTodo,
  type MatterUpdateInput,
  type MatterVersionInput,
  type RevertResponse,
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

export function intakeMatter(text: string, idempotencyKey: string): Promise<MatterIntakeResponse> {
  return apiRequest<MatterIntakeResponse>(pa("/matters/intake"), {
    method: "POST",
    body: { text, idempotency_key: idempotencyKey },
  });
}

export function intakeMatterProgress(
  matterId: string,
  text: string,
  idempotencyKey: string,
): Promise<MatterIntakeResponse> {
  return apiRequest<MatterIntakeResponse>(pa(`/matters/${id(matterId)}/intake`), {
    method: "POST",
    body: { text, idempotency_key: idempotencyKey },
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

export function confirmSummary(response: DraftConfirmResponse): string {
  if (!response.applied) return "服务器没有确认这次写入，请重新检查草稿。";
  const parts: string[] = [];
  if (response.schedule) parts.push(`日程「${response.schedule.title}」已更新`);
  if (response.reminders.length > 0) parts.push(`${response.reminders.length} 个提醒已保存`);
  if (response.conflict?.has_conflict) parts.push("仍有一处时间冲突需要留意");
  if (response.change) parts.push("变更已写入历史");
  return parts.length > 0 ? parts.join("；") : "事项已更新";
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

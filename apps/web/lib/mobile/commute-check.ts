/** Durable client record for one explicit "重新分析" operation. */

import type { CheckStatus } from "./api";
import { readJson, writeJson, commuteCheckKey } from "./storage";

export type PendingState = "submitting" | "uncertain" | CheckStatus;

export interface PendingCommuteCheck {
  planId: string;
  planRevision: number;
  idempotencyKey: string;
  requestHash: string;
  checkId: string | null;
  status: PendingState;
  advisoryId: string | null;
  error: string | null;
  errorCode: string | null;
  createdAt: string;
  updatedAt: string;
  /** Timestamps copied from the server check when available. */
  serverCreatedAt?: string | null;
  finishedAt?: string | null;
}

export const TERMINAL_CHECK_STATUSES: CheckStatus[] = [
  "succeeded",
  "degraded",
  "failed",
  "cancelled",
];

export const CHECK_STATUS_LABEL: Record<PendingState, string> = {
  submitting: "请求已提交",
  uncertain: "结果暂不确定",
  queued: "等待处理",
  running: "处理中",
  succeeded: "已完成",
  degraded: "已完成（数据不完整）",
  failed: "失败",
  cancelled: "已取消",
};

export function loadPendingCheck(
  accountId: string | null,
  planId: string,
): PendingCommuteCheck | null {
  const stored = readJson<PendingCommuteCheck | null>(
    commuteCheckKey(accountId, planId),
    null,
  );
  if (!stored || stored.planId !== planId) return null;
  return {
    ...stored,
    errorCode: stored.errorCode ?? null,
    advisoryId: stored.advisoryId ?? null,
    serverCreatedAt: stored.serverCreatedAt ?? null,
    finishedAt: stored.finishedAt ?? null,
  };
}

export function savePendingCheck(
  accountId: string | null,
  pending: PendingCommuteCheck,
): void {
  writeJson(commuteCheckKey(accountId, pending.planId), pending);
}

export function clearPendingCheck(accountId: string | null, planId: string): void {
  writeJson(commuteCheckKey(accountId, planId), null);
}

export function isTerminal(status: PendingState): boolean {
  return status === "succeeded" || status === "degraded" || status === "failed" || status === "cancelled";
}

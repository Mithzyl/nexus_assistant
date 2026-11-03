/** Durable client record for one explicit "重新分析" operation. */

import type { CheckPhase, CheckStatus } from "./api";
import { readJson, writeJson, commuteCheckKey } from "./storage";

export type PendingState = "submitting" | "uncertain" | CheckStatus;

export interface PendingCommuteCheck {
  serviceDate?: string | null;
  targetArrivalAt?: string | null;
  planId: string;
  planRevision: number;
  idempotencyKey: string;
  requestHash: string;
  checkId: string | null;
  status: PendingState;
  /** Server-reported step; never inferred by the browser. */
  phase?: CheckPhase | null;
  analysisMode?: "full" | "weather_only" | null;
  phaseUpdatedAt?: string | null;
  attemptCount?: number | null;
  nextAttemptAt?: string | null;
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
  "unknown",
];

export const CHECK_STATUS_LABEL: Record<PendingState, string> = {
  submitting: "请求已提交",
  uncertain: "结果暂不确定",
  queued: "等待处理",
  running: "后台智能分析中",
  succeeded: "已完成",
  degraded: "已完成（数据不完整）",
  failed: "失败",
  cancelled: "已取消",
  unknown: "结果未知",
};

/** Keep phase text factual; a missing phase is not "analyzing". */
export const CHECK_PHASE_LABEL: Record<CheckPhase, string> = {
  queued: "已提交，马上开始",
  fetching_route: "正在查路线和路况",
  fetching_weather: "正在核对通勤时段天气",
  analyzing: "正在整理出发时间",
  saving: "正在保存这次结果",
  retry_wait: "路线数据还没回来，稍后自动重试",
  done: "已经完成",
  failed: "没有完成",
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
    phase: stored.phase ?? null,
    analysisMode: stored.analysisMode ?? null,
    phaseUpdatedAt: stored.phaseUpdatedAt ?? null,
    attemptCount: stored.attemptCount ?? null,
    nextAttemptAt: stored.nextAttemptAt ?? null,
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
  return (
    status === "succeeded" ||
    status === "degraded" ||
    status === "failed" ||
    status === "cancelled" ||
    status === "unknown"
  );
}

"use client";

/**
 * Read-only view of the commute runtime endpoint.
 *
 * `worker.state` is the server's classification at response time. The page
 * ages `last_seen_at` against `server_time` and the local monotonic clock so a
 * once-online process does not stay "recent" forever, and it never treats a
 * failed runtime request as proof that the worker is offline.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiRequest, type CommuteRuntime } from "./api";

const RUNTIME_PATH = "/api/pa/commute/runtime";
const POLL_MS = 20_000;
const CLOCK_MS = 5_000;

function monotonicNow(): number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

function retryDelay(failures: number): number {
  if (failures <= 0) return POLL_MS;
  return Math.min(60_000, POLL_MS * 2 ** Math.min(failures - 1, 2));
}

export interface CommuteRuntimeState {
  data: CommuteRuntime | null;
  error: ApiError | Error | null;
  loading: boolean;
  receivedAt: number | null;
  lastSuccessAt: number | null;
  lastAttemptAt: number | null;
  /** Server time advanced by local monotonic elapsed time. */
  serverNow: number | null;
  refresh: () => void;
}

export function useCommuteRuntime({
  enabled,
  accountId,
  planId,
  active,
  onUnauthorized,
}: {
  enabled: boolean;
  accountId: string | null;
  planId: string | null;
  active: boolean;
  onUnauthorized?: () => void;
}): CommuteRuntimeState {
  const key = `${accountId ?? "guest"}:${planId ?? "none"}`;
  const keyRef = useRef(key);
  keyRef.current = key;
  const activeRef = useRef(active);
  activeRef.current = active;

  const [data, setData] = useState<CommuteRuntime | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [receivedAt, setReceivedAt] = useState<number | null>(null);
  const [receivedMono, setReceivedMono] = useState<number | null>(null);
  const [lastSuccessAt, setLastSuccessAt] = useState<number | null>(null);
  const [lastAttemptAt, setLastAttemptAt] = useState<number | null>(null);
  const [clock, setClock] = useState(() => ({ mono: monotonicNow() }));
  const [tickToken, setTickToken] = useState(0);

  // A plan/account change must not inherit the previous status or an in-flight
  // response. The fetch effect below starts a new request after this reset.
  useEffect(() => {
    setData(null);
    setError(null);
    setLoading(false);
    setReceivedAt(null);
    setReceivedMono(null);
    setLastSuccessAt(null);
    setLastAttemptAt(null);
  }, [key]);

  useEffect(() => {
    const id = window.setInterval(() => setClock({ mono: monotonicNow() }), CLOCK_MS);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!enabled || !accountId || !planId) return;
    if (typeof document === "undefined") return;

    let cancelled = false;
    let timer: number | null = null;
    let requestSeq = 0;
    let controller: AbortController | null = null;
    let failures = 0;

    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };
    const schedule = (delay: number) => {
      clearTimer();
      timer = window.setTimeout(() => void tick(), delay);
    };

    async function tick() {
      if (cancelled) return;
      if (document.visibilityState === "hidden") return;
      const mySeq = ++requestSeq;
      controller?.abort();
      const nextController = new AbortController();
      controller = nextController;
      setLoading(true);
      setLastAttemptAt(Date.now());
      try {
        const payload = await apiRequest<CommuteRuntime>(RUNTIME_PATH, {
          signal: nextController.signal,
        });
        if (cancelled || requestSeq !== mySeq || keyRef.current !== key) return;
        const mono = monotonicNow();
        const wall = Date.now();
        setData(payload);
        setError(null);
        setReceivedMono(mono);
        setReceivedAt(wall);
        setLastSuccessAt(wall);
        setClock({ mono });
        failures = 0;
      } catch (caught) {
        if (cancelled || requestSeq !== mySeq || keyRef.current !== key) return;
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        failures += 1;
        setError(caught instanceof Error ? caught : new Error("后台状态暂时无法读取。"));
        if (caught instanceof ApiError && caught.status === 401) onUnauthorized?.();
      } finally {
        if (!cancelled && requestSeq === mySeq && keyRef.current === key) {
          setLoading(false);
          if (activeRef.current) schedule(retryDelay(failures));
        }
      }
    }

    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        clearTimer();
      } else {
        void tick();
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    void tick();

    return () => {
      cancelled = true;
      requestSeq += 1;
      controller?.abort();
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [accountId, active, enabled, key, onUnauthorized, planId, tickToken]);

  const refresh = useCallback(() => setTickToken((value) => value + 1), []);

  const serverNow =
    data && receivedMono !== null
      ? Date.parse(data.server_time) + (clock.mono - receivedMono)
      : null;

  // Before the first effect tick there is no error and no attempt yet; show
  // "loading", not "unavailable", to avoid a false negative flash.
  const initialPending = enabled && !data && !error && lastAttemptAt === null;

  return {
    data,
    error,
    loading: loading || initialPending,
    receivedAt,
    lastSuccessAt,
    lastAttemptAt,
    serverNow: Number.isFinite(serverNow) ? serverNow : null,
    refresh,
  };
}

export type RuntimeWorkerKind =
  | "loading"
  | "recent"
  | "stale"
  | "not_seen"
  | "unavailable";

export interface RuntimeWorkerView {
  kind: RuntimeWorkerKind;
  label: string;
  detail: string;
  tone: "fog" | "amber" | "neutral";
  ageSeconds: number | null;
  lastSeenAt: string | null;
  lastSuccessAt: string | null;
  routeAvailable: boolean | null;
  isCurrent: boolean;
}

function ageText(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "";
  const value = Math.max(0, Math.round(seconds));
  if (value < 60) return `约 ${value} 秒前`;
  if (value < 3600) return `约 ${Math.floor(value / 60)} 分钟前`;
  return `约 ${Math.floor(value / 3600)} 小时前`;
}

/** Turn the three independent signals into one honest worker summary. */
export function describeCommuteRuntime({
  data,
  error,
  serverNow,
  lastSuccessAt,
  loading,
}: {
  data: CommuteRuntime | null;
  error: ApiError | Error | null;
  serverNow: number | null;
  lastSuccessAt: number | null;
  loading: boolean;
}): RuntimeWorkerView {
  const lastSuccess = lastSuccessAt ? new Date(lastSuccessAt).toISOString() : null;
  const routeAvailable = data ? data.route_provider_available : null;

  if (error) {
    return {
      kind: "unavailable",
      label: "暂时无法获取后台状态",
      detail: "暂时无法获取后台状态；任务已保存，不会因此判定失败。",
      tone: "amber",
      ageSeconds: null,
      lastSeenAt: data?.worker.last_seen_at ?? null,
      lastSuccessAt: lastSuccess,
      routeAvailable,
      isCurrent: false,
    };
  }

  if (!data) {
    return {
      kind: loading ? "loading" : "unavailable",
      label: loading ? "正在获取后台状态…" : "暂时无法获取后台状态",
      detail: loading
        ? "正在获取后台状态；任务状态以服务器保存的检查为准。"
        : "暂时无法获取后台状态；任务已保存，不会因此判定失败。",
      tone: loading ? "neutral" : "amber",
      ageSeconds: null,
      lastSeenAt: null,
      lastSuccessAt: lastSuccess,
      routeAvailable: null,
      isCurrent: false,
    };
  }

  const seen = data.worker.last_seen_at ? Date.parse(data.worker.last_seen_at) : NaN;
  const ageSeconds =
    Number.isFinite(seen) && serverNow !== null
      ? Math.max(0, (serverNow - seen) / 1000)
      : null;
  const staleAfter =
    Number.isFinite(data.stale_after_seconds) && data.stale_after_seconds > 0
      ? data.stale_after_seconds
      : 60;

  if (data.worker.state === "not_seen" || !Number.isFinite(seen)) {
    return {
      kind: "not_seen",
      label: "尚未收到执行程序的响应",
      detail: "尚未收到后台执行程序的响应；任务已保存，后台启动后会继续处理。",
      tone: "amber",
      ageSeconds: null,
      lastSeenAt: null,
      lastSuccessAt: lastSuccess,
      routeAvailable,
      isCurrent: true,
    };
  }

  if (data.worker.state === "stale" || (ageSeconds !== null && ageSeconds > staleAfter)) {
    return {
      kind: "stale",
      label: `近期未响应${ageSeconds !== null ? `（上次响应${ageText(ageSeconds)}）` : ""}`,
      detail: "后台执行程序近期未响应；任务已保存，不会因此判定失败。",
      tone: "amber",
      ageSeconds,
      lastSeenAt: data.worker.last_seen_at,
      lastSuccessAt: lastSuccess,
      routeAvailable,
      isCurrent: true,
    };
  }

  return {
    kind: "recent",
    label: `最近有响应${ageSeconds !== null ? `（${ageText(ageSeconds)}）` : ""}`,
    detail: "后台执行程序最近有响应；这不代表当前任务已经开始。",
    tone: "fog",
    ageSeconds,
    lastSeenAt: data.worker.last_seen_at,
    lastSuccessAt: lastSuccess,
    routeAvailable,
    isCurrent: true,
  };
}

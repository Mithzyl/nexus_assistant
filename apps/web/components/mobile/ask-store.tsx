"use client";

/**
 * Ask state machine shared by the whole shell.
 *
 * A submission gets one stable idempotency key that survives retries and reloads.
 * The SSE stream is used for progress and final-state discovery, but the answer
 * itself always comes from `GET /asks/{id}`; a finite stream ending is not treated
 * as completion.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  ApiError,
  apiRequest,
  pa,
  type AskCreated,
  type AskRead,
  type AskVerifyResponse,
  type RunStatus,
} from "@/lib/mobile/api";
import { accountKey, readJson, writeJson } from "@/lib/mobile/storage";
import { uuid } from "@/lib/mobile/format";
import { useAuth } from "./providers";

/** One screen can hold more than the server's newest page (20). */
const MAX_CACHED_ITEMS = 100;
const VERIFY_BATCH_SIZE = 50;

export type AskPhase =
  | "submitting"
  | "submissionUncertain"
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "unknown";

export interface AskItem {
  id: string;
  question: string;
  idempotencyKey: string;
  serverId: string | null;
  phase: AskPhase;
  answer: string | null;
  errorMessage: string | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
  pollingPaused: boolean;
  /**
   * `pending` = a pre-2026-10-25 cache entry whose server id has not been
   * classified yet. `unknown` = the server could not confirm it; keep the
   * local record but never show or resubmit its body.
   */
  classification?: "chat" | "unknown" | "pending";
}

interface AskValue {
  items: AskItem[];
  loadingHistory: boolean;
  historyError: string | null;
  verificationError: string | null;
  submitting: boolean;
  submit: (text: string) => Promise<AskItem | null>;
  retrySubmission: (id: string) => Promise<void>;
  cancel: (id: string) => Promise<void>;
  refreshOne: (id: string) => Promise<AskRead | null>;
  refreshHistory: () => Promise<void>;
  recheckItem: (id: string) => Promise<void>;
  hidePaused: (id: string) => void;
}

/** Items whose body may be shown and whose actions are allowed. */
export function isVisibleChat(item: AskItem): boolean {
  return item.classification !== "pending" && item.classification !== "unknown";
}

const AskContext = createContext<AskValue | null>(null);

export function useAskStore(): AskValue {
  const value = useContext(AskContext);
  if (!value) throw new Error("useAskStore must be used inside AskProvider");
  return value;
}

const TERMINAL: RunStatus[] = ["succeeded", "failed", "cancelled", "unknown"];
const ACTIVE_PHASES: AskPhase[] = [
  "submitting",
  "submissionUncertain",
  "queued",
  "running",
  "unknown",
];

function phaseForStatus(status: RunStatus | null): AskPhase {
  switch (status) {
    case "pending":
      return "queued";
    case "running":
      return "running";
    case "succeeded":
      return "succeeded";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
    case "unknown":
      return "unknown";
    default:
      return "queued";
  }
}

function messageForStatus(status: RunStatus): string | null {
  if (status === "failed") return "服务器未能完成这次处理。";
  if (status === "unknown") return "处理状态暂不确定，可以稍后刷新核对。";
  if (status === "cancelled") return "这次提问已取消。";
  return null;
}

function fromRead(read: AskRead, existing: AskItem | null): AskItem {
  const phase = phaseForStatus(read.status);
  return {
    id: existing?.id || uuid(),
    question: read.request_text,
    idempotencyKey: existing?.idempotencyKey || "",
    serverId: read.local_run_id,
    phase,
    answer: read.answer,
    errorMessage: read.error_code ? messageForStatus(read.status) : messageForStatus(read.status),
    createdAt: existing?.createdAt || read.created_at,
    updatedAt: new Date().toISOString(),
    completedAt: read.completed_at,
    pollingPaused: false,
    classification: "chat",
  };
}

function mergeRead(item: AskItem, read: AskRead): AskItem {
  // Terminal local states are irreversible; a stale poll cannot regress them.
  if (item.phase === "cancelled" && read.status !== "cancelled") return item;
  if (item.phase === "succeeded" && read.status !== "succeeded") return item;
  const phase = phaseForStatus(read.status);
  return {
    ...item,
    serverId: read.local_run_id,
    phase,
    answer: read.answer,
    errorMessage: read.error_code ? messageForStatus(read.status) : messageForStatus(read.status),
    updatedAt: new Date().toISOString(),
    completedAt: read.completed_at,
    pollingPaused: read.status === "unknown" ? item.pollingPaused : false,
    // A successful detail/list read proves this run is a chat for this account.
    classification: "chat",
  };
}

function terminalFromEvent(eventName: string, data: string): boolean {
  let payload: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(data) as unknown;
    if (parsed && typeof parsed === "object") payload = parsed as Record<string, unknown>;
  } catch {
    // Non-JSON frames only carry their event name below.
  }
  const status = payload.workflow_status ?? payload.status;
  if (typeof status === "string") {
    const normalized = status.toLowerCase();
    if (
      ["succeeded", "completed", "failed", "cancelled", "canceled", "unknown"].includes(
        normalized,
      )
    ) {
      return true;
    }
  }
  return /workflow\.(completed|failed|cancelled)$/i.test(eventName);
}

function parseFrames(buffer: string): { frames: string[]; rest: string } {
  const frames: string[] = [];
  let rest = buffer.replace(/\r\n/g, "\n");
  let index = rest.indexOf("\n\n");
  while (index >= 0) {
    frames.push(rest.slice(0, index));
    rest = rest.slice(index + 2);
    index = rest.indexOf("\n\n");
  }
  return { frames, rest };
}

function parseFrame(frame: string): {
  id: string | null;
  event: string;
  data: string;
} {
  let id: string | null = null;
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  return { id, event, data: data.join("\n") };
}

export function AskProvider({ children }: { children: React.ReactNode }) {
  const { user, state: authState, refresh: refreshAuth } = useAuth();
  const accountId = user?.user_id ?? null;
  const storageKey = accountKey("pa.web.asks", accountId);
  const [items, setItems] = useState<AskItem[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [verificationError, setVerificationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const streams = useRef<Map<string, AbortController>>(new Map());
  const tracked = useRef<Set<string>>(new Set());
  const hydratedFor = useRef<string | null>(null);
  const accountRef = useRef<string | null>(accountId);
  const itemsRef = useRef<AskItem[]>([]);
  const verificationSeq = useRef(0);
  const verifyInFlight = useRef<Set<string>>(new Set());

  accountRef.current = accountId;
  itemsRef.current = items;

  const applyVerification = useCallback(
    (results: AskVerifyResponse["results"], account: string, token: number) => {
      if (accountRef.current !== account || verificationSeq.current !== token) return;
      const byId = new Map(results.map((result) => [result.local_run_id, result]));
      const removed = new Set<string>();
      const unknown = new Set<string>();
      for (const item of itemsRef.current) {
        if (!item.serverId) continue;
        const result = byId.get(item.serverId);
        if (result?.classification === "not_chat") removed.add(item.id);
        if (result?.classification === "unknown") unknown.add(item.id);
      }
      for (const id of [...removed, ...unknown]) {
        streams.current.get(id)?.abort();
        streams.current.delete(id);
        tracked.current.delete(id);
      }
      setItems((current) =>
        current.flatMap((item) => {
          if (!item.serverId) return [item];
          const result = byId.get(item.serverId);
          if (!result) return [item];
          if (result.classification === "not_chat") return [];
          if (result.classification === "unknown") {
            return [
              {
                ...item,
                classification: "unknown" as const,
                errorMessage: null,
                pollingPaused: true,
              },
            ];
          }
          return [{ ...item, classification: "chat" as const }];
        }),
      );
    },
    [],
  );

  const verifyIds = useCallback(
    async (ids: string[], account: string) => {
      const wanted = Array.from(
        new Set(ids.filter((id) => Boolean(id) && !verifyInFlight.current.has(id))),
      );
      if (wanted.length === 0 || accountRef.current !== account) return;
      const token = ++verificationSeq.current;
      wanted.forEach((id) => verifyInFlight.current.add(id));
      setVerificationError(null);
      try {
        for (let index = 0; index < wanted.length; index += VERIFY_BATCH_SIZE) {
          const batch = wanted.slice(index, index + VERIFY_BATCH_SIZE);
          const response = await apiRequest<AskVerifyResponse>(pa("/asks/verify"), {
            method: "POST",
            body: { local_run_ids: batch },
          });
          if (accountRef.current !== account || verificationSeq.current !== token) return;
          applyVerification(response.results, account, token);
        }
      } catch (caught) {
        if (accountRef.current !== account) return;
        if (caught instanceof ApiError && caught.status === 401) {
          void refreshAuth();
          setVerificationError("登录状态已失效，本机旧记录暂时无法核对。");
        } else {
          setVerificationError(
            caught instanceof Error
              ? `本机旧记录暂时无法核对：${caught.message}`
              : "本机旧记录暂时无法核对。",
          );
        }
      } finally {
        wanted.forEach((id) => verifyInFlight.current.delete(id));
      }
    },
    [applyVerification, refreshAuth],
  );

  // Load this account's local records, then merge server history.
  useEffect(() => {
    streams.current.forEach((controller) => controller.abort());
    streams.current.clear();
    tracked.current.clear();
    verificationSeq.current += 1;
    verifyInFlight.current.clear();
    setVerificationError(null);

    const stored = readJson<AskItem[]>(storageKey, []);
    const hydrated = stored.map((item): AskItem => {
      const classification =
        item.classification ?? (item.serverId ? "pending" : "chat");
      if (item.phase === "submitting" && !item.serverId) {
        return {
          ...item,
          classification: "chat",
          phase: "submissionUncertain",
          errorMessage:
            "上次提交结果暂不确定：请求可能已到达服务器，也可能没有。重试会复用同一个请求键。",
        };
      }
      return { ...item, classification };
    });
    setItems(hydrated);
    hydratedFor.current = storageKey;

    // Only pre-separation cache entries need a verdict; verified chat and
    // never-submitted user input stay readable offline.
    const unverifiedIds = hydrated
      .filter((item) => item.serverId && item.classification === "pending")
      .map((item) => item.serverId as string);
    if (unverifiedIds.length > 0 && accountId) {
      void verifyIds(unverifiedIds, accountId);
    }
  }, [accountId, storageKey, verifyIds]);

  // Persist after hydration; the first render must not overwrite stored data.
  useEffect(() => {
    if (hydratedFor.current !== storageKey) return;
    writeJson(storageKey, items.slice(0, MAX_CACHED_ITEMS));
  }, [items, storageKey]);

  const applyRead = useCallback((localId: string, read: AskRead) => {
    setItems((current) =>
      current.map((item) => (item.id === localId ? mergeRead(item, read) : item)),
    );
  }, []);

  const refreshOne = useCallback(
    async (id: string): Promise<AskRead | null> => {
      // Use the ref so a stream started by an earlier render still sees the
      // item created by the current submission.
      const account = accountRef.current;
      const item = itemsRef.current.find((entry) => entry.id === id);
      if (!account || !item?.serverId || !isVisibleChat(item)) return null;
      try {
        const read = await apiRequest<AskRead>(pa(`/asks/${item.serverId}`));
        if (accountRef.current !== account) return null;
        applyRead(id, read);
        return read;
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 401) {
          void refreshAuth();
        }
        return null;
      }
    },
    [applyRead, refreshAuth],
  );

  const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

  const poll = useCallback(
    async (localId: string, serverId: string) => {
      // Slower than the first release: a queued task can legitimately wait for
      // the worker, and the page must not turn that into a busy loop.
      const delays = [2000, 3000, 5000, 8000, 12000];
      const account = accountRef.current;
      if (!account) return;
      let networkFailures = 0;
      for (const delay of delays) {
        await sleep(delay);
        if (accountRef.current !== account) return;
        const current = itemsRef.current.find((item) => item.id === localId);
        if (!current || !isVisibleChat(current)) return;
        try {
          const read = await apiRequest<AskRead>(pa(`/asks/${serverId}`));
          if (accountRef.current !== account) return;
          applyRead(localId, read);
          if (TERMINAL.includes(read.status)) return;
          networkFailures = 0;
        } catch (caught) {
          if (accountRef.current !== account) return;
          if (caught instanceof ApiError) {
            if (caught.status === 401) {
              void refreshAuth();
              return;
            }
            if (caught.status === 404) {
              // 404 also means "not this account / not a chat / temporarily
              // missing". Keep the local record instead of deleting it.
              setItems((state) =>
                state.map((item) =>
                  item.id === localId
                    ? {
                        ...item,
                        pollingPaused: true,
                        errorMessage:
                          "服务器暂时没有返回这条记录；本机记录仍保留，稍后可以再次核对。",
                      }
                    : item,
                ),
              );
              return;
            }
            if (caught.status === 429) {
              setItems((state) =>
                state.map((item) =>
                  item.id === localId
                    ? {
                        ...item,
                        pollingPaused: true,
                        errorMessage: `${caught.retryAfter ?? 30} 秒后再刷新。`,
                      }
                    : item,
                ),
              );
              return;
            }
          }
          // Offline/5xx: stop after two failures instead of hammering the
          // server; the run itself remains saved.
          networkFailures += 1;
          if (networkFailures >= 2) {
            setItems((state) =>
              state.map((item) =>
                item.id === localId
                  ? {
                      ...item,
                      pollingPaused: true,
                      errorMessage:
                        "暂时无法读取最新状态；服务器上的执行没有取消，稍后可继续核对。",
                    }
                  : item,
              ),
            );
            return;
          }
        }
      }
      setItems((current) =>
        current.map((item) =>
          item.id === localId ? { ...item, pollingPaused: true } : item,
        ),
      );
    },
    [applyRead, refreshAuth],
  );

  const connectStream = useCallback(
    (localId: string, serverId: string, after: number) => {
      const item = itemsRef.current.find((entry) => entry.id === localId);
      if (!item || !isVisibleChat(item)) return;
      if (streams.current.has(localId)) return;
      const account = accountRef.current;
      if (!account) return;
      const controller = new AbortController();
      streams.current.set(localId, controller);
      tracked.current.add(localId);

      const run = async () => {
        let sawTerminal = false;
        let lastSequence = after;
        try {
          const response = await fetch(
            pa(`/asks/${serverId}/events${lastSequence > 0 ? `?after=${lastSequence}` : ""}`),
            {
              headers: { Accept: "text/event-stream" },
              credentials: "same-origin",
              cache: "no-store",
              signal: controller.signal,
            },
          );
          if (!response.ok || !response.body) {
            throw new Error("stream_unavailable");
          }
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const parsed = parseFrames(buffer);
            buffer = parsed.rest;
            for (const frame of parsed.frames) {
              const event = parseFrame(frame);
              const sequence = event.id && /^\d+$/.test(event.id) ? Number(event.id) : 0;
              if (sequence > lastSequence) lastSequence = sequence;
              if (terminalFromEvent(event.event, event.data)) {
                sawTerminal = true;
              }
            }
            if (sawTerminal) {
              await reader.cancel();
              break;
            }
          }
        } catch {
          // A 409 (no workflow yet) or a dropped stream falls back to polling.
        } finally {
          streams.current.delete(localId);
        }

        if (accountRef.current !== account) return;
        const read = await refreshOne(localId);
        if (accountRef.current !== account) return;
        if (!read || !TERMINAL.includes(read.status)) {
          await poll(localId, serverId);
        }
      };

      void run();
    },
    [poll, refreshOne],
  );

  const mergeHistory = useCallback((reads: AskRead[]) => {
    setItems((current) => {
      const merged = [...current];
      for (const read of [...reads].reverse()) {
        const index = merged.findIndex((item) => item.serverId === read.local_run_id);
        if (index >= 0) {
          merged[index] = mergeRead(merged[index], read);
        } else if (!merged.some((item) => !item.serverId && item.question === read.request_text)) {
          merged.push(fromRead(read, null));
        }
      }
      // The server page is newest-first but deliberately not the whole cache:
      // keep older verified local records instead of truncating to 20.
      return merged
        .sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt))
        .slice(0, MAX_CACHED_ITEMS);
    });
  }, []);

  const refreshHistory = useCallback(async () => {
    if (authState !== "signedIn") return;
    const account = accountRef.current;
    if (!account) return;
    setLoadingHistory(true);
    setHistoryError(null);
    try {
      const reads = await apiRequest<AskRead[]>(pa("/asks?limit=20"));
      if (accountRef.current !== account) return;
      mergeHistory(reads);
    } catch (caught) {
      if (accountRef.current !== account) return;
      if (caught instanceof ApiError && caught.status === 401) {
        await refreshAuth();
        setHistoryError("登录状态已失效，请重新登录。");
      } else {
        setHistoryError(caught instanceof Error ? caught.message : "历史记录暂时无法读取。");
      }
    } finally {
      if (accountRef.current === account) setLoadingHistory(false);
    }
  }, [authState, mergeHistory, refreshAuth]);

  useEffect(() => {
    if (authState === "signedIn") {
      void refreshHistory();
    }
    // Run on sign-in/account switch only; `refreshHistory` already depends on auth.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authState, accountId]);

  // Track active runs: one SSE attempt per run, then polling fallback.
  useEffect(() => {
    if (authState !== "signedIn") return;
    for (const item of items) {
      if (
        item.serverId &&
        isVisibleChat(item) &&
        ACTIVE_PHASES.includes(item.phase) &&
        !tracked.current.has(item.id) &&
        !streams.current.has(item.id) &&
        !item.pollingPaused
      ) {
        connectStream(item.id, item.serverId, 0);
      }
    }
  }, [items, authState, connectStream]);

  // Returning to the tab re-checks active runs without reloading everything.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      for (const item of items) {
        if (
          item.serverId &&
          isVisibleChat(item) &&
          ACTIVE_PHASES.includes(item.phase) &&
          item.pollingPaused
        ) {
          void refreshOne(item.id);
        }
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [items, refreshOne]);

  const submit = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return null;
      if (submitting) return null;
      if (accountRef.current === null) return null;
      const account = accountRef.current;

      const now = new Date().toISOString();
      const item: AskItem = {
        id: uuid(),
        question: trimmed,
        idempotencyKey: uuid(),
        serverId: null,
        phase: "submitting",
        answer: null,
        errorMessage: null,
        createdAt: now,
        updatedAt: now,
        completedAt: null,
        pollingPaused: false,
        classification: "chat",
      };
      setSubmitting(true);
      setItems((current) => [item, ...current].slice(0, MAX_CACHED_ITEMS));

      try {
        const created = await apiRequest<AskCreated>(pa("/asks"), {
          method: "POST",
          body: { text: trimmed, idempotency_key: item.idempotencyKey },
        });
        if (accountRef.current !== account) return null;
        const phase = phaseForStatus(created.status);
        setItems((current) =>
          current.map((entry) =>
            entry.id === item.id
              ? {
                  ...entry,
                  serverId: created.local_run_id,
                  phase,
                  classification: "chat",
                  updatedAt: new Date().toISOString(),
                }
              : entry,
          ),
        );
        if (created.workflow_execution_id) {
          connectStream(item.id, created.local_run_id, 0);
        } else {
          void poll(item.id, created.local_run_id);
        }
        return item;
      } catch (caught) {
        if (accountRef.current !== account) return null;
        if (caught instanceof ApiError && caught.status === 401) {
          await refreshAuth();
          setItems((current) =>
            current.map((entry) =>
              entry.id === item.id
                ? {
                    ...entry,
                    phase: "failed",
                    errorMessage: "登录状态已失效，请重新登录后再提交。",
                  }
                : entry,
            ),
          );
        } else if (caught instanceof ApiError && caught.status < 500) {
          setItems((current) =>
            current.map((entry) =>
              entry.id === item.id
                ? {
                    ...entry,
                    phase: "failed",
                    errorMessage:
                      caught.status === 409
                        ? "服务端拒绝了这次提交，请稍后刷新核对。"
                        : caught.message,
                  }
                : entry,
            ),
          );
        } else {
          setItems((current) =>
            current.map((entry) =>
              entry.id === item.id
                ? {
                    ...entry,
                    phase: "submissionUncertain",
                    errorMessage:
                      "提交结果暂不确定：请求可能已经到达服务器，也可能没有。重试会复用同一个请求键，不会创建第二次执行。",
                  }
                : entry,
            ),
          );
        }
        return null;
      } finally {
        setSubmitting(false);
      }
    },
    [connectStream, poll, refreshAuth, submitting],
  );

  const retrySubmission = useCallback(
    async (id: string) => {
      const item = itemsRef.current.find((entry) => entry.id === id);
      // Only a user-authored submission without a server id can be retried.
      // Unknown internal cache entries expose no resubmit path.
      if (!item || !item.idempotencyKey || item.serverId || !isVisibleChat(item)) return;
      const account = accountRef.current;
      if (!account) return;
      setItems((current) =>
        current.map((entry) =>
          entry.id === id ? { ...entry, phase: "submitting", errorMessage: null } : entry,
        ),
      );
      try {
        const created = await apiRequest<AskCreated>(pa("/asks"), {
          method: "POST",
          body: { text: item.question, idempotency_key: item.idempotencyKey },
        });
        if (accountRef.current !== account) return;
        setItems((current) =>
          current.map((entry) =>
            entry.id === id
              ? {
                  ...entry,
                  serverId: created.local_run_id,
                  phase: phaseForStatus(created.status),
                  classification: "chat",
                  errorMessage: null,
                }
              : entry,
          ),
        );
        connectStream(id, created.local_run_id, 0);
      } catch (caught) {
        if (accountRef.current !== account) return;
        setItems((current) =>
          current.map((entry) =>
            entry.id === id
              ? {
                  ...entry,
                  phase:
                    caught instanceof ApiError && caught.status < 500
                      ? "failed"
                      : "submissionUncertain",
                  errorMessage:
                    caught instanceof ApiError ? caught.message : "提交结果仍不确定，请稍后刷新。",
                }
              : entry,
          ),
        );
      }
    },
    [connectStream],
  );

  const cancel = useCallback(
    async (id: string) => {
      const item = itemsRef.current.find((entry) => entry.id === id);
      if (!item?.serverId || !isVisibleChat(item)) return;
      const account = accountRef.current;
      if (!account) return;
      try {
        const read = await apiRequest<AskRead>(pa(`/asks/${item.serverId}/cancel`), {
          method: "POST",
        });
        if (accountRef.current !== account) return;
        applyRead(id, read);
      } catch (caught) {
        if (accountRef.current !== account) return;
        if (caught instanceof ApiError) {
          if (caught.status === 409) {
            await refreshOne(id);
            setItems((current) =>
              current.map((entry) =>
                entry.id === id
                  ? { ...entry, errorMessage: "已不可取消，已刷新真实状态。" }
                  : entry,
              ),
            );
          } else if (caught.status === 401) {
            await refreshAuth();
          } else {
            setItems((current) =>
              current.map((entry) =>
                entry.id === id
                  ? {
                      ...entry,
                      errorMessage: "取消结果暂不确定，正在继续核对。",
                    }
                  : entry,
              ),
            );
            void refreshOne(id);
          }
        }
      }
    },
    [applyRead, refreshAuth, refreshOne],
  );

  const recheckItem = useCallback(
    async (id: string) => {
      const item = itemsRef.current.find((entry) => entry.id === id);
      const account = accountRef.current;
      if (!item?.serverId || !account) return;
      setItems((current) =>
        current.map((entry) =>
          entry.id === id
            ? {
                ...entry,
                classification: "pending",
                errorMessage: null,
                pollingPaused: true,
              }
            : entry,
        ),
      );
      // A manual recheck must not be silently skipped by an older in-flight
      // verification of the same server id.
      verifyInFlight.current.delete(item.serverId);
      await verifyIds([item.serverId], account);
    },
    [verifyIds],
  );

  const hidePaused = useCallback((id: string) => {
    // Reconciling does not turn a queued task into "running"; keep the server
    // phase and only restart observation.
    setItems((current) =>
      current.map((item) => (item.id === id ? { ...item, pollingPaused: false } : item)),
    );
    tracked.current.delete(id);
  }, []);

  const value = useMemo<AskValue>(
    () => ({
      items,
      loadingHistory,
      historyError,
      verificationError,
      submitting,
      submit,
      retrySubmission,
      cancel,
      refreshOne,
      refreshHistory,
      recheckItem,
      hidePaused,
    }),
    [
      items,
      loadingHistory,
      historyError,
      verificationError,
      submitting,
      submit,
      retrySubmission,
      cancel,
      refreshOne,
      refreshHistory,
      recheckItem,
      hidePaused,
    ],
  );

  return <AskContext.Provider value={value}>{children}</AskContext.Provider>;
}

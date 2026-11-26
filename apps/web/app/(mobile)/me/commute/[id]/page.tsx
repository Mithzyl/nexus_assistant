"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Clock, Trash2 } from "lucide-react";

import type {
  Advisory,
  CommuteCheck,
  CommuteDayState,
  CommutePlan,
  CommutePlanDraft,
  CommutePlanUpdate,
} from "@/lib/mobile/api";
import { ApiError, apiRequest } from "@/lib/mobile/api";
import { formatTime, uuid } from "@/lib/mobile/format";
import {
  CHECK_PHASE_LABEL,
  clearPendingCheck,
  loadPendingCheck,
  savePendingCheck,
  type PendingCommuteCheck,
} from "@/lib/mobile/commute-check";
import {
  advisoryApplicability,
  routeState,
  routeUnconfigured,
} from "@/lib/mobile/advisory";
import {
  loadAdvisoryHistory,
  rememberAdvisory,
} from "@/lib/mobile/advisory-history";
import { useApiResource, errorText } from "@/lib/mobile/use-api-resource";
import {
  describeCommuteRuntime,
  useCommuteRuntime,
} from "@/lib/mobile/commute-runtime";
import { useAuth, useUI } from "@/components/mobile/providers";
import { AdvisoryCard } from "@/components/mobile/advisory-card";
import { CommuteForm } from "@/components/mobile/commute-form";
import { formatInTimeZone, serviceDateInTimeZone } from "@/lib/mobile/commute-day";
import { CommuteDayPanel } from "@/components/mobile/commute-day-panel";
import { HistoryAdvisoryCard } from "@/components/mobile/history-advisory-card";
import {
  LoginLink,
  ConfirmDialog,
  EmptyState,
  ErrorCard,
  LoadingCard,
  PageShell,
  Pill,
} from "@/components/mobile/ui";

const TERMINAL = new Set(["succeeded", "degraded", "failed", "cancelled", "unknown"]);
// The worker may queue a manual task behind scheduled work. Poll slower after
// the first few reads and never keep a spinner alive indefinitely.
const POLL_DELAYS = [1200, 2000, 3000, 5000, 8000, 12000, 15000, 20000, 30000];
const POLL_DEADLINE_MS = 150_000;

/**
 * Keep only editable plan fields for the PUT body.
 *
 * A long-term update response also carries the already-refreshed `next_day`;
 * spreading it back into the form would send a read-only field to the server.
 */
function toDraft(plan: CommutePlan | CommutePlanUpdate): CommutePlanDraft {
  const {
    id: _id,
    revision: _revision,
    created_at: _created,
    updated_at: _updated,
    matter_id: _matter,
    first_schedule_id: _schedule,
    ...draft
  } = plan as CommutePlan & { next_day?: CommuteDayState | null };
  const { next_day: _nextDay, ...editable } = draft;
  return editable;
}

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

function weekdayText(weekdays: number[]): string {
  if (weekdays.length === 5 && [1, 2, 3, 4, 5].every((day) => weekdays.includes(day))) {
    return "周一至周五";
  }
  if (weekdays.length === 7) return "每天";
  return weekdays.map((day) => `周${WEEKDAYS[day - 1]}`).join("、");
}

export default function CommuteDetailPage({ params }: { params: { id: string } }) {
  const router = useRouter();
  const { state, user, refresh: refreshAuth } = useAuth();
  const { toast } = useUI();
  const signedIn = state === "signedIn";
  const accountId = user?.user_id ?? null;
  const planId = params.id;

  const plan = useApiResource<CommutePlan>(
    signedIn ? `/api/pa/commute-plans/${planId}` : null,
    { enabled: signedIn },
  );
  const allPlans = useApiResource<CommutePlan[]>(
    signedIn ? "/api/pa/commute-plans" : null,
    { enabled: signedIn },
  );
  const [tab, setTab] = useState<"advice" | "settings">("advice");
  const [polling, setPolling] = useState(false);
  const [pending, setPending] = useState<PendingCommuteCheck | null>(null);
  const [, setViewMode] = useState<"latest" | "check" | null>(null);
  const [advisory, setAdvisory] = useState<Advisory | null>(null);
  const [history, setHistory] = useState<Advisory[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [adviceLoading, setAdviceLoading] = useState(false);
  const [adviceError, setAdviceError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<"reanalyze" | "latest" | "check" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // The runtime endpoint is account-wide, but its status is shown only for the
  // current check. A plan/account switch resets the hook's key.
  const active = Boolean(pending?.checkId) && pending !== null && !TERMINAL.has(pending.status);
  const runtime = useCommuteRuntime({
    enabled: signedIn,
    accountId,
    planId,
    active,
    onUnauthorized: refreshAuth,
  });

  const mounted = useRef(true);
  const pollToken = useRef(0);
  const pollingRef = useRef(false);
  const pendingRef = useRef<PendingCommuteCheck | null>(null);

  pendingRef.current = pending;

  useEffect(() => {
    mounted.current = true;
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => {
      mounted.current = false;
      pollToken.current += 1;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  useEffect(() => {
    if (!accountId || !planId) {
      setHistory([]);
      return;
    }
    setHistory(loadAdvisoryHistory(accountId, planId));
  }, [accountId, planId]);

  const persist = useCallback(
    (record: PendingCommuteCheck) => {
      savePendingCheck(accountId, record);
      setPending(record);
    },
    [accountId],
  );

  const loadLatest = useCallback(async () => {
    if (!signedIn) return;
    setAdviceLoading(true);
    setAdviceError(null);
    try {
      const data = await apiRequest<Advisory>(
        `/api/pa/commute-plans/${planId}/advisories/latest`,
      );
      if (!mounted.current) return;
      setAdvisory(data);
      setViewMode("latest");
      setHistory(rememberAdvisory(accountId, planId, data));
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiError && (caught.status === 404 || caught.code === "NO_ADVISORY")) {
        setAdvisory(null);
        setViewMode("latest");
        setAdviceError(null);
      } else if (caught instanceof ApiError && caught.status === 401) {
        setAdviceError("登录状态已失效，请重新登录。");
      } else {
        setAdviceError(caught instanceof Error ? caught.message : "建议暂时没读到。");
      }
    } finally {
      if (mounted.current) setAdviceLoading(false);
    }
  }, [accountId, planId, signedIn]);

  const settle = useCallback(
    async (record: PendingCommuteCheck) => {
      persist(record);
      setViewMode("check");
      setAdviceError(null);
      if (!record.advisoryId) {
        setAdvisory(null);
        if (record.status === "unknown") {
          setAdviceError(null);
          return;
        }
        if (record.status === "succeeded" || record.status === "degraded") {
          // `noAdviceResult` renders the explicit "no advisory_id" card.
          setAdviceError(null);
          return;
        }
        setAdviceError(
          record.error ||
            (record.status === "cancelled"
              ? "这次分析已取消。"
              : "这次没算出新方案。"),
        );
        return;
      }
      setAdviceLoading(true);
      try {
        const data = await apiRequest<Advisory>(`/api/pa/advisories/${record.advisoryId}`);
        if (!mounted.current) return;
        setAdvisory(data);
        setHistory(rememberAdvisory(accountId, planId, data));
      } catch (caught) {
        if (mounted.current) {
          setAdviceError(
            caught instanceof ApiError && caught.status === 401
              ? "登录状态已失效，请重新登录。"
              : "分析做完了，建议还没读出来，稍后再试。",
          );
        }
      } finally {
        if (mounted.current) setAdviceLoading(false);
      }
    },
    [accountId, persist, planId],
  );

  const pollCheck = useCallback(
    async (record: PendingCommuteCheck) => {
      if (!record.checkId || pollingRef.current) return;
      pollingRef.current = true;
      setPolling(true);
      const token = ++pollToken.current;
      setActionBusy("check");
      setMessage(null);
      let current = record;
      const startedAt = Date.now();
      let delay = POLL_DELAYS[0];
      let delayIndex = 0;
      try {
        while (Date.now() - startedAt < POLL_DEADLINE_MS) {
          await new Promise<void>((resolve) => window.setTimeout(resolve, delay));
          if (!mounted.current || pollToken.current !== token) return;
          let read: CommuteCheck;
          try {
            read = await apiRequest<CommuteCheck>(`/api/pa/checks/${current.checkId}`);
          } catch (caught) {
            if (caught instanceof ApiError && caught.status === 401) {
              setAdviceError("登录状态已失效，请重新登录。");
              return;
            }
            if (caught instanceof ApiError && caught.status === 404) {
              current = {
                ...current,
                status: "unknown",
                error: "暂时没有收到这次结果，记录已保存在当前设备。",
                errorCode: "check_not_found",
                updatedAt: new Date().toISOString(),
              };
              persist(current);
              await settle(current);
              return;
            }
            setMessage("暂时无法读取分析状态，本次分析记录已保留。");
            return;
          }
          if (!mounted.current || pollToken.current !== token) return;
          current = {
            ...current,
            status: read.status,
            phase: read.phase,
            analysisMode: read.analysis_mode,
            phaseUpdatedAt: read.phase_updated_at,
            serviceDate: read.local_service_date,
            targetArrivalAt: read.target_arrival_at,
            attemptCount: read.attempt_count,
            nextAttemptAt: read.next_attempt_at,
            advisoryId: read.advisory_id,
            error: read.error?.message ?? null,
            errorCode: read.error?.code ?? null,
            updatedAt: new Date().toISOString(),
            serverCreatedAt: read.created_at,
            finishedAt: read.finished_at,
          };
          persist(current);
          if (TERMINAL.has(read.status)) {
            await settle(current);
            runtime.refresh();
            setMessage(
              read.status === "degraded"
                ? "分析完成，但部分数据不可用。"
                : read.status === "succeeded"
                  ? "分析完成。"
                  : read.status === "failed"
                    ? read.error?.message || "分析失败。"
                    : read.status === "unknown"
                      ? "暂时无法确认这次结果，稍后可以看看查到哪一步了。"
                      : "这次分析已取消。",
            );
            return;
          }
          const retryAt = read.next_attempt_at ? Date.parse(read.next_attempt_at) : NaN;
          if (Number.isFinite(retryAt)) {
            delay = Math.max(2000, Math.min(30000, retryAt - Date.now() + 500));
          } else {
            delayIndex = Math.min(delayIndex + 1, POLL_DELAYS.length - 1);
            delay = POLL_DELAYS[delayIndex];
          }
        }
        setMessage(
          current.status === "queued"
            ? "这次更新还在排队，记录已保存；可以看看查到哪一步了。"
            : "这次更新比平时久一些，记录已保存；可以看看查到哪一步了。",
        );
      } finally {
        pollingRef.current = false;
        if (mounted.current) {
          setPolling(false);
          setActionBusy(null);
        }
      }
    },
    [persist, runtime, settle],
  );

  const refreshCheck = useCallback(
    async (record: PendingCommuteCheck) => {
      if (!record.checkId) {
        setViewMode("check");
        setMessage(record.error || "上次提交结果不确定；可以重试同一次分析。");
        return;
      }
      setActionBusy("check");
      setAdviceError(null);
      try {
        const read = await apiRequest<CommuteCheck>(`/api/pa/checks/${record.checkId}`);
        if (!mounted.current) return;
        const next: PendingCommuteCheck = {
          ...record,
          status: read.status,
          phase: read.phase,
          analysisMode: read.analysis_mode,
          phaseUpdatedAt: read.phase_updated_at,
            serviceDate: read.local_service_date,
            targetArrivalAt: read.target_arrival_at,
          attemptCount: read.attempt_count,
          nextAttemptAt: read.next_attempt_at,
          advisoryId: read.advisory_id,
          error: read.error?.message ?? null,
          errorCode: read.error?.code ?? null,
          updatedAt: new Date().toISOString(),
          serverCreatedAt: read.created_at,
          finishedAt: read.finished_at,
        };
        persist(next);
        setViewMode("check");
        if (TERMINAL.has(read.status)) {
          await settle(next);
          runtime.refresh();
        } else {
          runtime.refresh();
          void pollCheck(next);
        }
      } catch (caught) {
        if (!mounted.current) return;
        if (caught instanceof ApiError && caught.status === 404) {
          // "Not found now" is not proof of deletion; keep the local operation
          // and report an unknown result that the user can re-check.
          const next: PendingCommuteCheck = {
            ...record,
            status: "unknown",
            error: "暂时没有收到这次结果，记录已保存在当前设备。",
            errorCode: "check_not_found",
            updatedAt: new Date().toISOString(),
          };
          persist(next);
          await settle(next);
        } else {
          setMessage("暂时无法读取分析状态，本次分析记录已保留。");
        }
      } finally {
        if (mounted.current) setActionBusy(null);
      }
    },
    [persist, pollCheck, runtime, settle],
  );

  // Read-only restore: never creates a check during page load or refresh.
  useEffect(() => {
    if (!signedIn || !plan.data) return;
    const planRevision = plan.data.revision;
    const stored = loadPendingCheck(accountId, plan.data.id);
    setAdvisory(null);
    setAdviceError(null);
    setMessage(null);
    if (!stored) {
      setPending(null);
      setViewMode(null);
      void loadLatest();
      return;
    }
    if (stored.planRevision !== planRevision) {
      clearPendingCheck(accountId, plan.data.id);
      setPending(null);
      setViewMode(null);
      setMessage("计划改过了，上次分析作废，先看已有建议。");
      void loadLatest();
      return;
    }
    let normalized = stored;
    if (!stored.checkId && stored.status === "submitting") {
      normalized = {
        ...stored,
        status: "uncertain",
        error:
          "上次提交的结果不确定；重试不会重复提交。",
        updatedAt: new Date().toISOString(),
      };
      savePendingCheck(accountId, normalized);
    }
    setPending(normalized);
    setViewMode("check");
    if (normalized.checkId) {
      void refreshCheck(normalized);
    } else {
      setMessage(normalized.error || "上次重新分析的结果不确定；可以按原请求重试。");
    }
    // Restore is keyed by plan/account; callbacks are intentionally not deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn, plan.data?.id, plan.data?.revision, accountId]);

  const submitCheck = useCallback(
    async (record: PendingCommuteCheck) => {
      setActionBusy("reanalyze");
      setAdviceError(null);
      setMessage(null);
      try {
        const created = await apiRequest<{ check_id: string; status: CommuteCheck["status"]; local_service_date: string; target_arrival_at: string }>(
          `/api/pa/commute-plans/${planId}/checks`,
          {
            method: "POST",
            headers: { "Idempotency-Key": record.idempotencyKey },
          },
        );
        if (!mounted.current) return;
        const next: PendingCommuteCheck = {
          ...record,
          checkId: created.check_id,
          serviceDate: created.local_service_date,
          targetArrivalAt: created.target_arrival_at,
          status: created.status,
          phase: created.status === "queued" ? "queued" : null,
          analysisMode: "full",
          phaseUpdatedAt: new Date().toISOString(),
          error: null,
          errorCode: null,
          updatedAt: new Date().toISOString(),
        };
        persist(next);
        setViewMode("check");
        setAdvisory(null);
        await pollCheck(next);
      } catch (caught) {
        if (!mounted.current) return;
        if (caught instanceof ApiError && caught.status === 401) {
          setAdviceError("登录状态已失效，请重新登录。");
        } else if (
          caught instanceof ApiError &&
          caught.status < 500 &&
          ![408, 425, 429].includes(caught.status)
        ) {
          if (caught.code === "plan_revision_conflict") {
            clearPendingCheck(accountId, planId);
            setPending(null);
            setMessage("计划已在别处修改，请重新加载后再分析。");
            void plan.reload();
          } else if (caught.code === "idempotency_key_reused") {
            clearPendingCheck(accountId, planId);
            setPending(null);
            setMessage("这次和上次分析的内容变了，旧操作已作废，重新发起一次。");
          } else {
            const next: PendingCommuteCheck = {
              ...record,
              status: "failed",
              error: caught.message,
              errorCode: caught.code,
              updatedAt: new Date().toISOString(),
            };
            persist(next);
            setAdviceError(caught.message);
          }
        } else {
          const next: PendingCommuteCheck = {
            ...record,
            status: "uncertain",
            error:
              "还没收到结果；再试会沿用同一次提交，不会重复。",
            errorCode: "submission_uncertain",
            updatedAt: new Date().toISOString(),
          };
          persist(next);
          setMessage(next.error);
        }
      } finally {
        if (mounted.current) setActionBusy(null);
      }
    },
    [accountId, persist, plan, planId, pollCheck],
  );

  const startAnalysis = useCallback(async () => {
    if (!plan.data || actionBusy) return;
    const revision = plan.data.revision;
    const existing = pendingRef.current;

    if (existing && existing.planRevision === revision) {
      if (existing.checkId && !TERMINAL.has(existing.status)) {
        setViewMode("check");
        await refreshCheck(existing);
        return;
      }
      if (existing.checkId === null && existing.status === "uncertain") {
        setViewMode("check");
        setAdvisory(null);
        await submitCheck(existing);
        return;
      }
    }

    // A full manual analysis without a client route snapshot needs the server
    // route provider. Do not spend a queued check when the server already says
    // it is unavailable; weather and history remain visible.
    if (runtime.data?.route_provider_available === false) {
      setViewMode("check");
      setMessage(
        "路线服务还没开通，暂时算不了新方案；天气和历史方案还能看。",
      );
      return;
    }

    const record: PendingCommuteCheck = {
      planId,
      planRevision: revision,
      idempotencyKey: uuid(),
      requestHash: "no-route-snapshot",
      checkId: null,
      status: "submitting",
      advisoryId: null,
      error: null,
      errorCode: null,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    if (existing) clearPendingCheck(accountId, planId);
    persist(record);
    setViewMode("check");
    setAdvisory(null);
    await submitCheck(record);
  }, [accountId, actionBusy, persist, plan.data, planId, refreshCheck, runtime, submitCheck]);

  const viewLatest = useCallback(async () => {
    if (actionBusy) return;
    setActionBusy("latest");
    setViewMode("latest");
    await loadLatest();
    setActionBusy(null);
  }, [actionBusy, loadLatest]);

  const viewCheckResult = useCallback(async () => {
    const current = pendingRef.current;
    if (!current) return;
    setViewMode("check");
    if (current.advisoryId) {
      setAdviceLoading(true);
      setAdviceError(null);
      try {
        const data = await apiRequest<Advisory>(`/api/pa/advisories/${current.advisoryId}`);
        if (mounted.current) {
          setAdvisory(data);
          setHistory(rememberAdvisory(accountId, planId, data));
        }
      } catch {
        if (mounted.current) setAdviceError("这次分析的建议暂时没读到。");
      } finally {
        if (mounted.current) setAdviceLoading(false);
      }
    } else {
      await refreshCheck(current);
    }
  }, [accountId, planId, refreshCheck]);

  useEffect(() => {
    const resume = () => {
      if (document.visibilityState === "hidden") return;
      const current = pendingRef.current;
      if (!current?.checkId || TERMINAL.has(current.status) || pollingRef.current) {
        return;
      }
      void refreshCheck(current);
    };
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("focus", resume);
    return () => {
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("focus", resume);
    };
  }, [refreshCheck]);

  const savePlan = async (draft: CommutePlanDraft) => {
    if (!plan.data) return;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await apiRequest<CommutePlanUpdate>(`/api/pa/commute-plans/${planId}`, {
        method: "PUT",
        headers: { "If-Match": String(plan.data.revision) },
        body: draft,
      });
      plan.setData(updated);
      const current = pendingRef.current;
      if (current && current.planRevision !== updated.revision) {
        clearPendingCheck(accountId, planId);
        setPending(null);
        setViewMode(null);
        setMessage("计划改过了，上次分析作废。");
      }
      // The server already refreshed the next valid day; do not request it again.
      const localToday = serviceDateInTimeZone(new Date(), updated.timezone);
      const tomorrow = new Date(Date.parse(localToday) + 86_400_000).toISOString().slice(0, 10);
      const refreshedDeparture = formatInTimeZone(updated.next_day?.schedule?.starts_at, updated.timezone, false);
      toast(updated.next_day?.applied && updated.next_day.service_date === tomorrow && refreshedDeparture && refreshedDeparture < "12:00"
        ? "保存好了，明早的出发时间也跟着更新了" : "设置已保存");
      setTab("advice");
      await loadLatest();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setSaveError("设置刚刚被更新过，请刷新页面后再改");
      } else if (caught instanceof ApiError && caught.status === 428) {
        setSaveError("页面信息有点旧了，刷新一下再改");
        await plan.reload();
      } else {
        setSaveError(caught instanceof ApiError ? caught.message : "保存未能完成。");
      }
    } finally {
      setSaving(false);
    }
  };

  const removePlan = async () => {
    setDeleting(true);
    try {
      clearPendingCheck(accountId, planId);
      await apiRequest<void>(`/api/pa/commute-plans/${planId}`, { method: "DELETE" });
      router.replace("/me/commute");
    } catch (caught) {
      setSaveError(caught instanceof ApiError ? caught.message : "删除未能完成。");
      setConfirmDelete(false);
    } finally {
      setDeleting(false);
    }
  };

  const savedPlaces = useMemo(() => {
    const map = new Map<string, CommutePlan["origin"]>();
    for (const item of [...(allPlans.data ?? []), ...(plan.data ? [plan.data] : [])]) {
      for (const location of [item.origin, item.destination]) {
        if (!location.label.trim()) continue;
        map.set(`${location.label}|${location.latitude}|${location.longitude}|${location.coordinate_system ?? ""}`, location);
      }
    }
    return Array.from(map.values());
  }, [allPlans.data, plan.data]);

  const planRevision = plan.data?.revision;
  const applicability =
    advisory && planRevision !== undefined
      ? advisoryApplicability(advisory, planRevision, now)
      : null;
  const currentAdvisory = applicability?.applicable ? advisory : null;

  const historyMap = new Map<string, Advisory>();
  for (const item of history) historyMap.set(item.id, item);
  if (advisory && !applicability?.applicable) historyMap.set(advisory.id, advisory);
  if (currentAdvisory) historyMap.delete(currentAdvisory.id);
  const historyRows = [...historyMap.values()].sort(
    (a, b) => Date.parse(b.generated_at) - Date.parse(a.generated_at),
  );
  const routeProbe = advisory ?? historyRows[0] ?? null;
  const detectedRoute = routeState(routeProbe);
  const unconfiguredRoute =
    routeUnconfigured(advisory, pending?.errorCode ?? null) ||
    historyRows.some((item) => routeUnconfigured(item));
  // A stale/failed runtime response must not be treated as a current route
  // answer. Only a successful response can make the route-negative claim.
  const routeProviderUnavailable =
    !runtime.error && runtime.data?.route_provider_available === false;
  const routeNeedsNotice =
    routeProviderUnavailable || unconfiguredRoute || detectedRoute === "unavailable";
  const runtimeView = describeCommuteRuntime({
    data: runtime.data,
    error: runtime.error,
    serverNow: runtime.serverNow,
    lastSuccessAt: runtime.lastSuccessAt,
    loading: runtime.loading,
  });
  const currentPhaseLabel = pending?.phase ? CHECK_PHASE_LABEL[pending.phase] : null;
  const checkStartedAt = pending
    ? Date.parse(pending.serverCreatedAt || pending.createdAt)
    : NaN;
  const checkElapsedSeconds =
    Number.isFinite(checkStartedAt) && pending
      ? Math.max(1, Math.floor((now - checkStartedAt) / 1000))
      : 0;
  const elapsedLabel =
    checkElapsedSeconds < 60
      ? `${checkElapsedSeconds} 秒`
      : `${Math.floor(checkElapsedSeconds / 60)} 分钟`;
  const checkPhaseText =
    currentPhaseLabel ||
    (pending?.status === "submitting"
      ? "正在提交这次更新。"
      : pending?.status === "uncertain"
        ? "正在确认提交结果。"
        : "正在整理出发时间、路上时间和到达时间。");
  const workerHint =
    runtimeView.kind === "unavailable"
      ? "状态暂时没读到；这次记录已经保存，稍后可以继续看。"
      : runtimeView.kind === "not_seen"
        ? "还没开始处理；这次记录已经保存，稍后可以继续看。"
        : runtimeView.kind === "stale"
          ? "暂时没收到响应；这次记录已经保存，稍后可以继续看。"
          : null;
  const lastCheckText = pending?.updatedAt ? formatTime(pending.updatedAt) : null;
  const nextAttemptText = pending?.nextAttemptAt ? formatTime(pending.nextAttemptAt) : null;
  const showCheckFailure =
    pending && (pending.status === "failed" || pending.status === "cancelled") && !advisory;
  const showCheckUnknown = pending?.status === "unknown" && !advisory;
  const noAdviceResult =
    pending &&
    (pending.status === "succeeded" || pending.status === "degraded") &&
    !pending.advisoryId &&
    !active;
  const reanalyzeLabel = active ? "看看查到哪一步了" : "重新查路线";

  const openHistory = () => {
    const node = document.getElementById("commute-history") as HTMLDetailsElement | null;
    if (!node) {
      void viewLatest();
      return;
    }
    node.open = true;
    node.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <PageShell
      title={plan.data?.name || "通勤计划"}
      action={
        plan.data ? (
          <Pill tone={plan.data.enabled ? "fog" : "neutral"}>
            {plan.data.enabled ? "已启用" : "已停用"}
          </Pill>
        ) : undefined
      }
    >
      {!signedIn && state !== "loading" ? (
        <EmptyState title="登录后才能查看" body="通勤计划会同步到你的账号。">
          <LoginLink className="btn btn--primary">登录</LoginLink>
        </EmptyState>
      ) : null}

      {plan.loading ? <LoadingCard label="正在读取通勤计划…" /> : null}
      {plan.error ? <ErrorCard error={plan.error} message={errorText(plan.error)} onRetry={plan.reload} /> : null}

      {plan.data ? (
        <>
          <div className="commute-tabs" role="tablist" aria-label="通勤视图">
            <button
              type="button"
              role="tab"
              className="commute-tab"
              aria-pressed={tab === "advice"}
              onClick={() => setTab("advice")}
            >
              出行方案
            </button>
            <button
              type="button"
              role="tab"
              className="commute-tab"
              aria-pressed={tab === "settings"}
              onClick={() => setTab("settings")}
            >
              通勤设置
            </button>
          </div>

          {tab === "advice" ? (
            <div className="commute-detail">
              <div className="commute-layout">
                <div className="commute-main">
                  {adviceLoading ? <LoadingCard label="正在读取方案…" /> : null}

                  {routeNeedsNotice ? (
                    <div className="notice notice--amber">
                      暂时算不了新路线，之前的方案还能看。
                    </div>
                  ) : null}

                  {currentAdvisory && !adviceLoading ? (
                    <AdvisoryCard
                      advisory={currentAdvisory}
                      plan={plan.data}
                      onReanalyze={() => (active ? void viewCheckResult() : void startAnalysis())}
                      reanalyzeLabel={reanalyzeLabel}
                      busy={actionBusy !== null}
                    />
                  ) : null}

                  {active && pending ? (
                    <div className="card commute-runtime">
                      <div className="commute-runtime__head">
                        {polling ? <span className="spinner" /> : <Clock size={15} />}
                        <div className="grow">
                          <p className="commute-runtime__title">
                            {polling ? "正在更新建议" : "这次更新还在继续"}
                          </p>
                          <p className="commute-runtime__subtitle">{checkPhaseText}</p>
                        </div>
                      </div>
                      <details><summary>查看运行细节</summary><div className="commute-runtime__facts">
                        <span>已等待 {elapsedLabel}</span>
                        {pending.attemptCount ? <span>第 {pending.attemptCount} 次尝试</span> : null}
                        {lastCheckText ? <span>最近核对 {lastCheckText}</span> : null}
                        {nextAttemptText ? <span>下次重试 {nextAttemptText}</span> : null}
                      </div>
                      <p className="commute-runtime__hint">
                        {workerHint ||
                          (polling
                            ? "完成后会自动换成新的出发建议。"
                            : "任务已保存，可以看看查到哪一步了。")}
                      </p>
                      </details>
                      <button
                        type="button"
                        className="link-button mt-6"
                        onClick={() => {
                          runtime.refresh();
                          void viewCheckResult();
                        }}
                        disabled={actionBusy !== null}
                      >
                        看看查到哪一步了
                      </button>
                    </div>
                  ) : null}

                  {message ? <p className="micro amber" style={{ margin: 0 }}>{message}</p> : null}
                  {adviceError ? <p className="caption red" style={{ margin: 0 }}>{adviceError}</p> : null}

                  {noAdviceResult ? (
                    <div className="card card--amber">
                      <p className="card-title amber" style={{ fontSize: 15 }}>这次没算出可用方案</p>
                      <p className="caption" style={{ margin: "4px 0 0" }}>可以刷新路线，或查看历史记录。</p>
                      <div className="ask-actions">
                        <button type="button" className="link-button" onClick={() => void startAnalysis()} disabled={actionBusy !== null}>
                          {reanalyzeLabel}
                        </button>
                        {historyRows.length > 0 ? (
                          <button type="button" className="link-button" onClick={openHistory}>历史记录</button>
                        ) : null}
                      </div>
                    </div>
                  ) : null}

                  {showCheckUnknown ? (
                    <div className="card card--amber">
                      <p className="card-title amber" style={{ fontSize: 15 }}>暂时无法确认这次结果</p>
                      <p className="caption" style={{ margin: "4px 0 0" }}>
                        {pending?.error || "记录已保存，可以看看查到哪一步了。"}
                      </p>
                      <div className="ask-actions">
                        <button type="button" className="link-button" onClick={() => void viewCheckResult()} disabled={actionBusy !== null}>
                          看看查到哪一步了
                        </button>
                        {historyRows.length > 0 ? (
                          <button type="button" className="link-button" onClick={openHistory}>历史记录</button>
                        ) : null}
                      </div>
                    </div>
                  ) : null}

                  {showCheckFailure ? (
                    <div className="card card--red">
                      <p className="card-title red" style={{ fontSize: 15 }}>这次更新没有完成</p>
                      <p className="caption red" style={{ margin: "4px 0 0" }}>
                        {pending?.error || "暂时没有生成新的建议，请稍后再试。"}
                      </p>
                      <div className="ask-actions">
                        <button type="button" className="link-button" style={{ color: "var(--m-red)" }} onClick={() => void startAnalysis()} disabled={actionBusy !== null}>
                          {reanalyzeLabel}
                        </button>
                        {historyRows.length > 0 ? (
                          <button type="button" className="link-button" style={{ color: "var(--m-red)" }} onClick={openHistory}>
                            历史记录
                          </button>
                        ) : null}
                      </div>
                    </div>
                  ) : null}

                  {!advisory && !adviceLoading && !showCheckFailure && !showCheckUnknown && !noAdviceResult && !active && !adviceError && !routeNeedsNotice && historyRows.length === 0 ? (
                    <EmptyState title="还没查过路线">
                      <button type="button" className="btn btn--secondary" onClick={() => void startAnalysis()} disabled={actionBusy !== null}>
                        {reanalyzeLabel}
                      </button>
                    </EmptyState>
                  ) : null}

                  {!currentAdvisory && !adviceLoading && !active && !showCheckFailure && !showCheckUnknown && !noAdviceResult && !adviceError && historyRows.length > 0 ? (
                    <EmptyState title="这次没查到路线">
                      <button type="button" className="btn btn--secondary" onClick={() => void startAnalysis()} disabled={actionBusy !== null}>
                        {reanalyzeLabel}
                      </button>
                    </EmptyState>
                  ) : null}
                </div>

                <aside className="commute-side">
                  {plan.data?.enabled ? (
                    <CommuteDayPanel
                      onOpenSettings={() => setTab("settings")}
                      planId={plan.data.id}
                      plan={plan.data}
                      onPlanChanged={() => void plan.reload()}
                    />
                  ) : null}

                  <section className="card commute-config">
                    <div className="commute-config__head">
                      <h2>通勤安排</h2>
                      <button type="button" className="commute-link" onClick={() => setTab("settings")}>
                        调整计划
                      </button>
                    </div>
                    <div className="commute-config__rows">
                      <div className="commute-config__row">
                        <span>时间</span>
                        <b>{weekdayText(plan.data.weekdays)} · {plan.data.arrival_local_time} 到岗</b>
                      </div>
                      <div className="commute-config__row">
                        <span>路线</span>
                        <b>{plan.data.origin.label} → {plan.data.destination.label}</b>
                      </div>
                      <div className="commute-config__row">
                        <span>提醒</span>
                        <b>{plan.data.check_local_time} 查看路况 · {plan.data.notify_enabled ? "已开启" : "已关闭"}</b>
                      </div>
                    </div>
                  </section>

                  {historyRows.length > 0 ? (
                    <details id="commute-history" className="card commute-archive">
                      <summary>
                        <span>历史记录</span>
                        <span>{historyRows.length} 条 <i aria-hidden="true">›</i></span>
                      </summary>
                      <div className="commute-archive__body">
                        <div className="m-stack-10">
                          {historyRows.map((item) => (
                            <HistoryAdvisoryCard
                              key={item.id}
                              advisory={item}
                              planRevision={planRevision ?? item.plan_revision}
                            />
                          ))}
                        </div>
                      </div>
                    </details>
                  ) : null}
                </aside>
              </div>
            </div>
          ) : (
            <div className="mt-14">
              <CommuteForm
                initial={toDraft(plan.data)}
                savedPlaces={savedPlaces}
                submitLabel="保存计划"
                saving={saving}
                serverError={saveError}
                onSubmit={(draft) => void savePlan(draft)}
              />
              <div className="mt-16">
                <button
                  type="button"
                  className="btn btn--danger"
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 size={14} /> 删除这个计划
                </button>
              </div>
              <p className="micro mt-8">
                删除后不再检查；已有的记录和方案会保留。
              </p>
            </div>
          )}
        </>
      ) : null}

      <ConfirmDialog
        open={confirmDelete}
        title="删除这个通勤计划？"
        body="删除后不再提醒；历史方案会保留。"
        confirmLabel="删除计划"
        danger
        busy={deleting}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => void removePlan()}
      />
    </PageShell>
  );
}

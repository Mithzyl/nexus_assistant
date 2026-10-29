"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CloudSun, Clock, FileClock, RefreshCw, Trash2 } from "lucide-react";

import type {
  Advisory,
  CommuteCheck,
  CommutePlan,
  CommutePlanDraft,
} from "@/lib/mobile/api";
import { ApiError, apiRequest } from "@/lib/mobile/api";
import { formatDateTime, uuid } from "@/lib/mobile/format";
import { installationId } from "@/lib/mobile/storage";
import {
  CHECK_PHASE_LABEL,
  CHECK_STATUS_LABEL,
  clearPendingCheck,
  loadPendingCheck,
  savePendingCheck,
  type PendingCommuteCheck,
} from "@/lib/mobile/commute-check";
import {
  advisoryApplicability,
  routeState,
  usesClientSnapshot,
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
import { HistoryAdvisoryCard } from "@/components/mobile/history-advisory-card";
import { LatestWeatherCard } from "@/components/mobile/latest-weather-card";
import {
  ConfirmDialog,
  EmptyState,
  ErrorCard,
  LoadingCard,
  Notice,
  PageShell,
  Pill,
} from "@/components/mobile/ui";

const TERMINAL = new Set(["succeeded", "degraded", "failed", "cancelled", "unknown"]);
// The worker may queue a manual task behind scheduled work. Poll slower after
// the first few reads and never keep a spinner alive indefinitely.
const POLL_DELAYS = [1200, 2000, 3000, 5000, 8000, 12000];

function toDraft(plan: CommutePlan): CommutePlanDraft {
  const { id: _id, revision: _revision, created_at: _created, updated_at: _updated, matter_id: _matter, first_schedule_id: _schedule, ...draft } = plan;
  return draft;
}

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

function weekdayText(weekdays: number[]): string {
  if (weekdays.length === 5 && [1, 2, 3, 4, 5].every((day) => weekdays.includes(day))) {
    return "周一至周五";
  }
  if (weekdays.length === 7) return "每天";
  return weekdays.map((day) => `周${WEEKDAYS[day - 1]}`).join("、");
}

function checkTone(status: PendingCommuteCheck["status"]): "fog" | "amber" | "green" | "red" {
  if (status === "succeeded") return "green";
  if (status === "degraded" || status === "uncertain") return "amber";
  if (status === "failed" || status === "cancelled") return "red";
  return "fog";
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
  const [viewMode, setViewMode] = useState<"latest" | "check" | null>(null);
  const [advisory, setAdvisory] = useState<Advisory | null>(null);
  const [history, setHistory] = useState<Advisory[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [adviceLoading, setAdviceLoading] = useState(false);
  const [adviceError, setAdviceError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<"reanalyze" | "latest" | "check" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [skipBusy, setSkipBusy] = useState(false);
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
        setAdviceError(caught instanceof Error ? caught.message : "建议暂时无法读取。");
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
              : "这次分析失败，没有生成可用建议。"),
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
              : "这次分析已结束，但关联建议读取失败；可以稍后重试。",
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
      try {
        for (const delay of POLL_DELAYS) {
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
                error: "服务器暂时没有返回这次检查；任务记录仍保存在本机。",
                errorCode: "check_not_found",
                updatedAt: new Date().toISOString(),
              };
              persist(current);
              await settle(current);
              return;
            }
            setMessage("暂时无法读取检查状态。本次分析记录已保留，可以稍后继续同一次分析。");
            return;
          }
          if (!mounted.current || pollToken.current !== token) return;
          current = {
            ...current,
            status: read.status,
            phase: read.phase,
            analysisMode: read.analysis_mode,
            phaseUpdatedAt: read.phase_updated_at,
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
                      ? "服务器尚未确认本次结果；任务已保存，稍后可以继续核对。"
                      : "这次分析已取消。",
            );
            return;
          }
        }
        setMessage(
          current.status === "queued"
            ? "服务器仍返回等待处理，尚未给出终态。本次分析已保存，稍后回到页面只会继续读取同一个 check。"
            : current.status === "running"
              ? "服务器仍返回处理中，尚未给出终态。本次分析已保存，稍后回到页面只会继续读取同一个 check。"
              : "服务器尚未返回终态。本次分析已保存，稍后回到页面只会继续读取同一个 check。",
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
            error: "服务器暂时没有返回这次检查；任务记录仍保存在本机。",
            errorCode: "check_not_found",
            updatedAt: new Date().toISOString(),
          };
          persist(next);
          await settle(next);
        } else {
          setMessage("暂时无法读取检查状态；本次分析记录已保留。");
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
      setMessage("计划已更新，上次重新分析的状态已作废；现在只会读取已有建议。");
      void loadLatest();
      return;
    }
    let normalized = stored;
    if (!stored.checkId && stored.status === "submitting") {
      normalized = {
        ...stored,
        status: "uncertain",
        error:
          "上次提交的结果不确定：请求可能已到达服务器。重试会复用同一个幂等键，不会新建第二次分析。",
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
        const created = await apiRequest<{ check_id: string; status: CommuteCheck["status"] }>(
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
            setMessage("同一次分析的请求内容发生变化，已作废旧操作；请重新发起。");
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
              "提交结果暂不确定：请求可能已到达服务器。重试会复用同一个幂等键，不会新建第二次分析。",
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
        "路线服务尚未配置，暂时无法生成新的出发建议；最新天气和历史建议仍可查看。",
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
        if (mounted.current) setAdviceError("本次分析关联的建议暂时无法读取。");
      } finally {
        if (mounted.current) setAdviceLoading(false);
      }
    } else {
      await refreshCheck(current);
    }
  }, [accountId, planId, refreshCheck]);

  const savePlan = async (draft: CommutePlanDraft) => {
    if (!plan.data) return;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await apiRequest<CommutePlan>(`/api/pa/commute-plans/${planId}`, {
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
        setMessage("计划已更新，上次分析状态已作废。");
      }
      toast("通勤计划已保存");
      setTab("advice");
      await loadLatest();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setSaveError("设置已在其他设备修改，请重新加载后再保存。");
      } else if (caught instanceof ApiError && caught.status === 428) {
        setSaveError("缺少版本信息，请刷新页面后重试。");
        await plan.reload();
      } else {
        setSaveError(caught instanceof ApiError ? caught.message : "保存未能完成。");
      }
    } finally {
      setSaving(false);
    }
  };

  const skipToday = async () => {
    if (!advisory) return;
    setSkipBusy(true);
    try {
      await apiRequest(`/api/pa/advisories/${advisory.id}/actions`, {
        method: "POST",
        headers: { "Idempotency-Key": uuid() },
        body: { action: "skip_today", installation_id: installationId() },
      });
      toast("已跳过今天这条提醒");
    } catch (caught) {
      toast(caught instanceof ApiError ? caught.message : "跳过未能完成");
    } finally {
      setSkipBusy(false);
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
    const map = new Map<string, { label: string; latitude: number; longitude: number }>();
    for (const item of [...(allPlans.data ?? []), ...(plan.data ? [plan.data] : [])]) {
      for (const location of [item.origin, item.destination]) {
        if (!location.label.trim()) continue;
        map.set(`${location.label}|${location.latitude}|${location.longitude}`, location);
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
  const runtimeNeedsAttention = runtimeView.kind !== "recent";
  const currentPhaseLabel = pending?.phase ? CHECK_PHASE_LABEL[pending.phase] : null;
  const historyHasSnapshot = historyRows.some(usesClientSnapshot);
  const showCheckFailure =
    pending && (pending.status === "failed" || pending.status === "cancelled") && !advisory;
  const showCheckUnknown = pending?.status === "unknown" && !advisory;
  const noAdviceResult =
    pending &&
    (pending.status === "succeeded" || pending.status === "degraded") &&
    !pending.advisoryId &&
    !active;
  const reanalyzeLabel = active
    ? "继续等待"
    : pending?.status === "uncertain"
      ? "重试同一次分析"
      : "重新分析";

  return (
    <PageShell
      title={plan.data?.name || "通勤计划"}
      subtitle={plan.data ? `${plan.data.origin.label} → ${plan.data.destination.label}` : undefined}
      action={
        plan.data ? (
          <Pill tone={plan.data.enabled ? "fog" : "neutral"}>
            {plan.data.enabled ? "启用" : "停用"}
          </Pill>
        ) : undefined
      }
    >
      {!signedIn && state !== "loading" ? (
        <EmptyState title="登录后才能查看" body="通勤计划属于服务器上的账号。">
          <Link href="/login" className="btn btn--primary">登录</Link>
        </EmptyState>
      ) : null}

      {plan.loading ? <LoadingCard label="正在读取通勤计划…" /> : null}
      {plan.error ? <ErrorCard message={errorText(plan.error)} onRetry={plan.reload} /> : null}

      {plan.data ? (
        <>
          <div className="chip-row" role="tablist" aria-label="通勤视图">
            <button
              type="button"
              role="tab"
              className="chip"
              aria-pressed={tab === "advice"}
              onClick={() => setTab("advice")}
            >
              建议
            </button>
            <button
              type="button"
              role="tab"
              className="chip"
              aria-pressed={tab === "settings"}
              onClick={() => setTab("settings")}
            >
              设置
            </button>
          </div>

          {tab === "advice" ? (
            <div className="m-stack-14 mt-14">
              <div className="card card--tight">
                <div className="flex items-center justify-between gap-10">
                  <span className="micro">计划要求</span>
                  <span className="micro">服务器保存</span>
                </div>
                <div className="mt-8" style={{ display: "grid", gap: 5 }}>
                  <p>首次生效：{plan.data.effective_from ?? "未设置"}</p>
                  {plan.data.matter_id ? <Link href={`/matters/${plan.data.matter_id}`}>查看关联事项与首日日程</Link> : null}
                  <div className="flex items-start justify-between gap-12">
                    <span className="micro">到达</span>
                    <span style={{ fontSize: 13, textAlign: "right" }}>
                      {plan.data.arrival_local_time} · {weekdayText(plan.data.weekdays)}
                    </span>
                  </div>
                  <div className="flex items-start justify-between gap-12">
                    <span className="micro">起终点</span>
                    <span style={{ fontSize: 13, textAlign: "right", overflowWrap: "anywhere" }}>
                      {plan.data.origin.label} → {plan.data.destination.label}
                    </span>
                  </div>
                  <div className="flex items-start justify-between gap-12">
                    <span className="micro">检查</span>
                    <span style={{ fontSize: 13, textAlign: "right" }}>
                      {plan.data.check_local_time} ·{" "}
                      {plan.data.transport_mode === "driving" ? "驾车" : plan.data.transport_mode === "transit" ? "公交" : "步行"} · 缓冲{" "}
                      {plan.data.buffer_minutes} 分钟
                    </span>
                  </div>
                  <div className="flex items-start justify-between gap-12">
                    <span className="micro">提醒</span>
                    <span style={{ fontSize: 13 }}>
                      {plan.data.notify_enabled ? "开" : "关"} · 计划 revision {plan.data.revision}
                    </span>
                  </div>
                  <p className="micro" style={{ margin: "4px 0 0" }}>
                    计划更新于 {formatDateTime(plan.data.updated_at)}。
                  </p>
                </div>
              </div>

              {/* A finished check may have refreshed the weather; remount
                  once on completion instead of polling the weather endpoint. */}
              <LatestWeatherCard
                key={`${plan.data.revision}-${pending?.finishedAt ?? pending?.status ?? "initial"}`}
                planId={plan.data.id}
                planRevision={plan.data.revision}
                advisory={advisory}
              />

              <div className="card card--tight">
                <div className="flex items-center justify-between gap-10" style={{ flexWrap: "wrap" }}>
                  <span className="flex items-center gap-6">
                    <CloudSun size={15} className="fog" />
                    <span className="micro">本次分析</span>
                  </span>
                  {pending ? (
                    <Pill tone={checkTone(pending.status)}>
                      {CHECK_STATUS_LABEL[pending.status]}
                    </Pill>
                  ) : viewMode === "latest" ? (
                    <span className="micro">只读 · 已有建议</span>
                  ) : null}
                </div>

                <div className="flex items-center gap-10 mt-10" style={{ flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="btn btn--primary btn--small"
                    onClick={() => void startAnalysis()}
                    disabled={actionBusy !== null}
                  >
                    {actionBusy === "reanalyze" || actionBusy === "check" ? (
                      <span className="spinner" style={{ width: 14, height: 14 }} />
                    ) : (
                      <RefreshCw size={13} />
                    )}
                    {reanalyzeLabel}
                  </button>
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => void viewLatest()}
                    disabled={actionBusy !== null}
                  >
                    查看已有建议
                  </button>
                  {viewMode === "latest" && pending?.advisoryId ? (
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => void viewCheckResult()}
                    >
                      <FileClock size={12} /> 本次分析结果
                    </button>
                  ) : null}
                </div>

                <p className="micro" style={{ margin: "10px 0 0", lineHeight: 1.6 }}>
                  页面打开与刷新只读取服务器；只有点击“重新分析”才会创建 check。
                  浏览器不生成路线快照，也不会把定位当成 ETA。
                </p>

                {pending ? (
                  <>
                    <div className="mt-8" style={{ display: "grid", gap: 4 }}>
                      <div className="flex items-start justify-between gap-12">
                        <span className="micro">本次操作状态</span>
                        <span style={{ fontSize: 13 }}>{CHECK_STATUS_LABEL[pending.status]}</span>
                      </div>
                      {active && pending.phase ? (
                        <div className="flex items-start justify-between gap-12">
                          <span className="micro">当前步骤</span>
                          <span style={{ fontSize: 13, textAlign: "right" }}>
                            {CHECK_PHASE_LABEL[pending.phase]}
                            {pending.phaseUpdatedAt
                              ? ` · ${formatDateTime(pending.phaseUpdatedAt)}`
                              : ""}
                          </span>
                        </div>
                      ) : null}
                      <div className="flex items-start justify-between gap-12">
                        <span className="micro">服务器创建</span>
                        <span style={{ fontSize: 13, textAlign: "right" }}>
                          {pending.serverCreatedAt
                            ? formatDateTime(pending.serverCreatedAt)
                            : formatDateTime(pending.createdAt)}
                        </span>
                      </div>
                      <div className="flex items-start justify-between gap-12">
                        <span className="micro">{pending.finishedAt ? "服务器完成" : "本机更新"}</span>
                        <span style={{ fontSize: 13, textAlign: "right" }}>
                          {formatDateTime(pending.finishedAt || pending.updatedAt)}
                        </span>
                      </div>
                    </div>
                    <p className="micro" style={{ margin: "6px 0 0", lineHeight: 1.6 }}>
                      幂等键已保存在本机
                      {pending.planRevision !== plan.data.revision ? " · 计划版本已变化" : ""}。
                      页面只显示服务器返回的状态与步骤；没有返回步骤时，不推断正在分析。
                    </p>
                  </>
                ) : (
                  <p className="micro" style={{ margin: "6px 0 0", lineHeight: 1.6 }}>
                    本次页面只读恢复，没有创建新的 check。服务器记录仍以 PA 为准。
                  </p>
                )}

                {message ? (
                  <p className="micro amber" style={{ margin: "8px 0 0", lineHeight: 1.6 }}>
                    {message}
                  </p>
                ) : null}

                {adviceError ? (
                  <p className="caption red" style={{ margin: "8px 0 0" }}>
                    {adviceError}
                  </p>
                ) : null}
              </div>

              {routeNeedsNotice ? (
                <Notice
                  tone="amber"
                  title={
                    routeProviderUnavailable || unconfiguredRoute
                      ? "路线服务尚未配置"
                      : "当前无法查询新路线"
                  }
                >
                  {routeProviderUnavailable
                    ? "暂时无法生成新的出发建议；服务器仍保存计划，最新天气和历史建议仍可查看。"
                    : unconfiguredRoute
                      ? "服务器没有可用的地图路线 Provider，因此不能生成新的 ETA 或路线快照；计划和天气仍可查看。"
                      : "服务器路线来源当前不可用，因此不能生成新的 ETA 或路线快照；计划和天气仍可查看。"}
                  {historyRows.length > 0
                    ? historyHasSnapshot
                      ? ` 仍可查看此前客户端快照产生的历史建议（${historyRows.length} 条）；它们不是本轮新查询。`
                      : ` 仍可查看已有历史建议（${historyRows.length} 条）；它们不是本轮新查询。`
                    : " 目前没有可显示的历史建议。"}
                </Notice>
              ) : null}

              {applicability ? (
                <div className="card card--tight">
                  <div className="flex items-center justify-between gap-10" style={{ flexWrap: "wrap" }}>
                    <span className="micro">结果是否适用</span>
                    <Pill tone={applicability.tone}>{applicability.label}</Pill>
                  </div>
                  <p className="micro" style={{ margin: "6px 0 0", lineHeight: 1.6 }}>
                    {applicability.detail}
                  </p>
                  <p className="micro" style={{ margin: "4px 0 0" }}>
                    服务器更新于 {formatDateTime(advisory!.generated_at)} · 有效至{" "}
                    {formatDateTime(advisory!.expires_at)}
                  </p>
                </div>
              ) : null}

              {adviceLoading ? <LoadingCard label="正在读取服务器建议…" /> : null}

              {noAdviceResult ? (
                <div className="card card--amber">
                  <p className="card-title amber" style={{ fontSize: 15 }}>
                    检查完成，但没有关联建议
                  </p>
                  <p className="caption" style={{ margin: "4px 0 0" }}>
                    服务器返回终态，却没有 advisory_id。页面不会用另一条 latest 冒充本次结果。
                  </p>
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => void viewLatest()}
                  >
                    查看已有建议
                  </button>
                </div>
              ) : null}

              {showCheckUnknown ? (
                <div className="card card--amber">
                  <p className="card-title amber" style={{ fontSize: 15 }}>
                    服务器尚未确认本次结果
                  </p>
                  <p className="caption" style={{ margin: "4px 0 0" }}>
                    {pending?.error || "任务记录已保存，但不能把未知当作成功或失败。"}
                  </p>
                  <div className="ask-actions">
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => void viewCheckResult()}
                      disabled={actionBusy !== null}
                    >
                      继续核对
                    </button>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => void viewLatest()}
                    >
                      查看已有建议
                    </button>
                  </div>
                </div>
              ) : null}

              {showCheckFailure ? (
                <div className="card card--red">
                  <p className="card-title red" style={{ fontSize: 15 }}>
                    这次重新分析没有完成
                  </p>
                  <p className="caption red" style={{ margin: "4px 0 0" }}>
                    {pending?.error || "服务器未返回可用建议。"}
                  </p>
                  {pending?.errorCode === "provider_not_configured" ||
                  pending?.errorCode === "eta_not_configured" ? (
                    <p className="micro red" style={{ margin: "6px 0 0" }}>
                      路线服务尚未配置；本次没有生成 ETA 或假快照。
                    </p>
                  ) : null}
                  <button
                    type="button"
                    className="link-button"
                    style={{ color: "var(--m-red)" }}
                    onClick={() => void viewLatest()}
                  >
                    查看已有建议
                  </button>
                </div>
              ) : null}

              {currentAdvisory && !adviceLoading ? (
                <>
                  <div className="flex items-center justify-between gap-10">
                    <span className="micro">
                      {viewMode === "check" ? "本次重新分析的建议" : "已有最新建议"}
                    </span>
                    {viewMode === "check" && pending?.status === "degraded" ? (
                      <Pill tone="amber">数据不完整</Pill>
                    ) : null}
                  </div>
                  <AdvisoryCard
                    advisory={currentAdvisory}
                    onSkip={() => void skipToday()}
                    skipBusy={skipBusy}
                  />
                  <p className="micro" style={{ margin: 0, lineHeight: 1.6 }}>
                    建议按 check 关联读取；同时有其他分析在跑时，这里不会改用另一条
                    latest。过期或属于修改前计划的记录放进下方历史区，服务器记录不删除。
                  </p>
                </>
              ) : null}

              {active ? (
                <div className={`card ${runtimeNeedsAttention ? "card--amber" : "card--tight"}`}>
                  <div className="ask-status">
                    {polling && runtimeView.kind === "recent" ? (
                      <span className="spinner" />
                    ) : (
                      <Clock size={17} className="muted" />
                    )}
                    <span style={{ fontSize: 15 }}>
                      任务“{CHECK_STATUS_LABEL[pending.status]}”
                      {currentPhaseLabel ? `；当前步骤：${currentPhaseLabel}` : "；服务器尚未返回具体步骤"}。
                    </span>
                  </div>
                  <p
                    className="micro"
                    style={{
                      margin: "6px 0 0",
                      lineHeight: 1.6,
                      color: runtimeNeedsAttention ? "var(--m-amber)" : undefined,
                    }}
                  >
                    {runtimeView.label}
                    {runtimeView.kind === "unavailable" && runtime.lastSuccessAt
                      ? ` · 上次成功查询 ${formatDateTime(
                          new Date(runtime.lastSuccessAt).toISOString(),
                        )}`
                      : ""}
                  </p>
                  <p className="micro" style={{ margin: "3px 0 0", lineHeight: 1.6 }}>
                    {runtimeView.detail}
                  </p>
                  <p className="micro" style={{ margin: "4px 0 0", lineHeight: 1.6 }}>
                    本次操作已保存在本机；刷新页面只会读取同一个 check，不会新建第二次分析，
                    也不会取消服务器任务。
                  </p>
                  {!polling ? (
                    <button
                      type="button"
                      className="link-button mt-6"
                      onClick={() => {
                        runtime.refresh();
                        void viewCheckResult();
                      }}
                      disabled={actionBusy !== null}
                    >
                      继续核对
                    </button>
                  ) : null}
                </div>
              ) : null}

              {!advisory && !adviceLoading && !showCheckFailure && !showCheckUnknown && !noAdviceResult && !active && !adviceError && !routeNeedsNotice && historyRows.length === 0 ? (
                <EmptyState
                  title="今天还没有通勤建议"
                  body="建议由服务器检查生成；浏览器不会自行推算出发时间或天气。可以读取已有建议，或主动重新分析。"
                >
                  <button
                    type="button"
                    className="btn btn--secondary"
                    onClick={() => void startAnalysis()}
                    disabled={actionBusy !== null}
                  >
                    {reanalyzeLabel}
                  </button>
                </EmptyState>
              ) : null}

              {!currentAdvisory && !adviceLoading && !active && !showCheckFailure && !showCheckUnknown && !noAdviceResult && !adviceError && historyRows.length > 0 ? (
                <EmptyState
                  title="当前没有未过期建议"
                  body="服务器最新记录已过期或属于修改前的计划版本，已放入下方历史区；服务器记录不会被删除。可以主动重新分析，查看本次结果。"
                >
                  <button
                    type="button"
                    className="btn btn--secondary"
                    onClick={() => void startAnalysis()}
                    disabled={actionBusy !== null}
                  >
                    {reanalyzeLabel}
                  </button>
                </EmptyState>
              ) : null}

              {historyRows.length > 0 ? (
                <div className="mt-16">
                  <div className="flex items-center justify-between gap-10">
                    <h2 style={{ margin: 0, fontSize: 16 }}>历史建议</h2>
                    <Pill tone="neutral">{historyRows.length} 条</Pill>
                  </div>
                  <p className="micro" style={{ margin: "4px 0 8px", lineHeight: 1.6 }}>
                    本机已加载过的服务器记录，按服务器字段展示；PA 暂无历史列表接口，
                    因此这里不是完整历史，也不会删除服务器记录。
                  </p>
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
              ) : null}

              <Notice>
                服务器保存计划与检查状态；推送需要先在 Bark 里绑定并使用自动提醒。
                页面关闭后浏览器不再轮询，服务器端状态仍以 PA 为准。
              </Notice>
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
                删除会停止后续检查；不会删除已经生成的检查记录与建议。
              </p>
            </div>
          )}
        </>
      ) : null}

      <ConfirmDialog
        open={confirmDelete}
        title="删除这个通勤计划？"
        body="删除后不再按这个计划生成检查与提醒。已生成的历史记录仍留在服务器上。"
        confirmLabel="删除计划"
        danger
        busy={deleting}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => void removePlan()}
      />
    </PageShell>
  );
}

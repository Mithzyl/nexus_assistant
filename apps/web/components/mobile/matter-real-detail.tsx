"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, Circle, Pencil, Trash2, Undo2 } from "lucide-react";

import type {
  DraftConfirmResponse,
  IntakePendingContext,
  MatterIntakeResponse,
  MatterChange,
  MatterDetail,
  MatterDraft,
  MatterReminder,
  MatterSchedule,
  MatterSupplement,
  MatterTodo,
  RouteEstimate,
  TodayRoute,
} from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import { formatDateTime } from "@/lib/mobile/format";
import {
  archiveMatter,
  cancelDraft,
  completeMatterTodo,
  createMatterEditDraft,
  createSchedule,
  createTodo,
  deleteMatter,
  getMatter,
  getMatterProgressCard,
  getToday,
  initialDeleteMatter,
  intakeMatterProgress,
  getMatterSupplement,
  listMatterChanges,
  listMatterSupplements,
  newOperationKey,
  reopenMatter,
  reopenMatterTodo,
  confirmSummary,
  requestTodayRefresh,
  revertMatterChange,
  updateMatter,
} from "@/lib/mobile/matters-api";
import {
  changeReasonText,
  describeRevert,
  isEditableSchedule,
  isRecordChangedError,
  summarizeMatterChange,
  matterProgressText,
  reminderDisplayTitle,
} from "@/lib/mobile/matter-draft.mjs";
import { useAuth, useUI } from "./providers";
import { MatterDraftSheet } from "./matter-draft-sheet";
import { ApiOperationCard, EmbeddedResponseCard } from "./api-operation-card";
import { MatterFollowUps } from "./matter-follow-ups";
import { MatterSupplementComposer } from "./matter-supplement-composer";
import { MatterAttachmentIntake } from "./matter-attachment-intake";
import { MatterSupplementReplySheet } from "./matter-supplement-reply-sheet";
import type { SupplementReplyTarget } from "./matter-supplement-reply-sheet";
import { MatterSupplementList } from "./matter-supplement-list";
import { RouteTextCard } from "./route-text-card";
import { RoutePlannerSheet } from "./route-planner-sheet";
import { RouteMapSheet } from "./route-map-sheet";
import { ConfirmSheet, ProgressSheet } from "./work-plan-ui";
import {
  ConfirmDialog,
  EmptyState,
  ErrorCard,
  LoadingCard,
  LoginRequired,
  PageShell,
  Pill,
  Sheet,
} from "./ui";

function syncLabel(record: MatterSchedule | MatterReminder): string {
  const reminder = "trigger_at" in record;
  const source = record.source;
  const status = record.sync_status;
  if (source === "ios_eventkit" || source === "ios_reminders") {
    return status === "ios_synced" ? "已从日历同步" : "等待同步确认";
  }
  if (status === "pending_ios") {
    return reminder ? "提醒已保存，等待同步到手机" : "已加入日程，等待同步到手机";
  }
  if (status === "ios_synced") return "已同步";
  if (!reminder) return "已加入日程";
  const reminderStatus = (record as MatterReminder).status;
  if (reminderStatus === "due") return "已到提醒时间";
  if (reminderStatus === "acknowledged") return "已确认";
  if (reminderStatus === "cancelled") return "提醒已取消";
  return "提醒已保存";
}

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function whenLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return formatDateTime(value);
  const time = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  return `${date.getMonth() + 1}月${date.getDate()}日 ${WEEKDAYS[date.getDay()]} ${time}`;
}

/**
 * Read server-owned route summaries for the supplied schedules.
 *
 * Today exposes only persisted summary fields, so detail cards must not infer
 * that an estimate was linked merely because the estimate endpoint responded.
 */
async function fetchRouteSummaries(
  schedules: MatterSchedule[],
): Promise<Record<string, TodayRoute>> {
  const dates = Array.from(
    new Set(
      schedules.map((schedule) =>
        new Date(schedule.starts_at).toLocaleDateString("en-CA"),
      ),
    ),
  );
  const views = await Promise.all(dates.map((date) => getToday(date)));
  const summaries: Record<string, TodayRoute> = {};
  for (const view of views) {
    for (const schedule of view.schedules) {
      summaries[schedule.schedule_id] = schedule.route;
    }
  }
  return summaries;
}

function TodoRow({
  todo,
  busy,
  onToggle,
  onEditTime,
}: {
  todo: MatterTodo;
  busy: boolean;
  onToggle: () => void;
  onEditTime: () => void;
}) {
  return (
    <div className={`todo-row${todo.status === "done" ? " todo-row--done" : ""}`}>
      <button
        type="button"
        className="todo-check"
        onClick={onToggle}
        disabled={busy}
        aria-pressed={todo.status === "done"}
        aria-label={todo.status === "done" ? `取消完成 ${todo.title}` : `完成 ${todo.title}`}
      >
        {todo.status === "done" ? <Check size={16} /> : <Circle size={16} />}
      </button>
      <span className="grow" style={{ textAlign: "left" }}>
        <span style={{ display: "block" }}>{todo.title}</span>
        <span className="micro">
          {todo.due_at ? `截止 ${formatDateTime(todo.due_at)}` : "未设截止"}
          {todo.reminder_at ? ` · 提醒 ${formatDateTime(todo.reminder_at)}` : " · 未设提醒"}
          {todo.status === "done" ? " · 已完成" : ""}
        </span>
      </span>
      <button type="button" className="link-button" onClick={onEditTime} disabled={busy} aria-label={`调整 ${todo.title} 的时间`}>
        时间
      </button>
    </div>
  );
}

/** Convert a `datetime-local` value to UTC; empty input means "not provided". */
function localInputToIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/** Display real matter data and the results of API operations from progress input. */
export function MatterRealDetail({ id }: { id: string }) {
  const router = useRouter();
  const { state, user } = useAuth();
  const { toast } = useUI();
  const [matter, setMatter] = useState<MatterDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [nextStep, setNextStep] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [progressOpen, setProgressOpen] = useState(false);
  const [progressCard, setProgressCard] = useState<Awaited<ReturnType<typeof getMatterProgressCard>>["card"]>(null);
  const [editingProgressCard, setEditingProgressCard] = useState(false);
  const [progressError, setProgressError] = useState<string | null>(null);
  const [progressPendingContext, setProgressPendingContext] =
    useState<IntakePendingContext>({});
  const [progressContinuationRunId, setProgressContinuationRunId] =
    useState<string | null>(null);
  const [progressAnswer, setProgressAnswer] = useState<{ question: string; result: MatterIntakeResponse } | null>(null);
  const [supplements, setSupplements] = useState<MatterSupplement[]>([]);
  const [supplementsLoading, setSupplementsLoading] = useState(true);
  const [supplementsError, setSupplementsError] = useState<string | null>(null);
  const [supplementHasMore, setSupplementHasMore] = useState(false);
  const [supplementLoadingMore, setSupplementLoadingMore] = useState(false);
  const [replyTarget, setReplyTarget] = useState<SupplementReplyTarget | null>(null);
  const [editingDraft, setEditingDraft] = useState<Awaited<ReturnType<typeof intakeMatterProgress>>["draft"] | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiveCancelSchedules, setArchiveCancelSchedules] = useState(true);
  const [archiveCancelReminders, setArchiveCancelReminders] = useState(true);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [reopenOpen, setReopenOpen] = useState(false);
  const [changes, setChanges] = useState<MatterChange[]>([]);
  const [changesLoading, setChangesLoading] = useState(true);
  const [changesError, setChangesError] = useState<string | null>(null);
  const [routeBySchedule, setRouteBySchedule] = useState<Record<string, TodayRoute>>({});
  const [routeDetails, setRouteDetails] = useState<Record<string, RouteEstimate>>({});
  const [routeError, setRouteError] = useState(false);
  const [routeRefresh, setRouteRefresh] = useState(0);
  const [routeMapScheduleId, setRouteMapScheduleId] = useState<string | null>(null);
  const [routePlannerSchedule, setRoutePlannerSchedule] =
    useState<MatterSchedule | null>(null);
  const routePlannerDraftId = useRef<string | null>(null);
  const routePlannerDraftOperation = useRef(0);
  const [revertTarget, setRevertTarget] = useState<MatterChange | null>(null);
  const [initialDeleteTarget, setInitialDeleteTarget] = useState<MatterChange | null>(
    null,
  );
  const [quickAdd, setQuickAdd] = useState<"todo" | "schedule" | null>(null);
  const [quickTitle, setQuickTitle] = useState("");
  const [quickStart, setQuickStart] = useState("");
  const [quickEnd, setQuickEnd] = useState("");
  const [quickDue, setQuickDue] = useState("");
  const [quickLocation, setQuickLocation] = useState("");
  const [quickError, setQuickError] = useState<string | null>(null);
  const [quickSaving, setQuickSaving] = useState(false);
  const quickAddKey = useRef<string | null>(null);
  const progressOperation = useRef<{
    text: string;
    key: string;
    continuationRunId: string | null;
  } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setChangesLoading(true);
    setChangesError(null);
    const [matterResult, changesResult, supplementsResult, progressResult] = await Promise.allSettled([
      getMatter(id),
      listMatterChanges(id),
      listMatterSupplements(id),
      getMatterProgressCard(id),
    ]);
    setProgressCard(progressResult.status === "fulfilled" ? progressResult.value.card : null);
    if (matterResult.status === "fulfilled") {
      setMatter(matterResult.value);
    } else {
      setError(
        matterResult.reason instanceof Error
          ? matterResult.reason
          : new Error("读取事项失败"),
      );
    }
    if (changesResult.status === "fulfilled") {
      setChanges(changesResult.value);
    } else {
      setChangesError(
        changesResult.reason instanceof Error
          ? changesResult.reason.message
          : "读取修改记录失败",
      );
    }
    if (supplementsResult.status === "fulfilled") {
      setSupplements(supplementsResult.value);
      setSupplementHasMore(supplementsResult.value.length === 20);
      setSupplementsError(null);
    } else {
      setSupplementsError(
        supplementsResult.reason instanceof Error
          ? supplementsResult.reason.message
          : "读取补充内容失败",
      );
    }
    setLoading(false);
    setChangesLoading(false);
    setSupplementsLoading(false);
  }, [id]);

  /** Keep one supplement row in place after accept, retry or a poll result. */
  const onSupplementUpdated = useCallback((next: MatterSupplement) => {
    setSupplements((current) => {
      const known = current.some((item) => item.supplement_id === next.supplement_id);
      return known
        ? current.map((item) => (item.supplement_id === next.supplement_id ? next : item))
        : [next, ...current];
    });
  }, []);

  /** Refresh a corrected/answered original without inserting it as a new row. */
  const onSupplementRefreshed = useCallback((next: MatterSupplement) => {
    setSupplements((current) =>
      current.some((item) => item.supplement_id === next.supplement_id)
        ? current.map((item) => (item.supplement_id === next.supplement_id ? next : item))
        : current,
    );
  }, []);

  /** Load one older page of supplements with the owned cursor. */
  const loadMoreSupplements = async () => {
    const last = supplements[supplements.length - 1];
    if (!last || supplementLoadingMore) return;
    setSupplementLoadingMore(true);
    setSupplementsError(null);
    try {
      const page = await listMatterSupplements(id, { beforeId: last.supplement_id });
      setSupplements((current) => {
        const known = new Set(current.map((item) => item.supplement_id));
        return [...current, ...page.filter((item) => !known.has(item.supplement_id))];
      });
      setSupplementHasMore(page.length === 20);
    } catch (caught) {
      setSupplementsError(caught instanceof Error ? caught.message : "读取更早的补充失败");
    } finally {
      setSupplementLoadingMore(false);
    }
  };

  /** Show a prepared attachment draft and pull the latest saved progress into this matter. */
  const attachmentIntakeFinished = useCallback((result: { draft?: MatterDraft | null }) => {
    if (result.draft) setEditingDraft(result.draft);
    void load();
  }, [load]);

  useEffect(() => {
    if (state !== "loading" && state !== "anonymous") void load();
  }, [load, state, user?.user_id]);

  useEffect(
    () => () => {
      routePlannerDraftOperation.current += 1;
      const draftId = routePlannerDraftId.current;
      routePlannerDraftId.current = null;
      if (draftId) void cancelDraft(draftId).catch(() => undefined);
    },
    [],
  );

  useEffect(() => {
    setRouteError(false);
    if (!matter || matter.schedules.length === 0) {
      setRouteBySchedule({});
      setRouteDetails({});
      setRouteMapScheduleId(null);
      return;
    }
    setRouteDetails({});
    setRouteBySchedule({});
    setRouteMapScheduleId(null);
    let alive = true;
    void fetchRouteSummaries(matter.schedules)
      .then((summaries) => {
        if (!alive) return;
        setRouteBySchedule(summaries);
      })
      .catch(() => {
        if (alive) { setRouteBySchedule({}); setRouteError(true); }
      });
    return () => {
      alive = false;
    };
  }, [matter, routeRefresh]);

  /** Run a matter action and keep its result feedback brief. */
  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    try {
      await action();
    } catch (caught) {
      const message = caught instanceof Error ? caught.message.trim() : "";
      const briefError = message && Array.from(message).length <= 18 ? message : null;
      if (caught instanceof ApiError && isRecordChangedError(caught.code)) {
        toast("记录已发生变化，请刷新后检查");
        await load();
      } else if (caught instanceof ApiError && caught.code === "revert_from_source_matter") {
        toast(briefError ?? "请从原事项撤回");
        await load();
      } else if (caught instanceof ApiError && caught.code === "option_not_executable") {
        toast(briefError ?? "这项操作暂不可用");
      } else if (caught instanceof ApiError && caught.code === "creation_revert_unsupported") {
        toast("暂不支持撤回新建事项");
        await load();
      } else if (caught instanceof ApiError && caught.code === "incomplete_change_snapshot") {
        toast("旧变更记录不完整，无法撤回");
        await load();
      } else if (
        caught instanceof ApiError &&
        ["not_initial_create", "initial_delete_unavailable", "progress_added"].includes(
          caught.code,
        )
      ) {
        toast(briefError ?? "记录已改变，请重新查看");
        await load();
      } else {
        toast(briefError ?? "操作失败，请重试");
      }
    } finally {
      setBusy(null);
    }
  };

  const saveEdit = async () => {
    if (!matter) return;
    setEditError(null);
    if (!title.trim()) {
      setEditError("标题不能为空。");
      return;
    }
    await run("edit", async () => {
      try {
        await updateMatter(matter.matter_id, {
          expected_version: matter.version,
          title: title.trim(),
          goal: goal.trim(),
          next_step: nextStep.trim(),
        });
        setEditOpen(false);
        toast("事项内容已保存");
        await load();
      } catch (caught) {
        if (caught instanceof ApiError && caught.code === "matter_changed") {
          await load();
          setEditOpen(false);
          toast("事项已更新，请重新检查");
          return;
        }
        throw caught;
      }
    });
  };

  /** Open the change-arrangement sheet with a clean operation history. */
  const openChange = (adjustProgress = false) => {
    setEditingProgressCard(adjustProgress);
    setProgressError(null);
    setProgressPendingContext({});
    setProgressContinuationRunId(null);
    progressOperation.current = null;
    setProgressOpen(true);
  };

  /** Submit one explicit change request; missing details come back as a question. */
  const submitChange = async (
    text: string,
    continuationOverride?: string | null,
  ) => {
    if (!matter) return;
    const activeContinuation =
      continuationOverride === undefined
        ? progressContinuationRunId
        : continuationOverride;
    setBusy("progress");
    setProgressError(null);
    setProgressPendingContext({});
    if (
      !progressOperation.current ||
      progressOperation.current.text !== text ||
      progressOperation.current.continuationRunId !== activeContinuation
    ) {
      progressOperation.current = {
        text,
        key: newOperationKey("web-progress"),
        continuationRunId: activeContinuation,
      };
    }
    try {
      const result = await intakeMatterProgress(
        matter.matter_id,
        text,
        progressOperation.current.key,
        activeContinuation,
      );
      if (result.outcome === "answer" || ((result.outcome === "failed" || result.outcome === "unknown") && result.pending_context?.execution_result)) {
        setProgressOpen(false);
        setProgressContinuationRunId(null);
        setProgressPendingContext({});
        progressOperation.current = null;
        setProgressAnswer({ question: text, result });
        if (result.pending_context?.execution_result || editingProgressCard) await load();
        return;
      }
      if (result.draft) {
        setProgressOpen(false);
        setProgressContinuationRunId(null);
        setProgressPendingContext({});
        progressOperation.current = null;
        setEditingDraft(result.draft);
        return;
      }
      if (result.outcome === "needs_clarification") {
        progressOperation.current = null;
        setProgressPendingContext(result.pending_context ?? {});
        setProgressContinuationRunId(result.local_run_id);
      }
      setProgressError(
        result.clarify ||
          result.message ||
          "还缺一点信息，补充后可以继续。",
      );
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        (caught.status >= 500 ||
          caught.code.includes("timeout") ||
          caught.code.includes("unknown"))
      ) {
        setProgressError(
          "还没收到结果。保留内容，用同样的说法再试一次。",
        );
      } else {
        setProgressError(
          caught instanceof Error ? caught.message : "暂时没提交上，再试一次。",
        );
      }
    } finally {
      setBusy(null);
    }
  };

  const toggleTodo = async (todo: MatterTodo) => {
    if (!matter) return;
    await run(`todo-${todo.todo_id}`, async () => {
      if (todo.status === "done") await reopenMatterTodo(matter.matter_id, todo.todo_id);
      else await completeMatterTodo(matter.matter_id, todo.todo_id);
      await load();
    });
  };

  const onConfirmed = async (response: DraftConfirmResponse) => {
    setEditingDraft(null);
    toast(confirmSummary(response));
    await load();
    requestTodayRefresh();
  };

  const openScheduleDraft = async (schedule: MatterSchedule) => {
    if (!matter) return;
    await run(`schedule-draft-${schedule.schedule_id}`, async () => {
      const draft = await createMatterEditDraft(matter.matter_id, {
        idempotency_key: newOperationKey("web-schedule"),
        schedule_id: schedule.schedule_id,
      });
      setEditingDraft(draft);
    });
  };

  const openTodoDraft = async (todo: MatterTodo) => {
    if (!matter) return;
    await run(`todo-draft-${todo.todo_id}`, async () => {
      const draft = await createMatterEditDraft(matter.matter_id, {
        idempotency_key: newOperationKey("web-todo"),
        todo_id: todo.todo_id,
      });
      setEditingDraft(draft);
    });
  };

  /** Reset the small add form; the kind decides which fields are shown. */
  const openQuickAdd = (kind: "todo" | "schedule") => {
    quickAddKey.current = newOperationKey(kind === "todo" ? "web-todo-new" : "web-schedule-new");
    setQuickAdd(kind);
    setQuickTitle("");
    setQuickStart("");
    setQuickEnd("");
    setQuickDue("");
    setQuickLocation("");
    setQuickError(null);
  };

  /**
   * Save one todo or schedule through the independent record endpoints.
   *
   * This never creates an empty matter or a draft first. A conflict keeps the
   * typed values and reloads the matter so the next save uses the current version.
   */
  const submitQuickAdd = async () => {
    if (!matter || !quickAdd || quickSaving) return;
    const title = quickTitle.trim();
    if (!title) {
      setQuickError("给它起个名字。");
      return;
    }
    if (quickAdd === "schedule") {
      const startsAt = localInputToIso(quickStart);
      if (!startsAt) {
        setQuickError("选一下开始时间。");
        return;
      }
      const endsAt = localInputToIso(quickEnd);
      if (endsAt && new Date(endsAt) <= new Date(startsAt)) {
        setQuickError("结束时间要晚于开始。");
        return;
      }
    }
    setQuickSaving(true);
    setQuickError(null);
    try {
      if (quickAdd === "todo") {
        const dueAt = localInputToIso(quickDue);
        const result = await createTodo({
          idempotency_key: quickAddKey.current as string,
          title,
          matter_id: matter.matter_id,
          expected_matter_version: matter.version,
          ...(dueAt ? { due_at: dueAt } : {}),
        });
        if (!result.applied) {
          setQuickError("这次没存上，再试一次。");
          await load();
          return;
        }
        toast("待办已新增");
      } else {
        const startsAt = localInputToIso(quickStart) as string;
        const endsAt = localInputToIso(quickEnd);
        const result = await createSchedule({
          idempotency_key: quickAddKey.current as string,
          title,
          starts_at: startsAt,
          ends_at: endsAt,
          matter_id: matter.matter_id,
          expected_matter_version: matter.version,
          location: quickLocation.trim() || null,
        });
        if (!result.applied) {
          setQuickError("这次没存上，再试一次。");
          await load();
          return;
        }
        toast("日程已新增");
      }
      quickAddKey.current = null;
      setQuickAdd(null);
      requestTodayRefresh();
      await load();
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "matter_changed") {
        setQuickError("这件刚在别处改过，已经更新，再保存一次就好。");
        await load();
      } else if (caught instanceof ApiError && caught.code === "idempotency_key_reused") {
        quickAddKey.current = newOperationKey("web-todo-new");
        setQuickError("这次内容和刚才不一样，重新试一次。");
      } else {
        setQuickError(caught instanceof Error ? caught.message : "这次没加成功。");
      }
    } finally {
      setQuickSaving(false);
    }
  };

  /** Keep the confirmation open until the service acknowledges the reversal. */
  const revertChange = async (change: MatterChange) => {
    if (!matter) return;
    await run("revert", async () => {
      await revertMatterChange(matter.matter_id, change.change_id, matter.version);
      setRevertTarget(null);
      toast("已撤回这次调整");
      await load();
      requestTodayRefresh();
    });
  };

  /** Keep only a server-linked estimate and retain its known origin for the map. */
  const onRouteEstimated = async (
    scheduleId: string,
    result: RouteEstimate,
  ) => {
    const schedule = matter?.schedules.find(
      (item) => item.schedule_id === scheduleId,
    );
    if (!schedule) {
      throw new Error("当前日程已变化，请刷新后重新测算。");
    }
    const summaries = await fetchRouteSummaries([schedule]);
    if (summaries[scheduleId]?.status !== "available") {
      setRouteBySchedule((current) => {
        const next = { ...current };
        delete next[scheduleId];
        return next;
      });
      throw new Error("路线结果还没有关联到这条日程，请刷新后重试。");
    }
    setRouteDetails((current) => ({ ...current, [scheduleId]: result }));
    setRouteBySchedule((current) => ({ ...current, ...summaries }));
    requestTodayRefresh();
  };

  /** Create one short-lived preview only when a route search needs a bound draft. */
  const getRoutePlannerSearchDraft = async (): Promise<string> => {
    if (routePlannerDraftId.current) return routePlannerDraftId.current;
    if (!matter || !routePlannerSchedule) {
      throw new Error("当前日程已变化，请重新打开路线面板。");
    }
    const operation = routePlannerDraftOperation.current;
    const draft = await createMatterEditDraft(matter.matter_id, {
      idempotency_key: newOperationKey("web-route-search"),
      schedule_id: routePlannerSchedule.schedule_id,
    });
    if (operation !== routePlannerDraftOperation.current) {
      void cancelDraft(draft.draft_id).catch(() => undefined);
      throw new Error("路线面板已关闭，没有继续查询起点。");
    }
    routePlannerDraftId.current = draft.draft_id;
    return draft.draft_id;
  };

  /** Close the route planner and cancel any preview created only for place search. */
  const closeRoutePlanner = () => {
    routePlannerDraftOperation.current += 1;
    const draftId = routePlannerDraftId.current;
    routePlannerDraftId.current = null;
    setRoutePlannerSchedule(null);
    if (draftId) void cancelDraft(draftId).catch(() => undefined);
  };

  if (state === "anonymous") {
    return (
      <PageShell title="事项详情" fallbackHref="/matters">
        <LoginRequired title="登录后查看事项详情" body="登录账号后可以查看此事项的进展和安排。" />
      </PageShell>
    );
  }
  if (loading && !matter) {
    return <PageShell title="事项详情" fallbackHref="/matters"><div style={{ padding: "0 var(--m-gutter)" }}><LoadingCard label="正在读取事项…" /></div></PageShell>;
  }
  if (error && !matter) {
    return (
      <PageShell title="事项详情" fallbackHref="/matters">
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <ErrorCard message={error.message} onRetry={() => void load()} />
        </div>
      </PageShell>
    );
  }
  if (!matter) {
    return (
      <PageShell title="事项详情" fallbackHref="/matters">
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <EmptyState title="事项不存在" body="它可能已被删除，或当前账号没有这条事项。" />
        </div>
      </PageShell>
    );
  }

  const routeMapSchedule =
    matter.schedules.find((schedule) => schedule.schedule_id === routeMapScheduleId) ??
    null;
  const routeMapDetails = routeMapSchedule
    ? routeDetails[routeMapSchedule.schedule_id] ?? null
    : null;
  const routeMapSummary = routeMapSchedule
    ? routeBySchedule[routeMapSchedule.schedule_id] ?? null
    : null;
  // Only reminders that can still be cancelled are "not yet triggered"; due ones stay due.
  const pendingReminders = matter.reminders.filter(
    (reminder) => reminder.status === "scheduled",
  );
  const openReminders = matter.reminders.filter(
    (reminder) => reminder.status === "scheduled" || reminder.status === "due",
  );
  const futureSchedules = matter.schedules.filter(
    (schedule) =>
      !schedule.deleted_at && schedule.status === "scheduled" &&
      new Date(schedule.starts_at).getTime() > Date.now(),
  );
  const primarySchedule = futureSchedules[0] ?? matter.schedules.find((schedule) => !schedule.deleted_at) ?? null;
  // Supplement originals are rendered once in their own section, never twice.
  const visibleProgress = matter.progress.filter((entry) => entry.kind !== "supplement");
  return (
    <PageShell
      title="事项详情"
      fallbackHref="/matters"
      action={
        <div className="matter-heading-actions">
          <button type="button" className="link-button" onClick={() => openChange()}>
            改变安排
          </button>
          <button
            type="button"
            className="icon-button"
            aria-label="纠正事项"
            onClick={() => {
              setTitle(matter.title);
              setGoal(matter.goal);
              setNextStep(matter.next_step ?? "");
              setEditError(null);
              setEditOpen(true);
            }}
          >
            <Pencil size={17} />
          </button>
        </div>
      }
    >
      <div className="work-detail">
        <section className="matter-head">
          <Pill tone={matter.status === "active" ? "green" : matter.status === "waiting" ? "amber" : "neutral"}>
            {matter.status === "active" ? "正在推进" : matter.status === "waiting" ? "跟进中" : "已归档"}
          </Pill>
          <h2 className="serif-title matter-head__title">{matter.title}</h2>
          {primarySchedule ? (
            <>
              <p className="matter-head__when">{whenLabel(primarySchedule.starts_at)}</p>
              {primarySchedule.location ? <p className="matter-head__where">{primarySchedule.location}</p> : null}
            </>
          ) : null}
        </section>

        {progressCard?.card_type === "long_task_progress" ? (
          <EmbeddedResponseCard card={progressCard} onAdjust={matter.status === "active" || matter.status === "waiting" ? () => openChange(true) : undefined} />
        ) : null}

        <Link className="link-button" href={`/me/attachments?matter=${encodeURIComponent(matter.matter_id)}`}>事项图片</Link>
        {state === "signedIn" ? <MatterAttachmentIntake key={matter.matter_id} matterId={matter.matter_id}
          canOrganize={matter.status === "active" || matter.status === "waiting"}
          onFinished={attachmentIntakeFinished} /> : null}

        <div className="matter-detail-layout">
          <div className="matter-detail-main">
            <section>
              <MatterSupplementComposer
                matterId={matter.matter_id}
                onSaved={(supplement) => {
                  onSupplementUpdated(supplement);
                  // Saving bumps the matter version; refresh it so later edits do not conflict.
                  void getMatter(matter.matter_id).then(setMatter).catch(() => undefined);
                  toast("补充已保存");
                }}
                onMatterChanged={() => void load()}
              />
              {supplementsError ? (
                <p className="micro" style={{ marginTop: 8 }}>{supplementsError}</p>
              ) : null}
              {supplementsLoading ? (
                <p className="micro" style={{ marginTop: 10 }}>正在读取补充内容…</p>
              ) : supplements.length > 0 ? (
                <div className="card" style={{ marginTop: 12 }}>
                  <h2 className="section-title" style={{ fontSize: 16, margin: "0 0 10px" }}>补充记录</h2>
                  <MatterSupplementList
                    matterId={matter.matter_id}
                    items={supplements}
                    onUpdated={onSupplementUpdated}
                    onReply={setReplyTarget}
                    hasMore={supplementHasMore}
                    loadingMore={supplementLoadingMore}
                    onLoadMore={() => void loadMoreSupplements()}
                  />
                </div>
              ) : null}
            </section>

            <section>
              <h2 className="section-title" style={{ marginBottom: 8 }}>安排</h2>
          <div className="card card--tight">
            {matter.schedules.length === 0 &&
            openReminders.length === 0 ? (
              <p className="caption" style={{ margin: 0 }}>还没有安排日程或提醒</p>
            ) : (
              <div className="m-stack-10">
                {matter.schedules.map((schedule) => (
                  <div key={schedule.schedule_id} id={`schedule-${schedule.schedule_id}`} className="schedule-block">
                    <p className="card-title" style={{ fontSize: 15 }}>{schedule.title}</p>
                    <p className="caption" style={{ margin: "2px 0 0" }}>
                      {formatDateTime(schedule.starts_at)}
                      {schedule.ends_at ? `–${new Date(schedule.ends_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}` : ""}
                      {schedule.location ? ` · ${schedule.location}` : ""}
                    </p>
                    <div className="flex items-center gap-8 mt-4" style={{ flexWrap: "wrap" }}>
                      <Pill tone={schedule.sync_status === "ios_synced" ? "green" : schedule.sync_status === "pending_ios" ? "amber" : "neutral"}>
                        {syncLabel(schedule)}
                      </Pill>
                      <button
                        type="button"
                        className="link-button"
                        onClick={() => void openScheduleDraft(schedule)}
                        disabled={Boolean(busy) || !isEditableSchedule(schedule)}
                        title={isEditableSchedule(schedule) ? "调整标题、时间或地点" : "苹果导入日程需在苹果端修改"}
                      >
                        <Pencil size={12} /> 调整
                      </button>
                    </div>
                    {routeDetails[schedule.schedule_id]?.status === "available" || routeBySchedule[schedule.schedule_id]?.status === "available" ? <RouteTextCard
                      embedded
                      originLabel={
                        routeBySchedule[schedule.schedule_id]?.origin_basis === "previous_schedule"
                          ? "从上一项安排出发"
                          : routeBySchedule[schedule.schedule_id]?.origin_basis === "user_confirmed"
                            ? "从已确认起点出发"
                            : routeBySchedule[schedule.schedule_id]?.origin_basis === "memory"
                              ? "从记忆地点出发" : null
                      }
                      destinationLabel={schedule.location || schedule.title}
                      arrivalAt={schedule.starts_at}
                      arrivalLabel="安排时间"
                      mode={
                        routeDetails[schedule.schedule_id]?.mode ??
                        routeBySchedule[schedule.schedule_id]?.mode ??
                        null
                      }
                      durationSeconds={
                        routeDetails[schedule.schedule_id]?.duration_seconds ??
                        routeBySchedule[schedule.schedule_id]?.duration_seconds ??
                        null
                      }
                      distanceMeters={routeDetails[schedule.schedule_id]?.distance_meters}
                      walkingDistanceMeters={
                        routeDetails[schedule.schedule_id]?.walking_distance_meters
                      }
                      transferCount={routeDetails[schedule.schedule_id]?.transfer_count}
                      transfersSummary={
                        routeDetails[schedule.schedule_id]?.transfers_summary
                      }
                      fareCny={routeDetails[schedule.schedule_id]?.fare_cny}
                      fareKind={routeDetails[schedule.schedule_id]?.fare_kind}
                      fareNotice={routeDetails[schedule.schedule_id]?.fare_notice}
                      timingBasis={routeDetails[schedule.schedule_id]?.timing_basis}
                      accuracyNotice={routeDetails[schedule.schedule_id]?.accuracy_notice}
                      state={
                        routeBySchedule[schedule.schedule_id]?.status === "available"
                          ? "verified"
                          : routeBySchedule[schedule.schedule_id]?.status === "unavailable"
                            ? "unavailable"
                            : "unverified"
                      }
                      notice={
                        routeBySchedule[schedule.schedule_id]?.status === "available"
                          ? routeBySchedule[schedule.schedule_id]?.fetched_at
                            ? `路线结果取得于 ${formatDateTime(routeBySchedule[schedule.schedule_id].fetched_at || "")}`
                            : "路线结果已保存。"
                          : routeBySchedule[schedule.schedule_id]?.message ||
                            "这条日程还没有关联路线结果，不能沿用其他通勤建议。"
                      }
                      mapTarget={{
                        label: schedule.location_address || schedule.location,
                        latitude: schedule.latitude,
                        longitude: schedule.longitude,
                        coordinateSystem: schedule.coordinate_system,
                        address: schedule.location_address,
                        city: schedule.location_city,
                      }}
                      onViewRoute={
                        routeDetails[schedule.schedule_id]?.status === "available" ||
                        routeBySchedule[schedule.schedule_id]?.status === "available"
                          ? () => setRouteMapScheduleId(schedule.schedule_id)
                          : undefined
                      }
                      onPlanRoute={() => setRoutePlannerSchedule(schedule)}
                    /> : <div className="mt-6">
                      {routeBySchedule[schedule.schedule_id]?.status === "unavailable" ? <p className="caption">{routeBySchedule[schedule.schedule_id]?.message || "暂时无法测算出行路线，已保存的日程不受影响。"}</p> : null}
                      <button type="button" className="link-button" onClick={() => setRoutePlannerSchedule(schedule)}>规划出行（可选）</button>
                    </div>}
                  </div>
                ))}
                {openReminders.map((reminder) => (
                  <p key={reminder.reminder_id} className="caption" style={{ margin: "4px 0 0" }}>
                    {reminderDisplayTitle(reminder.title)} · {formatDateTime(reminder.trigger_at)} · {syncLabel(reminder)}
                    {reminder.status === "due" ? " · 等待你确认" : ""}
                  </p>
                ))}
              </div>
            )}
            {routeError ? <p role="alert" className="caption">路线信息读取失败，已保存的日程仍保留。<button type="button" className="link-button" onClick={() => setRouteRefresh(value => value + 1)}>重新读取路线</button></p> : null}
            <div className="action-row">
              <button type="button" className="btn btn--primary btn--small" onClick={() => openQuickAdd("schedule")} disabled={Boolean(busy)}>
                新建日程
              </button>
              <button type="button" className="link-button" onClick={() => {
                setProgressError(null);
                setProgressPendingContext({});
                setProgressContinuationRunId(null);
                progressOperation.current = null;
                setProgressOpen(true);
              }}>
                让助手安排
              </button>
            </div>
            {matter.commute_plan_id ? (
              <p className="micro" style={{ margin: "8px 0 0" }}>
                这条和通勤计划连着，
                <Link href={`/me/commute/${matter.commute_plan_id}`} className="link-button link-button--inline">
                  到达时间去通勤页改
                </Link>
                。
              </p>
            ) : null}
          </div>
        </section>

        <section>
          <h2 className="section-title" style={{ marginBottom: 8 }}>待办</h2>
          <div className="card card--tight">
            {matter.todos.length === 0 ? (
              <p className="caption" style={{ margin: 0 }}>没有待办子项。</p>
            ) : (
              <div className="m-stack-10">
                {matter.todos.map((todo) => (
                  <div key={todo.todo_id} id={`todo-${todo.todo_id}`}>
                    <TodoRow
                      todo={todo}
                      busy={busy === `todo-${todo.todo_id}`}
                      onToggle={() => void toggleTodo(todo)}
                      onEditTime={() => void openTodoDraft(todo)}
                    />
                  </div>
                ))}
              </div>
            )}
            <button type="button" className="link-button mt-8" onClick={() => openQuickAdd("todo")} disabled={Boolean(busy)}>
              新建待办
            </button>
          </div>
        </section>

        <MatterFollowUps matterId={matter.matter_id} onChanged={() => void load()} />

        {visibleProgress.length > 0 ? (
          <section>
            <div className="matter-section-head">
              <h2 className="section-title">其他进展</h2>
              <span className="micro">{`全部 ${visibleProgress.length} 条 ›`}</span>
            </div>
            <div className="card card--tight">
              <div className="matter-log">
                {visibleProgress.map((entry) => {
                  const [date, time] = formatDateTime(entry.created_at).split(" ");
                  return (
                    <div key={entry.progress_id} className="matter-log__item">
                      <span className="matter-log__time">{date}<br />{time}</span>
                      <span className="matter-log__text">{matterProgressText(entry.text, matter.title)}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </section>
        ) : null}

        <section id="changes">
          <div className="flex items-center justify-between gap-12" style={{ marginBottom: 8 }}>
            <h2 className="section-title">修改记录</h2>
            <span className="micro">已保存</span>
          </div>
          {changesLoading ? (
            <LoadingCard label="正在读取修改记录…" />
          ) : changesError ? (
            <ErrorCard message={changesError} onRetry={() => void load()} />
          ) : changes.length === 0 ? (
            <p className="caption">还没有修改记录。</p>
          ) : (
            <div className="card card--tight">
              <div className="matter-log">
                {changes.map((change) => {
                  const reason = change.revertable
                    ? null
                    : changeReasonText(change.reason_code, change.reason);
                  const [date, time] = formatDateTime(change.created_at).split(" ");
                  return (
                    <div key={change.change_id} className="matter-log__item">
                      <span className="matter-log__time">{date}<br />{time}</span>
                      <span className="matter-log__text">
                        {summarizeMatterChange(change)}
                        {reason ? <span className="matter-log__reason">{reason}</span> : null}
                        <span className="matter-log__actions">
                          {change.revertable ? (
                            <button
                              type="button"
                              className="link-button"
                              onClick={() => setRevertTarget(change)}
                              disabled={Boolean(busy)}
                            >
                              <Undo2 size={13} /> 撤回
                            </button>
                          ) : null}
                          {change.initial_delete_allowed ? (
                            <button
                              type="button"
                              className="link-button danger-text"
                              onClick={() => setInitialDeleteTarget(change)}
                              disabled={Boolean(busy)}
                            >
                              <Trash2 size={13} /> 删除新事项
                            </button>
                          ) : null}
                          {change.relation === "affected" ? (
                            <button
                              type="button"
                              className="link-button"
                              onClick={() =>
                                router.push(`/matters/${change.source_matter_id}#changes`)
                              }
                            >
                              到「{change.source_matter_title || "来源事项"}」查看并处理
                            </button>
                          ) : null}
                        </span>
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          <p className="micro" style={{ margin: "8px 0 0" }}>
            只有最近一次调整可以撤回。
          </p>
            </section>
          </div>

          <aside className="matter-detail-side">
            <section className="card card--tight">
              <h2 className="section-title" style={{ fontSize: 16, margin: 0 }}>事项状态</h2>
              <div className="matter-side-rows">
                <div className="matter-side-row">
                  <span>状态</span>
                  <span>{matter.status === "active" ? "正在推进" : matter.status === "waiting" ? "跟进中" : "已归档"}</span>
                </div>
                <div className="matter-side-row">
                  <span>最近更新</span>
                  <span>{formatDateTime(matter.updated_at)}</span>
                </div>
                <div className="matter-side-row">
                  <span>下一步</span>
                  <span>{matter.next_step || "还没定"}</span>
                </div>
              </div>
            </section>
            <section className="card card--tight matter-side-change">
              <h2 className="section-title" style={{ fontSize: 16, margin: 0 }}>改变安排</h2>
              <p className="micro" style={{ marginTop: 6 }}>
                说清楚要改哪一场、改成什么，助手会直接执行并返回结果。
              </p>
              <button
                type="button"
                className="btn btn--secondary"
                style={{ width: "100%", marginTop: 10 }}
                onClick={() => openChange()}
              >
                改变安排
              </button>
            </section>
          </aside>
        </div>

        <div className="work-actions">
          {matter.status !== "archived" ? (
            <button type="button" className="btn btn--secondary" onClick={() => {
              setArchiveCancelReminders(true);
              setArchiveCancelSchedules(true);
              setArchiveOpen(true);
            }} disabled={Boolean(busy)}>
              停止跟进
            </button>
          ) : (
            <button type="button" className="btn btn--secondary" onClick={() => setReopenOpen(true)} disabled={Boolean(busy)}>
              恢复跟进
            </button>
          )}
          <button type="button" className="btn btn--quiet danger-text" onClick={() => setDeleteOpen(true)} disabled={Boolean(busy)}>
            <Trash2 size={14} /> 删除
          </button>
        </div>
      </div>

      <MatterSupplementReplySheet
        open={Boolean(replyTarget)}
        matterId={matter.matter_id}
        target={replyTarget}
        onClose={() => setReplyTarget(null)}
        onSaved={(supplement, target) => {
          onSupplementUpdated(supplement);
          // The corrected/answered text lives in another supplement; refresh it so its
          // counts and previews include this new relation without inserting a duplicate.
          void getMatterSupplement(matter.matter_id, target.supplementId)
            .then(onSupplementRefreshed)
            .catch(() => undefined);
          void getMatter(matter.matter_id).then(setMatter).catch(() => undefined);
          toast(target.relationType === "answer" ? "回答已保存" : "纠正已保存");
        }}
      />

      <ProgressSheet
        open={progressOpen}
        title={matter.title}
        heading={editingProgressCard ? "调整进度卡" : "改变安排"}
        initialText={editingProgressCard && progressCard ? `请更新本事项的「${progressCard.title}」进度卡：` : ""}
        note={editingProgressCard ? "告诉助手哪些进展需要更新。" : "说清楚要改哪一场、改成什么，助手会直接执行并返回结果。"}
        stayOpenOnSubmit
        busy={busy === "progress"}
        error={progressError}
        choices={[...(progressPendingContext.choices ?? []).map((choice, index) => ({ id: `clarify-${index}`, title: choice, detail: "", onSelect: () => void submitChange(choice, progressContinuationRunId) })), ...(progressPendingContext.schedule_candidates ?? []).map((candidate) => ({
          id: candidate.schedule_id,
          title: candidate.title,
          detail: `${formatDateTime(candidate.starts_at)}${
            candidate.location ? ` · ${candidate.location}` : ""
          }`,
          onSelect: () => {
            const runId = progressContinuationRunId;
            setProgressError(null);
            setProgressPendingContext({});
            void submitChange(
              `选择「${candidate.title}」（${formatDateTime(candidate.starts_at)}${
                candidate.location ? `，地点：${candidate.location}` : ""
              }）`,
              runId,
            );
          },
        }))]}
        submitLabel={editingProgressCard ? "交给助手" : "改变安排"}
        onClose={() => {
          setProgressOpen(false);
          setProgressError(null);
          setProgressPendingContext({});
          setProgressContinuationRunId(null);
          progressOperation.current = null;
        }}
        onSubmit={(text) => void submitChange(text)}
      />
      <Sheet
        title={quickAdd === "schedule" ? "新建日程" : "新建待办"}
        open={Boolean(quickAdd)}
        onClose={() => setQuickAdd(null)}
      >
        <div className="m-stack-12">
          <label className="field">
            <span className="field__label">标题</span>
            <input
              className="input"
              value={quickTitle}
              onChange={(event) => setQuickTitle(event.target.value)}
            />
          </label>
          {quickAdd === "schedule" ? (
            <>
              <label className="field">
                <span className="field__label">开始时间</span>
                <input
                  className="input"
                  type="datetime-local"
                  value={quickStart}
                  onChange={(event) => setQuickStart(event.target.value)}
                />
              </label>
              <label className="field">
                <span className="field__label">结束时间（可选）</span>
                <input
                  className="input"
                  type="datetime-local"
                  value={quickEnd}
                  onChange={(event) => setQuickEnd(event.target.value)}
                />
              </label>
              <label className="field">
                <span className="field__label">地点（可选）</span>
                <input
                  className="input"
                  value={quickLocation}
                  onChange={(event) => setQuickLocation(event.target.value)}
                />
              </label>
              <p className="micro" style={{ margin: 0 }}>
                只会新增这条日程，不会自动加待办。
              </p>
            </>
          ) : (
            <>
              <label className="field">
                <span className="field__label">截止时间（可选）</span>
                <input
                  className="input"
                  type="datetime-local"
                  value={quickDue}
                  onChange={(event) => setQuickDue(event.target.value)}
                />
              </label>
              <p className="micro" style={{ margin: 0 }}>
                只会新增这条待办，不占用日历。
              </p>
            </>
          )}
          {quickError ? (
            <p className="danger-text" style={{ margin: 0 }} role="alert">
              {quickError}
            </p>
          ) : null}
          <button
            type="button"
            className="btn btn--primary"
            disabled={quickSaving}
            onClick={() => void submitQuickAdd()}
          >
            {quickSaving ? "正在保存…" : "保存"}
          </button>
          <button type="button" className="btn btn--quiet" onClick={() => setQuickAdd(null)}>
            取消
          </button>
        </div>
      </Sheet>

      <Sheet title="纠正事项内容" open={editOpen} onClose={() => setEditOpen(false)}>
        <div className="m-stack-12">
          <label className="field">
            <span className="field__label">标题</span>
            <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">背景</span>
            <textarea className="input" rows={3} value={goal} onChange={(event) => setGoal(event.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">下一步</span>
            <input className="input" value={nextStep} onChange={(event) => setNextStep(event.target.value)} />
          </label>
          {editError ? <p className="danger-text" style={{ margin: 0 }}>{editError}</p> : null}
          <button type="button" className="btn btn--primary" disabled={busy === "edit"} onClick={() => void saveEdit()}>
            {busy === "edit" ? "正在保存…" : "保存修改"}
          </button>
          <button type="button" className="btn btn--quiet" onClick={() => setEditOpen(false)}>取消</button>
        </div>
      </Sheet>

      <MatterDraftSheet
        draft={editingDraft}
        open={Boolean(editingDraft)}
        onClose={() => setEditingDraft(null)}
        onConfirmed={(response) => void onConfirmed(response)}
        onDraftCancelled={() => {
          setEditingDraft(null);
          toast("草稿已取消");
          void load();
          requestTodayRefresh();
        }}
      />
      <RoutePlannerSheet
        open={Boolean(routePlannerSchedule)}
        schedule={routePlannerSchedule}
        previousSchedules={matter.schedules
          .filter(
            (schedule) =>
              routePlannerSchedule &&
              schedule.schedule_id !== routePlannerSchedule.schedule_id &&
              new Date(schedule.starts_at).getTime() <
                new Date(routePlannerSchedule.starts_at).getTime(),
          )
          .sort(
            (left, right) =>
              new Date(right.ends_at || right.starts_at).getTime() -
              new Date(left.ends_at || left.starts_at).getTime(),
          )}
        getPlaceSearchDraft={getRoutePlannerSearchDraft}
        onClose={closeRoutePlanner}
        onEstimated={onRouteEstimated}
      />
      <RouteMapSheet
        open={Boolean(routeMapSchedule)}
        scheduleId={routeMapSchedule?.schedule_id ?? null}
        title={routeMapSchedule ? `路线 · ${routeMapSchedule.title}` : "查看路线"}
        fallbackOriginLabel={
          routeMapDetails?.origin_basis === "previous_schedule" ||
          routeMapSummary?.origin_basis === "previous_schedule"
            ? "从上一项安排出发"
            : routeMapDetails?.origin_basis === "user_confirmed" ||
                routeMapSummary?.origin_basis === "user_confirmed"
              ? "从已确认起点出发"
              : null
        }
        fallbackDestination={
          routeMapSchedule
            ? {
                id: routeMapSchedule.schedule_id,
                label:
                  routeMapSchedule.location_address ||
                  routeMapSchedule.location ||
                  routeMapSchedule.title,
                latitude: routeMapSchedule.latitude as number,
                longitude: routeMapSchedule.longitude as number,
                coordinateSystem: routeMapSchedule.coordinate_system,
              }
            : null
        }
        fallbackMode={
          routeMapDetails?.mode ?? routeMapSummary?.mode ?? null
        }
        fallbackDurationSeconds={
          routeMapDetails?.duration_seconds ??
          routeMapSummary?.duration_seconds ??
          null
        }
        fallbackWalkingDistanceMeters={
          routeMapDetails?.walking_distance_meters
        }
        fallbackTransferCount={routeMapDetails?.transfer_count}
        fallbackTransfersSummary={routeMapDetails?.transfers_summary}
        fallbackFareCny={routeMapDetails?.fare_cny}
        fallbackFareKind={routeMapDetails?.fare_kind}
        fallbackNotice={
          routeMapSummary?.fetched_at
            ? `路线结果取得于 ${formatDateTime(routeMapSummary.fetched_at)}`
            : null
        }
        onClose={() => setRouteMapScheduleId(null)}
      />
      <Sheet
        title="改变安排"
        open={Boolean(progressAnswer)}
        onClose={() => setProgressAnswer(null)}
      >
        {progressAnswer ? (
          <div className="m-stack-12">
            <p className="caption" style={{ margin: 0 }}>
              你说的是“{progressAnswer.question}”
            </p>
            <div className="card">
              <p className="body-text" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                {progressAnswer.result.answer || progressAnswer.result.clarify}
              </p>
            </div>
            <ApiOperationCard context={progressAnswer.result.pending_context ?? {}} />
            {progressAnswer.result.pending_context?.progress_card_status === "version_changed" ? (
              <p className="caption" role="status">进度已更新，请查看最新内容。</p>
            ) : null}
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => setProgressAnswer(null)}
            >
              知道了
            </button>
          </div>
        ) : null}
      </Sheet>
      <ConfirmDialog
        open={Boolean(revertTarget)}
        title="确定要撤回这次修改，恢复成原来的安排吗？"
        body={
          revertTarget
            ? `${describeRevert(revertTarget)}。`
            : ""
        }
        confirmLabel="确认撤回"
        busy={busy === "revert"}
        onCancel={() => setRevertTarget(null)}
        onConfirm={() => {
          if (revertTarget) void revertChange(revertTarget);
        }}
      />
      <ConfirmDialog
        open={Boolean(initialDeleteTarget)}
        title="删除这个新事项？"
        body="删除事项及这次新建的安排。"
        confirmLabel="删除新事项"
        danger
        onCancel={() => setInitialDeleteTarget(null)}
        onConfirm={() => {
          const target = initialDeleteTarget;
          setInitialDeleteTarget(null);
          if (!target) return;
          void run("initial-delete", async () => {
            const result = await initialDeleteMatter(target.matter_id);
            toast(
              result.schedule_ids.length + result.todo_ids.length + result.reminder_ids.length > 0
                ? "已删除新事项及相关安排"
                : "已删除这个新事项",
            );
            requestTodayRefresh();
            router.push("/matters");
          });
        }}
      />
      <Sheet title={`确认归档「${matter.title}」？`} open={archiveOpen} onClose={() => setArchiveOpen(false)}>
        <div className="m-stack-12">
          <div className="notice notice--amber">
            <p className="caption" style={{ margin: "4px 0 0" }}>
              归档后，这件事项还会留着给你查看。不会再自动跟进。勾选的提醒和未来日程会一并取消。
            </p>
          </div>
          <div className="card card--tight">
            <p className="micro" style={{ margin: 0 }}>还没发送的提醒</p>
            {pendingReminders.length > 0 ? (
              <div className="m-stack-8 mt-8">
                {pendingReminders.map((reminder) => (
                  <p key={reminder.reminder_id} className="caption" style={{ margin: 0 }}>
                    {reminder.title} · {formatDateTime(reminder.trigger_at)}
                  </p>
                ))}
              </div>
            ) : (
              <p className="caption" style={{ margin: "4px 0 0" }}>
                没有未触发的提醒。
              </p>
            )}
            {pendingReminders.length > 0 ? (
              <label className="flex items-start gap-8 mt-10">
                <input
                  type="checkbox"
                  checked={archiveCancelReminders}
                  onChange={(event) => setArchiveCancelReminders(event.target.checked)}
                  style={{ marginTop: 3 }}
                />
                <span className="caption" style={{ margin: 0 }}>
                  同时取消以上 {pendingReminders.length} 条还没发送的提醒
                </span>
              </label>
            ) : null}
          </div>
          <div className="card card--tight">
            <p className="micro" style={{ margin: 0 }}>日程范围</p>
            {futureSchedules.length > 0 ? (
              <label className="flex items-start gap-8 mt-10"><input type="checkbox" checked={archiveCancelSchedules} onChange={(event) => setArchiveCancelSchedules(event.target.checked)} /><span className="caption">同时取消以下未发生的应用内日程（共 {futureSchedules.filter((s) => !s.source || s.source === "web_native").length} 项）</span></label>
            ) : null}
            {futureSchedules.length > 0 ? (
              <ul className="compact-list">
                {futureSchedules.map((schedule) => (
                  <li key={schedule.schedule_id}>
                    {schedule.title} · {formatDateTime(schedule.starts_at)}
                    {schedule.source === "ios_eventkit" ? " · 苹果端日程需在苹果设备处理" : ""}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
          <button
            type="button"
            className="btn btn--primary"
            disabled={Boolean(busy)}
            onClick={() => {
              setArchiveOpen(false);
              void run("archive", async () => {
                const result = await archiveMatter(matter.matter_id, {
                  expected_version: matter.version,
                  cancel_reminders: archiveCancelReminders,
                  cancel_future_schedules: archiveCancelSchedules,
                });
                const effect = result.archive_effect;
                toast(effect?.cancelled_schedules ? `已归档，已取消 ${effect.cancelled_schedules} 项日程` : "已归档");
                router.push("/matters");
              });
            }}
          >
            {busy === "archive" ? "正在归档…" : "确认归档"}
          </button>
          <button type="button" className="btn btn--quiet" onClick={() => setArchiveOpen(false)}>
            取消
          </button>
        </div>
      </Sheet>
      <ConfirmSheet
        open={reopenOpen}
        title="恢复跟进这件事？"
        body="恢复后会重新出现在进行中的事项里。"
        confirmLabel="恢复跟进"
        onClose={() => setReopenOpen(false)}
        onConfirm={() => {
          setReopenOpen(false);
          void run("reopen", async () => {
            await reopenMatter(matter.matter_id, { expected_version: matter.version });
            await load();
            toast("已恢复跟进");
          });
        }}
      />
      <ConfirmSheet
        open={deleteOpen}
        title="彻底删除这件事？"
        body="删除此事项及关联安排。"
        confirmLabel="彻底删除"
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => {
          setDeleteOpen(false);
          void run("delete", async () => {
            await deleteMatter(matter.matter_id);
            toast("已删除");
            router.push("/matters");
          });
        }}
      />
    </PageShell>
  );
}

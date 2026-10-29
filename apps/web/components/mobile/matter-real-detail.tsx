"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Circle, Pencil, RefreshCw, Trash2, Undo2 } from "lucide-react";

import type {
  DraftConfirmResponse,
  IntakePendingContext,
  MatterChange,
  MatterDetail,
  MatterReminder,
  MatterSchedule,
  MatterTodo,
  RouteEstimate,
  TodayRoute,
} from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import {
  archiveMatter,
  cancelDraft,
  completeMatterTodo,
  createMatterEditDraft,
  deleteMatter,
  getMatter,
  getToday,
  initialDeleteMatter,
  intakeMatterProgress,
  listMatterChanges,
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
} from "@/lib/mobile/matter-draft.mjs";
import { dateTimeLabel } from "@/lib/mobile/work-plan";
import { useAuth, useUI } from "./providers";
import { MatterDraftSheet } from "./matter-draft-sheet";
import { RouteTextCard } from "./route-text-card";
import { RoutePlannerSheet } from "./route-planner-sheet";
import { RouteMapSheet } from "./route-map-sheet";
import { ConfirmSheet, ProgressSheet } from "./work-plan-ui";
import {
  ConfirmDialog,
  EmptyState,
  ErrorCard,
  LoadingCard,
  PageShell,
  Pill,
  Sheet,
} from "./ui";

function syncLabel(record: MatterSchedule | MatterReminder): string {
  const reminder = "trigger_at" in record;
  const source = record.source;
  const status = record.sync_status;
  if (source === "ios_eventkit" || source === "ios_reminders") {
    return status === "ios_synced" ? "来自苹果端的已同步副本" : "来自苹果端 · 待核对";
  }
  if (status === "pending_ios") {
    return reminder ? "应用内提醒（待 iOS 客户端同步）" : "已记入应用内日程（待 iOS 客户端同步）";
  }
  if (status === "ios_synced") return "已同步（有苹果端凭据）";
  if (!reminder) return "已记入应用内日程";
  const reminderStatus = (record as MatterReminder).status;
  if (reminderStatus === "due") return "应用内提醒 · 已到点";
  if (reminderStatus === "acknowledged") return "应用内提醒 · 已知晓";
  if (reminderStatus === "cancelled") return "应用内提醒 · 已取消";
  return "应用内提醒 · 已保存待触发";
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
          {todo.due_at ? `截止 ${dateTimeLabel(todo.due_at)}` : "未设截止"}
          {todo.reminder_at ? ` · 提醒 ${dateTimeLabel(todo.reminder_at)}` : " · 未设提醒"}
          {todo.status === "done" ? " · 已完成" : ""}
        </span>
      </span>
      <button type="button" className="link-button" onClick={onEditTime} disabled={busy} aria-label={`调整 ${todo.title} 的时间`}>
        时间
      </button>
    </div>
  );
}

export function MatterRealDetail({ id }: { id: string }) {
  const router = useRouter();
  const { user } = useAuth();
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
  const [progressError, setProgressError] = useState<string | null>(null);
  const [progressPendingContext, setProgressPendingContext] =
    useState<IntakePendingContext>({});
  const [progressContinuationRunId, setProgressContinuationRunId] =
    useState<string | null>(null);
  const [progressAnswer, setProgressAnswer] = useState<{ question: string; answer: string } | null>(null);
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
  const [routeMapScheduleId, setRouteMapScheduleId] = useState<string | null>(null);
  const [routePlannerSchedule, setRoutePlannerSchedule] =
    useState<MatterSchedule | null>(null);
  const routePlannerDraftId = useRef<string | null>(null);
  const routePlannerDraftOperation = useRef(0);
  const [revertTarget, setRevertTarget] = useState<MatterChange | null>(null);
  const [initialDeleteTarget, setInitialDeleteTarget] = useState<MatterChange | null>(
    null,
  );
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
    const [matterResult, changesResult] = await Promise.allSettled([
      getMatter(id),
      listMatterChanges(id),
    ]);
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
          : "读取变更历史失败",
      );
    }
    setLoading(false);
    setChangesLoading(false);
  }, [id]);

  useEffect(() => {
    void load();
  }, [load, user?.user_id]);

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
        if (alive) setRouteBySchedule({});
      });
    return () => {
      alive = false;
    };
  }, [matter]);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    try {
      await action();
    } catch (caught) {
      if (caught instanceof ApiError && isRecordChangedError(caught.code)) {
        toast("记录已发生变化，请刷新后检查");
        await load();
      } else if (caught instanceof ApiError && caught.code === "revert_from_source_matter") {
        toast(caught.message);
        await load();
      } else if (caught instanceof ApiError && caught.code === "option_not_executable") {
        toast(caught.message);
      } else if (caught instanceof ApiError && caught.code === "creation_revert_unsupported") {
        toast("暂不支持撤回新建事项");
        await load();
      } else if (caught instanceof ApiError && caught.code === "incomplete_change_snapshot") {
        toast("这条旧变更缺少完整记录，不能安全撤回");
        await load();
      } else if (
        caught instanceof ApiError &&
        ["not_initial_create", "initial_delete_unavailable", "progress_added"].includes(
          caught.code,
        )
      ) {
        toast(caught.message);
        await load();
      } else {
        toast(caught instanceof Error ? caught.message : "操作失败");
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
          toast("事项已在别处修改，已刷新，请重新检查");
          return;
        }
        throw caught;
      }
    });
  };

  const startProgress = async (
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
      if (result.intent === "question" && result.answer) {
        setProgressOpen(false);
        setProgressContinuationRunId(null);
        setProgressPendingContext({});
        progressOperation.current = null;
        setProgressAnswer({ question: text, answer: result.answer });
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
          "服务器还需要更具体的信息。请补充后再提交。",
      );
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        (caught.status >= 500 ||
          caught.code.includes("timeout") ||
          caught.code.includes("unknown"))
      ) {
        setProgressError(
          "处理结果暂时不确定。可以保留输入，并用同一操作继续查询或重试。",
        );
      } else {
        setProgressError(
          caught instanceof Error ? caught.message : "暂时无法提交补充进展。",
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

  const revertChange = async (change: MatterChange) => {
    if (!matter) return;
    setRevertTarget(null);
    await run("revert", async () => {
      await revertMatterChange(matter.matter_id, change.change_id, matter.version);
      toast("服务器已撤回这条变更");
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
      throw new Error("服务器尚未把路线结果关联到当前日程，请刷新后重试。");
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
  return (
    <PageShell
      title="事项详情"
      subtitle="服务器保存"
      fallbackHref="/matters"
      action={
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
      }
    >
      <div className="work-detail">
        <section className="card">
          <div className="flex items-start gap-10">
            <div className="grow">
              <p className="micro" style={{ margin: 0 }}>
                {matter.status === "active" ? "正在推进" : matter.status === "waiting" ? "等待外部反馈" : "已完成／归档"}
              </p>
              <h2 className="serif-title" style={{ margin: "4px 0 0", fontSize: 22 }}>{matter.title}</h2>
            </div>
            <button type="button" className="link-button" onClick={() => {
              setTitle(matter.title); setGoal(matter.goal); setNextStep(matter.next_step ?? ""); setEditError(null); setEditOpen(true);
            }}>纠正</button>
          </div>
          <p className="micro" style={{ margin: "10px 0 0" }}>目标与背景</p>
          <p className="caption" style={{ margin: "3px 0 0" }}>{matter.goal || "尚未填写目标。"}</p>
          <hr className="divider" />
          <p className="micro" style={{ margin: 0 }}>最新进展</p>
          <p className="body-text" style={{ margin: "4px 0 0" }}>{matter.latest_progress || "还没有进展记录。"}</p>
          <p className="caption" style={{ margin: "8px 0 0" }}>
            下一步：{matter.next_step || "尚未填写"}
          </p>
          <div className="flex items-center gap-8 mt-10" style={{ flexWrap: "wrap" }}>
            <Pill tone="fog">{matter.open_todos} 项待办</Pill>
            {matter.last_progress_at ? <span className="micro">最近进展 {dateTimeLabel(matter.last_progress_at)}</span> : null}
            <button type="button" className="link-button" onClick={() => void load()} disabled={loading}>
              <RefreshCw size={12} /> 刷新
            </button>
          </div>
        </section>

        <section>
          <h2 className="section-title" style={{ marginBottom: 8 }}>已确定安排</h2>
          <div className="card card--tight">
            {matter.schedules.length === 0 &&
            openReminders.length === 0 ? (
              <p className="caption" style={{ margin: 0 }}>还没有已确定的日程或提醒。</p>
            ) : (
              <div className="m-stack-10">
                {matter.schedules.map((schedule) => (
                  <div key={schedule.schedule_id} className="schedule-block">
                    <p className="card-title" style={{ fontSize: 15 }}>{schedule.title}</p>
                    <p className="caption" style={{ margin: "2px 0 0" }}>
                      {dateTimeLabel(schedule.starts_at)}
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
                    <RouteTextCard
                      embedded
                      originLabel={
                        routeBySchedule[schedule.schedule_id]?.origin_basis === "previous_schedule"
                          ? "从上一项安排出发"
                          : routeBySchedule[schedule.schedule_id]?.origin_basis === "user_confirmed"
                            ? "从已确认起点出发"
                            : "出发地未提供"
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
                            ? `路线结果取得于 ${dateTimeLabel(routeBySchedule[schedule.schedule_id].fetched_at || "")}`
                            : "路线结果由服务器返回。"
                          : routeBySchedule[schedule.schedule_id]?.message ||
                            "服务器尚未把这条日程关联到路线结果，不能沿用独立通勤建议。"
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
                    />
                  </div>
                ))}
                {openReminders.map((reminder) => (
                  <p key={reminder.reminder_id} className="caption" style={{ margin: "4px 0 0" }}>
                    提醒：{reminder.title} · {dateTimeLabel(reminder.trigger_at)} · {syncLabel(reminder)}
                    {reminder.status === "due" ? " · 已到点，待确认知晓" : ""}
                  </p>
                ))}
              </div>
            )}
            <p className="micro" style={{ margin: "10px 0 0" }}>
              “调整”会读取服务器里的当前日程并生成结构化预览；只有再次确认后才会改写正式安排。
            </p>
            <button type="button" className="link-button mt-6" onClick={() => {
              setProgressError(null);
              setProgressPendingContext({});
              setProgressContinuationRunId(null);
              progressOperation.current = null;
              setProgressOpen(true);
            }}>
              用一句话补充安排变化
            </button>
          </div>
        </section>

        <section>
          <h2 className="section-title" style={{ marginBottom: 8 }}>待处理清单</h2>
          <div className="card card--tight">
            {matter.todos.length === 0 ? (
              <p className="caption" style={{ margin: 0 }}>没有待办子项。</p>
            ) : (
              <div className="m-stack-10">
                {matter.todos.map((todo) => (
                  <TodoRow
                    key={todo.todo_id}
                    todo={todo}
                    busy={busy === `todo-${todo.todo_id}`}
                    onToggle={() => void toggleTodo(todo)}
                    onEditTime={() => void openTodoDraft(todo)}
                  />
                ))}
              </div>
            )}
          </div>
        </section>

        <section>
          <h2 className="section-title" style={{ marginBottom: 8 }}>跟进记录</h2>
          <div className="m-stack-8">
            {matter.progress.length === 0 ? (
              <p className="caption">还没有服务器跟进记录。</p>
            ) : (
              matter.progress.map((entry) => (
                <details key={entry.progress_id} className="history-item">
                  <summary>
                    <span>{dateTimeLabel(entry.created_at)}</span>
                    <span className="history-item__preview">{entry.text}</span>
                  </summary>
                  <p className="body-text" style={{ margin: "8px 0 0" }}>{entry.text}</p>
                </details>
              ))
            )}
          </div>
        </section>

        <section id="changes">
          <div className="flex items-center justify-between gap-12" style={{ marginBottom: 8 }}>
            <h2 className="section-title">变更历史</h2>
            <span className="micro">已生效</span>
          </div>
          {changesLoading ? (
            <LoadingCard label="正在读取变更历史…" />
          ) : changesError ? (
            <ErrorCard message={changesError} onRetry={() => void load()} />
          ) : changes.length === 0 ? (
            <p className="caption">服务器还没有记录这条事项的变更。</p>
          ) : (
            <div className="card card--tight">
              {changes.map((change, index) => {
                const reason = change.revertable
                  ? null
                  : changeReasonText(change.reason_code, change.reason);
                return (
                  <div key={change.change_id}>
                    {index > 0 ? <hr className="divider" /> : null}
                    <div className="change-history-item">
                      <div className="flex items-start justify-between gap-10">
                        <div className="grow">
                          <p className="micro" style={{ margin: 0 }}>
                            {dateTimeLabel(change.created_at)} · 已生效
                          </p>
                          <p className="body-text" style={{ margin: "4px 0 0" }}>
                            {summarizeMatterChange(change)}
                          </p>
                        </div>
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
                      </div>
                      {reason ? (
                        <p className="micro" style={{ margin: "6px 0 0" }}>
                          {reason}
                        </p>
                      ) : null}
                      {change.relation === "affected" ? (
                        <button
                          type="button"
                          className="link-button mt-6"
                          onClick={() =>
                            router.push(`/matters/${change.source_matter_id}#changes`)
                          }
                        >
                          到「{change.source_matter_title || "来源事项"}」查看并处理
                        </button>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          <p className="micro" style={{ margin: "8px 0 0" }}>
            只有服务器标记为可撤回的最近变更才显示撤回操作；初始新建事项的连带删除也以服务器返回的资格为准。
          </p>
        </section>

        <p className="micro text-center" style={{ marginTop: 0 }}>
          停止跟进的范围以确认面板中的服务器数据为准；提醒只表示服务器已有记录，不代表已经送达。
        </p>

        <div className="work-actions">
          <button type="button" className="btn btn--primary grow" onClick={() => {
            setProgressError(null);
            setProgressPendingContext({});
            setProgressContinuationRunId(null);
            progressOperation.current = null;
            setProgressOpen(true);
          }} disabled={Boolean(busy)}>
            补充进展
          </button>
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
              重新打开
            </button>
          )}
          <button type="button" className="btn btn--quiet danger-text" onClick={() => setDeleteOpen(true)} disabled={Boolean(busy)}>
            <Trash2 size={14} /> 删除
          </button>
        </div>
      </div>

      <ProgressSheet
        open={progressOpen}
        title={matter.title}
        note="提交后由服务器生成待确认草稿；确认前不会写正式进展或日程。"
        stayOpenOnSubmit
        busy={busy === "progress"}
        error={progressError}
        choices={(progressPendingContext.schedule_candidates ?? []).map((candidate) => ({
          id: candidate.schedule_id,
          title: candidate.title,
          detail: `${dateTimeLabel(candidate.starts_at)}${
            candidate.location ? ` · ${candidate.location}` : ""
          } · 只用于继续整理，不会直接保存`,
          onSelect: () => {
            const runId = progressContinuationRunId;
            setProgressError(null);
            setProgressPendingContext({});
            void startProgress(
              `选择「${candidate.title}」（${dateTimeLabel(candidate.starts_at)}${
                candidate.location ? `，地点：${candidate.location}` : ""
              }）`,
              runId,
            );
          },
        }))}
        submitLabel="交给服务器整理"
        onClose={() => {
          setProgressOpen(false);
          setProgressError(null);
          setProgressPendingContext({});
          setProgressContinuationRunId(null);
          progressOperation.current = null;
        }}
        onSubmit={(text) => void startProgress(text)}
      />
      <Sheet title="纠正事项内容" open={editOpen} onClose={() => setEditOpen(false)}>
        <div className="m-stack-12">
          <label className="field">
            <span className="field__label">标题</span>
            <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">目标与背景</span>
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
          toast("草稿已取消，没有写入正式安排");
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
            ? `路线结果取得于 ${dateTimeLabel(routeMapSummary.fetched_at)}`
            : null
        }
        onClose={() => setRouteMapScheduleId(null)}
      />
      <Sheet
        title="助手回答"
        open={Boolean(progressAnswer)}
        onClose={() => setProgressAnswer(null)}
      >
        {progressAnswer ? (
          <div className="m-stack-12">
            <p className="caption" style={{ margin: 0 }}>
              你补充的是“{progressAnswer.question}”
            </p>
            <div className="card">
              <p className="body-text" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                {progressAnswer.answer}
              </p>
            </div>
            <p className="micro" style={{ margin: 0 }}>
              服务器在事项整理中直接返回了这份回答；没有再次发起普通问答，也没有创建草稿。
            </p>
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
        title="撤回这条变更？"
        body={
          revertTarget
            ? `服务器会${describeRevert(revertTarget)}。撤回后事项、历史和今天的安排会重新读取。`
            : ""
        }
        confirmLabel="确认撤回"
        onCancel={() => setRevertTarget(null)}
        onConfirm={() => {
          if (revertTarget) void revertChange(revertTarget);
        }}
      />
      <ConfirmDialog
        open={Boolean(initialDeleteTarget)}
        title="删除这个新事项？"
        body="服务器会再次核对资格，并在同一事务中软删该事项及本次新建的日程、待办和提醒；其他事项、参考日程和苹果导入副本不受影响。"
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
                ? "已删除这个新事项及其本次新建的安排"
                : "已删除这个新事项",
            );
            requestTodayRefresh();
            router.push("/matters");
          });
        }}
      />
      <Sheet title={`确认暂停「${matter.title}」？`} open={archiveOpen} onClose={() => setArchiveOpen(false)}>
        <div className="m-stack-12">
          <div className="notice notice--amber">
            <strong>事项将归档，不会删除</strong>
            <p className="caption" style={{ margin: "4px 0 0" }}>
              「{matter.title}」会从正在推进中移到归档；过去安排仍会保留，未发生的未来日程默认一并取消。
            </p>
          </div>
          <div className="card card--tight">
            <p className="micro" style={{ margin: 0 }}>未触发提醒</p>
            {pendingReminders.length > 0 ? (
              <div className="m-stack-8 mt-8">
                {pendingReminders.map((reminder) => (
                  <p key={reminder.reminder_id} className="caption" style={{ margin: 0 }}>
                    {reminder.title} · {dateTimeLabel(reminder.trigger_at)}
                  </p>
                ))}
              </div>
            ) : (
              <p className="caption" style={{ margin: "4px 0 0" }}>
                服务器没有未触发的提醒。
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
                  同时取消以上 {pendingReminders.length} 条未触发提醒
                </span>
              </label>
            ) : null}
          </div>
          <div className="card card--tight">
            <p className="micro" style={{ margin: 0 }}>日程范围</p>
            <p className="caption" style={{ margin: "4px 0 0" }}>
              取消未来日程时，其尚未触发的提醒也会停止。手机导入的只读日程需在原设备处理。
            </p>
            {futureSchedules.length > 0 ? (
              <label className="flex items-start gap-8 mt-10"><input type="checkbox" checked={archiveCancelSchedules} onChange={(event) => setArchiveCancelSchedules(event.target.checked)} /><span className="caption">同时取消以下未发生的应用内日程（共 {futureSchedules.filter((s) => !s.source || s.source === "web_native").length} 项）</span></label>
            ) : null}
            {futureSchedules.length > 0 ? (
              <ul className="compact-list">
                {futureSchedules.map((schedule) => (
                  <li key={schedule.schedule_id}>
                    {schedule.title} · {dateTimeLabel(schedule.starts_at)}
                    {schedule.source === "ios_eventkit" ? " · 苹果端日程需在苹果设备处理" : ""}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
          <p className="caption" style={{ margin: 0 }}>
            关联的重复通勤计划将停止自动生成。注：若此前已有通知发送到手机锁屏，需在手机上手动清除；你单独保存在「个人记忆」中的习惯仍为你留存，如需移除可前往「个人记忆」管理。
          </p>
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
                toast(`已归档并停止后续自动生成；${effect?.cancelled_schedules ? `已取消 ${effect.cancelled_schedules} 项未来日程；` : ""}${effect?.retained_schedules ? `你选择保留的 ${effect.retained_schedules} 项未来日程仍在日程表中；` : ""}${effect?.readonly_schedules ? `${effect.readonly_schedules} 项只读日程需在原设备处理；` : ""}${archiveCancelReminders ? "未到期提醒已取消" : "你选择保留的独立提醒仍会生效"}`);
                router.push("/matters");
              });
            }}
          >
            {busy === "archive" ? "正在归档…" : "确认归档并停止跟进"}
          </button>
          <button type="button" className="btn btn--quiet" onClick={() => setArchiveOpen(false)}>
            取消
          </button>
        </div>
      </Sheet>
      <ConfirmSheet
        open={reopenOpen}
        title="重新打开这件事？"
        body="服务器会把状态改回正在推进，并重新出现在列表。"
        confirmLabel="重新打开"
        onClose={() => setReopenOpen(false)}
        onConfirm={() => {
          setReopenOpen(false);
          void run("reopen", async () => {
            await reopenMatter(matter.matter_id, { expected_version: matter.version });
            await load();
            toast("已重新打开");
          });
        }}
      />
      <ConfirmSheet
        open={deleteOpen}
        title="彻底删除这件事？"
        body="删除后，该事项及关联安排将移除；后续助手不再把这些已删除内容作为有效资料使用。当前聊天界面已经显示的文字不会自动抹除。"
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

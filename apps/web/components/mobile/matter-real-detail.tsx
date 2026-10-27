"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Circle, Pencil, RefreshCw, Trash2, Undo2 } from "lucide-react";

import type {
  DraftConfirmResponse,
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
  completeMatterTodo,
  createMatterEditDraft,
  deleteMatter,
  getMatter,
  getToday,
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
  return reminder ? "应用内提醒" : "已记入应用内日程";
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
  const [progressAnswer, setProgressAnswer] = useState<{ question: string; answer: string } | null>(null);
  const [editingDraft, setEditingDraft] = useState<Awaited<ReturnType<typeof intakeMatterProgress>>["draft"] | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [reopenOpen, setReopenOpen] = useState(false);
  const [changes, setChanges] = useState<MatterChange[]>([]);
  const [changesLoading, setChangesLoading] = useState(true);
  const [changesError, setChangesError] = useState<string | null>(null);
  const [routeBySchedule, setRouteBySchedule] = useState<Record<string, TodayRoute>>({});
  const [routeDetails, setRouteDetails] = useState<Record<string, RouteEstimate>>({});
  const [routePlannerSchedule, setRoutePlannerSchedule] =
    useState<MatterSchedule | null>(null);
  const [revertTarget, setRevertTarget] = useState<MatterChange | null>(null);

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

  useEffect(() => {
    if (!matter || matter.schedules.length === 0) {
      setRouteBySchedule({});
      setRouteDetails({});
      return;
    }
    setRouteDetails({});
    let alive = true;
    const dates = Array.from(
      new Set(
        matter.schedules.map((schedule) =>
          new Date(schedule.starts_at).toLocaleDateString("en-CA"),
        ),
      ),
    );
    void Promise.all(dates.map((date) => getToday(date)))
      .then((views) => {
        if (!alive) return;
        const next: Record<string, TodayRoute> = {};
        for (const view of views) {
          for (const schedule of view.schedules) {
            next[schedule.schedule_id] = schedule.route;
          }
        }
        setRouteBySchedule(next);
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

  const startProgress = async (text: string) => {
    if (!matter) return;
    setBusy("progress");
    setProgressError(null);
    try {
      const result = await intakeMatterProgress(matter.matter_id, text, newOperationKey("web-progress"));
      if (result.intent === "question" && result.answer) {
        setProgressOpen(false);
        setProgressAnswer({ question: text, answer: result.answer });
        return;
      }
      if (result.draft) {
        setProgressOpen(false);
        setEditingDraft(result.draft);
        return;
      }
      setProgressError(
        result.message || "服务器还需要更具体的信息。请补充后再提交。",
      );
    } catch (caught) {
      setProgressError(caught instanceof Error ? caught.message : "暂时无法提交补充进展。");
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

  const onRouteEstimated = (scheduleId: string, result: RouteEstimate) => {
    setRouteDetails((current) => ({ ...current, [scheduleId]: result }));
    setRouteBySchedule((current) => ({
      ...current,
      [scheduleId]: {
        status: "available",
        reason_code: null,
        message: "路线结果由服务器返回。",
        advisory_id: null,
        duration_seconds: result.duration_seconds,
        mode: result.mode,
        fetched_at: result.fetched_at,
        origin_basis: result.origin_basis,
      },
    }));
    requestTodayRefresh();
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
            matter.reminders.filter((reminder) => reminder.status === "pending").length === 0 ? (
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
                      onPlanRoute={() => setRoutePlannerSchedule(schedule)}
                    />
                  </div>
                ))}
                {matter.reminders
                  .filter((reminder) => reminder.status === "pending")
                  .map((reminder) => (
                  <p key={reminder.reminder_id} className="caption" style={{ margin: "4px 0 0" }}>
                    提醒：{reminder.title} · {dateTimeLabel(reminder.trigger_at)} · {syncLabel(reminder)}
                  </p>
                  ))}
              </div>
            )}
            <p className="micro" style={{ margin: "10px 0 0" }}>
              “调整”会读取服务器里的当前日程并生成结构化预览；只有再次确认后才会改写正式安排。
            </p>
            <button type="button" className="link-button mt-6" onClick={() => setProgressOpen(true)}>
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
            只有服务器标记为可撤回的最近变更才显示撤回操作。
          </p>
        </section>

        <p className="micro text-center" style={{ marginTop: 0 }}>
          停止跟进默认不取消提醒；提醒只表示服务器已有记录，尚未确认送达。彻底删除是服务器软删。
        </p>

        <div className="work-actions">
          <button type="button" className="btn btn--primary grow" onClick={() => setProgressOpen(true)} disabled={Boolean(busy)}>
            补充进展
          </button>
          {matter.status !== "archived" ? (
            <button type="button" className="btn btn--secondary" onClick={() => setArchiveOpen(true)} disabled={Boolean(busy)}>
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
        submitLabel="交给服务器整理"
        onClose={() => {
          setProgressOpen(false);
          setProgressError(null);
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
        onClose={() => setRoutePlannerSchedule(null)}
        onEstimated={onRouteEstimated}
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
      <ConfirmSheet
        open={archiveOpen}
        title="停止跟进这件事？"
        body="服务器会把它归档，但默认不取消已有提醒。该行为按当前后端定义展示。"
        confirmLabel="归档并停止跟进"
        onClose={() => setArchiveOpen(false)}
        onConfirm={() => {
          setArchiveOpen(false);
          void run("archive", async () => {
            await archiveMatter(matter.matter_id, { expected_version: matter.version, cancel_reminders: false });
            toast("已归档");
            router.push("/matters");
          });
        }}
      />
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
        body="服务器会软删事项和关联日程，并取消待发提醒。删除成功后列表和后续检索都不再引用它。"
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

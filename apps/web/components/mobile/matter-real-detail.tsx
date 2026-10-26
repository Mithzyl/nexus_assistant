"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Circle, Pencil, RefreshCw, Trash2, Undo2 } from "lucide-react";

import type {
  DraftConfirmResponse,
  MatterDetail,
  MatterReminder,
  MatterSchedule,
  MatterTodo,
} from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import {
  archiveMatter,
  completeMatterTodo,
  deleteMatter,
  getMatter,
  intakeMatterProgress,
  newOperationKey,
  reopenMatter,
  reopenMatterTodo,
  confirmSummary,
  revertMatterChange,
  updateMatter,
} from "@/lib/mobile/matters-api";
import { dateTimeLabel } from "@/lib/mobile/work-plan";
import { useAuth, useUI } from "./providers";
import { MatterDraftSheet } from "./matter-draft-sheet";
import { ConfirmSheet, ProgressSheet, ScheduleEditSheet } from "./work-plan-ui";
import { EmptyState, ErrorCard, LoadingCard, PageShell, Pill, Sheet } from "./ui";

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
  return reminder ? "应用内提醒 · 未确认送达" : "已记入应用内日程";
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
          {todo.reminder_at ? ` · 提醒 ${dateTimeLabel(todo.reminder_at)}` : ""}
          {todo.status === "done" ? " · 已完成" : ""}
        </span>
      </span>
      <button type="button" className="link-button" onClick={onEditTime} disabled={busy} aria-label={`调整 ${todo.title} 的时间`}>
        时间
      </button>
    </div>
  );
}

function toLocalInput(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
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
  const [progressDraft, setProgressDraft] = useState<Awaited<ReturnType<typeof intakeMatterProgress>>["draft"] | null>(null);
  const [editSchedule, setEditSchedule] = useState<MatterSchedule | null>(null);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [todoEdit, setTodoEdit] = useState<MatterTodo | null>(null);
  const [todoDue, setTodoDue] = useState("");
  const [todoReminder, setTodoReminder] = useState("");
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [reopenOpen, setReopenOpen] = useState(false);
  const [lastChangeId, setLastChangeId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setMatter(await getMatter(id));
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error("读取事项失败"));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load, user?.user_id]);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    try {
      await action();
    } catch (caught) {
      if (caught instanceof ApiError && (caught.code === "preview_stale" || caught.code === "matter_changed")) {
        toast("服务器上的事项已变化，已重新读取，请重新检查");
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
    setProgressOpen(false);
    await run("progress", async () => {
      const result = await intakeMatterProgress(matter.matter_id, text, newOperationKey("web-progress"));
      if (!result.draft) {
        toast(result.message || "这句话更像问题，没有生成事项变更；请写清要调整的时间或进展。");
        return;
      }
      setProgressDraft(result.draft);
    });
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
    setLastChangeId(response.change_id);
    setProgressDraft(null);
    toast(confirmSummary(response));
    await load();
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
            {matter.schedules.length === 0 && matter.reminders.length === 0 ? (
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
                        onClick={() => { setEditSchedule(schedule); setScheduleOpen(true); }}
                        disabled={Boolean(busy) || schedule.source === "ios_eventkit"}
                        title={schedule.source === "ios_eventkit" ? "苹果导入日程需在苹果端修改" : "调整时间或地点"}
                      >
                        <Pencil size={12} /> 调整
                      </button>
                    </div>
                  </div>
                ))}
                {matter.reminders.map((reminder) => (
                  <p key={reminder.reminder_id} className="caption" style={{ margin: "4px 0 0" }}>
                    提醒：{reminder.title} · {dateTimeLabel(reminder.trigger_at)} · {syncLabel(reminder)} · 未确认已送达
                  </p>
                ))}
              </div>
            )}
            <p className="micro" style={{ margin: "10px 0 0" }}>
              修改时间地点需要通过“补充进展/整理”生成草稿并确认；网页没有直接改写已确认日程的接口，也不会静默覆盖。
            </p>
            <button type="button" className="link-button mt-6" onClick={() => setProgressOpen(true)}>
              说明时间或地点调整
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
                    onEditTime={() => {
                      setTodoEdit(todo);
                      setTodoDue(todo.due_at ? toLocalInput(todo.due_at) : "");
                      setTodoReminder(todo.reminder_at ? toLocalInput(todo.reminder_at) : "");
                    }}
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

        {lastChangeId ? (
          <button
            type="button"
            className="btn btn--secondary"
            disabled={busy === "revert"}
            onClick={() => void run("revert", async () => {
              await revertMatterChange(matter.matter_id, lastChangeId, matter.version);
              setLastChangeId(null);
              toast("已按服务器变更记录撤回");
              await load();
            })}
          >
            <Undo2 size={14} /> 撤回刚才这次确认
          </button>
        ) : (
          <p className="micro" style={{ margin: 0 }}>
            撤回需要使用本次确认返回的 change_id；刷新后没有可列出的变更历史，因此不显示“可撤回”入口。
          </p>
        )}

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
        <p className="micro text-center" style={{ marginTop: 8 }}>
          停止跟进默认不取消提醒；彻底删除是服务器软删。服务器确认前不会显示已删除。
        </p>
      </div>

      <ProgressSheet
        open={progressOpen}
        title={matter.title}
        note="提交后由服务器生成待确认草稿；确认前不会写正式进展或日程。"
        onClose={() => setProgressOpen(false)}
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

      <Sheet title="调整待办时间" open={Boolean(todoEdit)} onClose={() => setTodoEdit(null)}>
        <div className="m-stack-12">
          <p className="caption" style={{ margin: 0 }}>{todoEdit?.title}</p>
          <label className="field">
            <span className="field__label">截止时间</span>
            <input className="input" type="datetime-local" value={todoDue} onChange={(event) => setTodoDue(event.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">提醒触发时间</span>
            <input className="input" type="datetime-local" value={todoReminder} onChange={(event) => setTodoReminder(event.target.value)} />
          </label>
          <p className="micro" style={{ margin: 0 }}>两者分别提交。保存只生成待确认草稿，确认前不改正式待办或提醒。</p>
          <button
            type="button"
            className="btn btn--primary"
            disabled={Boolean(busy) || !todoEdit}
            onClick={() => {
              if (!todoEdit) return;
              const item = todoEdit;
              const text = `把待办「${item.title}」的截止时间改为 ${todoDue ? new Date(todoDue).toISOString() : "不设"}，提醒时间改为 ${todoReminder ? new Date(todoReminder).toISOString() : "不设"}。`;
              setTodoEdit(null);
              void run("adjust-todo", async () => {
                const result = await intakeMatterProgress(matter.matter_id, text, newOperationKey("web-todo-time"));
                if (!result.draft) {
                  toast(result.message || "服务器没有生成变更草稿，请补充时间后再试。");
                  return;
                }
                setProgressDraft(result.draft);
              });
            }}
          >
            生成变更草稿
          </button>
          <button type="button" className="btn btn--quiet" onClick={() => setTodoEdit(null)}>取消</button>
        </div>
      </Sheet>
      <ScheduleEditSheet
        schedule={editSchedule ? {
          id: editSchedule.schedule_id,
          title: editSchedule.title,
          startsAt: editSchedule.starts_at,
          endsAt: editSchedule.ends_at ?? editSchedule.starts_at,
          location: editSchedule.location ?? "",
          source: "web_native",
          syncStatus: editSchedule.sync_status ?? "local_only",
        } : null}
        open={scheduleOpen}
        heading="调整这条安排"
        submitLabel="生成变更草稿"
        note="不会直接改写正式日程。保存后会调用服务器生成待确认草稿，再核对时间、地点与冲突并确认。"
        onClose={() => { setScheduleOpen(false); setEditSchedule(null); }}
        onSave={(patch) => {
          if (!editSchedule) return;
          const text = `把「${editSchedule.title}」调整到 ${new Date(patch.startsAt).toISOString()} 至 ${new Date(patch.endsAt).toISOString()}${patch.location ? `，地点改为「${patch.location}」` : ""}。`;
          setScheduleOpen(false);
          setEditSchedule(null);
          void run("adjust", async () => {
            const result = await intakeMatterProgress(matter.matter_id, text, newOperationKey("web-adjust"));
            if (!result.draft) {
              toast(result.message || "服务器没有生成变更草稿，请补充时间或地点后再试。");
              return;
            }
            setProgressDraft(result.draft);
          });
        }}
      />
      <MatterDraftSheet
        draft={progressDraft}
        open={Boolean(progressDraft)}
        onClose={() => setProgressDraft(null)}
        onConfirmed={(response) => void onConfirmed(response)}
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

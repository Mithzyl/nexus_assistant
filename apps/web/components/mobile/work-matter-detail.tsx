"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Circle, Pencil, Trash2, Undo2 } from "lucide-react";

import { useDemo, useUI } from "./providers";
import { useWorkPlan } from "./work-plan-store";
import {
  ConflictSheet,
  ConfirmSheet,
  ConflictCard,
  MatterEditSheet,
  PreviewBanner,
  ProgressSheet,
  ScheduleEditSheet,
  ScheduleRow,
} from "./work-plan-ui";
import { EmptyState, PageShell, Pill } from "./ui";
import {
  dateTimeLabel,
  findConflict,
  type WorkSchedule,
  type WorkSyncStatus,
} from "@/lib/mobile/work-plan";

function SyncPill({ status }: { status: WorkSyncStatus }) {
  const tone = status === "ios_synced" ? "green" : status === "pending_ios" ? "amber" : "neutral";
  return <Pill tone={tone}>{status === "local_only" ? "应用内预览" : status === "pending_ios" ? "待 iOS 同步" : "iOS 已同步"}</Pill>;
}

export function WorkMatterDetail({ id }: { id: string }) {
  const router = useRouter();
  const demo = useDemo();
  const plan = useWorkPlan();
  const { toast } = useUI();
  const matter = plan.getMatter(id);
  const [progressOpen, setProgressOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [schedule, setSchedule] = useState<WorkSchedule | null>(null);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [conflictOpen, setConflictOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [undoSchedule, setUndoSchedule] = useState<WorkSchedule | null>(null);
  const conflict = matter ? findConflict(matter) : null;

  if (!demo.enabled) {
    return (
      <PageShell title="事项" fallbackHref="/me/lab/plan">
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <EmptyState
            title="这是概念预览中的事项"
            body="事项服务尚未接入服务器。请先在“我的”开启概念预览，再查看本机示例。"
          />
        </div>
      </PageShell>
    );
  }

  if (!matter) {
    return (
      <PageShell title="事项" fallbackHref="/me/lab/plan">
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <EmptyState title="这条预览事项不存在" body="它可能已被删除，或当前账号没有这条本机记录。">
            <button type="button" className="btn btn--secondary" onClick={() => router.push("/me/lab/plan")}>
              返回事项
            </button>
          </EmptyState>
        </div>
      </PageShell>
    );
  }

  return (
    <PageShell
      title="事项详情"
      subtitle="本机概念预览 · 未写入服务器"
      fallbackHref="/me/lab/plan"
      action={
        <button type="button" className="icon-button" onClick={() => setEditOpen(true)} aria-label="纠正事项">
          <Pencil size={17} />
        </button>
      }
    >
      <div className="work-detail">
        <PreviewBanner />

        <section className="card">
          <div className="flex items-start gap-10">
            <div className="grow">
              <p className="micro" style={{ margin: 0 }}>
                {matter.status === "active" ? "正在推进" : matter.status === "waiting" ? "等待外部反馈" : "已完成 / 归档"}
                {matter.stopped ? " · 已停止跟进" : ""}
              </p>
              <h2 className="serif-title" style={{ margin: "4px 0 0", fontSize: 22 }}>{matter.title}</h2>
            </div>
            <button type="button" className="link-button" onClick={() => setEditOpen(true)}>
              纠正
            </button>
          </div>
          <p className="micro" style={{ margin: "10px 0 0" }}>目标与背景</p>
          <p className="caption" style={{ margin: "3px 0 0" }}>{matter.goal}</p>
          <hr className="divider" />
          <p className="micro" style={{ margin: 0 }}>最新进展</p>
          <p className="body-text" style={{ margin: "4px 0 0" }}>{matter.progress}</p>
          <p className="caption" style={{ margin: "8px 0 0" }}>
            下一步：{matter.nextAction || "待定"}
            {matter.nextAt ? ` · ${dateTimeLabel(matter.nextAt)}` : ""}
          </p>
        </section>

        {conflict ? (
          <ConflictCard conflict={conflict} onOpen={() => setConflictOpen(true)} />
        ) : null}

        <section>
          <div className="flex items-center justify-between gap-12">
            <h2 className="section-title" style={{ marginBottom: 8 }}>已确定安排</h2>
            <span className="micro">仅预览</span>
          </div>
          <div className="card card--tight">
            {matter.schedules.length === 0 ? (
              <p className="caption" style={{ margin: 0 }}>还没有关联日程。</p>
            ) : (
              <div className="m-stack-10">
                {matter.schedules.map((item) => (
                  <div key={item.id} className="schedule-block">
                    <ScheduleRow
                      schedule={item}
                      onEdit={(next) => {
                        setSchedule(next);
                        setScheduleOpen(true);
                      }}
                    />
                    <div className="flex items-center gap-8" style={{ paddingLeft: 48 }}>
                      <SyncPill status={item.syncStatus} />
                      <span className="micro">未写入 iPhone 系统日历</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <p className="micro" style={{ margin: "10px 0 0" }}>
              修改只保存到本机预览；正式日程、提醒和 iOS 回写接口尚未接通。
            </p>
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
                  <button
                    key={todo.id}
                    type="button"
                    className={`todo-row${todo.done ? " todo-row--done" : ""}`}
                    onClick={() => plan.toggleTodo(matter.id, todo.id)}
                    aria-pressed={todo.done}
                  >
                    {todo.done ? <Check size={16} /> : <Circle size={16} />}
                    <span className="grow" style={{ textAlign: "left" }}>
                      <span style={{ display: "block" }}>{todo.title}</span>
                      <span className="micro">
                        {todo.dueAt ? `截止 ${dateTimeLabel(todo.dueAt)}` : "未设截止"}
                        {todo.reminderAt ? ` · 提醒 ${dateTimeLabel(todo.reminderAt)}` : ""}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            )}
            <p className="micro" style={{ margin: "10px 0 0" }}>勾选只改本机预览，不会把正式提醒标为完成。</p>
          </div>
        </section>

        <section>
          <h2 className="section-title" style={{ marginBottom: 8 }}>跟进记录</h2>
          <div className="m-stack-8">
            {matter.history.map((entry) => (
              <details key={entry.id} className="history-item">
                <summary>
                  <span>{dateTimeLabel(entry.at)}</span>
                  <span className="history-item__preview">{entry.text}</span>
                </summary>
                <p className="body-text" style={{ margin: "8px 0 0" }}>{entry.text}</p>
              </details>
            ))}
          </div>
        </section>

        {undoSchedule ? (
          <button
            type="button"
            className="btn btn--secondary"
            onClick={() => {
              plan.updateSchedule(matter.id, undoSchedule.id, {
                title: undoSchedule.title,
                startsAt: undoSchedule.startsAt,
                endsAt: undoSchedule.endsAt,
                location: undoSchedule.location,
              });
              setUndoSchedule(null);
              toast("已撤回本机预览中的这次修改");
            }}
          >
            <Undo2 size={14} /> 撤回上次预览修改
          </button>
        ) : null}

        <div className="work-actions">
          <button type="button" className="btn btn--primary grow" onClick={() => setProgressOpen(true)}>
            补充进展
          </button>
          <button type="button" className="btn btn--secondary" onClick={() => setStopOpen(true)}>
            停止跟进
          </button>
          <button type="button" className="btn btn--quiet danger-text" onClick={() => setDeleteOpen(true)}>
            <Trash2 size={14} /> 删除
          </button>
        </div>
        <p className="micro text-center" style={{ marginTop: 8 }}>
          删除只移除此浏览器中的预览；不会删除服务器事项，也不会取消真实提醒。
        </p>
      </div>

      <ProgressSheet
        open={progressOpen}
        title={matter.title}
        onClose={() => setProgressOpen(false)}
        onSubmit={(text) => {
          plan.addProgress(matter.id, text);
          toast("进展已保存到本机预览");
        }}
      />
      <MatterEditSheet
        matter={matter}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        onSave={(patch) => {
          plan.updateMatter(matter.id, patch);
          toast("事项内容已保存到本机预览");
        }}
      />
      <ScheduleEditSheet
        schedule={schedule}
        open={scheduleOpen}
        onClose={() => {
          setScheduleOpen(false);
          setSchedule(null);
        }}
        onSave={(patch) => {
          if (!schedule) return;
          plan.updateSchedule(matter.id, schedule.id, patch);
          setUndoSchedule(schedule);
          toast("日程修改已保存到本机预览");
        }}
      />
      <ConflictSheet
        conflict={conflictOpen ? conflict : null}
        onClose={() => setConflictOpen(false)}
        onConfirm={(optionId) => {
          if (!conflict) return;
          plan.applyOption(conflict, optionId);
          setConflictOpen(false);
          toast("已按所选方案更新本机预览");
        }}
      />
      <ConfirmSheet
        open={stopOpen}
        title="停止跟进这件事？"
        body="预览会把它移入“已完成／归档”，并保留跟进记录。该操作不会取消服务器提醒，因为这里只有本机数据。"
        confirmLabel="停止跟进并归档"
        onClose={() => setStopOpen(false)}
        onConfirm={() => {
          plan.stopFollowing(matter.id);
          setStopOpen(false);
          toast("已在本机预览中停止跟进");
          router.push("/me/lab/plan");
        }}
      />
      <ConfirmSheet
        open={deleteOpen}
        title="彻底删除这条预览？"
        body="只会移除当前浏览器中的预览和关联日程；服务器数据和正式提醒不会被删除。删除后无法从本机撤回。"
        confirmLabel="彻底删除本机预览"
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => {
          plan.deleteMatter(matter.id);
          setDeleteOpen(false);
          toast("已删除本机预览");
          router.push("/me/lab/plan");
        }}
      />
    </PageShell>
  );
}

"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Bell, FileText, MapPin, Sparkles, Trash2 } from "lucide-react";

import { todayLabel, relativeTime } from "@/lib/mobile/format";
import {
  useAuth,
  useDemo,
  useLocalRecords,
  useUI,
  type LocalRecord,
} from "@/components/mobile/providers";
import { TodayCommuteCard } from "@/components/mobile/commute-card";
import { useWorkPlan } from "@/components/mobile/work-plan-store";
import { ConflictCard, ConflictSheet, PreviewBanner } from "@/components/mobile/work-plan-ui";
import { ConfirmDialog, EmptyState, Pill, SectionHeading, Sheet } from "@/components/mobile/ui";
import {
  findConflict,
  localDateKey,
  timeLabel,
  todayKey,
  type WorkConflict,
} from "@/lib/mobile/work-plan";

interface TimelineRow {
  id: string;
  time: string;
  title: string;
  detail: string;
  matterId: string;
  kind: "schedule" | "reminder";
}

export default function TodayPage() {
  const { state } = useAuth();
  const demo = useDemo();
  const plan = useWorkPlan();
  const { records, removeRecord } = useLocalRecords();
  const { openCapture } = useUI();
  const [selectedRecord, setSelectedRecord] = useState<LocalRecord | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [conflict, setConflict] = useState<WorkConflict | null>(null);

  const today = todayKey();
  const conflicts = demo.enabled
    ? plan.matters
        .map((matter) => ({ matter, conflict: findConflict(matter) }))
        .filter((entry): entry is { matter: (typeof plan.matters)[number]; conflict: WorkConflict } => Boolean(entry.conflict))
    : [];

  const timeline = useMemo<TimelineRow[]>(() => {
    if (!demo.enabled) return [];
    const rows: TimelineRow[] = [];
    for (const matter of plan.matters) {
      for (const schedule of matter.schedules) {
        if (localDateKey(schedule.startsAt) !== today) continue;
        rows.push({
          id: schedule.id,
          time: schedule.startsAt,
          title: schedule.title,
          detail: `${matter.title} · ${schedule.location || "地点未填"} · 应用内预览`,
          matterId: matter.id,
          kind: "schedule",
        });
      }
      for (const todo of matter.todos) {
        if (todo.done || !todo.reminderAt || localDateKey(todo.reminderAt) !== today) continue;
        rows.push({
          id: `${todo.id}-reminder`,
          time: todo.reminderAt,
          title: `提醒：${todo.title}`,
          detail: `${matter.title} · 预览提醒，不会由浏览器在关闭后送达`,
          matterId: matter.id,
          kind: "reminder",
        });
      }
    }
    return rows.sort((left, right) => new Date(left.time).getTime() - new Date(right.time).getTime());
  }, [demo.enabled, plan.matters, today]);

  const overview = demo.enabled
    ? timeline.length > 0
      ? `今天有 ${timeline.length} 个时间点${conflicts.length > 0 ? "，有一处安排需要你决定" : "，节奏还算从容"}。`
      : "今天没有预览安排，可以安心处理手边的事。"
    : state === "signedIn"
      ? "今天的通勤建议与已保存内容在下面。事项与提醒服务尚未接通。"
      : "未登录时只显示本机内容；登录后可读取服务器上的通勤建议。";

  return (
    <div className="m-screen m-stack-18" style={{ paddingBottom: 12 }}>
      <header style={{ padding: "8px var(--m-gutter) 0" }}>
        <div className="flex items-center justify-between gap-10">
          <h1 className="screen-title serif-title">今天</h1>
          {demo.enabled ? <Pill tone="amber">概念预览</Pill> : null}
        </div>
        <p className="micro" style={{ margin: "4px 0 0" }}>{todayLabel()}</p>
        <p className="today-overview">{overview}</p>
      </header>

      <div className="quick-row">
        <button type="button" className="quick-btn quick-btn--primary" onClick={() => openCapture("choose")}>
          <span aria-hidden="true">＋</span> 收下内容
        </button>
        <button type="button" className="quick-btn quick-btn--glass" onClick={() => openCapture("say")}>
          记一句
        </button>
      </div>

      <TodayCommuteCard />

      {demo.enabled ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <PreviewBanner>
            下方时间线和变化卡来自本机预览，不是服务器日程；确认后也只改预览。
          </PreviewBanner>
        </div>
      ) : null}

      {conflicts.length > 0 ? (
        <section className="m-stack-8" style={{ padding: "0 var(--m-gutter)" }}>
          <SectionHeading title="需要你决定" trailing={`${conflicts.length} 处`} />
          {conflicts.map((entry) => (
            <ConflictCard key={entry.conflict.matterId} conflict={entry.conflict} onOpen={() => setConflict(entry.conflict)} />
          ))}
        </section>
      ) : null}

      {demo.enabled ? (
        <section style={{ padding: "0 var(--m-gutter)" }}>
          <SectionHeading title="今日时间线" trailing="应用内预览" />
          {timeline.length === 0 ? (
            <EmptyState title="今天没有预览安排" body="事项详情里的日程和提醒会按时间出现在这里。" />
          ) : (
            <div className="card card--tight">
              {timeline.map((row) => (
                <Link key={row.id} href={`/matters/${row.matterId}`} className="timeline-link">
                  <span className="timeline-time">{timeLabel(row.time)}</span>
                  <span className="timeline-dot" aria-hidden="true" />
                  <span className="grow">
                    <span className="card-title" style={{ display: "block", fontSize: 15 }}>{row.title}</span>
                    <span className="caption" style={{ display: "block", margin: "2px 0 0" }}>{row.detail}</span>
                  </span>
                  {row.kind === "reminder" ? <Bell size={14} className="fog" /> : <MapPin size={14} className="fog" />}
                </Link>
              ))}
            </div>
          )}
          <p className="micro" style={{ margin: "8px 0 0" }}>
            提醒只在打开网页时显示；关闭网页后不会送达。没有调用路线、天气或通知服务。
          </p>
        </section>
      ) : null}

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <SectionHeading title="最近整理" trailing={demo.enabled ? "本机记录" : undefined} />
        {records.length === 0 ? (
          <EmptyState title="还没有整理记录" body="交入第一份内容后，结果会出现在这里。" />
        ) : (
          <div className="list-card">
            {records.slice(0, 5).map((record, index) => (
              <div key={record.id}>
                {index > 0 ? <div className="row-sep" /> : null}
                <button type="button" className="row" onClick={() => setSelectedRecord(record)}>
                  <span className="tile tile--sm"><FileText size={15} /></span>
                  <span className="row__body">
                    <span className="row__title" style={{ display: "block" }}>{record.title}</span>
                    <span className="row__detail" style={{ display: "block" }}>
                      本机保存 · {relativeTime(record.createdAt)}
                    </span>
                  </span>
                  <span className="chevron" aria-hidden="true">›</span>
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {demo.enabled ? (
        <section style={{ padding: "0 var(--m-gutter)" }}>
          <Link href="/matters" className="card card--tight flex items-center gap-10">
            <Sparkles size={18} className="fog" />
            <span className="grow">
              <span className="card-title" style={{ display: "block", fontSize: 15 }}>查看全部预览事项</span>
              <span className="caption" style={{ display: "block", margin: "2px 0 0" }}>按推进、等反馈、归档分组</span>
            </span>
            <span className="chevron">›</span>
          </Link>
        </section>
      ) : null}

      {!demo.enabled && state === "anonymous" ? (
        <section style={{ padding: "0 var(--m-gutter)" }}>
          <div className="notice">
            未登录时，今天只显示本机记录。登录后可读取服务器上的通勤建议与问答历史。
            <Link href="/login" className="link-button link-button--inline" style={{ display: "inline-flex", marginLeft: 6 }}>
              登录
            </Link>
          </div>
        </section>
      ) : null}

      <Sheet title="本机记录" open={Boolean(selectedRecord)} onClose={() => setSelectedRecord(null)}>
        {selectedRecord ? (
          <>
            <p className="card-title">{selectedRecord.title}</p>
            <p className="micro mt-4">
              {selectedRecord.kind === "note" ? "记一句" : "收下的内容"} · {relativeTime(selectedRecord.createdAt)}
            </p>
            <p className="body-text mt-12" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {selectedRecord.text}
            </p>
            <div className="notice mt-16">
              这条内容只保存在当前浏览器。它没有上传到 PA 服务器，也不是事项或正式日程。
            </div>
            <button type="button" className="btn btn--danger mt-16" onClick={() => setConfirmDelete(true)}>
              <Trash2 size={15} /> 删除本机记录
            </button>
          </>
        ) : null}
      </Sheet>

      <ConflictSheet
        conflict={conflict}
        onClose={() => setConflict(null)}
        onConfirm={(optionId) => {
          if (!conflict) return;
          plan.applyOption(conflict, optionId);
          setConflict(null);
        }}
      />

      <ConfirmDialog
        open={confirmDelete}
        title="删除这条本机记录？"
        body="删除后无法恢复。服务器上没有对应副本需要处理。"
        confirmLabel="删除"
        danger
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => {
          if (selectedRecord) removeRecord(selectedRecord.id);
          setConfirmDelete(false);
          setSelectedRecord(null);
        }}
      />
    </div>
  );
}

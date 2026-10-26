"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Bell, FileText, MapPin, RefreshCw, Trash2 } from "lucide-react";

import { relativeTime } from "@/lib/mobile/format";
import { useAuth, useLocalRecords, useUI, type LocalRecord } from "@/components/mobile/providers";
import { TodayCommuteCard } from "@/components/mobile/commute-card";
import { ConfirmDialog, EmptyState, ErrorCard, LoadingCard, Pill, SectionHeading, Sheet } from "@/components/mobile/ui";
import { useToday } from "@/lib/mobile/use-today";
import type { MatterReminder, MatterSchedule } from "@/lib/mobile/api";

function localDay(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function remark(item: MatterSchedule | MatterReminder, kind: "schedule" | "reminder"): string {
  const base = kind === "reminder" ? "应用内提醒" : "应用内日程";
  if (item.source === "ios_eventkit" || item.source === "ios_reminders") {
    return item.sync_status === "ios_synced" ? "来自苹果端的已同步副本" : "来自苹果端";
  }
  if (item.sync_status === "pending_ios") return `${base} · 待 iOS 客户端同步`;
  if (item.sync_status === "ios_synced") return `${base} · 已同步`;
  return base;
}

interface Row {
  id: string;
  at: string;
  title: string;
  detail: string;
  kind: "schedule" | "reminder";
}

export default function TodayPage() {
  const { state } = useAuth();
  const signedIn = state === "signedIn";
  const { records, removeRecord } = useLocalRecords();
  const { openCapture } = useUI();
  const todayData = useToday(signedIn);
  const [selectedRecord, setSelectedRecord] = useState<LocalRecord | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const rows = useMemo<Row[]>(() => {
    const data = todayData.data;
    if (!data) return [];
    const scheduleRows: Row[] = data.schedules.map((item) => ({
      id: `schedule-${item.schedule_id}`,
      at: item.starts_at,
      title: item.title,
      detail: `${item.location || "地点未填"} · ${remark(item, "schedule")}`,
      kind: "schedule",
    }));
    const reminderRows: Row[] = data.reminders.map((item) => ({
      id: `reminder-${item.reminder_id}`,
      at: item.trigger_at,
      title: item.title,
      detail: `提醒 · 待处理 · 没有送达凭据 · ${remark(item, "reminder")}`,
      kind: "reminder",
    }));
    return [...scheduleRows, ...reminderRows].sort(
      (left, right) => new Date(left.at).getTime() - new Date(right.at).getTime(),
    );
  }, [todayData.data]);

  const overview = (() => {
    if (todayData.loading && !todayData.data) return "正在读取今天的安排…";
    if (todayData.error) return "暂时无法读取今天的安排，下面显示的不是空日程。";
    if (!todayData.data) return "登录后从服务器读取今天的日程与提醒。";
    if (rows.length === 0) return "今天没有已确定的日程或提醒。";
    const reminders = todayData.data.reminders.length;
    return `今天有 ${todayData.data.schedules.length} 项日程${reminders > 0 ? `、${reminders} 个待处理提醒` : ""}。`;
  })();

  return (
    <div className="m-screen m-stack-18" style={{ paddingBottom: 12 }}>
      <header style={{ padding: "8px var(--m-gutter) 0" }}>
        <div className="flex items-center justify-between gap-10">
          <h1 className="screen-title serif-title">今天</h1>
          {todayData.data ? <Pill tone="fog">{todayData.data.timezone}</Pill> : null}
        </div>
        <p className="micro" style={{ margin: "4px 0 0" }}>{todayData.data?.date || localDay()}</p>
        <p className="today-overview">{overview}</p>
        {signedIn ? (
          <button type="button" className="link-button mt-6" onClick={todayData.refresh} disabled={todayData.loading}>
            <RefreshCw size={13} /> {todayData.loading ? "正在刷新…" : "重新读取"}
          </button>
        ) : null}
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

      {!signedIn ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <EmptyState title="登录后读取今天的安排" body="未登录时只显示本机记录；不会用示例日程冒充服务器。">
            <Link href="/login" className="btn btn--secondary">登录 PA 账号</Link>
          </EmptyState>
        </div>
      ) : todayData.loading && !todayData.data ? (
        <div style={{ padding: "0 var(--m-gutter)" }}><LoadingCard label="正在读取今天的安排…" /></div>
      ) : todayData.error ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <ErrorCard message={todayData.error.message} onRetry={todayData.refresh} />
        </div>
      ) : todayData.data ? (
        <>
          {todayData.data.pending_drafts > 0 ? (
            <div style={{ padding: "0 var(--m-gutter)" }}>
              <div className="notice notice--amber">
                <strong>有 {todayData.data.pending_drafts} 条待确认草稿。</strong>今天接口只返回数量；
                具体草稿在问助手里按服务器列表恢复。
                <Link href="/ask" className="link-button link-button--inline" style={{ display: "inline-flex", marginLeft: 6 }}>
                  去问助手查看
                </Link>
              </div>
            </div>
          ) : null}

          <section style={{ padding: "0 var(--m-gutter)" }}>
            <SectionHeading title="今日时间线" trailing="服务器日程与提醒" />
            {rows.length === 0 ? (
              <EmptyState title="今天没有安排" body="服务器确认没有日程或待处理提醒。" />
            ) : (
              <div className="card card--tight">
                {rows.map((row) => (
                  <div key={row.id} className="timeline-link">
                    <span className="timeline-time">{time(row.at)}</span>
                    <span className="timeline-dot" aria-hidden="true" />
                    <span className="grow">
                      <span className="card-title" style={{ display: "block", fontSize: 15 }}>{row.title}</span>
                      <span className="caption" style={{ display: "block", margin: "2px 0 0" }}>{row.detail}</span>
                    </span>
                    {row.kind === "reminder" ? <Bell size={14} className="fog" /> : <MapPin size={14} className="fog" />}
                  </div>
                ))}
              </div>
            )}
            <p className="micro" style={{ margin: "8px 0 0" }}>
              提醒只代表服务器有记录，不代表已送达；今天的响应尚未包含可执行的冲突方案。
            </p>
          </section>

          <section style={{ padding: "0 var(--m-gutter)" }}>
            <div className="notice">
              通勤建议仍按固定通勤计划显示，尚未与具体日程自动关联；修改时间地点需要通过事项草稿确认。
            </div>
          </section>
        </>
      ) : null}

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <SectionHeading title="最近整理" trailing="本机记录" />
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
                    <span className="row__detail" style={{ display: "block" }}>本机保存 · {relativeTime(record.createdAt)}</span>
                  </span>
                  <span className="chevron" aria-hidden="true">›</span>
                </button>
              </div>
            ))}
          </div>
        )}
        <p className="micro" style={{ margin: "8px 0 0" }}>本机记录不属于服务器日程，不会进入上面的时间线。</p>
      </section>

      <Sheet title="本机记录" open={Boolean(selectedRecord)} onClose={() => setSelectedRecord(null)}>
        {selectedRecord ? (
          <>
            <p className="card-title">{selectedRecord.title}</p>
            <p className="micro mt-4">{selectedRecord.kind === "note" ? "记一句" : "收下的内容"} · {relativeTime(selectedRecord.createdAt)}</p>
            <p className="body-text mt-12" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{selectedRecord.text}</p>
            <div className="notice mt-16">这条内容只保存在当前浏览器，没有上传到 PA 服务器。</div>
            <button type="button" className="btn btn--danger mt-16" onClick={() => setConfirmDelete(true)}>
              <Trash2 size={15} /> 删除本机记录
            </button>
          </>
        ) : null}
      </Sheet>

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

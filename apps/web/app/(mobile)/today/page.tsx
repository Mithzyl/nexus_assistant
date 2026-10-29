"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Bell, CalendarClock, ChevronRight, FileText, MapPin, RefreshCw, Trash2 } from "lucide-react";

import { MatterDraftSheet } from "@/components/mobile/matter-draft-sheet";
import { RouteMapSheet } from "@/components/mobile/route-map-sheet";
import { RouteTextCard } from "@/components/mobile/route-text-card";
import { TodayCommuteCard } from "@/components/mobile/commute-card";
import { useAuth, useLocalRecords, useUI, type LocalRecord } from "@/components/mobile/providers";
import {
  ConfirmDialog,
  EmptyState,
  ErrorCard,
  LoadingCard,
  Pill,
  SectionHeading,
  Sheet,
} from "@/components/mobile/ui";
import type {
  DraftConfirmResponse,
  MatterDraft,
  MatterReminder,
  TodayDraft,
  TodaySchedule,
} from "@/lib/mobile/api";
import { relativeTime } from "@/lib/mobile/format";
import { deadlineTiming, todayChangeSummary } from "@/lib/mobile/matter-draft.mjs";
import { confirmSummary, acknowledgeReminder, getDraft } from "@/lib/mobile/matters-api";
import { useToday } from "@/lib/mobile/use-today";

function localDay(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(
    now.getDate(),
  ).padStart(2, "0")}`;
}

function time(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    ...(timeZone ? { timeZone } : {}),
  });
}

function dateTime(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    ...(timeZone ? { timeZone } : {}),
  });
}

function reminderNote(item: MatterReminder): string {
  if (item.source === "ios_eventkit" || item.source === "ios_reminders") {
    return item.sync_status === "ios_synced" ? "来自苹果端副本" : "来自苹果端";
  }
  if (item.status === "acknowledged") return "应用内提醒 · 已知晓";
  if (item.status === "cancelled") return "应用内提醒 · 已取消";
  if (item.status === "due") return "应用内提醒已到点 · 等待你确认知晓";
  if (item.sync_status === "pending_ios") return "应用内提醒 · 等待 iOS 同步";
  if (item.sync_status === "ios_synced") return "应用内提醒 · iOS 已有副本";
  return "应用内提醒已保存 · 到点后可在网页确认，不能视为已经送达";
}

function routeState(schedule: TodaySchedule): {
  label: string;
  tone: "neutral" | "amber" | "green" | "red";
  detail: string | null;
} {
  const message = schedule.route.message || "相关路线尚未核实。";
  if (schedule.route.status === "available") {
    return { label: "路程已核实", tone: "green", detail: null };
  }
  if (schedule.route.status === "unavailable") {
    return { label: "路程不可用", tone: "red", detail: message };
  }
  return { label: "路程未核实", tone: "amber", detail: message };
}

function draftQuestion(draft: TodayDraft): string {
  if (draft.conflict?.has_conflict) {
    return "需要选择调整方案或明确接受行程风险。";
  }
  return draft.matter_id ? "需要核对服务器生成的变更预览。" : "需要核对新事项和安排。";
}

interface TimelineRow {
  id: string;
  at: string;
  title: string;
  matterTitle: string | null;
  detail: string;
  kind: "schedule" | "reminder";
  matterId: string | null;
  routeDetail?: string | null;
  routeLabel?: string;
  routeTone?: "neutral" | "amber" | "green" | "red";
  schedule?: TodaySchedule;
}

export default function TodayPage() {
  const { state } = useAuth();
  const signedIn = state === "signedIn";
  const { records, removeRecord } = useLocalRecords();
  const { openCapture, toast } = useUI();
  const todayData = useToday(signedIn);
  const [selectedRecord, setSelectedRecord] = useState<LocalRecord | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [selectedDraft, setSelectedDraft] = useState<MatterDraft | null>(null);
  const [openingDraftId, setOpeningDraftId] = useState<string | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [routeMapSchedule, setRouteMapSchedule] = useState<TodaySchedule | null>(
    null,
  );
  const [acknowledgingId, setAcknowledgingId] = useState<string | null>(null);
  const [acknowledgeError, setAcknowledgeError] = useState<string | null>(null);

  const dueReminders = useMemo(
    () => (todayData.data?.reminders ?? []).filter((item) => item.status === "due"),
    [todayData.data],
  );

  const acknowledge = async (reminder: MatterReminder) => {
    setAcknowledgingId(reminder.reminder_id);
    setAcknowledgeError(null);
    try {
      await acknowledgeReminder(reminder.reminder_id);
      todayData.refresh();
    } catch (caught) {
      setAcknowledgeError(
        caught instanceof Error ? caught.message : "服务器没有确认这条提醒已知晓。",
      );
    } finally {
      setAcknowledgingId(null);
    }
  };

  const rows = useMemo<TimelineRow[]>(() => {
    const data = todayData.data;
    if (!data) return [];
    const scheduleRows: TimelineRow[] = data.schedules.map((item) => {
      const route = routeState(item);
      return {
        id: `schedule-${item.schedule_id}`,
        at: item.starts_at,
        title: item.title,
        matterTitle: item.matter_title,
        detail: `${item.location || "地点未填"} · ${item.source === "ios_eventkit" ? "苹果端日程副本" : "应用内日程"}`,
        kind: "schedule",
        matterId: item.matter_id,
        routeDetail: route.detail,
        routeLabel: route.label,
        routeTone: route.tone,
        schedule: item,
      };
    });
    const reminderRows: TimelineRow[] = data.reminders.map((item) => ({
      id: `reminder-${item.reminder_id}`,
      at: item.trigger_at,
      title: item.title,
      matterTitle: item.matter_title,
      detail: reminderNote(item),
      kind: "reminder",
      matterId: item.matter_id,
    }));
    return [...scheduleRows, ...reminderRows].sort(
      (left, right) => new Date(left.at).getTime() - new Date(right.at).getTime(),
    );
  }, [todayData.data]);

  const deadlines = useMemo(
    () =>
      [...(todayData.data?.deadlines ?? [])].sort(
        (left, right) => new Date(left.due_at).getTime() - new Date(right.due_at).getTime(),
      ),
    [todayData.data],
  );

  const overview = (() => {
    if (todayData.loading && !todayData.data) return "正在读取今天的安排…";
    if (todayData.error) return "暂时无法读取今天的安排；这不是空日程。";
    if (!todayData.data) return "登录后从服务器读取今天的安排。";
    const items =
      deadlines.length + (todayData.data.drafts.length ?? 0) + dueReminders.length;
    if (items > 0) return `先处理 ${items} 项需要确认或跟进的内容。`;
    if (rows.length === 0) return "今天没有已确定的日程或待处理提醒。";
    return `今天有 ${todayData.data.schedules.length} 项日程${
      todayData.data.reminders.length > 0 ? `、${todayData.data.reminders.length} 个提醒` : ""
    }。`;
  })();

  const openDraft = async (item: TodayDraft) => {
    setOpeningDraftId(item.draft_id);
    setDraftError(null);
    try {
      setSelectedDraft(await getDraft(item.draft_id));
    } catch (caught) {
      setDraftError(caught instanceof Error ? caught.message : "无法读取这条待确认草稿。");
    } finally {
      setOpeningDraftId(null);
    }
  };

  const onDraftConfirmed = (response: DraftConfirmResponse) => {
    setSelectedDraft(null);
    toast(confirmSummary(response));
    todayData.refresh();
  };

  return (
    <div className="m-screen m-stack-18" style={{ paddingBottom: 16 }}>
      <header style={{ padding: "8px var(--m-gutter) 0" }}>
        <div className="flex items-center justify-between gap-10">
          <h1 className="screen-title serif-title">今天</h1>
          {todayData.data ? <Pill tone="fog">{todayData.data.timezone}</Pill> : null}
        </div>
        <p className="micro" style={{ margin: "4px 0 0" }}>
          {todayData.data?.date || localDay()}
        </p>
        <p className="today-overview">{overview}</p>
        {signedIn ? (
          <button
            type="button"
            className="link-button mt-6"
            onClick={todayData.refresh}
            disabled={todayData.loading}
          >
            <RefreshCw size={13} /> {todayData.loading ? "正在刷新…" : "重新读取"}
          </button>
        ) : null}
      </header>

      <div className="quick-row">
        <button
          type="button"
          className="quick-btn quick-btn--primary"
          onClick={() => openCapture("choose")}
        >
          <span aria-hidden="true">＋</span> 收下内容
        </button>
        <button
          type="button"
          className="quick-btn quick-btn--glass"
          onClick={() => openCapture("say")}
        >
          记一句
        </button>
      </div>

      <TodayCommuteCard />

      {!signedIn ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <EmptyState
            title="登录后读取今天的安排"
            body="未登录时只显示本机记录；不会用示例日程冒充服务器。"
          >
            <Link href="/login" className="btn btn--secondary">
              登录 PA 账号
            </Link>
          </EmptyState>
        </div>
      ) : todayData.loading && !todayData.data ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <LoadingCard label="正在读取今天的安排…" />
        </div>
      ) : todayData.error ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <ErrorCard message={todayData.error.message} onRetry={todayData.refresh} />
        </div>
      ) : todayData.data ? (
        <>
          {dueReminders.length > 0 ? (
            <section style={{ padding: "0 var(--m-gutter)" }}>
              <SectionHeading title="已经到点" trailing="需要确认知晓" />
              <div className="m-stack-10">
                {dueReminders.map((reminder) => (
                  <div key={reminder.reminder_id} className="deadline-card deadline-card--overdue">
                    <span className="tile tile--sm">
                      <Bell size={15} />
                    </span>
                    <span className="grow">
                      <span className="deadline-card__title">{reminder.title}</span>
                      <span className="deadline-card__meta">
                        {reminder.matter_title || "所属事项"} ·{" "}
                        {dateTime(reminder.trigger_at, todayData.data?.timezone)}
                      </span>
                    </span>
                    <button
                      type="button"
                      className="btn btn--secondary btn--small"
                      onClick={() => void acknowledge(reminder)}
                      disabled={acknowledgingId !== null}
                    >
                      {acknowledgingId === reminder.reminder_id ? "确认中…" : "知道了"}
                    </button>
                  </div>
                ))}
              </div>
              <p className="micro" style={{ margin: "8px 0 0" }}>
                “知道了”只表示你已在网页确认这条提醒，不代表服务器已送达推送。
              </p>
              {acknowledgeError ? (
                <p className="danger-text" style={{ margin: "8px 0 0" }} role="alert">
                  {acknowledgeError}
                </p>
              ) : null}
            </section>
          ) : null}

          {deadlines.length > 0 ? (
            <section style={{ padding: "0 var(--m-gutter)" }}>
              <SectionHeading title="需要跟进" trailing="逾期与今天到期" />
              <div className="m-stack-10">
                {deadlines.map((deadline) => {
                  const timing = deadlineTiming(
                    deadline.due_at,
                    todayData.data?.date || localDay(),
                    todayData.data?.timezone,
                  );
                  return (
                    <Link
                      key={deadline.todo_id}
                      href={`/matters/${deadline.matter_id}#changes`}
                      className={`deadline-card${timing.state === "overdue" ? " deadline-card--overdue" : ""}`}
                    >
                      <span className="tile tile--sm">
                        <CalendarClock size={15} />
                      </span>
                      <span className="grow">
                        <span className="deadline-card__title">{deadline.title}</span>
                        <span className="deadline-card__meta">
                          {deadline.matter_title || "所属事项"} ·{" "}
                          {dateTime(deadline.due_at, todayData.data?.timezone)}
                        </span>
                      </span>
                      <span className="deadline-card__state">{timing.label}</span>
                      <ChevronRight size={15} className="faint" />
                    </Link>
                  );
                })}
              </div>
            </section>
          ) : null}

          {todayData.data.drafts.length > 0 ? (
            <section style={{ padding: "0 var(--m-gutter)" }}>
              <SectionHeading title="待确认" trailing={`${todayData.data.drafts.length} 条`} />
              <div className="m-stack-10">
                {todayData.data.drafts.map((item) => (
                  <button
                    key={item.draft_id}
                    type="button"
                    className="draft-card"
                    onClick={() => void openDraft(item)}
                    disabled={openingDraftId !== null}
                  >
                    <span className="draft-card__line" aria-hidden="true" />
                    <span className="grow">
                      <span className="draft-card__title">
                        {item.matter_title || item.schedule?.title || "新事项"}
                      </span>
                      {item.matter_title && item.schedule?.title ? (
                        <span className="draft-card__subject">
                          日程「{item.schedule.title}」
                        </span>
                      ) : null}
                      <span className="draft-card__impact">{item.impact}</span>
                      <span className="draft-card__question">{draftQuestion(item)}</span>
                    </span>
                    <span className="draft-card__action">
                      {openingDraftId === item.draft_id ? "读取中" : "查看"}
                      <ChevronRight size={14} />
                    </span>
                  </button>
                ))}
              </div>
              {draftError ? (
                <p className="danger-text" style={{ margin: "8px 0 0" }} role="alert">
                  {draftError}
                </p>
              ) : null}
            </section>
          ) : null}

          <section style={{ padding: "0 var(--m-gutter)" }}>
            <SectionHeading title="今日时间线" trailing="已确定安排" />
            {rows.length === 0 ? (
              <EmptyState title="今天没有安排" body="服务器确认没有日程或待处理提醒。" />
            ) : (
              <div className="m-stack-8">
                {rows.map((row) => (
                  <div key={row.id} className="card card--tight timeline-link">
                    <span className="timeline-time">{time(row.at, todayData.data?.timezone)}</span>
                    <span className="timeline-dot" aria-hidden="true" />
                    <span className="grow">
                      <span className="card-title" style={{ display: "block", fontSize: 15 }}>
                        {row.title}
                      </span>
                      {row.matterTitle ? (
                        row.matterId ? (
                          <Link
                            href={`/matters/${row.matterId}`}
                            className="caption timeline-matter"
                          >
                            {row.matterTitle}
                          </Link>
                        ) : (
                          <span className="caption timeline-matter">{row.matterTitle}</span>
                        )
                      ) : null}
                      <span className="caption" style={{ display: "block", margin: "2px 0 0" }}>
                        {row.detail}
                      </span>
                      {row.schedule ? (
                        <RouteTextCard
                          embedded
                          originLabel={
                            row.schedule.route.origin_basis === "previous_schedule"
                              ? "从上一项安排出发"
                              : row.schedule.route.origin_basis === "user_confirmed"
                                ? "从已确认起点出发"
                                : "出发地未提供"
                          }
                          destinationLabel={row.schedule.location || row.schedule.title}
                          arrivalAt={row.schedule.starts_at}
                          arrivalLabel="安排时间"
                          mode={row.schedule.route.mode}
                          durationSeconds={row.schedule.route.duration_seconds}
                          state={
                            row.schedule.route.status === "available"
                              ? "verified"
                              : row.schedule.route.status === "unavailable"
                                ? "unavailable"
                                : "unverified"
                          }
                          notice={
                            row.schedule.route.status === "available"
                              ? row.schedule.route.fetched_at
                                ? `路线结果取得于 ${dateTime(row.schedule.route.fetched_at, todayData.data?.timezone)}`
                                : "路线结果由服务器返回。"
                              : row.schedule.route.message || "尚无与该日程关联的路线结果。"
                          }
                          replanHref={
                            row.matterId
                              ? `/matters/${row.matterId}`
                              : undefined
                          }
                          onViewRoute={
                            row.schedule.route.status === "available"
                              ? () => setRouteMapSchedule(row.schedule ?? null)
                              : undefined
                          }
                          mapTarget={{
                            label: row.schedule.location_address || row.schedule.location,
                            latitude: row.schedule.latitude,
                            longitude: row.schedule.longitude,
                            coordinateSystem: row.schedule.coordinate_system,
                            address: row.schedule.location_address,
                            city: row.schedule.location_city,
                          }}
                        />
                      ) : null}
                    </span>
                    {row.kind === "reminder" ? (
                      <Bell size={14} className="fog" />
                    ) : (
                      <MapPin size={14} className="fog" />
                    )}
                  </div>
                ))}
              </div>
            )}
            <p className="micro" style={{ margin: "8px 0 0" }}>
              提醒只表示服务器保存了待发记录，尚未确认送达。
            </p>
          </section>

          {todayData.data.changes.length > 0 ? (
            <section style={{ padding: "0 var(--m-gutter)" }}>
              <details className="today-changes">
                <summary>
                  <span>已生效变更</span>
                  <span>{todayData.data.changes.length} 条</span>
                </summary>
                <div className="today-changes__body">
                  {todayData.data.changes.map((change) => (
                    <Link
                      key={change.change_id}
                      href={`/matters/${change.matter_id}#changes`}
                      className="today-change-row"
                    >
                      <span className="grow">
                        <span className="today-change-row__title">
                          {change.matter_title || "事项"}
                        </span>
                        <span className="today-change-row__summary">
                          {todayChangeSummary(change)}
                        </span>
                        <span className="micro">
                          {dateTime(change.created_at, todayData.data?.timezone)}
                        </span>
                      </span>
                      <ChevronRight size={14} className="faint" />
                    </Link>
                  ))}
                </div>
              </details>
            </section>
          ) : null}

          <section style={{ padding: "0 var(--m-gutter)" }}>
            <div className="notice">
              组合通勤草稿确认后，首日日程与重复计划会关联保存；后续日期调度仍需单独核验。
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
                  <span className="tile tile--sm">
                    <FileText size={15} />
                  </span>
                  <span className="row__body">
                    <span className="row__title" style={{ display: "block" }}>
                      {record.title}
                    </span>
                    <span className="row__detail" style={{ display: "block" }}>
                      本机保存 · {relativeTime(record.createdAt)}
                    </span>
                  </span>
                  <span className="chevron" aria-hidden="true">
                    ›
                  </span>
                </button>
              </div>
            ))}
          </div>
        )}
        <p className="micro" style={{ margin: "8px 0 0" }}>
          本机记录不属于服务器日程，不会进入上面的时间线。
        </p>
      </section>

      <Sheet title="本机记录" open={Boolean(selectedRecord)} onClose={() => setSelectedRecord(null)}>
        {selectedRecord ? (
          <>
            <p className="card-title">{selectedRecord.title}</p>
            <p className="micro mt-4">
              {selectedRecord.kind === "note" ? "记一句" : "收下的内容"} ·{" "}
              {relativeTime(selectedRecord.createdAt)}
            </p>
            <p
              className="body-text mt-12"
              style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
            >
              {selectedRecord.text}
            </p>
            <div className="notice mt-16">这条内容只保存在当前浏览器，没有上传到 PA 服务器。</div>
            <button
              type="button"
              className="btn btn--danger mt-16"
              onClick={() => setConfirmDelete(true)}
            >
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

      <MatterDraftSheet
        draft={selectedDraft}
        open={Boolean(selectedDraft)}
        onClose={() => setSelectedDraft(null)}
        onConfirmed={onDraftConfirmed}
        onDraftCancelled={() => {
          setSelectedDraft(null);
          toast("草稿已取消");
          todayData.refresh();
        }}
      />
      <RouteMapSheet
        open={Boolean(routeMapSchedule)}
        scheduleId={routeMapSchedule?.schedule_id ?? null}
        title={routeMapSchedule ? `路线 · ${routeMapSchedule.title}` : "查看路线"}
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
        fallbackMode={routeMapSchedule?.route.mode ?? null}
        fallbackDurationSeconds={
          routeMapSchedule?.route.duration_seconds ?? null
        }
        fallbackNotice={
          routeMapSchedule?.route.fetched_at
            ? `路线结果取得于 ${dateTime(
                routeMapSchedule.route.fetched_at,
                todayData.data?.timezone,
              )}`
            : null
        }
        onClose={() => setRouteMapSchedule(null)}
      />
    </div>
  );
}

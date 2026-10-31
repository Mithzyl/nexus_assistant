"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Bell, CalendarClock, ChevronRight, FileText, MapPin, PenLine, RefreshCw, Sparkles, Trash2 } from "lucide-react";

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
  LoginRequired,
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
import { formatServiceDate, relativeTime, timezoneLabel } from "@/lib/mobile/format";
import { deadlineTiming, todayChangeSummary } from "@/lib/mobile/matter-draft.mjs";
import { confirmSummary, acknowledgeReminder, completeMatterTodo, getDraft } from "@/lib/mobile/matters-api";
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
    return item.sync_status === "ios_synced" ? "来自 Apple 日历" : "来自 Apple 日历";
  }
  if (item.status === "acknowledged") return "应用内提醒 · 已知晓";
  if (item.status === "cancelled") return "应用内提醒 · 已取消";
  if (item.status === "due") return "应用内提醒已到点 · 等待你确认知晓";
  if (item.sync_status === "pending_ios") return "应用内提醒 · 等待同步到 Apple 日历";
  if (item.sync_status === "ios_synced") return "应用内提醒 · Apple 日历已有";
  return "提醒已设好 · 本页面保持打开时，到点将在页面内提醒你";
}

function routeState(schedule: TodaySchedule): {
  label: string;
  tone: "neutral" | "amber" | "green" | "red";
  detail: string | null;
} {
  const message = schedule.route.message || "相关路线尚未核实。";
  if (schedule.route.status === "available") {
    return { label: "路线已核实", tone: "green", detail: null };
  }
  if (schedule.route.status === "unavailable") {
    return { label: "路线不可用", tone: "red", detail: message };
  }
  return { label: "路线未核实", tone: "amber", detail: "尚无与该日程关联的路线结果。" };
}

function draftQuestion(draft: TodayDraft): string {
  if (draft.conflict?.has_conflict) {
    return "需要选择调整方案或明确接受行程风险。";
  }
  return draft.matter_id ? "需要核对助手整理出的变更。" : "需要核对新事项和安排。";
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

  const [notificationPermission, setNotificationPermission] = useState<NotificationPermission | "unsupported">("default");
  const notified = useRef(new Set<string>());
  useEffect(() => {
    setNotificationPermission("Notification" in window ? Notification.permission : "unsupported");
  }, []);
  useEffect(() => {
    if (!signedIn || notificationPermission !== "granted") return;
    for (const reminder of dueReminders) {
      const deliveryKey = `${reminder.reminder_id}:${reminder.trigger_at}`;
      if (notified.current.has(deliveryKey)) continue;
      try {
        const notice = new Notification(`到点提醒 · ${reminder.title}`, {
          body: `来自「${reminder.matter_title || "事项"}」`, tag: reminder.reminder_id,
        });
        notice.onclick = () => { window.focus(); notice.close(); };
        notified.current.add(deliveryKey);
      } catch { setNotificationPermission("unsupported"); }
    }
  }, [dueReminders, signedIn, notificationPermission]);
  const enableNotifications = async () => {
    if ("Notification" in window) setNotificationPermission(await Notification.requestPermission());
  };

  const acknowledge = async (reminder: MatterReminder, complete = false) => {
    setAcknowledgingId(reminder.reminder_id);
    setAcknowledgeError(null);
    try {
      if (complete && reminder.matter_id && reminder.todo_id) await completeMatterTodo(reminder.matter_id, reminder.todo_id);
      await acknowledgeReminder(reminder.reminder_id);
      todayData.refresh();
    } catch (caught) {
      setAcknowledgeError(
        caught instanceof Error ? caught.message : "暂时没能更新这条提醒，请稍后再试。",
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
        detail: `${item.location || "地点未填"} · ${item.source === "ios_eventkit" ? "Apple 日历日程" : "应用内日程"}`,
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
    if (todayData.loading && !todayData.data) return "正在同步今天的安排…";
    if (todayData.error) return "暂时无法同步今天的安排，请稍后再试。";
    if (!todayData.data) return "今天还没有同步的内容。";
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
      setDraftError(caught instanceof Error ? caught.message : "暂时无法查看这条待确认草稿。");
    } finally {
      setOpeningDraftId(null);
    }
  };

  const onDraftConfirmed = (response: DraftConfirmResponse) => {
    setSelectedDraft(null);
    toast(confirmSummary(response));
    todayData.refresh();
  };

  const displayDate = formatServiceDate(todayData.data?.date);
  const displayTimezone = timezoneLabel(todayData.data?.timezone);
  const showTimezone = Boolean(displayTimezone && displayTimezone !== "北京时间 (GMT+8)");

  return (
    <div className="m-screen m-stack-18" style={{ paddingBottom: 16 }}>
      <header style={{ padding: "8px var(--m-gutter) 0" }}>
        <div className="flex items-center justify-between gap-10">
          <h1 className="screen-title serif-title">今天</h1>
          {showTimezone && displayTimezone ? <Pill tone="fog">{displayTimezone}</Pill> : null}
        </div>
        <p className="micro" style={{ margin: "4px 0 0" }}>
          {displayDate}
          {!signedIn ? " · 记录与整理今日安排" : ""}
        </p>
        {signedIn ? <p className="today-overview">{overview}</p> : null}
        {signedIn ? (
          <button
            type="button"
            className="link-button mt-6"
            onClick={todayData.refresh}
            disabled={todayData.loading}
          >
            <RefreshCw size={13} /> {todayData.loading ? "正在刷新…" : "刷新"}
          </button>
        ) : null}
      </header>

      <div className="quick-row">
        <button
          type="button"
          className="quick-btn quick-btn--primary"
          onClick={() => openCapture("choose")}
        >
          <span className="quick-btn__title">
            <Sparkles size={16} strokeWidth={1.5} aria-hidden="true" /> 交给助手整理
          </span>
          <span className="quick-btn__hint">粘贴消息、会议或待办</span>
        </button>
        <button
          type="button"
          className="quick-btn quick-btn--glass"
          onClick={() => openCapture("say")}
        >
          <span className="quick-btn__title">
            <PenLine size={16} strokeWidth={1.5} aria-hidden="true" /> 随手记一条
          </span>
          <span className="quick-btn__hint">记下想法或单条待办</span>
        </button>
      </div>

      {signedIn ? <TodayCommuteCard /> : null}

      {!signedIn ? null : todayData.loading && !todayData.data ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <LoadingCard label="正在同步今天的安排…" />
        </div>
      ) : todayData.error ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <ErrorCard message={todayData.error.message} onRetry={todayData.refresh} />
        </div>
      ) : todayData.data ? (
        <>
          <section style={{ padding: "0 var(--m-gutter)", marginBottom: 12 }} aria-label="提醒送达方式">
            <p className="micro">{notificationPermission === "granted"
              ? "已开启系统通知 · 本页面保持打开时，可在后台发送浏览器通知"
              : "仅页面内提醒 · 页面保持打开时，每 15 秒核对一次到点提醒"}</p>
            <p className="micro">页面关闭后不再发送提醒；日历订阅的提醒由日历应用负责。</p>
            {notificationPermission === "default" ? <button className="btn btn--secondary btn--small" onClick={() => void enableNotifications()}>开启浏览器系统通知</button> : null}
            {notificationPermission === "denied" ? <p className="micro">浏览器通知未获允许，页面内提醒仍可使用。</p> : null}
          </section>
          {dueReminders.length > 0 ? (
            <section style={{ padding: "0 var(--m-gutter)" }}>
              <SectionHeading title="到点提醒" trailing="需要确认知晓" />
              <div className="m-stack-10">
                {dueReminders.map((reminder) => (
                  <div key={reminder.reminder_id} className="deadline-card deadline-card--overdue">
                    <span className="tile tile--sm">
                      <Bell size={15} />
                    </span>
                    <span className="grow">
                      <span className="deadline-card__title">到点提醒 · {reminder.title}</span>
                      <span className="deadline-card__meta">
                        原定 {dateTime(reminder.trigger_at, todayData.data?.timezone)} 提醒 · 来自「{reminder.matter_title || "所属事项"}」
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
                    {reminder.todo_id && reminder.matter_id ? <button type="button" className="btn btn--secondary btn--small" disabled={acknowledgingId !== null} onClick={() => void acknowledge(reminder, true)}>已办完，一并完成待办</button> : null}
                  </div>
                ))}
              </div>
              <p className="micro" style={{ margin: "8px 0 0" }}>
                确认后，这条提醒会标记为已知晓。
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
                      {openingDraftId === item.draft_id ? "加载中" : "查看"}
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
              <EmptyState title="今天没有安排" body="没有已确定的日程或待处理提醒。" />
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
                                : "路线结果已更新。"
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
              提醒已保存；到点后需要你在这里确认。
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
              组合通勤确认后，首日日程会与固定计划一起保存；后续日期仍可单独调整。
            </div>
          </section>
        </>
      ) : null}

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <SectionHeading title="最近整理" trailing="本机暂存" />
        {records.length === 0 ? (
          <EmptyState title="还没有暂存内容" body="交给助手整理或随手记一条，内容会出现在这里。" />
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
                      仅保存在当前设备 · {relativeTime(record.createdAt)}
                    </span>
                  </span>
                  <ChevronRight size={16} className="chevron" aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>
        )}
        <p className="micro" style={{ margin: "8px 0 0" }}>
          仅保存在当前设备，可随时删除。
        </p>
      </section>

      {!signedIn ? (
        <section style={{ padding: "0 var(--m-gutter)" }}>
          <LoginRequired
            title="登录后同步今日安排"
            body="登录账号后，今天的日程、提醒和通勤建议会同步到这里；暂存内容会留在当前设备。"
          />
        </section>
      ) : null}

      <Sheet title="本机暂存" open={Boolean(selectedRecord)} onClose={() => setSelectedRecord(null)}>
        {selectedRecord ? (
          <>
            <p className="card-title">{selectedRecord.title}</p>
            <p className="micro mt-4">
              {selectedRecord.kind === "note" ? "随手记一条" : "交给助手整理"} ·{" "}
              {relativeTime(selectedRecord.createdAt)}
            </p>
            <p
              className="body-text mt-12"
              style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
            >
              {selectedRecord.text}
            </p>
            <div className="notice mt-16">这条内容仅保存在当前设备，不会同步到云端。</div>
            <button
              type="button"
              className="btn btn--danger mt-16"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 size={15} /> 删除这条暂存
            </button>
          </>
        ) : null}
      </Sheet>

      <ConfirmDialog
        open={confirmDelete}
        title="删除这条暂存？"
        body="删除后无法恢复，云端没有这份内容。"
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

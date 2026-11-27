"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Bell, CalendarClock, ChevronRight, FileText, MapPin, PenLine, RefreshCw, Sparkles, Trash2 } from "lucide-react";

import { MatterDraftSheet } from "@/components/mobile/matter-draft-sheet";
import { RouteMapSheet } from "@/components/mobile/route-map-sheet";
import { RouteTextCard } from "@/components/mobile/route-text-card";
import { TodayNextActions } from "@/components/mobile/today-next-actions";
import type { TodayAction } from "@/lib/mobile/next-actions-api";
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
import { confirmSummary, acknowledgeReminder, completeMatterTodo, getDraft, createMatterEditDraft, newOperationKey, requestTodayRefresh } from "@/lib/mobile/matters-api";
import { useToday } from "@/lib/mobile/use-today";
import { CAPTURE_LOGIN_DRAFT_KEY, readSession } from "@/lib/mobile/storage";

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
    return item.sync_status === "ios_synced" ? "来自 苹果日历" : "来自 苹果日历";
  }
  if (item.status === "acknowledged") return "应用内提醒 · 已确认";
  if (item.status === "cancelled") return "应用内提醒 · 已取消";
  if (item.status === "due") return "提醒到点了";
  if (item.sync_status === "pending_ios") return "应用内提醒 · 苹果日历待更新";
  if (item.sync_status === "ios_synced") return "应用内提醒 · 苹果日历已更新";
  return "提醒已设置";
}

/** Display known commute endpoints independently of the saved route's freshness. */
function TimelineRoute({ schedule, timezone, onViewRoute }: {
  schedule: TodaySchedule;
  timezone?: string;
  onViewRoute: () => void;
}) {
  const route = schedule.route;
  const commute = route.commute;
  const available = route.status === "available";
  if (!commute && !available && !schedule.location && schedule.latitude == null) return null;
  const originLabel = commute?.origin_label || (route.origin_basis === "previous_schedule"
    ? "从上一项安排出发"
    : route.origin_basis === "user_confirmed" ? "从已确认起点出发"
    : route.origin_known ? "已记录出发点" : null);
  const historical = !available && Boolean(commute?.saved_route);
  return (
    <RouteTextCard
      embedded
      originLabel={originLabel}
      destinationLabel={commute?.destination_label || schedule.location || schedule.title}
      arrivalAt={schedule.starts_at}
      arrivalLabel="安排时间"
      mode={route.mode ?? commute?.saved_route?.mode}
      durationSeconds={route.duration_seconds ?? commute?.saved_route?.duration_seconds}
      transfersSummary={commute?.saved_route?.transfers_summary}
      state={route.status === "available" ? "verified" : route.status}
      historical={historical}
      allowOriginPrompt={!commute && route.origin_known !== true}
      commuteHref={commute
        ? `/me/commute/${encodeURIComponent(commute.plan_id)}?service_date=${encodeURIComponent(commute.service_date)}`
        : undefined}
      notice={available && route.fetched_at
        ? `路线更新于 ${dateTime(route.fetched_at, timezone)}`
        : commute ? undefined : route.message}
      replanHref={!commute && schedule.matter_id
        ? `/matters/${encodeURIComponent(schedule.matter_id)}`
        : undefined}
      onViewRoute={available ? onViewRoute : undefined}
      mapTarget={{
        label: schedule.location_address || schedule.location,
        latitude: schedule.latitude,
        longitude: schedule.longitude,
        coordinateSystem: schedule.coordinate_system,
        address: schedule.location_address,
        city: schedule.location_city,
      }}
    />
  );
}

function draftQuestion(draft: TodayDraft): string {
  if (draft.conflict?.has_conflict) {
    return "先选一个调整方案，或确认按原时间走。";
  }
  return draft.matter_id ? "看看助手整理了哪些修改。" : "看看整理出的新事项和安排。";
}

interface TimelineRow {
  id: string;
  at: string;
  title: string;
  matterTitle: string | null;
  detail: string;
  kind: "schedule" | "reminder";
  matterId: string | null;
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
  useEffect(() => {
    if (!signedIn || new URLSearchParams(window.location.search).get("capture") !== "resume") return;
    const saved = readSession<{ preset?: "choose" | "say" } | null>(CAPTURE_LOGIN_DRAFT_KEY, null);
    if (saved?.preset === "say" || saved?.preset === "choose") openCapture(saved.preset);
  }, [signedIn, openCapture]);

  const unfinishedSchedule = todayData.data?.schedules.find((item) =>
    item.status === "scheduled" && !item.ends_at && Date.parse(item.starts_at) <= Date.now());
  const unfinishedScheduleHref = unfinishedSchedule?.matter_id
    ? `/matters/${encodeURIComponent(unfinishedSchedule.matter_id)}#schedule-${encodeURIComponent(unfinishedSchedule.schedule_id)}`
    : "#today-timeline";

  const dueReminders = useMemo(
    () => (todayData.data?.reminders ?? []).filter((item) => item.status === "due"),
    [todayData.data],
  );

  const acknowledge = async (reminder: MatterReminder, complete = false) => {
    setAcknowledgingId(reminder.reminder_id);
    setAcknowledgeError(null);
    try {
      if (complete && reminder.matter_id && reminder.todo_id) await completeMatterTodo(reminder.matter_id, reminder.todo_id);
      await acknowledgeReminder(reminder.reminder_id);
      todayData.refresh();
    } catch (caught) {
      setAcknowledgeError(
        caught instanceof Error ? caught.message : "这条提醒刚才没更新上，再试一次。",
      );
    } finally {
      setAcknowledgingId(null);
    }
  };

  const rows = useMemo<TimelineRow[]>(() => {
    const data = todayData.data;
    if (!data) return [];
    const scheduleRows: TimelineRow[] = data.schedules.map((item) => {
      return {
        id: `schedule-${item.schedule_id}`,
        at: item.starts_at,
        title: item.title,
        matterTitle: item.matter_title,
        detail: item.location || "",
        kind: "schedule",
        matterId: item.matter_id,
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

  const captureReplies = todayData.data?.capture_replies ?? [];

  const overview = (() => {
    if (todayData.loading && !todayData.data) return "正在同步今天的安排…";
    if (todayData.error) return "今天的安排暂时没同步上，稍后再试。";
    if (!todayData.data) return "";
    const items =
      deadlines.length + todayData.data.drafts.length + dueReminders.length + captureReplies.length;
    if (items > 0) return `先处理 ${items} 项需要确认或跟进的内容。`;
    if (rows.length === 0) return "";
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
      setDraftError(caught instanceof Error ? caught.message : "这条待确认草稿暂时打不开。");
    } finally {
      setOpeningDraftId(null);
    }
  };

  /** Reuse the original record draft and the existing edit/confirmation sheet for rescheduling. */
  const openSuggestedEdit = async (item: TodayAction) => {
    if (!item.matter_id) return;
    setSelectedDraft(await createMatterEditDraft(item.matter_id, {
      idempotency_key: newOperationKey("web-next-reschedule"),
      ...(item.kind === "todo" ? { todo_id: item.record_id } : { schedule_id: item.record_id }),
    }));
  };

  const onDraftConfirmed = (response: DraftConfirmResponse) => {
    setSelectedDraft(null);
    toast(confirmSummary(response));
    requestTodayRefresh();
  };

  const displayDate = formatServiceDate(todayData.data?.date);
  const displayTimezone = timezoneLabel(todayData.data?.timezone);
  const showTimezone = Boolean(displayTimezone && displayTimezone !== "北京时间");

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
            onClick={requestTodayRefresh}
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
          <span className="quick-btn__hint">贴一段聊天记录或会议通知</span>
        </button>
        <button
          type="button"
          className="quick-btn quick-btn--glass"
          onClick={() => openCapture("say")}
        >
          <span className="quick-btn__title">
            <PenLine size={16} strokeWidth={1.5} aria-hidden="true" /> 随手记一条
          </span>
          <span className="quick-btn__hint">记个想法，或一件小事</span>
        </button>
      </div>

      {signedIn && captureReplies.length > 0 ? (
        <section style={{ padding: "0 var(--m-gutter)" }}>
          <SectionHeading title="随手记待确认" />
          <div className="m-stack-8">
            {captureReplies.map((item) => (
              <Link key={item.local_run_id} href={`/ask?reply=${encodeURIComponent(item.local_run_id)}`} className="card card--amber">
                <p className="card-title">{item.request_text}</p>
                <p className="caption">{item.question}</p>
                {item.choices.length ? <p className="caption">{item.choices.join(" · ")}</p> : null}
                <span className="link-button">回复助手 <ChevronRight size={14} /></span>
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      {signedIn ? <TodayNextActions onReschedule={openSuggestedEdit} onCapture={() => openCapture("say")} unfinishedScheduleHref={unfinishedScheduleHref} /> : null}
      {signedIn ? <TodayCommuteCard /> : null}

      {!signedIn ? null : todayData.loading && !todayData.data ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <LoadingCard label="正在同步今天的安排…" />
        </div>
      ) : todayData.error ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <ErrorCard error={todayData.error} message={todayData.error.message} onRetry={todayData.refresh} />
        </div>
      ) : todayData.data ? (
        <>
          {dueReminders.length > 0 ? (
            <section aria-label="到点提醒" style={{ padding: "0 var(--m-gutter)" }}>
              <SectionHeading title="到点提醒" />
              <div className="m-stack-10">
                {dueReminders.map((reminder) => (
                  <div key={reminder.reminder_id} className="deadline-card deadline-card--overdue deadline-card--with-actions">
                    <div className="deadline-card__content">
                      <span className="tile tile--sm"><Bell size={15} /></span>
                      <span className="grow">
                        <span className="deadline-card__title">{reminder.title}</span>
                        <span className="deadline-card__meta">
                          {dateTime(reminder.trigger_at, todayData.data?.timezone)} · 「{reminder.matter_title || "所属事项"}」
                        </span>
                      </span>
                    </div>
                    <div className="action-row">
                      <button type="button" className={reminder.todo_id && reminder.matter_id ? "link-button" : "btn btn--secondary btn--small"}
                        onClick={() => void acknowledge(reminder)} disabled={acknowledgingId !== null}>
                        {acknowledgingId === reminder.reminder_id ? "确认中…" : "知道了"}
                      </button>
                      {reminder.todo_id && reminder.matter_id ? (
                        <button type="button" className="btn btn--secondary btn--small" disabled={acknowledgingId !== null}
                          onClick={() => void acknowledge(reminder, true)}>完成待办</button>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
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
              <SectionHeading title="待确认的安排" trailing={`${todayData.data.drafts.length} 条`} />
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

          <section id="today-timeline" style={{ padding: "0 var(--m-gutter)" }}>
            <SectionHeading title="今日时间线" />
            {rows.length === 0 ? (
              <EmptyState title="今天还没有安排"><button className="btn btn--secondary" type="button" onClick={() => openCapture("say")}>记一条</button></EmptyState>
            ) : (
              <div className="m-stack-8">
                {rows.map((row) => (
                  <div key={row.id} id={row.schedule ? `schedule-${row.schedule.schedule_id}` : undefined} className="card card--tight timeline-link">
                    <span className="timeline-time">{time(row.at, todayData.data?.timezone)}</span>
                    <span className="timeline-dot" aria-hidden="true" />
                    <span className="grow">
                      {row.matterId ? (
                        <Link href={`/matters/${row.matterId}`} className="card-title timeline-matter">{row.title}</Link>
                      ) : <span className="card-title">{row.title}</span>}
                      {row.matterTitle?.trim() && row.matterTitle.trim() !== row.title.trim() ? (
                        <span className="caption timeline-matter">{row.matterTitle}</span>
                      ) : null}
                      {row.detail ? <span className="caption timeline-matter">{row.detail}</span> : null}
                      {row.schedule ? (
                        <TimelineRoute
                          schedule={row.schedule}
                          timezone={todayData.data?.timezone}
                          onViewRoute={() => setRouteMapSchedule(row.schedule ?? null)}
                        />
                      ) : null}
                    </span>
                    {row.kind === "reminder" ? (
                      <Bell size={18} className="timeline-type-icon" aria-hidden="true" />
                    ) : (
                      <MapPin size={18} className="timeline-type-icon" aria-hidden="true" />
                    )}
                  </div>
                ))}
              </div>
            )}
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

        </>
      ) : null}

      {records.length > 0 ? <section style={{ padding: "0 var(--m-gutter)" }}>
        <SectionHeading title="留在这台设备上的笔记" />
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
                      {relativeTime(record.createdAt)}
                    </span>
                  </span>
                  <ChevronRight size={16} className="chevron" aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>
      </section> : null}
      {signedIn ? <div style={{ padding: "0 var(--m-gutter)" }}>
        <Link href="/ask" className="link-button">去看助手整理记录</Link>
      </div> : null}

      {!signedIn ? (
        <section style={{ padding: "0 var(--m-gutter)" }}>
          <LoginRequired
            title="登录生活助手"
            body="跨设备同步你的今日安排、待办事项与个人习惯。"
          />
        </section>
      ) : null}

      <Sheet title="本机笔记" open={Boolean(selectedRecord)} onClose={() => setSelectedRecord(null)}>
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
            <div className="notice mt-16">只留在这台设备上，换设备看不到。</div>
            <button
              type="button"
              className="btn btn--danger mt-16"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 size={15} /> 删除本机笔记
            </button>
          </>
        ) : null}
      </Sheet>

      <ConfirmDialog
        open={confirmDelete}
        title="删除这份本机笔记？"
        body="将删除这台设备上的笔记，已发给助手的内容会保留。"
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

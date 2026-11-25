"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/mobile/api";
import { newOperationKey, requestTodayRefresh, TODAY_REFRESH_EVENT } from "@/lib/mobile/matters-api";
import {
  completeRecommendedTodo, getNextActions, sendNextActionFeedback,
  type ActionTarget, type FeedbackAction, type NextActions, type TodayAction,
} from "@/lib/mobile/next-actions-api";
import { ErrorCard, LoadingCard, SectionHeading } from "@/components/mobile/ui";

const emptyMessage = {
  ready: "目前没有可推荐的下一步。",
  availability_missing: "还没有可用时间信息，目前无法确认哪些待办能放进空档。",
  no_current_window: "现在没有可用空档，目前没有可推荐的下一步。",
  calendar_end_unknown: "当前日程没有结束时间，暂时无法确认下一段空档。",
};

/** Keep the recommendation independent of the Today timeline and discard cancelled reads. */
function useRecommendation() {
  const [data, setData] = useState<NextActions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((n) => n + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    getNextActions(controller.signal).then((value) => {
      if (!controller.signal.aborted) { setData(value); setError(null); }
    }).catch(() => {
      if (!controller.signal.aborted) setError("暂时无法读取下一步建议，今天的时间线仍可查看。");
    });
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    // Poll independently of the timeline to catch a departure becoming due.
    const timer = window.setInterval(() => { if (!document.hidden) refresh(); }, 15000);
    window.addEventListener("focus", refresh);
    window.addEventListener(TODAY_REFRESH_EVENT, refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener(TODAY_REFRESH_EVENT, refresh);
    };
  }, [refresh]);
  return { data, error, refresh };
}

/** Link to the actual schedule or todo row, even when a device schedule has no matter. */
function targetLink(item: TodayAction): string {
  const anchor = `#${item.kind}-${encodeURIComponent(item.record_id)}`;
  return item.matter_id ? `/matters/${encodeURIComponent(item.matter_id)}${anchor}` : `/today${anchor}`;
}

/** Show one local recommendation and use the original handlers for changes to business records. */
export function TodayNextActions({ onReschedule }: { onReschedule: (item: TodayAction) => Promise<void> }) {
  const { data, error, refresh } = useRecommendation();
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const running = useRef(false);
  const retry = useRef<{ signature: string; run: () => Promise<unknown> } | null>(null);

  /** Block duplicate submits; stale state refreshes, and transport errors retain the exact original request. */
  async function run(signature: string, execute: () => Promise<unknown>, retryable = true) {
    if (running.current) return;
    running.current = true;
    setPending(true);
    setMessage(null);
    try {
      await execute();
      retry.current = null;
      requestTodayRefresh();
    } catch (e) {
      const stale = e instanceof ApiError && (e.status === 409 || e.status === 404);
      const unknown = !(e instanceof ApiError) || e.status >= 500;
      retry.current = unknown && retryable ? { signature, run: execute } : null;
      setMessage(stale ? "这条建议或原记录已经变化，已重新读取，请确认后再操作。"
        : unknown && retryable ? "尚未确认操作结果。可用原请求重试一次，或先查看原记录。"
          : e instanceof Error ? e.message : "操作未能完成。");
      if (stale) refresh();
    } finally {
      running.current = false;
      setPending(false);
    }
  }

  /** Preserve the exact deadline and request identity when retrying recommendation feedback. */
  function feedback(target: ActionTarget, action: FeedbackAction) {
    if (!data) return;
    const key = newOperationKey("web-next-action");
    const until = action === "snooze" ? new Date(Date.now() + 30 * 60_000).toISOString() : undefined;
    void run(`${action}:${target.record_id}`, () => sendNextActionFeedback(data, target, action, key, until));
  }

  /** Render one recommendation with only the actions the backend currently offers. */
  function renderItem(item: TodayAction) {
    const label = item.departure_at && data
      ? new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", timeZone: data.timezone }).format(new Date(item.departure_at))
      : null;
    const disabled = pending || retry.current !== null || error !== null;
    const canComplete = item.actions.includes("complete") && item.kind === "todo";
    return (
      <div className="card card--tight" key={`${item.kind}:${item.record_id}`}>
        <p className="card-title">{item.state === "departure_due" ? "现在该出发 · " : ""}{item.title}</p>
        {label ? <p className="caption">建议出发 {label}{item.destination ? ` · ${item.destination}` : ""}</p> : null}
        <p className="caption">{item.reason}</p>
        {canComplete || item.actions.includes("reschedule") ? (
          <div className="action-row">
            {canComplete ? (
              <button type="button" className="btn btn--primary btn--small" disabled={disabled} onClick={() => {
                const key = newOperationKey("web-next-complete");
                void run(`complete:${item.record_id}`, () => completeRecommendedTodo(item.record_id, key, item.matter_version));
              }}>完成待办</button>
            ) : null}
            {item.actions.includes("reschedule") ? <button type="button" className={canComplete ? "link-button" : "btn btn--primary btn--small"} disabled={disabled}
              onClick={() => void run(`reschedule:${item.record_id}`, () => onReschedule(item), false)}>改期</button> : null}
          </div>
        ) : null}
        <div className="action-row action-row--compact">
          {item.actions.includes("view") ? <Link className="link-button" href={targetLink(item)}>查看原记录</Link> : null}
          {item.commute_plan_id ? <Link className="link-button" href={`/me/commute/${encodeURIComponent(item.commute_plan_id)}`}>查看通勤计划</Link> : null}
          {item.actions.includes("snooze") ? <button type="button" className="link-button" disabled={disabled}
            onClick={() => feedback(item, "snooze")}>暂缓 30 分钟</button> : null}
          {item.actions.includes("dismiss") ? <button type="button" className="link-button" disabled={disabled}
            onClick={() => feedback(item, "dismiss")}>今天忽略</button> : null}
        </div>
      </div>
    );
  }

  return (
    <section className="m-stack-8" aria-label="下一步" style={{ padding: "0 var(--m-gutter)" }}>
      <SectionHeading title="下一步" />
      {message ? <div className="card card--tight" role="status">
        <p className="caption">{message}</p>
        {retry.current ? <div className="action-row">
          <button type="button" className="link-button" disabled={pending}
            onClick={() => { const saved = retry.current; if (saved) void run(saved.signature, saved.run); }}>用原请求重试</button>
          <button type="button" className="link-button" disabled={pending}
            onClick={() => { retry.current = null; setMessage(null); refresh(); }}>重新查看建议</button>
        </div> : null}
      </div> : null}
      {error ? <ErrorCard message={error} onRetry={refresh} /> : !data ? <LoadingCard label="正在读取下一步建议…" /> : (
        <>
          {data.primary ? renderItem(data.primary) : <div className="card card--tight"><p className="caption">{emptyMessage[data.data_status]}</p></div>}
          {data.alternatives.length > 0 ? <details><summary>备选建议</summary><div className="m-stack-8">{data.alternatives.map(renderItem)}</div></details> : null}
          {data.suppressed.length > 0 ? <details><summary>已暂缓或忽略</summary>
            {data.suppressed.map((item) => <div className="card card--tight" key={`${item.kind}:${item.record_id}`}>
              <span className="caption">{item.title}</span>{" "}
              <button type="button" className="link-button" disabled={pending || retry.current !== null}
                onClick={() => feedback(item, "resume")}>恢复推荐</button>
            </div>)}
          </details> : null}
        </>
      )}
    </section>
  );
}

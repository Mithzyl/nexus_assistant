"use client";

/**
 * Single-day commute editor.
 *
 * Reads one local service date, shows the server-computed window, and changes
 * the arrival time through the day endpoints. Start, end, buffer and the route
 * are never recalculated here; the returned schedule and route replace local
 * state so the same `schedule_id` stays visible.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { CommuteDayState, CommutePlan } from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import {
  formatInTimeZone,
  nextServiceDate,
  queryCommuteDay,
  refreshCommuteDay,
  updateCommuteDay,
} from "@/lib/mobile/commute-day";
import { formatDuration } from "@/lib/mobile/format";
import { conflictReasonText } from "@/lib/mobile/matter-draft.mjs";
import { newOperationKey } from "@/lib/mobile/matters-api";
import { Pill } from "./ui";

const STATUS_LABEL: Record<CommuteDayState["status"], string> = {
  upcoming: "待出发",
  past: "时间已过",
  inactive: "计划已暂停",
  ineligible: "这天不跑通勤",
};

const STATUS_TONE: Record<CommuteDayState["status"], "green" | "neutral" | "amber"> = {
  upcoming: "green",
  past: "neutral",
  inactive: "neutral",
  ineligible: "amber",
};

function isValidLocalTime(value: string): boolean {
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
}

export function CommuteDayPanel({
  planId,
  plan,
  onPlanChanged,
}: {
  planId: string;
  plan: CommutePlan;
  /** Reload the long-term plan so later edits use the new revision. */
  onPlanChanged?: () => void;
}) {
  const [serviceDate, setServiceDate] = useState(() => nextServiceDate(plan, new Date()));
  const [day, setDay] = useState<CommuteDayState | null>(null);
  const [arrival, setArrival] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const requestSeq = useRef(0);
  const operationKey = useRef<{ signature: string; key: string } | null>(null);
  const refreshKey = useRef<{ signature: string; key: string } | null>(null);
  const planKey = `${plan.id}|${plan.timezone}|${plan.weekdays.join(",")}|${plan.effective_from ?? ""}`;

  // Follow the plan's own schedule when its configuration changes, not on every
  // revision bump caused by saving a single day.
  useEffect(() => {
    setServiceDate(nextServiceDate(plan, new Date()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planKey]);

  const loadDay = useCallback(
    async (target: string) => {
      if (!target) {
        setDay(null);
        setLoading(false);
        return;
      }
      const seq = ++requestSeq.current;
      setLoading(true);
      setError(null);
      setMessage(null);
      try {
        const state = await queryCommuteDay(planId, target);
        if (seq !== requestSeq.current) return;
        setDay(state);
        setArrival(state.arrival_local_time);
      } catch (caught) {
        if (seq !== requestSeq.current) return;
        setDay(null);
        setError(
          caught instanceof ApiError && caught.status === 404
            ? "没找到这一天的通勤安排。"
            : caught instanceof Error
              ? caught.message
              : "这一天的安排暂时没取到。",
        );
      } finally {
        if (seq === requestSeq.current) setLoading(false);
      }
    },
    [planId],
  );

  useEffect(() => {
    void loadDay(serviceDate);
    return () => {
      requestSeq.current += 1;
    };
  }, [loadDay, serviceDate]);

  /** Save one arrival time; only `applied` from the server counts as saved. */
  const saveArrival = async () => {
    if (!day || saving) return;
    const normalized = arrival.trim();
    if (!isValidLocalTime(normalized)) {
      setMessage("时间格式像 10:00 这样。");
      return;
    }
    if (day.version == null) {
      setMessage("这一天暂时不能改，稍后再试。");
      return;
    }
    if (normalized === day.arrival_local_time) {
      setMessage("时间没变。");
      return;
    }
    const signature = `${day.service_date}|${normalized}|${day.revision}|${day.version}`;
    if (!operationKey.current || operationKey.current.signature !== signature) {
      operationKey.current = { signature, key: newOperationKey("web-commute-day") };
    }
    setSaving(true);
    setMessage(null);
    try {
      const result = await updateCommuteDay(planId, {
        service_date: day.service_date,
        arrival_local_time: normalized,
        expected_plan_revision: day.revision,
        expected_matter_version: day.version,
        idempotency_key: operationKey.current.key,
      });
      setDay(result);
      if (result.applied) {
        operationKey.current = null;
        setArrival(result.arrival_local_time);
        setMessage("改好了，出发和结束时间也一起更新了。");
        onPlanChanged?.();
      } else {
        setMessage(
          result.conflict?.has_conflict
            ? "和当天的安排时间撞车了，先没保存。改个时间再试。"
            : "这次没保存成功，重新看一下再试。",
        );
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        const note = caught.message || "刚才有变化，已经帮你刷新。";
        await loadDay(day.service_date);
        setMessage(note);
      } else {
        setMessage(caught instanceof Error ? caught.message : "这次没存上。");
      }
    } finally {
      setSaving(false);
    }
  };

  /** Recompute one day; this writes, so a plain "read" wording would be wrong. */
  const refreshDay = async () => {
    if (!day || refreshing) return;
    const signature = `${day.service_date}|${day.revision}`;
    if (!refreshKey.current || refreshKey.current.signature !== signature) {
      refreshKey.current = { signature, key: newOperationKey("web-commute-refresh") };
    }
    setRefreshing(true);
    setMessage(null);
    try {
      const result = await refreshCommuteDay(planId, {
        service_date: day.service_date,
        idempotency_key: refreshKey.current.key,
      });
      setDay(result);
      setArrival(result.arrival_local_time);
      if (result.applied) {
        refreshKey.current = null;
        setMessage("刷新好了，时间以刚算出的为准。");
        onPlanChanged?.();
      } else {
        setMessage("这次没算出新的安排，换个日期或稍后再试。");
      }
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "这次没刷新成功。");
    } finally {
      setRefreshing(false);
    }
  };

  const busy = loading || saving || refreshing;
  const editable = day?.status === "upcoming" && day.version != null;
  const conflict = day?.conflict?.has_conflict ? day.conflict : null;
  const conflictItem = conflict?.conflicts?.[0] ?? null;

  return (
    <section className="card card--tight" aria-label="单日通勤">
      <div className="flex items-center justify-between gap-8">
        <h2 className="section-title" style={{ margin: 0, fontSize: 16 }}>
          单日通勤
        </h2>
        {day ? <Pill tone={STATUS_TONE[day.status]}>{STATUS_LABEL[day.status]}</Pill> : null}
      </div>
      <p className="micro" style={{ margin: "4px 0 10px" }}>
        只看某一天；改这里，长期设置不受影响。
      </p>

      <label className="field">
        <span className="field__label">日期</span>
        <input
          className="input"
          type="date"
          value={serviceDate}
          onChange={(event) => setServiceDate(event.target.value)}
        />
      </label>

      {loading ? (
        <p className="caption" style={{ margin: "8px 0 0" }}>
          正在看看这一天…
        </p>
      ) : null}
      {error ? (
        <div className="card card--amber" style={{ marginTop: 10 }}>
          <p className="caption" style={{ margin: 0 }}>{error}</p>
          <button type="button" className="link-button mt-6" onClick={() => void loadDay(serviceDate)}>
            再试一次
          </button>
        </div>
      ) : null}

      {day && !loading ? (
        <div className="m-stack-10" style={{ marginTop: 10 }}>
          <label className="field" style={{ marginBottom: 0 }}>
            <span className="field__label">到达时间 · 当地时间</span>
            <input
              className="input"
              type="time"
              value={arrival}
              disabled={!editable || busy}
              onChange={(event) => setArrival(event.target.value)}
            />
          </label>

          <div className="commute-config__rows">
            <div className="commute-config__row">
              <span>日程</span>
              <b>
                {day.schedule
                  ? [
                      formatInTimeZone(day.schedule.starts_at, day.timezone),
                      day.schedule.ends_at
                        ? formatInTimeZone(day.schedule.ends_at, day.timezone, false)
                        : null,
                    ]
                      .filter(Boolean)
                      .join("–")
                  : "这一天还没排"}
              </b>
            </div>
            <div className="commute-config__row">
              <span>路线</span>
              <b>
                {day.route?.status === "available"
                  ? `${formatDuration(day.route.duration_seconds) || "时长未知"}${
                      day.route.distance_meters
                        ? ` · ${(day.route.distance_meters / 1000).toFixed(1)} 公里`
                        : ""
                    }`
                  : day.route?.status === "unavailable"
                    ? "暂时算不出"
                    : "还没测算"}
              </b>
            </div>
          </div>

          {conflict ? (
            <div className="notice notice--amber">
              <p className="caption" style={{ margin: 0 }}>
                {conflictItem
                  ? `${conflictItem.title}：${conflictReasonText(conflictItem.reason, conflictItem.eta_status)}`
                  : "这一天的安排有冲突。"}
              </p>
            </div>
          ) : null}

          {day.status !== "upcoming" ? (
            <p className="micro" style={{ margin: 0 }}>
              这里只能改还没到点的日子；过去的只供查看。
            </p>
          ) : null}

          <div className="flex gap-8" style={{ flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn btn--secondary btn--small"
              disabled={!editable || busy || arrival === day.arrival_local_time}
              onClick={() => void saveArrival()}
            >
              {saving ? "正在保存…" : "保存时间"}
            </button>
            <button
              type="button"
              className="btn btn--quiet btn--small"
              disabled={busy || day.status !== "upcoming"}
              onClick={() => void refreshDay()}
            >
              {refreshing ? "正在刷新…" : "刷新路线"}
            </button>
          </div>

          {message ? (
            <p className="micro" style={{ margin: 0 }} role="status">
              {message}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

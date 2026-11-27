"use client";

import { commuteModeLabel } from "@/lib/mobile/commute-mode";

/** Today's compact commute summary. Detail and diagnostics live on the plan page. */

import Link from "next/link";
import { CloudSun, MapPin, RefreshCw } from "lucide-react";

import type { Advisory, CommutePlan } from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import { formatDuration, formatServiceDate } from "@/lib/mobile/format";
import { useApiResource } from "@/lib/mobile/use-api-resource";
import {
  addServiceDays, formatInTimeZone, localTimeInTimeZone, serviceDateInTimeZone, serviceWeekday,
} from "@/lib/mobile/commute-day";
import { advisoryApplicability } from "@/lib/mobile/advisory";
import { useAuth } from "./providers";
import { LoginLink } from "./ui";

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

function weekdayText(weekdays: number[]): string {
  if (weekdays.length === 5 && [1, 2, 3, 4, 5].every((day) => weekdays.includes(day))) {
    return "周一至周五";
  }
  if (weekdays.length === 7) return "每天";
  return weekdays.map((day) => `周${WEEKDAYS[day - 1]}`).join("、");
}

/** Format the estimated arrival in the plan's own time zone. */
function timeAfter(value: string | null, seconds: number | null, timezone: string): string {
  if (!value || seconds === null) return "";
  const start = Date.parse(value);
  if (!Number.isFinite(start)) return "";
  return formatInTimeZone(new Date(start + seconds * 1000).toISOString(), timezone, false);
}

function weatherText(advisory: Advisory): string {
  const weather = advisory.weather;
  if (!weather) return "天气数据暂缺";
  const bits: string[] = [];
  if (weather.temperature_c !== null) bits.push(`${Math.round(weather.temperature_c)}℃`);
  if (weather.precipitation_probability_known && weather.precipitation_probability_max !== null) {
    bits.push(`降雨概率 ${Math.round(weather.precipitation_probability_max * 100)}%`);
  }
  return bits.length > 0 ? bits.join(" · ") : "天气数据暂缺";
}

/** Show the first commute plan and distinguish a finished day from advice that needs refreshing. */
export function TodayCommuteCard() {
  const { state } = useAuth();
  const signedIn = state === "signedIn";
  const plans = useApiResource<CommutePlan[]>(
    signedIn ? "/api/pa/commute-plans" : null,
    { enabled: signedIn },
  );
  const plan = plans.data?.[0] ?? null;
  const advisory = useApiResource<Advisory>(
    plan ? `/api/pa/commute-plans/${plan.id}/advisories/latest` : null,
    { enabled: Boolean(plan) },
  );

  const noAdvisory =
    advisory.error instanceof ApiError &&
    (advisory.error.status === 404 || advisory.error.code === "NO_ADVISORY");
  const data = advisory.data;
  const advisoryPending = advisory.loading || (!data && !advisory.error);
  const now = new Date();
  const timestamp = now.getTime();
  const today = plan ? serviceDateInTimeZone(now, plan.timezone) : "";
  const scheduledToday = plan && plan.enabled && plan.weekdays.includes(serviceWeekday(today))
    && (!plan.effective_from || plan.effective_from <= today);
  // A matching advisory may carry a one-day arrival override even after its route expires.
  const todayAdvice = data && plan && data.local_service_date === today && data.plan_revision === plan.revision
    ? data : null;
  const targetTimestamp = todayAdvice ? Date.parse(todayAdvice.target_arrival_at) : NaN;
  const todayPassed = plan && scheduledToday && (Number.isFinite(targetTimestamp)
    ? targetTimestamp <= timestamp
    : localTimeInTimeZone(now, plan.timezone) >= plan.arrival_local_time);
  const departure = data && plan ? formatInTimeZone(data.recommended_departure_at, plan.timezone, false) : "";
  const current = plan?.enabled && data && !advisory.error && departure && data.local_service_date >= today
    && Date.parse(data.target_arrival_at) > timestamp && !(Date.parse(data.expires_at) <= timestamp)
    && advisoryApplicability(data, plan.revision, timestamp).applicable ? data : null;

  const adviceDate = current?.local_service_date;
  const departureTitle = adviceDate === addServiceDays(today, 1) ? `${departure < "12:00" ? "明早" : "明天"} ${departure} 出发`
    : adviceDate && adviceDate !== today ? `${formatServiceDate(adviceDate).split(" ")[0]} 建议 ${departure} 出发`
    : `建议 ${departure} 出发`;
  const needsRoute = !current && scheduledToday && !todayPassed;
  const readError = needsRoute && advisory.error && !noAdvisory;
  const inactiveTitle = !plan?.enabled ? "通勤计划已停用"
    : todayPassed ? "今天的通勤时间已过"
    : !scheduledToday ? "今天没有通勤安排"
    : readError ? "路线没加载出来" : "暂无最新路线";
  const detailDate = adviceDate || (needsRoute ? today : null);
  const target = plan ? formatInTimeZone((current ?? todayAdvice)?.target_arrival_at, plan.timezone, false) : "";
  const eta = current?.ai_analysis?.used_duration_seconds ?? current?.eta_seconds ?? null;
  const arrival = current && plan ? timeAfter(current.recommended_departure_at, eta, plan.timezone) : "";
  const resultBits: string[] = [];
  if (eta !== null) resultBits.push(`车程${formatDuration(eta)}`);
  if (arrival) resultBits.push(`预计 ${arrival} 到达`);

  return (
    <section style={{ padding: "0 var(--m-gutter)", marginBottom: 12 }} aria-label="通勤">
      <div className="card today-commute">
        <div className="today-commute__head">
          <span className="today-commute__eyebrow">
            <CloudSun size={15} strokeWidth={1.5} aria-hidden="true" />
            {plan?.name || "通勤"}
          </span>
        </div>

        {state === "loading" ? (
          <p className="today-commute__body">正在同步…</p>
        ) : null}

        {!signedIn && state !== "loading" ? (
          <>
            <p className="today-commute__title">登录后查看出行方案</p>
            <p className="today-commute__body">登录账号后，这里会显示常用路线与出发建议。</p>
            <LoginLink className="btn btn--primary btn--small mt-10">登录账号</LoginLink>
          </>
        ) : null}

        {signedIn && plans.loading ? (
          <p className="today-commute__body">正在读取通勤计划…</p>
        ) : null}

        {signedIn && plans.error ? (
          <>
            <p className="today-commute__title">暂时无法读取通勤计划</p>
            <p className="today-commute__body">
              {plans.error instanceof ApiError && plans.error.status === 401
                ? "登录状态已失效，请重新登录。"
                : "网络不可用。"}
            </p>
            <button type="button" className="today-commute__action" onClick={plans.reload}>
              <RefreshCw size={12} strokeWidth={1.5} aria-hidden="true" /> 重试
            </button>
          </>
        ) : null}

        {signedIn && !plans.loading && !plans.error && !plan ? (
          <>
            <p className="today-commute__title">还没有通勤计划</p>
            <p className="today-commute__body">设置常用路线后，这里会显示每天的建议出发时间。</p>
            <Link href="/me/commute" className="today-commute__action">
              新建计划 <span aria-hidden="true">→</span>
            </Link>
          </>
        ) : null}

        {signedIn && plan ? (
          <>
            <div className="today-commute__work">
              <span>到达</span>
              <b>{target || plan.arrival_local_time}</b>
              <span className="today-commute__plan-meta">
                {weekdayText(plan.weekdays)} · {commuteModeLabel(plan.transport_mode)}
              </span>
            </div>
            <details className="commute-address">
              <summary>
                <MapPin size={12} aria-hidden="true" />
                <span className="commute-address__preview">{plan.origin.label} → {plan.destination.label}</span>
                <span className="commute-address__collapse">收起地址</span>
              </summary>
              <div>出发地：{plan.origin.label}<br />目的地：{plan.destination.label}</div>
            </details>

            {advisoryPending ? (
              <p className="today-commute__body">正在读取通勤建议…</p>
            ) : null}

            {!advisoryPending && current ? (
              <>
                <p className="today-commute__time">{departureTitle}</p>
                {resultBits.length > 0 ? (
                  <p className="today-commute__result">{resultBits.join(" · ")}</p>
                ) : null}
                <p className="today-commute__meta">
                  <CloudSun size={12} strokeWidth={1.5} aria-hidden="true" />
                  <span>{weatherText(current)}</span>
                </p>
                <p className="today-commute__note">按常规路况估算的。</p>
              </>
            ) : null}

            {!advisoryPending ? (
              <>
                {!current ? <p className="today-commute__title">{inactiveTitle}</p> : null}
                <div className="today-commute__foot">
                  {readError ? (
                    <button type="button" className="link-button" onClick={advisory.reload}>再试一次</button>
                  ) : (
                    <Link
                      href={`/me/commute/${plan.id}${detailDate ? `?service_date=${detailDate}` : ""}`}
                      className="today-commute__link"
                    >
                      {needsRoute ? "去查路线" : "查看通勤"}
                    </Link>
                  )}
                </div>
              </>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  );
}

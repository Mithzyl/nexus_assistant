"use client";

import { commuteModeLabel } from "@/lib/mobile/commute-mode";

/** Today's compact commute summary. Detail and diagnostics live on the plan page. */

import Link from "next/link";
import { CloudSun, MapPin, RefreshCw } from "lucide-react";

import type { Advisory, CommutePlan } from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import { formatDuration, formatServiceDate } from "@/lib/mobile/format";
import { useApiResource } from "@/lib/mobile/use-api-resource";
import { formatInTimeZone, serviceDateInTimeZone } from "@/lib/mobile/commute-day";
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
  if (weather.temperature_c !== null) bits.push(`${Math.round(weather.temperature_c)}°C`);
  if (weather.precipitation_probability_known && weather.precipitation_probability_max !== null) {
    bits.push(`降雨概率 ${Math.round(weather.precipitation_probability_max * 100)}%`);
  }
  return bits.length > 0 ? bits.join(" · ") : "天气数据暂缺";
}

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
  const applicability =
    data && plan ? advisoryApplicability(data, plan.revision) : null;
  const current = applicability?.applicable ? data : null;
  const departure = current && plan ? formatInTimeZone(current.recommended_departure_at, plan.timezone, false) : "";
  const today = plan ? serviceDateInTimeZone(new Date(), plan.timezone) : "";
  const tomorrow = today ? new Date(Date.parse(today) + 86_400_000).toISOString().slice(0, 10) : "";
  const adviceDate = data ? formatServiceDate(data.local_service_date).split(" ")[0] : "";
  const departureTitle = !departure ? "还没有建议出发时间"
    : current?.local_service_date === tomorrow ? `${departure < "12:00" ? "明早" : "明天"} ${departure} 出发`
    : current?.local_service_date !== today ? `${adviceDate} 建议 ${departure} 出发`
    : `建议 ${departure} 出发`;
  const inactiveTitle = {
    changed: "通勤设置改了，需要重新查路线",
    expired: "上次的路线已经过期了",
    history: `这是 ${adviceDate} 的路线`,
    unavailable: "这次没查到路线",
    current: "",
  };
  const target = current && plan ? formatInTimeZone(current.target_arrival_at, plan.timezone, false) : "";
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
          {signedIn && plan ? (
            <Link href={`/me/commute/${plan.id}`} className="today-commute__head-link">
              详情
            </Link>
          ) : null}
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
              <b>{current ? target || plan.arrival_local_time : plan.arrival_local_time}</b>
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

            {advisory.loading ? (
              <p className="today-commute__body">正在读取通勤建议…</p>
            ) : null}

            {!advisory.loading && current ? (
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
                <div className="today-commute__foot">
                  <Link href={`/me/commute/${plan.id}`} className="today-commute__link">
                    查看通勤详情
                  </Link>
                  <Link href="/me/commute" className="today-commute__link today-commute__link--muted">
                    调整计划
                  </Link>
                </div>
              </>
            ) : null}

            {!advisory.loading && !current && applicability ? (
              <>
                <p className="today-commute__title">{inactiveTitle[applicability.reason]}</p>
                <div className="today-commute__foot">
                  <Link href={`/me/commute/${plan.id}`} className="today-commute__link">
                    去查路线
                  </Link>
                  <Link href="/me/commute" className="today-commute__link today-commute__link--muted">
                    调整计划
                  </Link>
                </div>
              </>
            ) : null}

            {!advisory.loading && !current && !applicability && noAdvisory ? (
              <>
                <p className="today-commute__title">还没查过路线</p>
                <div className="today-commute__foot">
                  <Link href={`/me/commute/${plan.id}`} className="today-commute__link">
                    去查路线
                  </Link>
                  <Link href="/me/commute" className="today-commute__link today-commute__link--muted">
                    调整计划
                  </Link>
                </div>
              </>
            ) : null}

            {!advisory.loading && !current && !applicability && advisory.error && !noAdvisory ? (
              <>
                <p className="today-commute__title">路线没加载出来</p>
                <div className="today-commute__foot">
                  <button type="button" className="link-button" onClick={advisory.reload}>再试一次</button>
                </div>
              </>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  );
}

"use client";

/** Today's compact commute summary. Detail and diagnostics live on the plan page. */

import Link from "next/link";
import { CloudSun, MapPin, RefreshCw } from "lucide-react";

import type { Advisory, CommutePlan } from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import { formatDuration, formatTime } from "@/lib/mobile/format";
import { useApiResource } from "@/lib/mobile/use-api-resource";
import { advisoryApplicability } from "@/lib/mobile/advisory";
import { useAuth } from "./providers";

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

function weekdayText(weekdays: number[]): string {
  if (weekdays.length === 5 && [1, 2, 3, 4, 5].every((day) => weekdays.includes(day))) {
    return "周一至周五";
  }
  if (weekdays.length === 7) return "每天";
  return weekdays.map((day) => `周${WEEKDAYS[day - 1]}`).join("、");
}

function timeAfter(value: string | null, seconds: number | null): string {
  if (!value || seconds === null) return "";
  const start = Date.parse(value);
  if (!Number.isFinite(start)) return "";
  return formatTime(new Date(start + seconds * 1000).toISOString());
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
  const departure = current ? formatTime(current.recommended_departure_at) : "";
  const target = current ? formatTime(current.target_arrival_at) : "";
  const eta = current?.ai_analysis?.used_duration_seconds ?? current?.eta_seconds ?? null;
  const arrival = current ? timeAfter(current.recommended_departure_at, eta) : "";
  const resultBits: string[] = [];
  if (eta !== null) resultBits.push(`车程${formatDuration(eta)}`);
  if (arrival) resultBits.push(`预计 ${arrival} 到达`);

  return (
    <section style={{ padding: "0 var(--m-gutter)", marginBottom: 12 }} aria-label="通勤">
      <div className="card today-commute">
        <div className="today-commute__head">
          <span className="today-commute__eyebrow">
            <CloudSun size={15} strokeWidth={1.5} aria-hidden="true" />
            通勤
          </span>
          {signedIn && plan ? (
            <Link href={`/me/commute/${plan.id}`} className="today-commute__head-link">
              详情
            </Link>
          ) : null}
        </div>

        {state === "loading" ? (
          <p className="today-commute__body">正在同步账号状态…</p>
        ) : null}

        {!signedIn && state !== "loading" ? (
          <>
            <p className="today-commute__title">登录后查看通勤建议</p>
            <p className="today-commute__body">登录账号后，这里会显示常用路线与出发建议。</p>
            <Link href="/login" className="btn btn--primary btn--small mt-10">登录账号</Link>
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
              <span>到岗</span>
              <b>{current ? target || plan.arrival_local_time : plan.arrival_local_time}</b>
              <span className="today-commute__plan-meta">
                {weekdayText(plan.weekdays)} · {plan.transport_mode === "driving" ? "驾车" : plan.transport_mode === "transit" ? "公交" : "步行"}
              </span>
            </div>

            {advisory.loading ? (
              <p className="today-commute__body">正在读取通勤建议…</p>
            ) : null}

            {!advisory.loading && current ? (
              <>
                <p className="today-commute__label">建议出发</p>
                <p className="today-commute__time">{departure || "待确认"}</p>
                {resultBits.length > 0 ? (
                  <p className="today-commute__result">{resultBits.join(" · ")}</p>
                ) : null}
                <p className="today-commute__meta">
                  <MapPin size={12} strokeWidth={1.5} aria-hidden="true" />
                  <span>{plan.origin.label} → {plan.destination.label}</span>
                </p>
                <p className="today-commute__meta">
                  <CloudSun size={12} strokeWidth={1.5} aria-hidden="true" />
                  <span>{weatherText(current)}</span>
                </p>
                <p className="today-commute__note">按常规路况估算。</p>
                <div className="today-commute__foot">
                  <Link href={`/me/commute/${plan.id}`} className="today-commute__link">
                    查看通勤详情
                  </Link>
                  <Link href="/me/commute" className="today-commute__link today-commute__link--muted">
                    设置
                  </Link>
                </div>
              </>
            ) : null}

            {!advisory.loading && !current && applicability ? (
              <>
                <div className="today-commute__state-row">
                  <p className="today-commute__title">
                    {applicability.label === "已过期" ? "当前建议已过期" : "当前建议对应旧设置"}
                  </p>
                  <span className="today-commute__state">{applicability.label}</span>
                </div>
                <p className="today-commute__body">刷新路线后，这里会显示下一次通勤。</p>
                <p className="today-commute__route">
                  {plan.origin.label} → {plan.destination.label}
                </p>
                <div className="today-commute__foot">
                  <Link href={`/me/commute/${plan.id}`} className="today-commute__link">
                    刷新路线
                  </Link>
                  <Link href="/me/commute" className="today-commute__link today-commute__link--muted">
                    设置
                  </Link>
                </div>
              </>
            ) : null}

            {!advisory.loading && !current && !applicability && noAdvisory ? (
              <>
                <p className="today-commute__title">今天还没有通勤建议</p>
                <p className="today-commute__body">刷新路线后，这里会显示建议出发时间。</p>
                <div className="today-commute__foot">
                  <Link href={`/me/commute/${plan.id}`} className="today-commute__link">
                    刷新路线
                  </Link>
                  <Link href="/me/commute" className="today-commute__link today-commute__link--muted">
                    设置
                  </Link>
                </div>
              </>
            ) : null}

            {!advisory.loading && !current && !applicability && advisory.error && !noAdvisory ? (
              <>
                <p className="today-commute__title">暂时无法读取通勤建议</p>
                <p className="today-commute__body">计划时间仍可查看，稍后可以再刷新。</p>
                <div className="today-commute__foot">
                  <Link href={`/me/commute/${plan.id}`} className="today-commute__link">
                    查看通勤详情
                  </Link>
                  <Link href="/me/commute" className="today-commute__link today-commute__link--muted">
                    设置
                  </Link>
                </div>
              </>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  );
}

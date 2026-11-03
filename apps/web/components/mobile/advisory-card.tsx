"use client";

/** Commute conclusion and its time ledger, presented in the page's visual language. */

import { CloudRain, MapPin, RefreshCw } from "lucide-react";

import type { Advisory, CommutePlan } from "@/lib/mobile/api";
import { formatDateTime, formatDuration, formatServiceDate, formatTime } from "@/lib/mobile/format";

function minuteLabel(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds)) return null;
  const minutes = Math.round(seconds / 60);
  if (minutes <= 0) return null;
  return `${minutes} 分钟`;
}

function timeAfter(value: string | null, seconds: number | null): string {
  if (!value || seconds === null) return "";
  const start = Date.parse(value);
  if (!Number.isFinite(start)) return "";
  return formatTime(new Date(start + seconds * 1000).toISOString());
}

export function AdvisoryCard({
  advisory,
  plan,
  onReanalyze,
  reanalyzeLabel = "刷新路线",
  busy = false,
}: {
  advisory: Advisory;
  plan: CommutePlan;
  onReanalyze?: () => void;
  reanalyzeLabel?: string;
  busy?: boolean;
}) {
  const weather = advisory.weather;
  const ai = advisory.ai_analysis;
  const departure = formatTime(advisory.recommended_departure_at);
  const target = formatTime(advisory.target_arrival_at);
  const eta = ai?.used_duration_seconds ?? advisory.eta_seconds ?? null;
  const arrival = timeAfter(advisory.recommended_departure_at, eta);

  const computedBuffer =
    advisory.recommended_departure_at && advisory.target_arrival_at && eta !== null
      ? (Date.parse(advisory.target_arrival_at) - Date.parse(advisory.recommended_departure_at)) / 1000 - eta
      : null;
  const bufferSeconds =
    computedBuffer !== null && Number.isFinite(computedBuffer) && computedBuffer > 0
      ? computedBuffer
      : advisory.buffer_seconds + advisory.weather_extra_buffer_seconds;
  const buffer = minuteLabel(bufferSeconds);

  const baseBuffer = ai?.base_buffer_seconds ?? advisory.buffer_seconds;
  const weatherExtra = advisory.weather_extra_buffer_seconds;
  const bufferBreakdown =
    baseBuffer > 0 &&
    weatherExtra > 0 &&
    Math.abs(baseBuffer + weatherExtra - bufferSeconds) <= 90
      ? `含日常 ${minuteLabel(baseBuffer)}、雨天 ${minuteLabel(weatherExtra)}`
      : null;

  const weatherBits: string[] = [];
  if (weather?.temperature_c !== null && weather?.temperature_c !== undefined) {
    weatherBits.push(`${Math.round(weather.temperature_c)}°C`);
  }
  if (
    weather?.precipitation_probability_known &&
    weather?.precipitation_probability_max !== null &&
    weather?.precipitation_probability_max !== undefined
  ) {
    weatherBits.push(`降雨概率 ${Math.round(weather.precipitation_probability_max * 100)}%`);
  }
  const weatherText = weatherBits.length > 0 ? weatherBits.join(" · ") : "天气数据暂缺";

  const resultBits: string[] = [];
  if (eta !== null) resultBits.push(`车程${formatDuration(eta)}`);
  if (arrival) resultBits.push(`预计 ${arrival} 到达`);

  const weatherBufferFits = weatherExtra > 0 && bufferSeconds >= weatherExtra;
  const note =
    weatherBufferFits
      ? `按常规路况估算，已包含 ${minuteLabel(weatherExtra) ?? "部分"}雨天缓冲。`
      : "按常规路况估算。";

  return (
    <>
      <section className="card commute-conclusion">
        <div className="commute-conclusion__head">
          <h2>下一次通勤</h2>
          <span>{formatServiceDate(advisory.local_service_date)}</span>
        </div>
        <p className="commute-conclusion__label">建议出发</p>
        <p className="commute-conclusion__time">{departure || "待确认"}</p>
        {resultBits.length > 0 ? (
          <p className="commute-conclusion__result">{resultBits.join(" · ")}</p>
        ) : null}
        <p className="commute-conclusion__meta">
          <MapPin size={14} strokeWidth={1.5} aria-hidden="true" />
          <span>{plan.origin.label} → {plan.destination.label}</span>
        </p>
        <p className="commute-conclusion__meta">
          <CloudRain size={14} strokeWidth={1.5} aria-hidden="true" />
          <span>{weatherText}</span>
        </p>
        <p className="commute-conclusion__note">{note}</p>
      </section>

      <section className="card commute-time">
        <div className="commute-time__head">
          <h2>时间明细</h2>
        </div>
        <div className="commute-time__rows">
          <div className="commute-time__row">
            <span>到岗时间</span>
            <b>{target || "待确认"}</b>
          </div>
          <div className="commute-time__row">
            <span>路上车程</span>
            <b>{eta !== null ? formatDuration(eta) : "待确认"}</b>
          </div>
          {buffer ? (
            <div className="commute-time__row">
              <span>预留时间</span>
              <b>{buffer}</b>
            </div>
          ) : null}
          {bufferBreakdown ? <p className="commute-time__sub">{bufferBreakdown}</p> : null}
          <div className="commute-time__row commute-time__row--result">
            <span>出发时间</span>
            <b>{departure || "待确认"}</b>
          </div>
        </div>
        <div className="commute-time__foot">
          <span>更新于 {formatDateTime(advisory.generated_at)}</span>
          {onReanalyze ? (
            <button
              type="button"
              className="commute-refresh"
              onClick={onReanalyze}
              disabled={busy}
            >
              {busy ? (
                <span className="spinner" style={{ width: 12, height: 12 }} />
              ) : (
                <RefreshCw size={12} strokeWidth={1.5} aria-hidden="true" />
              )}
              {busy ? "刷新中" : reanalyzeLabel}
            </button>
          ) : null}
        </div>
      </section>
    </>
  );
}

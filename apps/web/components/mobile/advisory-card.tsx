"use client";

/** Server-generated commute advice, presented as a recommendation first. */

import { useRef } from "react";
import { ChevronDown, CloudRain, RefreshCw, Thermometer } from "lucide-react";

import type { Advisory, PlanWeather } from "@/lib/mobile/api";
import { formatDateTime, formatDuration, formatServiceDate, formatTime } from "@/lib/mobile/format";
import { mockProviders } from "@/lib/mobile/advisory";
import { Pill } from "./ui";

const PROVIDER_LABEL: Record<string, string> = {
  amap: "高德地图",
  open_meteo: "Open-Meteo",
  mock: "示例数据",
};

function providerName(provider: string): string {
  return PROVIDER_LABEL[provider] ?? provider;
}

export function AdvisoryCard({
  advisory,
  onReanalyze,
  onHistory,
  reanalyzeLabel = "更新建议",
  busy = false,
  latestWeather = null,
}: {
  advisory: Advisory;
  onReanalyze?: () => void;
  onHistory?: () => void;
  reanalyzeLabel?: string;
  busy?: boolean;
  latestWeather?: PlanWeather | null;
}) {
  const basisRef = useRef<HTMLDetailsElement>(null);
  const expired =
    advisory.is_expired ?? Date.parse(advisory.expires_at) <= Date.now();
  const weather = advisory.weather;
  const ai = advisory.ai_analysis;
  const mock = mockProviders(advisory);
  const degraded = advisory.quality !== "fresh";
  const dataIncomplete = !weather || weather.coverage !== "full" ||
    !advisory.sources.some((source) => source.kind === "route") ||
    advisory.sources.some((source) => source.freshness !== "fresh");
  const fallbackLabel = dataIncomplete ? "部分数据暂缺" : "基础出发建议";
  const status = expired
    ? { label: "已过期", tone: "red" as const }
    : mock.length > 0
      ? { label: "示例数据", tone: "amber" as const }
      : degraded
        ? { label: fallbackLabel, tone: "amber" as const }
        : { label: "已更新", tone: "green" as const };

  const departure = formatTime(advisory.recommended_departure_at);
  const arrival = formatTime(advisory.target_arrival_at);
  const eta = ai?.used_duration_seconds ?? advisory.eta_seconds;
  const weatherExtra = advisory.weather_extra_buffer_seconds;

  const weatherBits: string[] = [];
  if (weather?.temperature_c !== null && weather?.temperature_c !== undefined) {
    weatherBits.push(`${Math.round(weather.temperature_c)}°C`);
  }
  if (
    weather?.precipitation_probability_known &&
    weather?.precipitation_probability_max !== null &&
    weather?.precipitation_probability_max !== undefined
  ) {
    weatherBits.push(`降雨 ${Math.round(weather.precipitation_probability_max * 100)}%`);
  }
  if (weatherExtra > 0 && weather?.rain_risk) {
    weatherBits.push(`雨天已预留 ${formatDuration(weatherExtra)}`);
  } else if (weather?.coverage && weather.coverage !== "full") {
    weatherBits.push("天气覆盖部分时段");
  }

  const statusNote = expired
    ? "这条建议已过期，请更新建议。"
    : mock.length > 0
      ? "当前为示例数据，仅用于演示。"
      : degraded
        ? dataIncomplete
          ? "部分天气或路线数据暂缺，出发前请更新建议。"
          : "AI 分析暂未采用，出发时间按路线耗时和预留时间计算。"
        : "已按路线和天气更新。";

  const sourceText = advisory.sources
    .map((source) => `${source.kind === "weather" ? "天气" : "路线"} ${providerName(source.provider)}`)
    .join(" · ");
  const latestSummary = latestWeather?.summary ?? null;
  const latestParts: string[] = [];
  if (latestSummary?.temperature_c !== null && latestSummary?.temperature_c !== undefined) {
    latestParts.push(`${Math.round(latestSummary.temperature_c)}°C`);
  }
  if (
    latestSummary?.precipitation_probability_known &&
    latestSummary?.precipitation_probability_max !== null &&
    latestSummary?.precipitation_probability_max !== undefined
  ) {
    latestParts.push(`降雨 ${Math.round(latestSummary.precipitation_probability_max * 100)}%`);
  }
  const latestIsNewer =
    latestWeather?.local_service_date === advisory.local_service_date &&
    latestWeather?.plan_revision === advisory.plan_revision &&
    !latestWeather?.is_stale && Boolean(latestWeather?.fetched_at) &&
    Date.parse(latestWeather!.fetched_at!) > Date.parse(advisory.generated_at);

  const openBasis = () => {
    if (!basisRef.current) return;
    basisRef.current.open = true;
    basisRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <section className="card commute-advice">
      <div className="commute-advice__top">
        <span className="micro">{formatServiceDate(advisory.local_service_date)}</span>
        <Pill tone={status.tone}>{status.label}</Pill>
      </div>

      <div className="commute-advice__times">
        <div className="commute-advice__time">
          <span>建议出发</span>
          <strong>{departure || "待确认"}</strong>
        </div>
        <span className="commute-advice__arrow" aria-hidden="true">→</span>
        <div className="commute-advice__time">
          <span>到达</span>
          <strong className="is-arrival">{arrival || "待确认"}</strong>
        </div>
        {eta ? <span className="commute-advice__duration">{formatDuration(eta)}</span> : null}
      </div>

      <p className="commute-advice__weather">
        {weatherBits.length > 0 ? (
          <>
            {weather?.temperature_c !== null && weather?.temperature_c !== undefined ? (
              <Thermometer size={14} aria-hidden="true" />
            ) : (
              <CloudRain size={14} aria-hidden="true" />
            )}
            <span>{weatherBits.join(" · ")}</span>
          </>
        ) : (
          <span>天气数据暂缺</span>
        )}
      </p>

      <div className="commute-advice__actions">
        {onReanalyze ? (
          <button
            type="button"
            className="btn btn--primary commute-advice__primary"
            onClick={onReanalyze}
            disabled={busy}
          >
            {busy ? (
              <span className="spinner" style={{ width: 14, height: 14 }} />
            ) : (
              <RefreshCw size={14} strokeWidth={1.6} aria-hidden="true" />
            )}
            {reanalyzeLabel}
          </button>
        ) : null}
        {onHistory ? (
          <button type="button" className="btn btn--secondary commute-advice__secondary" onClick={onHistory}>
            历史建议
          </button>
        ) : null}
      </div>

      <p className={`commute-advice__note${degraded || expired || mock.length > 0 ? " is-warning" : ""}`}>
        {statusNote}
        <button type="button" className="commute-advice__link" onClick={openBasis}>
          {degraded || expired || mock.length > 0 ? "了解原因" : "查看依据"}
        </button>
      </p>

      <details className="commute-basis" ref={basisRef}>
        <summary>
          <span>计算依据</span>
          <span className={degraded ? "commute-basis__state" : "commute-basis__hint"}>
            {degraded ? fallbackLabel : "查看"}
          </span>
          <ChevronDown size={16} strokeWidth={1.5} aria-hidden="true" className="commute-basis__chevron" />
        </summary>
        <div className="commute-basis__body">
          <div className="commute-calc">
            {arrival ? (
              <div className="commute-calc__row">
                <span>计划到达</span>
                <b>{arrival}</b>
              </div>
            ) : null}
            {eta ? (
              <div className="commute-calc__row">
                <span>车程</span>
                <b>{formatDuration(eta)}</b>
              </div>
            ) : null}
            <div className="commute-calc__row">
              <span>计划预留</span>
              <b>{formatDuration(ai?.base_buffer_seconds ?? advisory.buffer_seconds)}</b>
            </div>
            {weatherExtra > 0 ? (
              <div className="commute-calc__row">
                <span>天气预留</span>
                <b>{formatDuration(weatherExtra)}</b>
              </div>
            ) : null}
            {ai?.status === "available" && (ai.additional_buffer_seconds ?? 0) > 0 ? (
              <div className="commute-calc__row">
                <span>额外预留</span>
                <b>{formatDuration(ai.additional_buffer_seconds!)}</b>
              </div>
            ) : null}
            <div className="commute-calc__row commute-calc__row--total">
              <span>建议出发</span>
              <b>{departure || "待确认"}</b>
            </div>
          </div>
          {latestIsNewer && latestParts.length > 0 ? (
            <div className="commute-weather-compare">
              <span>最新天气 {latestParts.join(" · ")}</span>
              <span>建议采用 {weatherBits.filter((bit) => !bit.includes("预留")).join(" · ") || "已保存的天气"}（未重算）</span>
            </div>
          ) : null}
          {sourceText ? <p className="commute-basis__meta">{sourceText}</p> : null}
          <p className="commute-basis__meta">
            生成于 {formatDateTime(advisory.generated_at)} · 有效至 {formatDateTime(advisory.expires_at)}
          </p>
        </div>
      </details>
    </section>
  );
}

"use client";

/**
 * Latest server-stored weather for one plan.
 *
 * This is deliberately separate from the advisory card: a newer weather
 * snapshot does not mean the old departure recommendation was recomputed.
 */

import { CloudRain, RefreshCw, Thermometer } from "lucide-react";

import type { Advisory, PlanWeather } from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import { formatDateTime, formatServiceDate } from "@/lib/mobile/format";
import { useApiResource } from "@/lib/mobile/use-api-resource";
import { Pill } from "./ui";

const FRESHNESS: Record<
  PlanWeather["freshness"],
  { label: string; tone: "green" | "amber" | "neutral" }
> = {
  fresh: { label: "刚更新", tone: "green" },
  stale: { label: "已过期", tone: "amber" },
  unavailable: { label: "不可用", tone: "neutral" },
};

export function LatestWeatherCard({
  planId,
  advisory,
}: {
  planId: string;
  advisory: Advisory | null;
}) {
  const weather = useApiResource<PlanWeather>(
    `/api/pa/commute-plans/${planId}/weather`,
    { enabled: Boolean(planId) },
  );
  const noWeather =
    weather.error instanceof ApiError &&
    (weather.error.status === 404 || weather.error.code === "NO_WEATHER");
  const data = weather.data;
  const summary = data?.summary ?? null;
  const fetchedAt = data?.fetched_at ? Date.parse(data.fetched_at) : NaN;
  const generatedAt = advisory ? Date.parse(advisory.generated_at) : NaN;
  const weatherIsNewer =
    Number.isFinite(fetchedAt) &&
    Number.isFinite(generatedAt) &&
    fetchedAt > generatedAt;
  const freshness = data ? FRESHNESS[data.freshness] : null;

  return (
    <div className="card card--tight">
      <div className="flex items-center justify-between gap-10" style={{ flexWrap: "wrap" }}>
        <span className="flex items-center gap-6">
          <CloudRain size={15} className="fog" />
          <span className="micro">最新天气</span>
        </span>
        {freshness ? <Pill tone={freshness.tone}>{freshness.label}</Pill> : null}
      </div>

      {weather.loading ? (
        <p className="caption" style={{ margin: "8px 0 0" }}>
          正在读取天气…
        </p>
      ) : null}

      {noWeather ? (
        <p className="caption" style={{ margin: "8px 0 0" }}>
          今天还没有天气记录；检查完成后会显示在这里。
        </p>
      ) : null}

      {weather.error && !noWeather ? (
        <>
          <p className="caption" style={{ margin: "8px 0 0" }}>
            天气暂时没读到。
          </p>
          <button type="button" className="link-button mt-6" onClick={weather.reload}>
            <RefreshCw size={12} /> 重试
          </button>
        </>
      ) : null}

      {data ? (
        <>
          <div className="flex items-center gap-10 mt-8" style={{ flexWrap: "wrap" }}>
            {summary?.temperature_c !== null && summary?.temperature_c !== undefined ? (
              <span className="flex items-center gap-4" style={{ fontSize: 14 }}>
                <Thermometer size={14} className="fog" /> {Math.round(summary.temperature_c)}°C
              </span>
            ) : null}
            {summary?.precipitation_probability_known &&
            summary.precipitation_probability_max !== null ? (
              <span className="flex items-center gap-4" style={{ fontSize: 14 }}>
                <CloudRain size={14} className="fog" /> 降雨{" "}
                {Math.round(summary.precipitation_probability_max * 100)}%
              </span>
            ) : (
              <span className="micro">降雨概率未知</span>
            )}
            {summary?.rain_risk ? <Pill tone="amber">可能下雨</Pill> : null}
          </div>

          <p className="micro" style={{ margin: "6px 0 0", lineHeight: 1.6 }}>
            {data.provider === "mock" ? "示例天气 · " : ""}获取于 {formatDateTime(data.fetched_at) || "时间未知"}
            {data.valid_to ? ` · 有效至 ${formatDateTime(data.valid_to)}` : ""}
          </p>
          <p className="micro" style={{ margin: "3px 0 0", lineHeight: 1.6 }}>
            通勤日期 {formatServiceDate(data.local_service_date)}
          </p>

          {!data.matches_current_plan ? (
            <p className="micro amber" style={{ margin: "6px 0 0", lineHeight: 1.6 }}>
              这条天气对应旧的通勤设置，只能参考。
            </p>
          ) : null}

          {data.is_stale ? (
            <p className="micro amber" style={{ margin: "6px 0 0", lineHeight: 1.6 }}>
              天气已过期，这里是最近一次记录。
            </p>
          ) : null}

          {data.coverage !== "full" ? (
            <p className="micro" style={{ margin: "6px 0 0", lineHeight: 1.6 }}>
              天气范围：{data.coverage === "partial" ? "只覆盖部分时段" : "暂无覆盖"}。
            </p>
          ) : null}

          {weatherIsNewer ? (
            <div className="notice notice--amber" style={{ marginTop: 8 }}>
              <strong>天气已更新</strong>
              <p style={{ margin: "4px 0 0" }}>
                出发方案还是上次分析的（{formatDateTime(advisory?.generated_at)}），
                可以刷新再看。
              </p>
            </div>
          ) : (
            <p className="micro" style={{ margin: "6px 0 0", lineHeight: 1.6 }}>
              这是最近一次天气，出发方案可能还没更新。
            </p>
          )}

          {advisory && !advisory.matches_current_plan ? (
            <p className="micro" style={{ margin: "6px 0 0", lineHeight: 1.6 }}>
              通勤设置改了，需要重新查路线。
            </p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

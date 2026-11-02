"use client";

/** A muted, expandable record of an earlier server advisory. */

import type { Advisory } from "@/lib/mobile/api";
import { formatDateTime, formatDuration, formatServiceDate, formatTime } from "@/lib/mobile/format";
import { advisoryApplicability } from "@/lib/mobile/advisory";
import { Pill } from "./ui";

const PROVIDER_LABEL: Record<string, string> = {
  amap: "高德地图",
  open_meteo: "Open-Meteo",
  mock: "示例数据",
};

export function HistoryAdvisoryCard({
  advisory,
  planRevision,
}: {
  advisory: Advisory;
  planRevision: number;
}) {
  const applicability = advisoryApplicability(advisory, planRevision);
  const eta = advisory.ai_analysis?.used_duration_seconds ?? advisory.eta_seconds;
  const departure = formatTime(advisory.recommended_departure_at);
  const arrival = formatTime(advisory.target_arrival_at);
  const sources = advisory.sources
    .map((source) => `${source.kind === "weather" ? "天气" : "路线"} ${PROVIDER_LABEL[source.provider] ?? source.provider}`)
    .join(" · ");

  return (
    <details className="card card--tight history-advice">
      <summary className="history-advice__summary">
        <span className="history-advice__date">{formatServiceDate(advisory.local_service_date)}</span>
        <span className="history-advice__time">{departure ? `${departure} 出发` : "时间待确认"}</span>
        <Pill tone={applicability.tone}>{applicability.label}</Pill>
      </summary>
      <div className="history-advice__body">
        <div className="history-advice__row">
          <span>到达</span>
          <b>{arrival || "待确认"}</b>
        </div>
        {eta ? (
          <div className="history-advice__row">
            <span>用时</span>
            <b>{formatDuration(eta)}</b>
          </div>
        ) : null}
        {advisory.weather ? (
          <div className="history-advice__row">
            <span>天气</span>
            <b>
              {advisory.weather.temperature_c !== null ? `${Math.round(advisory.weather.temperature_c)}°C` : "温度未知"}
              {advisory.weather.precipitation_probability_known &&
              advisory.weather.precipitation_probability_max !== null
                ? ` · 降雨 ${Math.round(advisory.weather.precipitation_probability_max * 100)}%`
                : ""}
            </b>
          </div>
        ) : null}
        <p className="history-advice__summary-text">{advisory.summary}</p>
        {sources ? <p className="history-advice__meta">{sources}</p> : null}
        <p className="history-advice__meta">
          更新于 {formatDateTime(advisory.generated_at)} · 有效至 {formatDateTime(advisory.expires_at)}
        </p>
      </div>
    </details>
  );
}

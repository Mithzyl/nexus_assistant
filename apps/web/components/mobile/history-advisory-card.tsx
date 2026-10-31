"use client";

/**
 * Muted archive row for a server advisory this browser has already loaded.
 * PA has no history-list endpoint, so this never claims to be full history.
 */

import type { Advisory } from "@/lib/mobile/api";
import { formatDateTime, formatDuration } from "@/lib/mobile/format";
import {
  advisoryApplicability,
  aiReasonText,
  aiStatus,
  durationSourceLabel,
  mockProviders,
  usesClientSnapshot,
  routeProviderLabel,
} from "@/lib/mobile/advisory";
import { Pill } from "./ui";

export function HistoryAdvisoryCard({
  advisory,
  planRevision,
}: {
  advisory: Advisory;
  planRevision: number;
}) {
  const applicability = advisoryApplicability(advisory, planRevision);
  const ai = advisory.ai_analysis;
  const mock = mockProviders(advisory);
  const baseBadge = aiStatus(advisory);
  const badge =
    mock.length > 0 && baseBadge.available
      ? "示例数据分析"
      : baseBadge.label;
  const mapDuration = ai?.map_duration_seconds ?? null;
  const usedDuration = ai?.used_duration_seconds ?? advisory.eta_seconds ?? null;
  const baseBuffer = ai?.base_buffer_seconds ?? advisory.buffer_seconds ?? null;
  const extraBuffer = ai?.extra_buffer_seconds ?? null;
  const additional = ai?.additional_buffer_seconds ?? null;
  const sourceText = advisory.sources
    .map((source) => `${source.kind === "weather" ? "天气" : "路线"} · ${source.provider}`)
    .join("；");

  return (
    <details className="card card--tight history-advice">
      <summary style={{ cursor: "pointer", listStylePosition: "outside" }}>
        <div className="flex items-center justify-between gap-10" style={{ flexWrap: "wrap" }}>
          <span className="flex items-center gap-6">
            <Pill tone="neutral">{applicability.label}</Pill>
            <span className="micro">历史建议</span>
          </span>
          <span className="micro">云端记录保留</span>
        </div>
        <span className="card-title" style={{ display: "block", marginTop: 7, fontSize: 15 }}>
          {advisory.title}
        </span>
        <span className="micro" style={{ display: "block", marginTop: 3 }}>
          更新于 {formatDateTime(advisory.generated_at)} · 有效至 {formatDateTime(advisory.expires_at)}
        </span>
      </summary>

      <div className="mt-10" style={{ borderTop: "1px solid var(--m-line)", paddingTop: 10 }}>
        <p className="caption" style={{ margin: "0 0 8px" }}>
          {applicability.detail}
        </p>
        <p className="caption" style={{ margin: "0 0 8px" }}>
          历史原文：{advisory.summary}
        </p>
        <p className="caption" style={{ margin: "0 0 8px" }}>
          历史原文保留，不代表本次已重新测算。天气是否可用以目标时段覆盖为准。
          {advisory.weather?.coverage !== "full" ? "该记录天气未完整覆盖分析时段，不能据此判断目标到达时的天气。" : ""}
        </p>
        <div style={{ display: "grid", gap: 5 }}>
          <div className="flex items-start justify-between gap-12">
            <span className="micro">路线来源</span>
            <span style={{ fontSize: 13, textAlign: "right" }}>{routeProviderLabel(advisory)}</span>
          </div>
          {mapDuration !== null ? (
            <div className="flex items-start justify-between gap-12">
              <span className="micro">地图耗时</span>
              <span style={{ fontSize: 13 }}>
                {formatDuration(mapDuration)} · {durationSourceLabel(ai?.duration_source)}
              </span>
            </div>
          ) : null}
          {usedDuration !== null ? (
            <div className="flex items-start justify-between gap-12">
              <span className="micro">采用耗时</span>
              <span style={{ fontSize: 13 }}>{formatDuration(usedDuration)}</span>
            </div>
          ) : null}
          <div className="flex items-start justify-between gap-12">
            <span className="micro">缓冲</span>
            <span style={{ fontSize: 13, textAlign: "right" }}>
              {baseBuffer !== null ? `计划 ${formatDuration(baseBuffer)}` : "计划缓冲未记录"}
              {additional === null && extraBuffer !== null
                ? ` · 历史总预留 ${formatDuration(extraBuffer)}（未拆分 AI 额外）`
                : ""}
              {additional !== null ? ` · AI 额外 ${formatDuration(additional)}` : ""}
            </span>
          </div>
          <div className="flex items-start justify-between gap-12">
            <span className="micro">分析状态</span>
            <span style={{ fontSize: 13, textAlign: "right" }}>{badge}</span>
          </div>
        </div>

        {aiReasonText(advisory) ? (
          <p className="micro amber" style={{ margin: "8px 0 0" }}>
            {aiReasonText(advisory)}
          </p>
        ) : null}
        {usesClientSnapshot(advisory) ? (
          <p className="micro" style={{ margin: "6px 0 0" }}>
            这条记录来自以前保存的路线结果。
          </p>
        ) : null}
        {mock.length > 0 ? (
          <p className="micro amber" style={{ margin: "6px 0 0" }}>
            示例数据：{mock.join(" / ")}；不是真实路况或真实模型分析。
          </p>
        ) : null}
        {sourceText ? <p className="micro" style={{ margin: "6px 0 0" }}>来源：{sourceText}</p> : null}
        <p className="micro" style={{ margin: "6px 0 0" }}>
          旧记录不会从云端删除；这里只是当前设备已加载过的云端记录。
        </p>
      </div>
    </details>
  );
}

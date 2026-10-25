"use client";

/** Server-generated commute advice. Nothing here is computed in the browser. */

import { Clock, CloudRain, Thermometer, TriangleAlert } from "lucide-react";

import type { Advisory } from "@/lib/mobile/api";
import { formatDateTime, formatDuration, formatTime } from "@/lib/mobile/format";
import {
  aiReasonText,
  aiStatus,
  durationSourceLabel,
  mockProviders,
  usesClientSnapshot,
  routeProviderLabel,
} from "@/lib/mobile/advisory";
import { Notice, Pill } from "./ui";

const REASON_LABEL: Record<string, string> = {
  ROUTE_UNAVAILABLE: "路线数据不可用",
  ROUTE_CLIENT_SNAPSHOT: "使用客户端路线快照",
  ROUTE_SERVER_PROVIDER: "使用服务器路线查询",
  AI_ANALYSIS_AVAILABLE: "AI 分析结果可用",
  ADVISORY_DEGRADED: "部分数据不完整",
  WEATHER_FRESH: "天气数据新鲜",
  WEATHER_RAIN_THRESHOLD_MET: "降雨达到阈值",
  DEPARTURE_UNKNOWN: "出发时间待确认",
};

const QUALITY: Record<Advisory["quality"], { label: string; tone: "green" | "amber" | "red" }> = {
  fresh: { label: "数据完整", tone: "green" },
  degraded: { label: "数据不完整", tone: "amber" },
  unavailable: { label: "暂时无法生成", tone: "red" },
};

const DEPARTURE: Record<Advisory["departure_status"], string> = {
  upcoming: "按建议时间出发",
  leave_now: "现在就该出发",
  past_due: "建议出发时间已过",
  unknown: "出发时间待确认",
};

export function AdvisoryCard({
  advisory,
  onSkip,
  skipBusy,
}: {
  advisory: Advisory;
  onSkip?: () => void;
  skipBusy?: boolean;
}) {
  const expired =
    advisory.is_expired ?? Date.parse(advisory.expires_at) <= Date.now();
  const quality = QUALITY[advisory.quality];
  const weather = advisory.weather;
  const ai = advisory.ai_analysis;
  const baseAiBadge = aiStatus(advisory);
  const aiReason = aiReasonText(advisory);
  const mock = mockProviders(advisory);
  const weatherSource = advisory.sources.find((source) => source.kind === "weather") ?? null;
  const aiBadge = mock.length > 0
    ? {
        ...baseAiBadge,
        label: baseAiBadge.available
          ? "模型分析（基于开发/测试数据）"
          : baseAiBadge.label,
        tone: "amber" as const,
      }
    : baseAiBadge;
  const historySnapshot = usesClientSnapshot(advisory);
  const usedDuration = ai?.used_duration_seconds ?? advisory.eta_seconds;
  const mapDuration = ai?.map_duration_seconds ?? null;
  const baseBuffer = ai?.base_buffer_seconds ?? advisory.buffer_seconds;
  const extraBuffer = ai?.extra_buffer_seconds ?? null;
  const additional = ai?.additional_buffer_seconds ?? null;
  const reasons = advisory.reason_codes
    .map((code) => REASON_LABEL[code])
    .filter(Boolean);

  return (
    <div className="card">
      <div className="flex items-center justify-between gap-10" style={{ flexWrap: "wrap" }}>
        <Pill tone={quality.tone}>{quality.label}</Pill>
        <span className="micro">{DEPARTURE[advisory.departure_status]}</span>
      </div>

      <h2 className="serif mt-10" style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>
        {advisory.title}
      </h2>
      <p className="body-text muted mt-6">{advisory.summary}</p>

      <div className="flex items-end gap-14 mt-12" style={{ flexWrap: "wrap" }}>
        {advisory.recommended_departure_at ? (
          <div>
            <span className="micro" style={{ display: "block" }}>建议出发</span>
            <span className="tabular" style={{ fontSize: 28, fontWeight: 600, color: "var(--m-fog-deep)" }}>
              {formatTime(advisory.recommended_departure_at)}
            </span>
          </div>
        ) : (
          <div>
            <span className="micro" style={{ display: "block" }}>建议出发</span>
            <span style={{ fontSize: 15 }}>服务器未给出</span>
          </div>
        )}
        <div>
          <span className="micro" style={{ display: "block" }}>目标到达</span>
          <span className="tabular" style={{ fontSize: 20, fontWeight: 600 }}>
            {formatTime(advisory.target_arrival_at)}
          </span>
        </div>
        {advisory.eta_seconds ? (
          <div>
            <span className="micro" style={{ display: "block" }}>预计用时</span>
            <span style={{ fontSize: 15 }}>{formatDuration(advisory.eta_seconds)}</span>
          </div>
        ) : null}
      </div>

      <div className="card card--mist mt-12" style={{ padding: 12 }}>
        <div className="flex items-center justify-between gap-10" style={{ flexWrap: "wrap" }}>
          <span className="micro">路线与分析</span>
          <Pill tone={aiBadge.tone === "neutral" ? "neutral" : aiBadge.tone}>{aiBadge.label}</Pill>
        </div>
        <div className="mt-8" style={{ display: "grid", gap: 5 }}>
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
              <span className="micro">本次采用耗时</span>
              <span style={{ fontSize: 13 }}>{formatDuration(usedDuration)}</span>
            </div>
          ) : null}
          <div className="flex items-start justify-between gap-12">
            <span className="micro">缓冲</span>
            <span style={{ fontSize: 13, textAlign: "right" }}>
              {baseBuffer !== null
                ? `计划 ${formatDuration(baseBuffer)}`
                : "计划缓冲未记录"}
              {additional !== null ? ` · AI 额外 ${formatDuration(additional)}` : ""}
              {additional === null && extraBuffer !== null
                ? ` · 历史总预留 ${formatDuration(extraBuffer)}（未拆分 AI 额外）`
                : ""}
            </span>
          </div>
        </div>

        {historySnapshot ? (
          <p className="micro mt-8">
            这条建议使用了客户端路线快照，不是本轮 Web 发起的服务器路线查询；Web 不生成同类快照。
          </p>
        ) : null}

        {aiReason ? (
          <p className="micro amber" style={{ margin: "8px 0 0" }}>
            {aiReason}
          </p>
        ) : null}

        {ai && ai.status === "available" ? (
          <>
            {ai.basis.length > 0 ? (
              <details className="mt-8">
                <summary className="micro" style={{ cursor: "pointer" }}>
                  查看分析依据（{ai.basis.length}）
                </summary>
                <ul className="caption" style={{ margin: "6px 0 0", paddingLeft: "1.2em" }}>
                  {ai.basis.map((item, index) => (
                    <li key={`${item}-${index}`}>{item}</li>
                  ))}
                </ul>
              </details>
            ) : null}
            {ai.missing_information.length > 0 ? (
              <details className="mt-6">
                <summary className="micro" style={{ cursor: "pointer" }}>
                  模型声明的缺失信息（{ai.missing_information.length}）
                </summary>
                <ul className="caption" style={{ margin: "6px 0 0", paddingLeft: "1.2em" }}>
                  {ai.missing_information.map((item, index) => (
                    <li key={`${item}-${index}`}>{item}</li>
                  ))}
                </ul>
              </details>
            ) : null}
          </>
        ) : null}

        {ai && ai.status !== "available" ? (
          <p className="micro" style={{ margin: "8px 0 0" }}>
            分析没有完成；标题与摘要仍来自服务器保存的记录，不当作 AI 成功结果。
          </p>
        ) : null}

        {mock.length > 0 ? (
          <div className="notice notice--amber" style={{ marginTop: 10 }}>
            <strong>开发/测试数据</strong>
            <p style={{ margin: "4px 0 0" }}>
              {mock.join(" / ")}：不是真实路况或真实天气，也不是 Apple 签名结果。
            </p>
          </div>
        ) : null}

        {reasons.length > 0 ? (
          <p className="micro" style={{ margin: "8px 0 0", lineHeight: 1.6 }}>
            判定：{reasons.join(" · ")}
          </p>
        ) : null}
      </div>

      {weather ? (
        <div className="card card--mist mt-12" style={{ padding: 12 }}>
          <div className="flex items-center justify-between gap-8" style={{ flexWrap: "wrap" }}>
            <span className="micro">上次分析采用的天气</span>
            {weatherSource?.fetched_at ? (
              <span className="micro">取得于 {formatDateTime(weatherSource.fetched_at)}</span>
            ) : null}
          </div>
          <div className="flex items-center gap-8 mt-6" style={{ flexWrap: "wrap" }}>
            {weather.temperature_c !== null ? (
              <span className="flex items-center gap-4" style={{ fontSize: 14 }}>
                <Thermometer size={14} className="fog" /> {Math.round(weather.temperature_c)}°C
              </span>
            ) : null}
            {weather.precipitation_probability_known &&
            weather.precipitation_probability_max !== null ? (
              <span className="flex items-center gap-4" style={{ fontSize: 14 }}>
                <CloudRain size={14} className="fog" /> 降雨{" "}
                {Math.round(weather.precipitation_probability_max * 100)}%
              </span>
            ) : (
              <span className="micro">降雨概率未知</span>
            )}
            {weather.rain_risk ? <Pill tone="amber">可能下雨</Pill> : null}
          </div>
          {advisory.weather_extra_buffer_seconds > 0 ? (
            <p className="micro" style={{ margin: "6px 0 0" }}>
              雨天额外缓冲 {formatDuration(advisory.weather_extra_buffer_seconds)}
            </p>
          ) : null}
          {weather.coverage !== "full" ? (
            <p className="micro" style={{ margin: "4px 0 0" }}>
              天气覆盖范围：{weather.coverage === "partial" ? "部分时段" : "不可用"}，
              不完整时不把它当作确定天气。
            </p>
          ) : null}
          <p className="micro" style={{ margin: "6px 0 0", lineHeight: 1.6 }}>
            这是建议生成时保存的天气，不随上方“最新天气”更新。
          </p>
        </div>
      ) : (
        <p className="micro mt-10">这条建议没有保存可用的天气摘要。</p>
      )}

      {expired ? (
        <Notice tone="amber" title="这条建议已过期">
          过期建议只作参考；请重新检查以获取新的天气与 ETA。
        </Notice>
      ) : null}

      {advisory.quality === "degraded" ? (
        <Notice tone="amber" title="数据不完整">
          部分天气或路线数据暂不可用。建议仍按服务器给出的时间展示，不补齐缺失值。
        </Notice>
      ) : null}

      {advisory.sources.length > 0 ? (
        <div className="mt-12">
          <p className="micro" style={{ marginBottom: 4 }}>数据来源</p>
          {advisory.sources.map((source, index) => (
            <div key={`${source.provider}-${index}`} className="mt-6">
              <span className="micro" style={{ display: "block" }}>
                {source.kind === "weather" ? "天气" : "路线"} · {source.provider}
                {source.freshness === "stale"
                  ? " · 已过期"
                  : source.freshness === "unavailable"
                    ? " · 不可用"
                    : ""}
              </span>
              {source.attribution ? (
                source.attribution.url ? (
                  <a
                    href={source.attribution.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="micro fog-deep"
                    style={{ display: "block", overflowWrap: "anywhere" }}
                  >
                    {source.attribution.text || "来源"}
                  </a>
                ) : (
                  <span className="micro" style={{ display: "block", overflowWrap: "anywhere" }}>
                    {source.attribution.text}
                  </span>
                )
              ) : null}
            </div>
          ))}
          {advisory.sources.some((source) => source.note) ? (
            <p className="micro mt-4">
              {advisory.sources.map((source) => source.note).filter(Boolean).join(" ")}
            </p>
          ) : null}
        </div>
      ) : null}

      <p className="micro mt-12" style={{ lineHeight: 1.6 }}>
        建议更新于 {formatDateTime(advisory.generated_at)} · 有效至 {formatDateTime(advisory.expires_at)}
        <br />
        文案来源：{advisory.explanation_source === "model" ? "模型文案" : "确定性模板"}；
        分析状态：{aiBadge.label}。数字与出发时间以服务器保存的记录为准。
      </p>

      {onSkip ? (
        <button
          type="button"
          className="btn btn--secondary mt-12"
          onClick={onSkip}
          disabled={skipBusy}
        >
          <TriangleAlert size={14} />
          {skipBusy ? "正在记录…" : "跳过今天这条提醒"}
        </button>
      ) : null}

      <p className="micro mt-8" style={{ lineHeight: 1.6 }}>
        <Clock size={11} style={{ display: "inline", verticalAlign: "-1px" }} /> 提醒由服务器调度；
        浏览器定时器不负责后台检查，Web Push 未接入。
      </p>
    </div>
  );
}

"use client";

import { useState } from "react";
import { Clock3, Copy, ExternalLink, Map, MapPin } from "lucide-react";

import { formatDateTime, formatDuration } from "@/lib/mobile/format";

type RouteState = "enough" | "verified" | "tight" | "unverified" | "unavailable";

export interface RouteMapTarget {
  label: string | null;
  latitude: number | null;
  longitude: number | null;
  coordinateSystem?: "gcj02" | "wgs84" | "unknown" | null;
  address?: string | null;
  city?: string | null;
}

/** Text-first route evidence with an optional on-demand map action. */
export function RouteTextCard({
  originLabel,
  destinationLabel,
  departureAt,
  arrivalAt,
  mode,
  durationSeconds,
  distanceMeters,
  walkingDistanceMeters,
  transferCount,
  transfersSummary,
  fareCny,
  fareKind,
  fareNotice,
  instruction,
  state,
  notice,
  mapTarget,
  weatherLine,
  timingBasis,
  accuracyNotice,
  onPlanRoute,
  onViewRoute,
  replanHref,
  arrivalLabel = "到达",
  embedded = false,
}: {
  originLabel?: string | null;
  destinationLabel?: string | null;
  departureAt?: string | null;
  arrivalAt?: string | null;
  mode?: "driving" | "walking" | "transit" | null;
  durationSeconds?: number | null;
  distanceMeters?: number | null;
  walkingDistanceMeters?: number | null;
  transferCount?: number | null;
  transfersSummary?: string | null;
  fareCny?: number | null;
  fareKind?: string | null;
  fareNotice?: string | null;
  instruction?: string | null;
  state: RouteState;
  notice?: string | null;
  mapTarget?: RouteMapTarget | null;
  weatherLine?: string | null;
  timingBasis?: string | null;
  accuracyNotice?: string | null;
  onPlanRoute?: () => void;
  onViewRoute?: () => void;
  replanHref?: string;
  arrivalLabel?: string;
  embedded?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const modeLabel =
    mode === "driving"
      ? "驾车"
      : mode === "walking"
        ? "步行"
        : mode === "transit"
          ? "公共交通"
          : "出行方式未返回";
  const stateLabel =
    state === "enough"
      ? "时间充裕"
      : state === "verified"
        ? "路线已核实"
      : state === "tight"
        ? "可能赶不上"
        : state === "unverified"
          ? "路程未核实"
          : "路线不可用";
  const fareLabel =
    mode === "transit" || fareKind === "amap_transit_fare"
      ? "公交票价"
      : fareKind === "amap_taxi_cost_estimate"
        ? "打车费用估计"
        : "费用估计";
  const timingNotice =
    timingBasis === "planned_time_not_live_forecast"
      ? "按计划时间估算，不等同于实时路况预测。"
      : timingBasis === "provider_departure_time"
        ? "按当前条件估算，实际到达时间会随路况变化。"
        : null;
  const address = [mapTarget?.city, mapTarget?.address, mapTarget?.label]
    .filter(Boolean)
    .join(" ");
  const mapUrl = (() => {
    if (
      mapTarget?.coordinateSystem === "gcj02" &&
      mapTarget?.latitude !== null &&
      mapTarget?.latitude !== undefined &&
      mapTarget.longitude !== null &&
      mapTarget.longitude !== undefined
    ) {
      return `https://uri.amap.com/marker?position=${mapTarget.longitude},${mapTarget.latitude}&name=${encodeURIComponent(mapTarget.label || "目的地")}&coordinate=gaode&callnative=0`;
    }
    if (
      mapTarget?.coordinateSystem === "wgs84" &&
      mapTarget.latitude !== null &&
      mapTarget.latitude !== undefined &&
      mapTarget.longitude !== null &&
      mapTarget.longitude !== undefined
    ) {
      return `https://maps.apple.com/?daddr=${mapTarget.latitude},${mapTarget.longitude}`;
    }
    if (address) {
      return `https://maps.apple.com/?q=${encodeURIComponent(address)}`;
    }
    return null;
  })();

  const copyAddress = async () => {
    const value = address || mapTarget?.label;
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section className={`route-text-card${embedded ? " route-text-card--embedded" : ""}`}>
      <div className="flex items-start justify-between gap-10">
        <div className="grow">
          <p className="route-text-card__eyebrow">路线文字摘要</p>
          <p className="route-text-card__title">
            {originLabel || "出发地未提供"} <span aria-hidden="true">→</span>{" "}
            {destinationLabel || "目的地待确认"}
          </p>
        </div>
        <span className={`route-state route-state--${state}`}>{stateLabel}</span>
      </div>

      <div className="route-text-card__facts">
        <span><Clock3 size={13} /> {modeLabel}</span>
        {departureAt ? <span>出发 {formatDateTime(departureAt)}</span> : null}
        <span>
          {arrivalAt
            ? `${arrivalLabel} ${formatDateTime(arrivalAt)}`
            : `${arrivalLabel}时间未提供`}
        </span>
        <span>{durationSeconds !== null && durationSeconds !== undefined ? formatDuration(durationSeconds) : "预计耗时未返回"}</span>
        {mode !== "transit" &&
        distanceMeters !== null &&
        distanceMeters !== undefined ? (
          <span>{distanceMeters >= 1000 ? `约 ${(distanceMeters / 1000).toFixed(1)} 公里` : `约 ${distanceMeters} 米`}</span>
        ) : null}
        {walkingDistanceMeters !== null && walkingDistanceMeters !== undefined ? (
          <span>步行约 {walkingDistanceMeters} 米</span>
        ) : null}
        {transferCount !== null && transferCount !== undefined ? (
          <span>{transferCount === 0 ? "无需换乘" : `换乘 ${transferCount} 次`}</span>
        ) : null}
      </div>

      {instruction ||
      transfersSummary ||
      (distanceMeters !== null && distanceMeters !== undefined) ||
      (walkingDistanceMeters !== null && walkingDistanceMeters !== undefined) ||
      (fareCny !== null && fareCny !== undefined) ||
      fareNotice ||
      timingNotice ||
      accuracyNotice ||
      (state === "verified" && notice) ? (
        <details className="route-text-card__details">
          <summary>查看费用与判断说明</summary>
          {instruction ? <p>{instruction}</p> : null}
          {transfersSummary ? <p>换乘：{transfersSummary}</p> : null}
          {mode === "transit" &&
          distanceMeters !== null &&
          distanceMeters !== undefined ? (
            <p>
              路线距离约{" "}
              {distanceMeters >= 1000
                ? `${(distanceMeters / 1000).toFixed(1)} 公里`
                : `${distanceMeters} 米`}
            </p>
          ) : null}
          {fareCny !== null && fareCny !== undefined ? (
            <p>{fareLabel} ¥{fareCny.toFixed(2)}</p>
          ) : null}
          {fareNotice ? <p>{fareNotice}</p> : null}
          {timingNotice ? <p>{timingNotice}</p> : null}
          {accuracyNotice ? <p>{accuracyNotice}</p> : null}
          {state === "verified" ? (
            <p>路线为服务器测算结果，不代表实际到达时间。</p>
          ) : null}
          {state === "verified" && notice ? <p>{notice}</p> : null}
        </details>
      ) : null}
      {state !== "verified" && notice ? (
        <p className="route-text-card__notice">{notice}</p>
      ) : null}
      {(!originLabel || originLabel === "出发地未提供") &&
      (state === "unverified" || state === "unavailable") ? (
        <p className="route-text-card__notice">
          需要先确认从哪里出发；起点未确认时不能计算可用耗时。
        </p>
      ) : null}
      {weatherLine ? <p className="route-text-card__weather">{weatherLine}</p> : null}

      <div className="route-text-card__actions">
        {onPlanRoute ? (
          <button type="button" className="link-button" onClick={onPlanRoute}>
            <Clock3 size={13} />{" "}
            {state === "verified" ? "重新测算路线" : "补充起点并测算路线"}
          </button>
        ) : null}
        {onViewRoute ? (
          <button type="button" className="link-button" onClick={onViewRoute}>
            <Map size={13} /> 查看路线
          </button>
        ) : null}
        {replanHref && (state === "unverified" || state === "unavailable") ? (
          <a className="link-button" href={replanHref}>
            <Clock3 size={13} /> 打开事项重新测算
          </a>
        ) : null}
        {mapUrl ? (
          <a
            className="link-button"
            href={mapUrl}
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={13} /> 在外部地图打开
          </a>
        ) : null}
        {address || mapTarget?.label ? (
          <button type="button" className="link-button" onClick={() => void copyAddress()}>
            <Copy size={13} /> {copied ? "已复制地址" : "复制地址"}
          </button>
        ) : (
          <span className="micro"><MapPin size={12} /> 地点尚未确认，暂不能打开地图</span>
        )}
        {mapUrl &&
        (mapTarget?.coordinateSystem === "gcj02" ||
          mapTarget?.coordinateSystem === "wgs84") ? (
          <span className="micro">
            {mapTarget.coordinateSystem === "gcj02"
              ? "使用高德 GCJ-02 坐标打开高德地图，不做坐标系转换。"
              : "使用 WGS-84 坐标打开 Apple 地图，不做坐标系转换。"}
          </span>
        ) : address ? (
          <span className="micro">坐标来源不兼容，按地址打开搜索。</span>
        ) : null}
      </div>
    </section>
  );
}

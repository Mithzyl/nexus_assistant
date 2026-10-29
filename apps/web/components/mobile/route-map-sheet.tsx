"use client";

import { useEffect, useState } from "react";
import { Clock3, Info, MapPin, RefreshCw } from "lucide-react";

import type { MapEndpoint } from "@/lib/mobile/amap";
import type { ScheduledRoute } from "@/lib/mobile/api";
import { formatDuration } from "@/lib/mobile/format";
import { getScheduleRoute } from "@/lib/mobile/matters-api";
import {
  AmapMapCanvas,
  type AmapPoint,
  type AmapRouteSegment,
} from "./amap-map";
import { LoadingCard, Sheet } from "./ui";

/** Convert a server route mode into the same label used by text cards. */
function modeLabel(mode: "driving" | "walking" | "transit" | null): string {
  if (mode === "driving") return "驾车";
  if (mode === "walking") return "步行";
  if (mode === "transit") return "公共交通";
  return "出行方式未返回";
}

/** Convert persisted endpoint and segment coordinates into drawable GCJ-02 data. */
function mapEndpoint(
  endpoint: ScheduledRoute["origin"],
  label: string,
): MapEndpoint | null {
  if (
    endpoint?.coordinate_system !== "gcj02" ||
    !Number.isFinite(endpoint.latitude) ||
    !Number.isFinite(endpoint.longitude)
  ) {
    return null;
  }
  return {
    id: endpoint.schedule_id || label,
    label,
    latitude: endpoint.latitude,
    longitude: endpoint.longitude,
    coordinateSystem: endpoint.coordinate_system,
  };
}

/**
 * Show one persisted route inside the existing bottom sheet.
 *
 * Clicking "查看路线" may read the saved route endpoint, but this component
 * never asks AMap or the server to calculate a new route. Walking and transit
 * shape segments stay separately styled and labelled.
 */
export function RouteMapSheet({
  open,
  scheduleId,
  title,
  fallbackOriginLabel,
  fallbackDestination,
  fallbackMode,
  fallbackDurationSeconds,
  fallbackWalkingDistanceMeters,
  fallbackTransferCount,
  fallbackTransfersSummary,
  fallbackFareCny,
  fallbackFareKind,
  fallbackNotice,
  onClose,
}: {
  open: boolean;
  scheduleId: string | null;
  title: string;
  fallbackOriginLabel?: string | null;
  fallbackDestination: MapEndpoint | null;
  fallbackMode: "driving" | "walking" | "transit" | null;
  fallbackDurationSeconds: number | null;
  fallbackWalkingDistanceMeters?: number | null;
  fallbackTransferCount?: number | null;
  fallbackTransfersSummary?: string | null;
  fallbackFareCny?: number | null;
  fallbackFareKind?: string | null;
  fallbackNotice?: string | null;
  onClose: () => void;
}) {
  const [route, setRoute] = useState<ScheduledRoute | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!open || !scheduleId) {
      setRoute(null);
      setLoading(false);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void getScheduleRoute(scheduleId, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setRoute(result);
      })
      .catch((caught) => {
        if (controller.signal.aborted) return;
        setRoute(null);
        setError(
          caught instanceof Error
            ? caught.message
            : "暂时无法读取服务器保存的路线。",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open, reloadKey, scheduleId]);

  const available = route?.status === "available" ? route : null;
  const origin = available
    ? mapEndpoint(available.origin, fallbackOriginLabel || "路线起点")
    : null;
  const destination = available
    ? mapEndpoint(available.destination, fallbackDestination?.label || "目的地")
    : fallbackDestination;
  const points: AmapPoint[] = [];
  if (origin) points.push({ ...origin, kind: "origin" });
  if (destination) points.push({ ...destination, kind: "destination" });

  const routeSegments: AmapRouteSegment[] =
    available?.shape?.status === "available" ||
    available?.shape?.status === "partial"
      ? available.shape.segments.map((segment) => ({
          kind: segment.kind,
          label: segment.label,
          coordinateSystem: segment.coordinate_system,
          points: segment.points,
        }))
      : [];
  const hasWalking = routeSegments.some((segment) => segment.kind === "walking");
  const hasTransit = routeSegments.some((segment) => segment.kind === "transit");
  const hasDriving = routeSegments.some((segment) => segment.kind === "driving");
  const shapeMissing =
    available?.shape?.status === "missing" || routeSegments.length === 0;
  const mode = available?.mode ?? fallbackMode;
  const durationSeconds =
    available?.duration_seconds ?? fallbackDurationSeconds;
  const walkingDistanceMeters =
    available?.walking_distance_meters ?? fallbackWalkingDistanceMeters;
  const transferCount =
    available?.transfer_count ?? fallbackTransferCount;
  const transfersSummary =
    available?.transfers_summary ?? fallbackTransfersSummary;
  const fareCny = available?.fare_cny ?? fallbackFareCny;
  const fareKind = available?.fare_kind ?? fallbackFareKind;
  const routeLabel =
    mode === "transit" || fareKind === "amap_transit_fare"
      ? "公交票价"
      : fareKind === "amap_taxi_cost_estimate"
        ? "打车费用估计"
        : "费用估计";

  return (
    <Sheet title={title} open={open} onClose={onClose}>
      <div className="route-map-sheet">
        {loading ? <LoadingCard label="正在读取服务器保存的路线…" /> : null}
        {error ? (
          <div className="notice notice--red" role="alert">
            <strong>路线图暂时无法读取</strong>
            <p className="caption">{error}</p>
            <button
              type="button"
              className="link-button"
              onClick={() => setReloadKey((current) => current + 1)}
            >
              <RefreshCw size={13} /> 重新读取服务器路线
            </button>
          </div>
        ) : null}
        {!loading && route?.status === "unverified" ? (
          <div className="notice notice--amber" role="status">
            <strong>已保存路线当前不可用</strong>
            <p className="caption">
              {route.message || "日程或起点已变化，旧路线不再有效。"}
            </p>
          </div>
        ) : null}

        {!loading && available && points.length > 0 ? (
          <AmapMapCanvas
            points={points}
            routeSegments={routeSegments}
            purpose="route"
          />
        ) : !loading && available ? (
          <div className="notice notice--amber">
            起终点没有可用于高德地图的 GCJ-02 坐标，无法显示地图。
          </div>
        ) : null}

        {!loading && available && shapeMissing ? (
          <div className="route-map-sheet__notice" role="status">
            <Info size={16} aria-hidden="true" />
            <div>
              <strong>暂未提供路线图</strong>
              <p>
                服务器保存了方式、耗时和费用，但没有真实道路形状。这里只标出
                已有起终点，不画直线，也不在浏览器重新计算路线。
              </p>
              {mode === "transit" ? (
                <p>
                  公交步行段与乘车段的分段线条需要服务器保留对应形状后才能显示。
                </p>
              ) : null}
            </div>
          </div>
        ) : null}
        {!loading &&
        available?.shape?.status === "partial" &&
        !shapeMissing ? (
          <div className="notice notice--amber" role="status">
            服务器只保存了部分路线形状；缺少形状的步行或乘车段不会用直线补齐。
          </div>
        ) : null}

        {!loading && available && !shapeMissing ? (
          <>
            <div className="route-map-sheet__source" role="status">
              <Info size={15} aria-hidden="true" />
              地图使用服务器保存的真实路线形状，不在浏览器再次查询或计算路线。
            </div>
            <div className="route-map-sheet__legend" aria-label="路线线条说明">
              {hasDriving ? (
                <span>
                  <i className="route-line route-line--driving" /> 驾车实线
                </span>
              ) : null}
              {hasTransit ? (
                <span>
                  <i className="route-line route-line--transit" /> 乘车实线
                </span>
              ) : null}
              {hasWalking ? (
                <span>
                  <i className="route-line route-line--walking" /> 步行虚线
                </span>
              ) : null}
            </div>
            {routeSegments.some((segment) => segment.label) ? (
              <p className="micro">
                路线分段：
                {routeSegments
                  .filter((segment) => segment.label)
                  .map((segment) => segment.label)
                  .join(" → ")}
              </p>
            ) : null}
          </>
        ) : null}

        {!loading && available ? (
          <>
            <div className="route-map-sheet__endpoints">
              <span>
                <span className="route-map-sheet__dot route-map-sheet__dot--start" />
                起点：{origin?.label || "起点坐标未保存"}
              </span>
              <span>
                <span className="route-map-sheet__dot route-map-sheet__dot--end" />
                终点：{destination?.label || "地点未确认"}
              </span>
            </div>

            <div className="route-map-sheet__facts">
              <span>
                <Clock3 size={13} /> {modeLabel(mode)}
              </span>
              {durationSeconds !== null && durationSeconds !== undefined ? (
                <span>{formatDuration(durationSeconds)}</span>
              ) : (
                <span>耗时未返回</span>
              )}
              {walkingDistanceMeters !== null &&
              walkingDistanceMeters !== undefined ? (
                <span>步行约 {walkingDistanceMeters} 米</span>
              ) : null}
              {transferCount !== null && transferCount !== undefined ? (
                <span>
                  {transferCount === 0 ? "无需换乘" : `换乘 ${transferCount} 次`}
                </span>
              ) : null}
              {fareCny !== null && fareCny !== undefined ? (
                <span>
                  {routeLabel} ¥{fareCny.toFixed(2)}
                </span>
              ) : null}
            </div>
            {transfersSummary ? (
              <p className="caption">
                <MapPin size={13} aria-hidden="true" /> 换乘：{transfersSummary}
              </p>
            ) : null}
            {available.fare_notice ? (
              <p className="micro">{available.fare_notice}</p>
            ) : null}
            {route?.fetched_at || fallbackNotice ? (
              <p className="micro">
                {route?.fetched_at
                  ? `路线结果取得于 ${new Date(route.fetched_at).toLocaleString("zh-CN")}`
                  : fallbackNotice}
              </p>
            ) : null}
          </>
        ) : null}
        <button type="button" className="btn btn--quiet" onClick={onClose}>
          关闭地图
        </button>
      </div>
    </Sheet>
  );
}

"use client";

import { useEffect, useRef, useState } from "react";
import { MapPin, TriangleAlert } from "lucide-react";

import {
  loadAmap,
  type AMapMap,
  type AMapMarker,
  type AMapNamespace,
  type AMapPolyline,
  type MapEndpoint,
} from "@/lib/mobile/amap";

export type AmapPointKind = "candidate" | "saved" | "origin" | "destination";

export interface AmapPoint extends MapEndpoint {
  kind: AmapPointKind;
  selected?: boolean;
}

export interface AmapRouteSegment {
  kind: "driving" | "walking" | "transit";
  label: string | null;
  coordinateSystem: "gcj02" | "wgs84" | "unknown";
  points: Array<{ latitude: number; longitude: number }>;
}

/** Build the small HTML marker used for candidates and route endpoints. */
function markerContent(point: AmapPoint, index: number): string {
  const selected = point.selected ? " place-map-marker--selected" : "";
  if (point.kind === "candidate") {
    return `<span class="place-map-marker place-map-marker--candidate${selected}" aria-hidden="true">${index + 1}</span>`;
  }
  const kind = point.kind === "destination" ? "end" : point.kind;
  return `<span class="place-map-marker place-map-marker--${kind}${selected}" aria-hidden="true"><span></span></span>`;
}

/** Reserve extra map padding when a route sheet follows the bottom controls. */
function boundsPadding(kind: "candidate" | "route"): number[] {
  return kind === "route" ? [56, 56, 112, 56] : [42, 42, 86, 42];
}

/**
 * Render one AMap instance for the currently open sheet.
 *
 * The component never reads browser geolocation and never asks AMap for a
 * route. It only draws coordinates supplied by the server or search results.
 */
export function AmapMapCanvas({
  points,
  routeSegments = [],
  purpose,
  pickMode = false,
  focusCenter = null,
  onPointSelect,
  onCenterChange,
  onStatusChange,
}: {
  points: AmapPoint[];
  routeSegments?: AmapRouteSegment[];
  purpose: "candidate" | "route";
  pickMode?: boolean;
  focusCenter?: [number, number] | null;
  onPointSelect?: (point: AmapPoint) => void;
  onCenterChange?: (latitude: number, longitude: number) => void;
  onStatusChange?: (status: "loading" | "ready" | "error") => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<AMapMap | null>(null);
  const overlaysRef = useRef<Array<AMapMarker | AMapPolyline>>([]);
  const pointsRef = useRef(points);
  const centerRef = useRef(focusCenter);
  const focusCenterRef = useRef(focusCenter);
  const onPointSelectRef = useRef(onPointSelect);
  const onCenterChangeRef = useRef(onCenterChange);
  const onStatusChangeRef = useRef(onStatusChange);
  const [amap, setAmap] = useState<AMapNamespace | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [error, setError] = useState<string | null>(null);

  pointsRef.current = points;
  centerRef.current = focusCenter;
  focusCenterRef.current = focusCenter;
  onPointSelectRef.current = onPointSelect;
  onCenterChangeRef.current = onCenterChange;
  onStatusChangeRef.current = onStatusChange;

  useEffect(() => {
    onStatusChangeRef.current?.(status);
  }, [status]);

  useEffect(() => {
    let cancelled = false;
    const initialPoint = pointsRef.current.find(
      (point) =>
        point.coordinateSystem === "gcj02" &&
        Number.isFinite(point.latitude) &&
        Number.isFinite(point.longitude),
    );
    const initialCenter = centerRef.current ?? (
      initialPoint
        ? [initialPoint.longitude, initialPoint.latitude] as [number, number]
        : null
    );
    if (!initialCenter) {
      setStatus("error");
      setError("没有可用于高德地图的 GCJ-02 坐标。");
      return;
    }

    void loadAmap()
      .then((loaded) => {
        const container = containerRef.current;
        if (cancelled || !container) return;
        const map = new loaded.Map(container, {
          center: initialCenter,
          zoom: 15,
          viewMode: "2D",
        });
        mapRef.current = map;
        setAmap(loaded);
        setStatus("ready");
      })
      .catch((caught) => {
        if (cancelled) return;
        setStatus("error");
        setError(
          caught instanceof Error
            ? caught.message
            : "高德地图加载失败，请稍后重试。",
        );
      });

    return () => {
      cancelled = true;
      for (const overlay of overlaysRef.current) overlay.setMap(null);
      overlaysRef.current = [];
      mapRef.current?.destroy();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!amap || !map || !pickMode) return;
    const reportCenter = () => {
      const center = map.getCenter();
      onCenterChangeRef.current?.(center.getLat(), center.getLng());
    };
    map.on("moveend", reportCenter);
    return () => {
      map.off("moveend", reportCenter);
    };
  }, [amap, pickMode]);

  useEffect(() => {
    const center = focusCenterRef.current;
    if (!amap || !mapRef.current || !pickMode || !center) return;
    mapRef.current.setZoomAndCenter(15, center);
  }, [amap, pickMode]);

  useEffect(() => {
    const map = mapRef.current;
    if (!amap || !map) return;

    for (const overlay of overlaysRef.current) overlay.setMap(null);
    overlaysRef.current = [];

    const drawable = points.filter(
      (point) =>
        point.coordinateSystem === "gcj02" &&
        Number.isFinite(point.latitude) &&
        Number.isFinite(point.longitude),
    );
    const segments = routeSegments.filter(
      (segment) =>
        segment.coordinateSystem === "gcj02" &&
        segment.points.length >= 2,
    );
    if (
      drawable.length === 0 &&
      segments.length === 0 &&
      !focusCenterRef.current
    ) {
      setStatus("error");
      setError("没有可用于高德地图的 GCJ-02 坐标。");
      return;
    }

    const overlays: Array<AMapMarker | AMapPolyline> = [];
    if (!pickMode) {
      const markers = drawable.map(
        (point, index) =>
          new amap.Marker({
            position: [point.longitude, point.latitude],
            title: point.label,
            anchor: point.kind === "destination" ? "bottom-center" : "center",
            content: markerContent(point, index),
            zIndex: point.selected ? 120 : 100,
          }),
      );
      for (const [index, marker] of markers.entries()) {
        const point = drawable[index];
        if (point.kind === "candidate" && onPointSelectRef.current) {
          marker.on("click", () => onPointSelectRef.current?.(point));
        }
      }
      overlays.push(...markers);

      for (const segment of segments) {
        const walking = segment.kind === "walking";
        overlays.push(
          new amap.Polyline({
            path: segment.points.map(
              (point) => [point.longitude, point.latitude] as [number, number],
            ),
            strokeColor: walking ? "#3f7355" : segment.kind === "transit" ? "#9a6b2f" : "#3c5f73",
            strokeWeight: 6,
            strokeOpacity: 0.9,
            strokeStyle: walking ? "dashed" : "solid",
            ...(walking ? { strokeDasharray: [8, 8] } : {}),
            lineJoin: "round",
            lineCap: "round",
            showDir: !walking,
            zIndex: walking ? 70 : 80,
          }),
        );
      }
    }
    if (overlays.length > 0) map.add(overlays);
    overlaysRef.current = overlays;
    setStatus("ready");
    setError(null);

    if (pickMode) return;
    const selected = drawable.find((point) => point.selected);
    if (selected && segments.length === 0) {
      map.setZoomAndCenter(15, [selected.longitude, selected.latitude]);
    } else if (overlays.length === 1 && drawable.length === 1) {
      map.setZoomAndCenter(15, [drawable[0].longitude, drawable[0].latitude]);
    } else {
      map.setFitView(overlays, false, boundsPadding(purpose), 15);
    }
  }, [amap, pickMode, points, purpose, routeSegments]);

  return (
    <div className="amap-panel">
      <div className="amap-panel__heading">
        <MapPin size={14} />
        <span>
          {purpose === "candidate"
            ? "搜索结果地图"
            : "已保存路线的起终点"}
        </span>
      </div>
      <p className="amap-panel__notice" role="status">
        {status === "loading"
          ? "正在向高德请求相关区域的地图资源。不会读取当前位置，也不会查询路线。"
          : status === "error"
            ? "地图没有加载；搜索候选和文字路线仍可使用。"
            : "地图只显示下方已确认的坐标，不会自动读取当前位置或测算路线。"}
      </p>
      <div className="amap-panel__canvas">
        <div
          ref={containerRef}
          className="amap-panel__map"
          role="img"
          aria-label={
            purpose === "candidate"
              ? "地点候选地图"
              : "路线起终点地图"
          }
        />
        {pickMode && status === "ready" ? (
          <div className="amap-center-pin" aria-hidden="true">
            <MapPin size={34} strokeWidth={2.2} />
            <span />
          </div>
        ) : null}
        {status === "loading" ? (
          <div className="amap-panel__loading">
            <span className="spinner" aria-hidden="true" />
            <span>正在加载地图…</span>
          </div>
        ) : null}
        {status === "error" ? (
          <div className="amap-panel__error" role="alert">
            <TriangleAlert size={18} />
            <span>{error}</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

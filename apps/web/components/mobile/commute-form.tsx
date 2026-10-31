"use client";

/** Editable commute plan form with the same validation as the server schema. */

import { useState } from "react";

import type { CommutePlanDraft } from "@/lib/mobile/api";

const WEEKDAYS = [
  [1, "一"],
  [2, "二"],
  [3, "三"],
  [4, "四"],
  [5, "五"],
  [6, "六"],
  [7, "日"],
] as const;

const DEFAULT_DRAFT: CommutePlanDraft = {
  name: "上班通勤",
  enabled: true,
  timezone:
    typeof Intl !== "undefined"
      ? Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai"
      : "Asia/Shanghai",
  weekdays: [1, 2, 3, 4, 5],
  arrival_local_time: "09:00",
  check_local_time: "07:30",
  transport_mode: "driving",
  buffer_minutes: 10,
  rain_probability_threshold: 0.4,
  rain_extra_buffer_minutes: 10,
  notify_enabled: true,
  origin: { label: "", latitude: 0, longitude: 0 },
  destination: { label: "", latitude: 0, longitude: 0 },
};

function validate(draft: CommutePlanDraft): string | null {
  if (!draft.name.trim()) return "计划名称不能为空。";
  if (!draft.origin.label.trim() || !draft.destination.label.trim()) {
    return "起点和终点都需要名称。";
  }
  if (draft.weekdays.length === 0) return "至少选择一个星期。";
  if (draft.weekdays.some((day) => day < 1 || day > 7)) return "星期取值无效。";
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(draft.arrival_local_time)) return "到达时间格式无效。";
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(draft.check_local_time)) return "检查时间格式无效。";
  if (draft.check_local_time >= draft.arrival_local_time) {
    return "检查时间必须早于到达时间，不支持跨午夜。";
  }
  if (draft.buffer_minutes < 0 || draft.buffer_minutes > 120) return "缓冲时间应在 0–120 分钟。";
  if (
    draft.rain_probability_threshold < 0 ||
    draft.rain_probability_threshold > 1
  ) {
    return "降雨阈值应在 0–1 之间。";
  }
  for (const point of [draft.origin, draft.destination]) {
    if (point.latitude === 0 && point.longitude === 0) {
      return "请手动填写起点和终点的真实经纬度；浏览器不会自动定位，也不会生成占位坐标。";
    }
    if (point.latitude < -90 || point.latitude > 90) return "纬度应在 -90–90。";
    if (point.longitude < -180 || point.longitude > 180) return "经度应在 -180–180。";
  }
  return null;
}

export interface SavedPlace {
  label: string;
  latitude: number;
  longitude: number;
}

export function CommuteForm({
  initial,
  savedPlaces = [],
  submitLabel,
  saving,
  serverError,
  onSubmit,
}: {
  initial?: CommutePlanDraft;
  savedPlaces?: SavedPlace[];
  submitLabel: string;
  saving: boolean;
  serverError: string | null;
  onSubmit: (draft: CommutePlanDraft) => void;
}) {
  const [draft, setDraft] = useState<CommutePlanDraft>(initial ?? DEFAULT_DRAFT);
  const [error, setError] = useState<string | null>(null);
  const [geoBusy, setGeoBusy] = useState<"origin" | "destination" | null>(null);
  const [geoError, setGeoError] = useState<string | null>(null);

  const set = <K extends keyof CommutePlanDraft>(key: K, value: CommutePlanDraft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const useCurrentLocation = (key: "origin" | "destination") => {
    setGeoError(null);
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setGeoError("这个浏览器没有定位能力；仍可使用已保存地点或手动坐标。");
      return;
    }
    setGeoBusy(key);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setDraft((current) => ({
          ...current,
          [key]: {
            city: null,
            coordinate_system: "wgs84",
            label: "当前位置",
            latitude: Number(position.coords.latitude.toFixed(6)),
            longitude: Number(position.coords.longitude.toFixed(6)),
          },
        }));
        setGeoBusy(null);
      },
      () => {
        setGeoBusy(null);
        setGeoError("没有取得当前位置。拒绝定位不影响使用已保存地点或手动填写坐标。");
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 },
    );
  };

  const submit = () => {
    const problem = validate(draft);
    setError(problem);
    if (problem) return;
    onSubmit({
      ...draft,
      name: draft.name.trim(),
      origin: { ...draft.origin, label: draft.origin.label.trim() },
      destination: { ...draft.destination, label: draft.destination.label.trim() },
    });
  };

  return (
    <div>
      <label className="field" htmlFor="commute-name">
        <span className="field__label">计划名称</span>
        <input
          id="commute-name"
          className="input"
          value={draft.name}
          onChange={(event) => set("name", event.target.value)}
          maxLength={120}
        />
      </label>

      <div className="field">
        <span className="field__label">星期</span>
        <div className="chip-row">
          {WEEKDAYS.map(([value, label]) => {
            const active = draft.weekdays.includes(value);
            return (
              <button
                key={value}
                type="button"
                className="chip chip--sm"
                aria-pressed={active}
                onClick={() =>
                  set(
                    "weekdays",
                    active
                      ? draft.weekdays.filter((day) => day !== value)
                      : [...draft.weekdays, value].sort(),
                  )
                }
                style={{ minWidth: 44 }}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex gap-10">
        <label className="field grow" htmlFor="commute-arrival">
          <span className="field__label">到达时间</span>
          <input
            id="commute-arrival"
            className="input"
            type="time"
            value={draft.arrival_local_time}
            onChange={(event) => set("arrival_local_time", event.target.value)}
          />
        </label>
        <label className="field grow" htmlFor="commute-check">
          <span className="field__label">检查时间</span>
          <input
            id="commute-check"
            className="input"
            type="time"
            value={draft.check_local_time}
            onChange={(event) => set("check_local_time", event.target.value)}
          />
        </label>
      </div>

      <div className="flex gap-10">
        <label className="field grow" htmlFor="commute-mode">
          <span className="field__label">交通方式</span>
          <select
            id="commute-mode"
            className="select"
            value={draft.transport_mode}
            onChange={(event) =>
              set("transport_mode", event.target.value as CommutePlanDraft["transport_mode"])
            }
          >
            <option value="driving">驾车</option>
            <option value="transit">公交</option>
            <option value="walking">步行</option>
          </select>
        </label>
        <label className="field grow" htmlFor="commute-buffer">
          <span className="field__label">缓冲（分钟）</span>
          <input
            id="commute-buffer"
            className="input"
            type="number"
            min={0}
            max={120}
            value={draft.buffer_minutes}
            onChange={(event) => set("buffer_minutes", Number(event.target.value))}
          />
        </label>
      </div>

      <div className="flex gap-10">
        <label className="field grow" htmlFor="commute-rain">
          <span className="field__label">降雨阈值（0–1）</span>
          <input
            id="commute-rain"
            className="input"
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={draft.rain_probability_threshold}
            onChange={(event) => set("rain_probability_threshold", Number(event.target.value))}
          />
        </label>
        <label className="field grow" htmlFor="commute-rain-extra">
          <span className="field__label">雨天额外缓冲</span>
          <input
            id="commute-rain-extra"
            className="input"
            type="number"
            min={0}
            max={120}
            value={draft.rain_extra_buffer_minutes}
            onChange={(event) => set("rain_extra_buffer_minutes", Number(event.target.value))}
          />
        </label>
      </div>

      {(["origin", "destination"] as const).map((key) => (
        <div
          key={key}
          style={{ border: 0, padding: 0, margin: "0 0 12px" }}
        >
          <div className="flex items-center justify-between gap-8">
            <span className="field__label" style={{ marginBottom: 0 }}>
              {key === "origin" ? "起点" : "终点"}
            </span>
            <button
              type="button"
              className="link-button link-button--inline"
              onClick={() => useCurrentLocation(key)}
              disabled={geoBusy !== null}
            >
              {geoBusy === key ? "正在定位…" : "使用当前位置"}
            </button>
          </div>

          {savedPlaces.length > 0 ? (
            <div className="mt-6" style={{ marginBottom: 6 }}>
              <span className="micro" style={{ display: "block", marginBottom: 4 }}>
                复用已保存地点
              </span>
              <div className="chip-row">
                {savedPlaces.map((place) => (
                  <button
                    key={`${key}-${place.label}-${place.latitude}-${place.longitude}`}
                    type="button"
                    className="chip chip--sm"
                    onClick={() =>
                      set(key, {
                        label: place.label,
                        latitude: place.latitude,
                        longitude: place.longitude,
                      })
                    }
                  >
                    {place.label}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <p className="field__hint" style={{ marginTop: 4 }}>
              还没有已保存地点；请手动填写，或使用当前位置。
            </p>
          )}

          <label className="field">
            <span className="field__label">城市（公交使用；留空时按坐标查询）</span>
            <input className="input" value={draft[key].city ?? ""}
              onChange={(event) => set(key, { ...draft[key], city: event.target.value.trim() || null })} />
          </label>
          <label className="field" htmlFor={`${key}-label`}>
            <input
              id={`${key}-label`}
              className="input"
              value={draft[key].label}
              placeholder="地点名称（仅你可见）"
              onChange={(event) =>
                set(key, { ...draft[key], label: event.target.value })
              }
            />
          </label>
          <p className="field__hint" style={{ marginTop: -4 }}>
            浏览器没有地图选点，请输入真实坐标。
          </p>
          <div className="flex gap-10">
            <label className="field grow" htmlFor={`${key}-lat`}>
              <span className="field__label">纬度</span>
              <input
                id={`${key}-lat`}
                className="input"
                type="number"
                step="0.000001"
                value={draft[key].latitude}
                onChange={(event) =>
                  set(key, { ...draft[key], latitude: Number(event.target.value) })
                }
              />
            </label>
            <label className="field grow" htmlFor={`${key}-lng`}>
              <span className="field__label">经度</span>
              <input
                id={`${key}-lng`}
                className="input"
                type="number"
                step="0.000001"
                value={draft[key].longitude}
                onChange={(event) =>
                  set(key, { ...draft[key], longitude: Number(event.target.value) })
                }
              />
            </label>
          </div>
        </div>
      ))}

      {geoError ? (
        <p className="caption red" role="status" style={{ margin: "0 0 10px" }}>
          {geoError}
        </p>
      ) : null}

      <button
        type="button"
        className="switch-row"
        onClick={() => set("notify_enabled", !draft.notify_enabled)}
        aria-pressed={draft.notify_enabled}
      >
        <span className="grow" style={{ textAlign: "left" }}>
          <span className="card-title" style={{ display: "block", fontSize: 15 }}>
            到达前提醒
          </span>
          <span className="caption" style={{ display: "block", marginTop: 2 }}>
            由云端调度检查；浏览器关闭后仍可经 Bark 送达（需先绑定）。
          </span>
        </span>
        <span className="switch" aria-checked={draft.notify_enabled} role="switch" />
      </button>

      <div className="notice mt-12">
        <strong>路线与出发时间</strong>
        <p style={{ margin: "4px 0 0" }}>
          填写起终点与坐标后，路线和出发时间会由服务计算；也可以直接手动填写常用地点。
        </p>
      </div>

      {error || serverError ? (
        <p className="caption red" role="alert" style={{ margin: "12px 0 0" }}>
          {error || serverError}
        </p>
      ) : null}

      <button type="button" className="btn btn--primary mt-14" onClick={submit} disabled={saving}>
        {saving ? <span className="spinner" /> : null}
        {saving ? "正在保存…" : submitLabel}
      </button>
    </div>
  );
}

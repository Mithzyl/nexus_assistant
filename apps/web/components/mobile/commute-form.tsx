"use client";

/** Editable commute plan form with the same validation as the server schema. */

import { useState } from "react";
import { commuteModeLabel, commuteModes } from "@/lib/mobile/commute-mode";
import { CommutePlaceInput } from "./commute-place-input";

import type { CommuteLocation, CommutePlanDraft } from "@/lib/mobile/api";

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
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(draft.check_local_time)) return "请填写早上确认路况的时间。";
  if (draft.check_local_time >= draft.arrival_local_time) {
    return "请把早上确认路况的时间设在到达时间之前。";
  }
  if (draft.buffer_minutes < 0 || draft.buffer_minutes > 120) return "备用时间请填 0 到 120 分钟。";
  if (
    draft.rain_probability_threshold < 0 ||
    draft.rain_probability_threshold > 1
  ) {
    return "下雨概率请填 0% 到 100%。";
  }
  for (const point of [draft.origin, draft.destination]) {
    if (point.latitude === 0 && point.longitude === 0) {
      return "请搜索并选择起点和终点，也可以使用当前位置。";
    }
    if (point.latitude < -90 || point.latitude > 90) return "纬度应在 -90–90。";
    if (point.longitude < -180 || point.longitude > 180) return "经度应在 -180–180。";
  }
  return null;
}

export type SavedPlace = CommuteLocation;

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
  const [geoError, setGeoError] = useState<"origin" | "destination" | null>(null);

  const set = <K extends keyof CommutePlanDraft>(key: K, value: CommutePlanDraft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const useCurrentLocation = (key: "origin" | "destination") => {
    setGeoError(null);
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setGeoError(key);
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
        setGeoError(key);
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
          <span className="field__label">每天早上几点帮你确认路况</span>
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
          <span className="field__label" id="commute-mode-label">交通方式</span>
          <select
            id="commute-mode"
            aria-labelledby="commute-mode-label"
            className="select"
            value={draft.transport_mode}
            onChange={(event) =>
              set("transport_mode", event.target.value as CommutePlanDraft["transport_mode"])
            }
          >
            {!commuteModes.includes(draft.transport_mode) ? (
              <option value={draft.transport_mode} disabled>其他出行方式</option>
            ) : null}
            {commuteModes.map((mode) => <option key={mode} value={mode}>{commuteModeLabel(mode)}</option>)}
          </select>
        </label>
        <label className="field grow" htmlFor="commute-buffer">
          <span className="field__label">平时多留几分钟备用</span>
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
          <span className="field__label">下雨概率达到多少就提前出门（%）</span>
          <input
            id="commute-rain"
            className="input"
            type="number"
            min={0}
            max={100}
            step="any"
            value={Number((draft.rain_probability_threshold * 100).toFixed(10))}
            onChange={(event) => set("rain_probability_threshold", Number(event.target.value) / 100)}
          />
        </label>
        <label className="field grow" htmlFor="commute-rain-extra">
          <span className="field__label">下雨天多留几分钟</span>
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
              <div className="chip-row" id={`${key}-saved-places`}>
                {savedPlaces.map((place) => (
                  <button
                    key={`${key}-${place.label}-${place.latitude}-${place.longitude}-${place.coordinate_system ?? ""}`}
                    type="button"
                    className="chip chip--sm"
                    onClick={() => set(key, { ...place })}
                  >
                    {place.label}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <p className="field__hint" style={{ marginTop: 4 }}>
              搜索具体地点，或使用当前位置。
            </p>
          )}

          <CommutePlaceInput id={key} title={key === "origin" ? "起点" : "终点"}
            value={draft[key]} onChange={point => set(key, point)} />
        </div>
      ))}

      {geoError ? (
        <div className="caption" role="status">
          没拿到你的位置，手动搜一下吧
          <div className="action-row">
            <button type="button" className="link-button" onClick={() => document.getElementById(`${geoError}-place`)?.focus()}>搜地点</button>
            {savedPlaces.length ? <button type="button" className="link-button" onClick={() =>
              document.querySelector<HTMLButtonElement>(`#${geoError}-saved-places button`)?.focus()}>用存好的地点</button> : null}
          </div>
        </div>
      ) : null}

      <button
        type="button"
        className="switch-row"
        onClick={() => set("notify_enabled", !draft.notify_enabled)}
        aria-pressed={draft.notify_enabled}
      >
        <span className="grow" style={{ textAlign: "left" }}>
          <span className="card-title" style={{ display: "block", fontSize: 15 }}>
            通勤提醒
          </span>
          <span className="caption" style={{ display: "block", marginTop: 2 }}>
            前一晚发预告、早上发确认，路况有大变化时再提醒。
          </span>
        </span>
        <span className="switch" aria-checked={draft.notify_enabled} role="switch" />
      </button>

      {draft.notify_enabled ? (
        <div className="grid-2 mt-12">
          <label className="field">
            <span className="field__label">前晚预告时间</span>
            <input className="input" type="time" value={draft.evening_notice_local_time ?? "22:00"}
              onChange={(event) => set("evening_notice_local_time", event.target.value || null)} />
          </label>
          <label className="field">
            <span className="field__label">前晚预告方式</span>
            <select className="select" value={draft.evening_notice_level ?? "passive"}
              onChange={(event) => set("evening_notice_level", event.target.value === "active" ? "active" : "passive")}>
              <option value="passive">静默</option>
              <option value="active">普通通知</option>
            </select>
          </label>
        </div>
      ) : null}

      <div className="notice mt-12">
        <strong>路线与出发时间</strong>
        <p style={{ margin: "4px 0 0" }}>
          选好起点和终点，路线和出发时间交给助手测算。
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

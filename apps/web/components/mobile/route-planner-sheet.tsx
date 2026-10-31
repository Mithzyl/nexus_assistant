"use client";

import { useEffect, useRef, useState } from "react";
import { Check, MapPin } from "lucide-react";

import type {
  MatterSchedule,
  PlaceCandidate,
  RouteEstimate,
} from "@/lib/mobile/api";
import { estimateRoute, searchPlaces } from "@/lib/mobile/matters-api";
import { dateTimeLabel } from "@/lib/mobile/work-plan";
import { Sheet } from "./ui";

/** Translate server route reasons into user-facing recovery guidance. */
function routeUnavailableMessage(reasonCode: string | null): string {
  if (reasonCode === "coordinate_system_mismatch") {
    return "起点和目的地的坐标来源不兼容，请重新确认地点。";
  }
  if (reasonCode === "transit_city_required") {
    return "公交路线需要明确城市，请补充城市后再测算。";
  }
  if (reasonCode === "amap_timeout") {
    return "路线服务响应超时，请稍后重试。";
  }
  if (reasonCode === "amap_rate_limited") {
    return "路线查询次数暂时达到服务限制，请稍后再试。";
  }
  if (reasonCode === "route_provider_unavailable") {
    return "路线服务当前不可用，请稍后重试。";
  }
  return "路线服务暂时无法返回结果，请稍后重试。";
}

/** Choose one origin explicitly, then request one server-side route estimate. */
export function RoutePlannerSheet({
  open,
  schedule,
  previousSchedules,
  getPlaceSearchDraft,
  onClose,
  onEstimated,
}: {
  open: boolean;
  schedule: MatterSchedule | null;
  previousSchedules: MatterSchedule[];
  getPlaceSearchDraft: () => Promise<string>;
  onClose: () => void;
  onEstimated: (scheduleId: string, result: RouteEstimate) => Promise<void>;
}) {
  const [originMode, setOriginMode] = useState<"previous" | "place">("previous");
  const [originScheduleId, setOriginScheduleId] = useState("");
  const [query, setQuery] = useState("");
  const [city, setCity] = useState("");
  const [candidates, setCandidates] = useState<PlaceCandidate[]>([]);
  const [selectedToken, setSelectedToken] = useState<string | null>(null);
  const [mode, setMode] = useState<"driving" | "walking" | "transit">("driving");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const searchRequestId = useRef(0);

  const selectedCandidate =
    candidates.find((candidate) => candidate.selection_token === selectedToken) ?? null;
  const previousWithCoordinates = previousSchedules.filter(
    (item) => item.latitude !== null && item.longitude !== null,
  );
  const hasDestinationCoordinates =
    schedule?.latitude !== null &&
    schedule?.latitude !== undefined &&
    schedule.longitude !== null &&
    schedule.longitude !== undefined;

  useEffect(() => {
    if (!open) return;
    setOriginMode("previous");
    setOriginScheduleId("");
    setQuery("");
    setCity(schedule?.location_city ?? "");
    setCandidates([]);
    setSelectedToken(null);
    setMode("driving");
    setBusy(null);
    setError(null);
    searchRequestId.current += 1;
  }, [open, schedule?.schedule_id, schedule?.location_city]);

  const search = async () => {
    if (!query.trim()) {
      setError("请输入起点名称或地址。");
      return;
    }
    setBusy("search");
    setError(null);
    setSelectedToken(null);
    const requestId = ++searchRequestId.current;
    try {
      const draftId = await getPlaceSearchDraft();
      if (requestId !== searchRequestId.current) return;
      const result = await searchPlaces(
        query.trim(),
        city.trim() || null,
        "place",
        draftId,
      );
      if (requestId !== searchRequestId.current) return;
      setCandidates(result.candidates);
      if (result.candidates.length === 0) {
        setError("没有找到候选，请补充城市或更详细的地址。");
      }
    } catch (caught) {
      if (requestId !== searchRequestId.current) return;
      setCandidates([]);
      setError(caught instanceof Error ? caught.message : "起点查询失败。");
    } finally {
      if (requestId === searchRequestId.current) setBusy(null);
    }
  };

  const estimate = async () => {
    if (!schedule || !hasDestinationCoordinates) {
      setError("目的地还没有确认坐标，请先在日程中确认地点。");
      return;
    }
    if (originMode === "previous" && !originScheduleId) {
      setError("请选择上一项安排作为起点。");
      return;
    }
    if (originMode === "place" && !selectedCandidate) {
      setError("请先选择地点候选作为起点。");
      return;
    }
    setBusy("estimate");
    setError(null);
    try {
      const result = await estimateRoute({
        ...(originMode === "previous"
          ? {
              origin_schedule_id: originScheduleId,
              origin_basis: "previous_schedule" as const,
            }
          : {
              origin: {
                latitude: selectedCandidate!.latitude,
                longitude: selectedCandidate!.longitude,
                coordinate_system: selectedCandidate!.coordinate_system,
                city: selectedCandidate!.city,
              },
              origin_basis: "user_confirmed" as const,
            }),
        destination: {
          latitude: schedule.latitude as number,
          longitude: schedule.longitude as number,
          coordinate_system: schedule.coordinate_system,
          city: schedule.location_city,
        },
        mode,
        arrive_by: schedule.starts_at,
        schedule_id: schedule.schedule_id,
      });
      if (result.status !== "available") {
        setError(routeUnavailableMessage(result.reason_code));
        return;
      }
      const expectedOriginBasis =
        originMode === "previous" ? "previous_schedule" : "user_confirmed";
      if (
        result.schedule_id !== schedule.schedule_id ||
        result.origin_basis !== expectedOriginBasis ||
        (originMode === "previous" &&
          result.origin_schedule_id !== originScheduleId)
      ) {
        setError("云端返回的路线关联与当前日程或起点不一致，请刷新后重试。");
        return;
      }
      await onEstimated(schedule.schedule_id, result);
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "路线测算失败。");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Sheet title="补充起点并测算路线" open={open} onClose={onClose}>
      {schedule ? (
        <div className="m-stack-12">
          <div className="card card--tight">
            <p className="card-title" style={{ fontSize: 15 }}>{schedule.title}</p>
            <p className="caption" style={{ margin: "3px 0 0" }}>
              目的地：{schedule.location_address || schedule.location || "尚未确认"} ·{" "}
              {dateTimeLabel(schedule.starts_at)}
            </p>
          </div>

          <div className="chip-row" role="tablist" aria-label="起点方式">
            <button
              type="button"
              className="chip chip--sm"
              aria-pressed={originMode === "previous"}
              onClick={() => {
                setOriginMode("previous");
                setError(null);
              }}
            >
              从上一项安排
            </button>
            <button
              type="button"
              className="chip chip--sm"
              aria-pressed={originMode === "place"}
              onClick={() => {
                setOriginMode("place");
                setError(null);
              }}
            >
              搜索起点
            </button>
          </div>

          {originMode === "previous" ? (
            previousWithCoordinates.length > 0 ? (
              <div className="m-stack-8">
                {previousWithCoordinates.map((item) => (
                  <button
                    key={item.schedule_id}
                    type="button"
                    className={`choice-card${originScheduleId === item.schedule_id ? " choice-card--selected" : ""}`}
                    onClick={() => setOriginScheduleId(item.schedule_id)}
                  >
                    <span className="choice-card__dot">
                      {originScheduleId === item.schedule_id ? <Check size={12} /> : null}
                    </span>
                    <span className="grow">
                      <span className="choice-card__title">{item.title}</span>
                      <span className="choice-card__detail">
                        {item.location_address || item.location || "地点未命名"} ·{" "}
                        {dateTimeLabel(item.ends_at || item.starts_at)}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="caption">没有带坐标的上一项安排，请使用“搜索起点”。</p>
            )
          ) : (
            <>
              <div className="grid-2">
                <label className="field">
                  <span className="field__label">起点名称或地址</span>
                  <input
                    className="input"
                    value={query}
                    onChange={(event) => {
                      setQuery(event.target.value);
                      searchRequestId.current += 1;
                      setCandidates([]);
                      setSelectedToken(null);
                      setError(null);
                      setBusy(null);
                    }}
                    placeholder="从哪里出发"
                  />
                </label>
                <label className="field">
                  <span className="field__label">城市</span>
                  <input
                    className="input"
                    value={city}
                    onChange={(event) => {
                      setCity(event.target.value);
                      searchRequestId.current += 1;
                      setCandidates([]);
                      setSelectedToken(null);
                      setError(null);
                      setBusy(null);
                    }}
                    placeholder="例如：上海"
                  />
                </label>
              </div>
              <button
                type="button"
                className="btn btn--secondary btn--small"
                onClick={() => void search()}
                disabled={busy !== null}
              >
                {busy === "search" ? "正在查找…" : "查找起点候选"}
              </button>
              <div className="m-stack-8" role="radiogroup" aria-label="起点候选">
                {candidates.map((candidate) => (
                  <button
                    key={candidate.provider_place_id}
                    type="button"
                    role="radio"
                    aria-checked={selectedToken === candidate.selection_token}
                    className={`choice-card${selectedToken === candidate.selection_token ? " choice-card--selected" : ""}`}
                    onClick={() => setSelectedToken(candidate.selection_token)}
                  >
                    <span className="choice-card__dot">
                      {selectedToken === candidate.selection_token ? <Check size={12} /> : null}
                    </span>
                    <span className="grow">
                      <span className="choice-card__title">{candidate.name}</span>
                      <span className="choice-card__detail">
                        {[candidate.city, candidate.address].filter(Boolean).join(" · ") || "地址未提供"}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}

          <div>
            <p className="field__label">出行方式</p>
            <div className="chip-row">
              {(
                [
                  ["driving", "驾车"],
                  ["walking", "步行"],
                  ["transit", "公共交通"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  className="chip chip--sm"
                  aria-pressed={mode === value}
                  onClick={() => setMode(value)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {!hasDestinationCoordinates ? (
            <div className="notice notice--amber">
              目的地还没有确认坐标，请先返回日程编辑并确认地点。
            </div>
          ) : null}
          {error ? <p className="danger-text">{error}</p> : null}
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => void estimate()}
            disabled={busy !== null || !hasDestinationCoordinates}
          >
            {busy === "estimate" ? "正在测算…" : "确认起点并测算路线"}
          </button>
          <p className="micro" style={{ margin: 0 }}>
            <MapPin size={12} style={{ display: "inline", verticalAlign: "-2px" }} />{" "}
            只有你选定的起点会发送给云端路线服务，不会自动联系外部地图。
          </p>
        </div>
      ) : null}
    </Sheet>
  );
}

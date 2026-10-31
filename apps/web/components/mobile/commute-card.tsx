"use client";

/** Today's commute summary; only server-provided advice is shown as fact. */

import Link from "next/link";
import { ArrowRight, CloudSun, MapPin, RefreshCw } from "lucide-react";

import type { Advisory, CommuteCheck, CommutePlan, PlanWeather } from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import { formatDateTime, formatDuration, formatTime } from "@/lib/mobile/format";
import { useApiResource } from "@/lib/mobile/use-api-resource";
import {
  advisoryApplicability,
  mockProviders,
  routeState,
  routeUnconfigured,
} from "@/lib/mobile/advisory";
import {
  CHECK_PHASE_LABEL,
  CHECK_STATUS_LABEL,
  loadPendingCheck,
} from "@/lib/mobile/commute-check";
import { useAuth } from "./providers";
import { Pill } from "./ui";

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

function weekdayText(weekdays: number[]): string {
  if (weekdays.length === 5 && [1, 2, 3, 4, 5].every((day) => weekdays.includes(day))) {
    return "周一至周五";
  }
  if (weekdays.length === 7) return "每天";
  return weekdays.map((day) => `周${WEEKDAYS[day - 1]}`).join("、");
}

function qualityLabel(advisory: Advisory): {
  text: string;
  tone: "fog" | "amber" | "red";
} {
  if (advisory.quality === "fresh") return { text: "数据完整", tone: "fog" };
  if (advisory.quality === "degraded") return { text: "数据不完整", tone: "amber" };
  return { text: "暂时无法生成", tone: "amber" };
}

export function TodayCommuteCard() {
  const { state, user } = useAuth();
  const signedIn = state === "signedIn";
  const plans = useApiResource<CommutePlan[]>(
    signedIn ? "/api/pa/commute-plans" : null,
    { enabled: signedIn },
  );
  const plan = plans.data?.[0] ?? null;
  const advisory = useApiResource<Advisory>(
    plan ? `/api/pa/commute-plans/${plan.id}/advisories/latest` : null,
    { enabled: Boolean(plan) },
  );
  const pending =
    plan && user?.user_id ? loadPendingCheck(user.user_id, plan.id) : null;
  const check = useApiResource<CommuteCheck>(
    pending?.checkId ? `/api/pa/checks/${pending.checkId}` : null,
    { enabled: Boolean(pending?.checkId) },
  );
  const latestWeather = useApiResource<PlanWeather>(
    plan ? `/api/pa/commute-plans/${plan.id}/weather` : null,
    { enabled: Boolean(plan) },
  );

  const noAdvisory =
    advisory.error instanceof ApiError &&
    (advisory.error.status === 404 || advisory.error.code === "NO_ADVISORY");
  const data = advisory.data;
  const applicability =
    data && plan ? advisoryApplicability(data, plan.revision) : null;
  const current = applicability?.applicable ? data : null;
  const checkStatus = check.data?.status ?? pending?.status ?? null;
  const unconfigured = data ? routeUnconfigured(data) : false;
  const routeIssue =
    unconfigured ||
    routeState(data) === "unavailable" ||
    (check.data?.error?.code
      ? /(provider_not_configured|eta_not_configured)/i.test(check.data.error.code)
      : false);
  const mocks = data ? mockProviders(data) : [];

  return (
    <section style={{ padding: "0 var(--m-gutter)" }} aria-label="通勤">
      <div className="card card--tight">
        <div className="flex items-center justify-between gap-10">
          <span className="flex items-center gap-6">
            <CloudSun size={15} className="fog" aria-hidden="true" />
            <span className="micro" style={{ color: "var(--m-fog-deep)" }}>
              通勤
            </span>
          </span>
          {current ? (
            <Pill tone={qualityLabel(current).tone}>{qualityLabel(current).text}</Pill>
          ) : applicability && !applicability.applicable ? (
            <Pill tone="neutral">{applicability.label}</Pill>
          ) : null}
        </div>

        {state === "loading" ? (
          <p className="caption mt-10" style={{ marginBottom: 0 }}>
            正在同步账号状态…
          </p>
        ) : null}

        {!signedIn && state !== "loading" ? (
          <>
            <p className="card-title mt-10" style={{ fontSize: 16 }}>
              登录后查看通勤建议
            </p>
            <p className="caption mt-4" style={{ marginBottom: 0 }}>
              登录账号后，这里会显示常用路线、天气与出发建议。
            </p>
            <Link href="/login" className="btn btn--primary btn--small mt-10">
              登录账号
            </Link>
          </>
        ) : null}

        {signedIn && plans.loading ? (
          <div className="mt-10">
            <div className="skeleton" style={{ height: 14, width: "55%" }} />
            <div
              className="skeleton mt-8"
              style={{ height: 12, width: "85%", opacity: 0.7 }}
            />
          </div>
        ) : null}

        {signedIn && plans.error ? (
          <>
            <p className="card-title mt-10" style={{ fontSize: 16 }}>
              暂时无法读取通勤计划
            </p>
            <p className="caption mt-4" style={{ marginBottom: 0 }}>
              {plans.error instanceof ApiError
                ? plans.error.status === 401
                  ? "登录状态已失效，请重新登录。"
                  : plans.error.message
                : "网络不可用。"}
            </p>
            <button type="button" className="link-button mt-4" onClick={plans.reload}>
              <RefreshCw size={13} /> 重试
            </button>
          </>
        ) : null}

        {signedIn && !plans.loading && !plans.error && !plan ? (
          <>
            <p className="card-title mt-10" style={{ fontSize: 16 }}>
              还没有通勤计划
            </p>
            <p className="caption mt-4" style={{ marginBottom: 0 }}>
              可在“问助手”发起通勤安排，确认地点后比较真实驾车与公交路线，也可手动填写计划。
            </p>
            <Link href="/me/commute" className="link-button mt-6">
              新建通勤计划 <ArrowRight size={14} strokeWidth={1.5} />
            </Link>
          </>
        ) : null}

        {signedIn && plan ? (
          <>
            <p className="card-title mt-10" style={{ fontSize: 16 }}>
              {current ? current.title : applicability && !applicability.applicable ? "已有建议不适用于当前计划" : plan.name}
            </p>

            <div className="mt-4" style={{ display: "grid", gap: 3 }}>
              <span className="caption" style={{ margin: 0 }}>
                {weekdayText(plan.weekdays)} · {plan.arrival_local_time} 到达 ·{" "}
                {plan.transport_mode === "driving" ? "驾车" : plan.transport_mode === "transit" ? "公交" : "步行"} · 检查{" "}
                {plan.check_local_time}
              </span>
              <span className="micro flex items-center gap-6" style={{ margin: 0 }}>
                <MapPin size={12} />
                {plan.origin.label} → {plan.destination.label}
              </span>
            </div>

            {checkStatus ? (
              <p className="micro" style={{ margin: "7px 0 0" }}>
                上次重新分析：{CHECK_STATUS_LABEL[checkStatus]}
                {check.data?.phase ? ` · ${CHECK_PHASE_LABEL[check.data.phase]}` : ""}
                {check.data?.finished_at
                  ? ` · 完成于 ${formatDateTime(check.data.finished_at)}`
                  : ""}
                {" · "}
                <Link href={`/me/commute/${plan.id}`} className="link-button link-button--inline">
                  查看进度
                </Link>
              </p>
            ) : null}

            {latestWeather.data?.summary ? (
              <p className="micro" style={{ margin: "5px 0 0", lineHeight: 1.6 }}>
                最新天气：{latestWeather.data.summary.temperature_c !== null
                  ? `${Math.round(latestWeather.data.summary.temperature_c)}°C`
                  : "温度未记录"}
                {latestWeather.data.summary.precipitation_probability_known &&
                latestWeather.data.summary.precipitation_probability_max !== null
                  ? ` · 降雨 ${Math.round(
                      latestWeather.data.summary.precipitation_probability_max * 100,
                    )}%`
                  : " · 降雨概率未知"}
                {" · 获取于 "}
                {formatDateTime(latestWeather.data.fetched_at) || "时间未记录"}
                {latestWeather.data.is_stale ? " · 已过期" : ""}
                {latestWeather.data.matches_current_plan ? "" : " · 较早的设置"}
              </p>
            ) : null}

            {advisory.loading ? (
              <p className="caption mt-10" style={{ marginBottom: 0 }}>
                正在更新通勤建议…
              </p>
            ) : null}

            {!advisory.loading && current ? (
              <>
                <p className="caption mt-8" style={{ marginBottom: 0 }}>
                  {current.summary}
                </p>
                {current.recommended_departure_at ? (
                  <div className="flex items-center gap-10 mt-10" style={{ flexWrap: "wrap" }}>
                    <span
                      className="tabular"
                      style={{ fontSize: 22, fontWeight: 600, color: "var(--m-fog-deep)" }}
                    >
                      {formatTime(current.recommended_departure_at)}
                    </span>
                    <span className="caption" style={{ margin: 0 }}>
                      建议出发 · 目标 {formatTime(current.target_arrival_at)} 到达
                      {current.eta_seconds ? ` · ${formatDuration(current.eta_seconds)}` : ""}
                    </span>
                  </div>
                ) : null}
                <p className="micro mt-6" style={{ marginBottom: 0 }}>
                  结果：{applicability?.label} · 更新于{" "}
                  {formatDateTime(current.generated_at)} · 有效至{" "}
                  {formatDateTime(current.expires_at)}
                </p>
              </>
            ) : null}

            {!advisory.loading && !current && applicability ? (
              <div className="mt-10">
                <p className="caption" style={{ margin: 0 }}>
                  有一条较早的通勤建议，但{applicability.label === "已过期" ? "已经过期" : applicability.label === "修改前结果" ? "属于修改前的设置" : applicability.label === "历史建议" ? "是历史建议" : "当前没有可用结果"}，暂时不能作为今天的出发时间。
                </p>
                <p className="micro" style={{ margin: "4px 0 0" }}>
                  {applicability.detail}
                </p>
                <p className="micro" style={{ margin: "4px 0 0" }}>
                  更新于 {formatDateTime(data!.generated_at)} · 有效至{" "}
                  {formatDateTime(data!.expires_at)} · 可在详情历史区查看。
                </p>
              </div>
            ) : null}

            {!advisory.loading && !advisory.data && !advisory.error ? (
              <p className="caption mt-8" style={{ marginBottom: 0 }}>
                今天还没有生成建议。
              </p>
            ) : null}
            {!advisory.loading && noAdvisory ? (
              <p className="caption mt-8" style={{ marginBottom: 0 }}>
                今天还没有生成建议。可以打开详情重新分析。
              </p>
            ) : null}
            {!advisory.loading && advisory.error && !noAdvisory ? (
              <p className="caption mt-8" style={{ marginBottom: 0 }}>
                暂时无法读取建议；计划时间仍在下面。
              </p>
            ) : null}

            {routeIssue ? (
              <p className="micro amber" style={{ margin: "7px 0 0" }}>
                {unconfigured
                  ? "路线服务尚未开通；已有或历史建议仍可查看。"
                  : "当前无法查询新路线；已有或历史建议仍可在详情中查看。"}
              </p>
            ) : null}
            {mocks.length > 0 ? (
              <p className="micro amber" style={{ margin: "4px 0 0" }}>
                当前为示例数据，不是实时路况
              </p>
            ) : null}

            <div className="flex items-center justify-between gap-10 mt-10">
              <Link href={`/me/commute/${plan.id}`} className="link-button link-button--inline">
                查看详情与历史建议
              </Link>
              <Link href="/me/commute" className="link-button link-button--inline">
                通勤设置
              </Link>
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}

/** Presentation helpers for server-returned commute advice provenance. */

import type { Advisory } from "./api";

export const AI_REASON_LABEL: Record<string, string> = {
  route_snapshot_missing: "缺少路线快照",
  route_provider_unconfigured: "路线服务尚未配置",
  ai_nexus_not_configured: "服务端未配置 AI 分析",
  ai_timeout: "AI 分析超时，结果未确认",
  ai_workflow_failed: "AI 工作流失败",
  ai_upstream_error: "AI 服务调用异常",
  ai_output_invalid_json: "AI 输出不是严格 JSON",
  ai_duration_not_from_snapshot: "AI 采用的地图耗时不在路线快照中",
  ai_departure_arithmetic_invalid: "AI 计算的出发时间与算术不一致",
  ai_duration_source_invalid: "AI 的耗时来源与字段不一致",
  ai_extra_buffer_invalid: "AI 的额外预留无效",
  ai_route_reference_invalid: "AI 引用了不存在的路线",
  ai_result_empty: "AI 没有返回可读结果",
  account_missing: "账号不存在",
};

export function routeSources(advisory: Advisory) {
  return advisory.sources.filter((source) => source.kind === "route");
}

export function hasUnavailableRoute(advisory: Advisory): boolean {
  const routes = routeSources(advisory);
  if (routes.length === 0) return true;
  return routes.every(
    (source) =>
      source.freshness === "unavailable" ||
      source.provider === "unavailable" ||
      source.provider === "none",
  );
}

export function routeProviderLabel(advisory: Advisory): string {
  const aiSource = advisory.ai_analysis?.route_source;
  if (aiSource === "server_provider") return "服务器路线查询";
  if (aiSource === "client_snapshot") return "客户端路线快照";
  if (aiSource === "none") return "没有可用路线";
  const routes = routeSources(advisory);
  if (routes.length === 0) return "未记录路线来源";
  if (routes.every((source) => source.provider === "unavailable")) return "路线不可用";
  return routes
    .map((source) => source.provider)
    .filter((value, index, all) => all.indexOf(value) === index)
    .join(" / ");
}

export function routeUnconfigured(advisory: Advisory | null, checkError: string | null = null): boolean {
  if (checkError && /(provider_not_configured|eta_not_configured)/i.test(checkError)) return true;
  return advisory?.ai_analysis?.reason === "route_provider_unconfigured";
}

export function historyUsesSnapshot(advisory: Advisory): boolean {
  if (advisory.ai_analysis?.route_source) return false;
  const text = (advisory.ai_analysis?.basis ?? []).join(" ");
  return /route_snapshot|provider=(stub|mock)|mapkit_client/i.test(text);
}

export function mockProviders(advisory: Advisory): string[] {
  const names = advisory.sources
    .map((source) => source.provider)
    .filter((provider) => /mock|stub/i.test(provider));
  if (advisory.sources.some((source) => /example\.invalid/i.test(source.attribution?.url ?? ""))) {
    if (!names.includes("mock")) names.push("mock");
  }
  return names.filter((value, index, all) => all.indexOf(value) === index);
}

export function aiStatus(advisory: Advisory): {
  label: string;
  tone: "green" | "amber" | "red" | "fog" | "neutral";
  available: boolean;
} {
  const ai = advisory.ai_analysis;
  if (!ai) {
    return {
      label:
        advisory.explanation_source === "model"
          ? "模型文案（缺少分析详情）"
          : "仅模板文案",
      tone: "neutral",
      available: false,
    };
  }
  if (ai.status === "available" && advisory.explanation_source === "model") {
    return { label: "模型分析（服务端校验）", tone: "green", available: true };
  }
  if (ai.status === "available") {
    return { label: "分析可用（非模型文案）", tone: "fog", available: true };
  }
  if (ai.status === "rejected") {
    return { label: "模型分析被拒绝", tone: "red", available: false };
  }
  return { label: "模型分析未完成", tone: "amber", available: false };
}

export function aiReasonText(advisory: Advisory): string | null {
  const reason = advisory.ai_analysis?.reason;
  if (!reason) return null;
  return AI_REASON_LABEL[reason] || `AI 分析不可用（${reason}）`;
}

export function durationSourceLabel(source: string | null | undefined): string {
  if (source === "duration_in_traffic_seconds") return "含交通耗时";
  if (source === "duration_seconds") return "基础路线耗时";
  return "未记录来源";
}

export type AdvisoryApplicability = {
  applicable: boolean;
  label: string;
  detail: string;
  tone: "green" | "amber" | "red" | "neutral";
};

/**
 * Applicability is derived only from server-stored fields and the current plan.
 * It never repairs missing history fields or turns an old snapshot into a new one.
 */
export function advisoryApplicability(
  advisory: Advisory,
  planRevision: number,
  now = Date.now(),
): AdvisoryApplicability {
  const expires = Date.parse(advisory.expires_at);
  if (Number.isFinite(expires) && expires <= now) {
    return {
      applicable: false,
      label: "已过期",
      detail: "已超过服务器给出的有效期，不适用于当前出行；服务器记录仍保留。",
      tone: "neutral",
    };
  }
  if (advisory.plan_revision !== planRevision) {
    return {
      applicable: false,
      label: "旧计划版本",
      detail: `建议属于计划 revision ${advisory.plan_revision}，当前为 revision ${planRevision}，不能当作当前计划的结果。`,
      tone: "neutral",
    };
  }
  if (advisory.quality === "unavailable") {
    return {
      applicable: false,
      label: "没有可用结果",
      detail: "服务器保存了记录，但这次没有生成可用的路线/天气结论。",
      tone: "amber",
    };
  }
  if (routeUnconfigured(advisory)) {
    return {
      applicable: true,
      label: "无新路线结论",
      detail: "服务器未配置路线 Provider；建议只反映服务器保存的旧快照/天气，不代表新的路线查询。",
      tone: "amber",
    };
  }
  return {
    applicable: true,
    label: "在有效期内",
    detail: "这是服务器保存、仍在有效期内的建议，可作为出行参考。",
    tone: "green",
  };
}

export type RouteState = "server_provider" | "client_snapshot" | "unavailable" | "unknown";

export function routeState(advisory: Advisory | null): RouteState {
  if (!advisory) return "unknown";
  const source = advisory.ai_analysis?.route_source;
  if (source === "server_provider") return "server_provider";
  if (source === "client_snapshot") return "client_snapshot";
  if (source === "none") return "unavailable";
  const routes = routeSources(advisory);
  if (routes.length === 0) return "unknown";
  if (
    routes.every(
      (route) =>
        route.freshness === "unavailable" ||
        route.provider === "unavailable" ||
        route.provider === "none",
    )
  ) {
    return "unavailable";
  }
  return "unknown";
}

export function isExpired(advisory: Advisory, now = Date.now()): boolean {
  const expires = Date.parse(advisory.expires_at);
  return Number.isFinite(expires) && expires <= now;
}

/** Evidence that a saved advisory used a client-produced route snapshot. */
export function usesClientSnapshot(advisory: Advisory): boolean {
  return (
    advisory.ai_analysis?.route_source === "client_snapshot" ||
    historyUsesSnapshot(advisory)
  );
}

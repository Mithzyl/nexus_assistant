/** Cross-site write protection for cookie-authenticated route handlers. */

import type { NextRequest } from "next/server";

import { errorResponse } from "./server";

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * Reject browser writes whose Origin host does not match this deployment.
 *
 * SameSite=Lax already keeps most cross-site POSTs from carrying cookies; this
 * is the explicit second check. Requests without an Origin header (curl,
 * service-to-service) are allowed; browsers send Origin on cross-site writes.
 */
export function sameOriginFailure(request: NextRequest): Response | null {
  if (!MUTATING.has(request.method.toUpperCase())) return null;

  if (request.headers.get("sec-fetch-site") === "cross-site") {
    return errorResponse(403, "cross_site_request_blocked", "跨站写入已被拒绝。");
  }

  const origin = request.headers.get("origin");
  if (!origin) return null;

  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return errorResponse(403, "cross_site_request_blocked", "请求来源无效。");
  }

  const host =
    request.headers.get("x-forwarded-host")?.split(",")[0]?.trim() ||
    request.headers.get("host");
  if (!host) {
    return errorResponse(403, "cross_site_request_blocked", "无法确认请求来源。");
  }
  if (originHost !== host) {
    return errorResponse(403, "cross_site_request_blocked", "跨站写入已被拒绝。");
  }
  return null;
}

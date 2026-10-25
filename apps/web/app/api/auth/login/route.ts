/**
 * Login endpoint: exchanges local PA credentials for an httpOnly session cookie.
 *
 * The opaque PA token is never returned to JavaScript; the browser only receives
 * the identity and the server-confirmed expiry.
 */

import { NextRequest, NextResponse } from "next/server";

import {
  errorResponse,
  paFetch,
  sessionCookieOptions,
  upstreamError,
} from "@/lib/mobile/server";
import { sameOriginFailure } from "@/lib/mobile/origin";
import { normalizeUtcTimestamp } from "@/lib/mobile/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface LoginBody {
  email?: unknown;
  password?: unknown;
}

interface PaLoginResponse {
  token: string;
  expires_at: string;
  server_time: string;
  user_id: string;
}

interface PaMeResponse {
  user_id: string;
  email: string;
  display_name: string;
}

export async function POST(request: NextRequest) {
  const crossSite = sameOriginFailure(request);
  if (crossSite) return crossSite;

  let body: LoginBody;
  try {
    body = (await request.json()) as LoginBody;
  } catch {
    return errorResponse(400, "invalid_request", "请求格式无效。");
  }
  const email = typeof body.email === "string" ? body.email.trim() : "";
  const password = typeof body.password === "string" ? body.password : "";
  if (email.length < 3 || password.length < 1) {
    return errorResponse(422, "invalid_request", "请填写邮箱和密码。");
  }

  let upstream: Response;
  try {
    upstream = await paFetch(
      "/auth/login",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      },
      "",
    );
  } catch {
    return errorResponse(502, "web_upstream_unavailable", "无法连接 PA 服务器。", true);
  }

  if (!upstream.ok) {
    const detail = await upstreamError(upstream);
    return errorResponse(detail.status, detail.code, detail.message, detail.retryable);
  }

  const session = (await upstream.json()) as PaLoginResponse;
  const meResponse = await paFetch("/auth/me", {}, session.token);
  if (!meResponse.ok) {
    return errorResponse(502, "web_upstream_unavailable", "登录已受理，但身份读取失败。", true);
  }
  const me = (await meResponse.json()) as PaMeResponse;
  const expiresAt = normalizeUtcTimestamp(session.expires_at);
  if (!expiresAt) {
    return errorResponse(502, "invalid_session_response", "登录已受理，但会话期限无效。", true);
  }
  const cookies = sessionCookieOptions(expiresAt, session.token);
  const response = NextResponse.json({
    user: me,
    expires_at: expiresAt,
    expires_in: Math.max(0, Math.floor((Date.parse(expiresAt) - Date.now()) / 1000)),
  });
  response.cookies.set(cookies.token);
  response.cookies.set(cookies.expires);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Vary", "Cookie");
  return response;
}

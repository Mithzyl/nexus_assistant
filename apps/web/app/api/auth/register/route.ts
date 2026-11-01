/**
 * Registration endpoint: exchanges local PA credentials for an httpOnly session cookie.
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

interface RegistrationBody {
  email?: unknown;
  password?: unknown;
  display_name?: unknown;
  invite_code?: unknown;
}

interface PaRegistrationResponse {
  user: PaMeResponse;
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

/** Create an account upstream and expose only its identity and cookie-backed session. */
export async function POST(request: NextRequest) {
  const crossSite = sameOriginFailure(request);
  if (crossSite) return crossSite;

  let body: RegistrationBody;
  try {
    const value: unknown = await request.json();
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return errorResponse(400, "invalid_request", "请求格式无效。");
    }
    body = value as RegistrationBody;
  } catch {
    return errorResponse(400, "invalid_request", "请求格式无效。");
  }
  const email = typeof body.email === "string" ? body.email.trim() : "";
  const password = typeof body.password === "string" ? body.password : "";
  if (email.length < 3 || password.length < 1) {
    return errorResponse(422, "invalid_request", "请填写邮箱和密码。");
  }

  const display_name = typeof body.display_name === "string" ? body.display_name.trim() : "";
  const invite_code = typeof body.invite_code === "string" ? body.invite_code : "";
  if (!display_name || password.length < 8) {
    return errorResponse(422, "invalid_registration", "请填写称呼，密码至少需要8个字符。");
  }
  let upstream: Response;
  try {
    upstream = await paFetch(
      "/auth/register",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, display_name, invite_code }),
      },
      "",
    );
  } catch {
    // A lost response does not prove that account creation failed; never retry here.
    return errorResponse(502, "web_upstream_unavailable", "未能确认注册结果，请先尝试登录。");
  }

  if (!upstream.ok) {
    const detail = await upstreamError(upstream);
    return errorResponse(detail.status, detail.code, detail.message, detail.retryable);
  }

  let session: PaRegistrationResponse;
  try {
    session = (await upstream.json()) as PaRegistrationResponse;
    if (!session?.token || !session.user?.user_id || !session.expires_at) {
      return errorResponse(502, "invalid_session_response", "注册已受理，但登录状态无效，请尝试登录。");
    }
  } catch {
    return errorResponse(502, "invalid_session_response", "注册已受理，但登录状态无法读取，请尝试登录。");
  }
  const me = session.user;
  const expiresAt = normalizeUtcTimestamp(session.expires_at);
  if (!expiresAt) {
    return errorResponse(502, "invalid_session_response", "账号已创建，但会话期限无效，请尝试登录。");
  }
  const cookies = sessionCookieOptions(expiresAt, session.token);
  const response = NextResponse.json({
    user: me,
    expires_at: expiresAt,
    expires_in: Math.max(0, Math.floor((Date.parse(expiresAt) - Date.now()) / 1000)),
  }, { status: 201 });
  response.cookies.set(cookies.token);
  response.cookies.set(cookies.expires);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Vary", "Cookie");
  return response;
}

/** Read public registration settings without sending an existing account token. */
export async function GET() {
  try {
    const upstream = await paFetch("/auth/register", {}, "");
    if (!upstream.ok) {
      const detail = await upstreamError(upstream);
      return errorResponse(detail.status, detail.code, detail.message, detail.retryable);
    }
    return NextResponse.json(await upstream.json(), { headers: { "Cache-Control": "no-store" } });
  } catch {
    return errorResponse(502, "web_upstream_unavailable", "暂时无法读取注册设置，请稍后重试。", true);
  }
}

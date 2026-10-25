/**
 * Shared session checks for the auth route handlers.
 *
 * Only the known credential-failure codes clear the cookie. Transport failures
 * keep the token so an offline browser can recover, exactly like the iOS store.
 */

import type { ResponseCookies } from "next/dist/compiled/@edge-runtime/cookies";

import { paFetch, readExpiresAt, readToken, sessionCookieOptions } from "./server";

const INVALID_CODES = new Set([
  "invalid_session",
  "session_expired",
  "session_revoked",
  "account_disabled",
  "unauthenticated",
]);

export interface SessionUser {
  user_id: string;
  email: string;
  display_name: string;
}

export interface MeResponse extends SessionUser {}

/**
 * Normalize a PA timestamp before using it for a cookie deadline.
 *
 * `/auth/session/renew` currently returns a naive UTC timestamp without `Z`
 * (login does include it). `Date.parse` would read that as browser-local time
 * and shorten the cookie by the timezone offset. Naive values from PA are UTC;
 * append `Z` and normalize to a single ISO form.
 */
export function normalizeUtcTimestamp(value: string | null | undefined): string | null {
  if (!value) return null;
  const text = value.trim();
  if (!text) return null;
  const hasZone = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(text);
  const parsed = new Date(hasZone ? text : `${text}Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

type Failure = {
  ok: false;
  status: number;
  code: string;
  message: string;
  retryable: boolean;
  clear: boolean;
};

type Success<T> = { ok: true; body: T; expiresAt?: string };

export function invalidSessionCodes(): Set<string> {
  return INVALID_CODES;
}

/** Verify the cookie against `/auth/me`; never extends the deadline locally. */
export async function expiringSession(): Promise<
  Success<{ user: MeResponse; expires_at: string | null }> | Failure
> {
  const token = readToken();
  if (!token) {
    return {
      ok: false,
      status: 401,
      code: "unauthenticated",
      message: "请先登录。",
      retryable: false,
      clear: false,
    };
  }

  let upstream: Response;
  try {
    upstream = await paFetch("/auth/me", {}, token);
  } catch {
    return {
      ok: false,
      status: 502,
      code: "web_upstream_unavailable",
      message: "无法连接 PA 服务器，已保留本机登录状态。",
      retryable: true,
      clear: false,
    };
  }

  if (!upstream.ok) {
    let code = `http_${upstream.status}`;
    let message = "登录状态暂时无法验证。";
    let retryable = upstream.status >= 500;
    try {
      const body = (await upstream.json()) as {
        error?: { code?: string; message?: string; retryable?: boolean };
      };
      code = body.error?.code || code;
      message = body.error?.message || message;
      retryable = body.error?.retryable ?? retryable;
    } catch {
      // Keep generic copy.
    }
    return {
      ok: false,
      status: upstream.status,
      code,
      message,
      retryable,
      clear: INVALID_CODES.has(code),
    };
  }

  const user = (await upstream.json()) as MeResponse;
  return {
    ok: true,
    body: { user, expires_at: readExpiresAt() },
    expiresAt: readExpiresAt() || undefined,
  };
}

/** Renew and return the server-confirmed deadline; never invents one. */
export async function renewCurrentSession(): Promise<
  Success<{ user: MeResponse; expires_at: string }> | Failure
> {
  const token = readToken();
  if (!token) {
    return {
      ok: false,
      status: 401,
      code: "unauthenticated",
      message: "请先登录。",
      retryable: false,
      clear: false,
    };
  }

  let upstream: Response;
  try {
    upstream = await paFetch("/auth/session/renew", { method: "POST" }, token);
  } catch {
    return {
      ok: false,
      status: 502,
      code: "web_upstream_unavailable",
      message: "无法连接 PA 服务器，本机登录状态保持不变。",
      retryable: true,
      clear: false,
    };
  }

  if (!upstream.ok) {
    let code = `http_${upstream.status}`;
    let message = "会话续期失败。";
    let retryable = upstream.status >= 500;
    try {
      const body = (await upstream.json()) as {
        error?: { code?: string; message?: string; retryable?: boolean };
      };
      code = body.error?.code || code;
      message = body.error?.message || message;
      retryable = body.error?.retryable ?? retryable;
    } catch {
      // Keep generic copy.
    }
    return {
      ok: false,
      status: upstream.status,
      code,
      message,
      retryable,
      clear: INVALID_CODES.has(code),
    };
  }

  const body = (await upstream.json()) as {
    user: MeResponse;
    expires_at: string;
    server_time?: string;
    token_rotated?: boolean;
  };
  const expiresAt = normalizeUtcTimestamp(body.expires_at);
  if (!expiresAt) {
    return {
      ok: false,
      status: 502,
      code: "invalid_session_response",
      message: "服务器返回的会话期限无效，本机登录状态保持不变。",
      retryable: true,
      clear: false,
    };
  }
  return {
    ok: true,
    body: { user: body.user, expires_at: expiresAt },
    expiresAt,
  };
}

/** Apply the renewed deadline to the existing httpOnly cookies. */
export function applyRenewedCookies(
  jar: ResponseCookies,
  expiresAt: string,
  token: string,
) {
  const cookies = sessionCookieOptions(expiresAt, token);
  jar.set(cookies.token);
  jar.set(cookies.expires);
}

/** Delete both session cookies. */
export function clearSessionCookies(jar: ResponseCookies) {
  for (const name of ["pa_session", "pa_expires"]) {
    jar.set({
      name,
      value: "",
      path: "/",
      maxAge: 0,
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    });
  }
}

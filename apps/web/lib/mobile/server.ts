/**
 * Server-only helpers for talking to the Personal Assistant API.
 *
 * The browser never sees the PA session token: it lives in an httpOnly cookie
 * and every upstream call is made from a Next.js route handler. This mirrors
 * the iOS Bearer contract without storing a bearer token in JavaScript.
 */

import { cookies } from "next/headers";

export const SESSION_COOKIE = "pa_session";
export const EXPIRES_COOKIE = "pa_expires";

const SESSION_MAX_AGE_FALLBACK = 60 * 60 * 24;

/** Resolve the PA service base URL; defaults to the documented local port. */
export function paBaseUrl(): string {
  return (process.env.PA_API_URL || "http://127.0.0.1:8010").replace(/\/+$/, "");
}

/** Read the opaque session token from the request cookies. */
export function readToken(): string | null {
  const value = cookies().get(SESSION_COOKIE)?.value;
  return value && value.length > 0 ? value : null;
}

/** Read the server-provided expiry instant cached at login/renew time. */
export function readExpiresAt(): string | null {
  return cookies().get(EXPIRES_COOKIE)?.value || null;
}

/** Build the cookie attributes for a session; token stays httpOnly. */
export function sessionCookieOptions(expiresAt: string, token: string) {
  const deadline = Date.parse(expiresAt);
  const seconds = Number.isFinite(deadline)
    ? Math.max(0, Math.floor((deadline - Date.now()) / 1000))
    : SESSION_MAX_AGE_FALLBACK;
  return {
    token: {
      name: SESSION_COOKIE,
      value: token,
      httpOnly: true,
      sameSite: "lax" as const,
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: seconds,
    },
    expires: {
      name: EXPIRES_COOKIE,
      value: expiresAt,
      httpOnly: true,
      sameSite: "lax" as const,
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: seconds,
    },
  };
}

/**
 * One upstream fetch with the caller's bearer token.
 *
 * `trustEnv` is disabled by the caller through `redirect: "manual"` semantics:
 * we only ever target localhost or an explicitly configured PA origin.
 */
export async function paFetch(
  path: string,
  init: RequestInit = {},
  token: string,
): Promise<Response> {
  const url = `${paBaseUrl()}/api/v1${path.startsWith("/") ? path : `/${path}`}`;
  return fetch(url, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      ...(init.headers || {}),
    },
  });
}

/** Return a JSON error envelope with the shape the PA API uses. */
export function errorResponse(
  status: number,
  code: string,
  message: string,
  retryable = false,
): Response {
  return new Response(
    JSON.stringify({ error: { code, message, retryable } }),
    {
      status,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    },
  );
}

/** Read the PA error message without leaking an HTML error page. */
export async function upstreamError(upstream: Response): Promise<{
  status: number;
  code: string;
  message: string;
  retryable: boolean;
}> {
  let code = "upstream_error";
  let message = "云端暂时无法处理该请求。";
  let retryable = upstream.status >= 500;
  try {
    const body = (await upstream.json()) as {
      error?: { code?: string; message?: string; retryable?: boolean };
      detail?: string;
    };
    code = body.error?.code || code;
    message = body.error?.message || body.detail || message;
    retryable = body.error?.retryable ?? retryable;
  } catch {
    // Non-JSON upstream bodies are deliberately not echoed to the browser.
  }
  return { status: upstream.status, code, message, retryable };
}

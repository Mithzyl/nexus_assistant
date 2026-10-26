/**
 * Same-origin PA proxy.
 *
 * The browser calls `/api/pa/...`, which forwards to `assistant-api` with the
 * httpOnly session token as a Bearer header. Only documented PA path groups are
 * proxied, so this cannot become an open relay. SSE bodies are piped unbuffered.
 */

import { NextRequest } from "next/server";

import { errorResponse, paBaseUrl, readToken } from "@/lib/mobile/server";
import { sameOriginFailure } from "@/lib/mobile/origin";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED_ROOTS = new Set([
  "asks",
  "memory",
  "data-sources",
  "content",
  "commute-plans",
  "checks",
  "advisories",
  "commute",
  "matters",
  "today",
  "reminders",
]);

const FORWARDED_HEADERS = [
  "content-type",
  "accept",
  "idempotency-key",
  "if-match",
  "last-event-id",
];

const SEGMENT = /^[A-Za-z0-9._~-]{1,128}$/;

async function proxy(request: NextRequest, segments: string[]) {
  const crossSite = sameOriginFailure(request);
  if (crossSite) return crossSite;

  if (segments.length === 0 || !ALLOWED_ROOTS.has(segments[0])) {
    return errorResponse(404, "not_found", "请求的接口不存在。");
  }
  if (segments.some((segment) => !SEGMENT.test(segment) || segment === "." || segment === "..")) {
    return errorResponse(400, "invalid_request", "请求路径无效。");
  }

  const token = readToken();
  if (!token) {
    return errorResponse(401, "unauthenticated", "请先登录 PA 账号。");
  }

  const search = request.nextUrl.search || "";
  const target = `${paBaseUrl()}/api/v1/${segments.map(encodeURIComponent).join("/")}${search}`;
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    Accept: request.headers.get("accept") || "application/json",
  };
  for (const name of FORWARDED_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers[name] = value;
  }

  const method = request.method.toUpperCase();
  let body: ArrayBuffer | undefined;
  if (!["GET", "HEAD"].includes(method)) {
    body = await request.arrayBuffer();
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
      signal: request.signal,
    });
  } catch {
    return errorResponse(
      502,
      "web_upstream_unavailable",
      "无法连接 PA 服务器，请检查服务是否启动。",
      true,
    );
  }

  const responseHeaders = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType) responseHeaders.set("Content-Type", contentType);
  const requestId = upstream.headers.get("x-request-id");
  if (requestId) responseHeaders.set("X-Request-ID", requestId);
  const retryAfter = upstream.headers.get("retry-after");
  if (retryAfter) responseHeaders.set("Retry-After", retryAfter);
  responseHeaders.set("Cache-Control", "no-store");
  if (method === "HEAD") {
    return new Response(null, { status: upstream.status, headers: responseHeaders });
  }
  if (contentType?.includes("text/event-stream")) {
    responseHeaders.set("X-Accel-Buffering", "no");
    responseHeaders.set("Connection", "keep-alive");
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export async function GET(
  request: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return proxy(request, params.path);
}

export async function POST(
  request: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return proxy(request, params.path);
}

export async function PUT(
  request: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return proxy(request, params.path);
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return proxy(request, params.path);
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return proxy(request, params.path);
}

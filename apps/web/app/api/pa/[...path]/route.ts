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
  "browser-notifications",
  "asks",
  "memory",
  "data-sources",
  "content",
  "image-attachments",
  "commute-plans",
  "checks",
  "advisories",
  "commute",
  "places",
  "matters",
  "todos",
  "schedules",
  "follow-ups",
  "today",
  "reminders",
  "calendar-subscription",
  "sync",
]);

const FORWARDED_HEADERS = [
  "content-type",
  "accept",
  "idempotency-key",
  "if-match",
  "last-event-id",
];

const SEGMENT = /^[A-Za-z0-9._~-]{1,128}$/;

/** Forward authorized calls, rejecting URL query strings on sensitive place lookup routes. */
async function proxy(request: NextRequest, segments: string[]) {
  const crossSite = sameOriginFailure(request);
  if (crossSite) return crossSite;

  const nextActionEndpoint = segments[0] === "next-actions" && segments.length === 2 &&
    request.method === "POST" && (segments[1] === "query" || segments[1] === "commands");
  if (!nextActionEndpoint && (segments.length === 0 || !ALLOWED_ROOTS.has(segments[0]))) {
    return errorResponse(404, "not_found", "请求的接口不存在。");
  }
  if (segments.some((segment) => !SEGMENT.test(segment) || segment === "." || segment === "..")) {
    return errorResponse(400, "invalid_request", "请求路径无效。");
  }

  const placeLookup = segments[0] === "places" ||
    (segments[0] === "commute" && segments[1] === "places");
  if (placeLookup && request.nextUrl.search) {
    return errorResponse(422, "unexpected_query_parameters", "地点查询参数必须放在 JSON 请求体中。");
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
  const isImageUpload = method === "POST" && segments.length === 1 && segments[0] === "image-attachments";
  const requestBody: RequestInit & { duplex?: "half" } = {};
  if (isImageUpload && request.body) {
    const matterId = request.headers.get("x-matter-id");
    if (matterId) headers["x-matter-id"] = matterId;
    requestBody.body = request.body;
    requestBody.duplex = "half";
  } else if (!["GET", "HEAD"].includes(method)) {
    requestBody.body = await request.arrayBuffer();
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      ...requestBody,
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
  if (segments[0] === "image-attachments") {
    for (const name of ["cache-control", "content-disposition", "content-security-policy",
      "cross-origin-resource-policy", "x-content-type-options"]) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
  }
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

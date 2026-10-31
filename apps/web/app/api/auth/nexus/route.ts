/** Optional Nexus login: PKCE stays in an HttpOnly cookie, then PA issues its own session. */
import { randomBytes, createHash, timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { paFetch, readToken, sessionCookieOptions, errorResponse, upstreamError } from "@/lib/mobile/server";
import { sameOriginFailure } from "@/lib/mobile/origin";
import { normalizeUtcTimestamp } from "@/lib/mobile/session";

export const dynamic = "force-dynamic";
const FLOW_COOKIE = "pa_nexus_login";
const flowOptions = {httpOnly: true, sameSite: "lax" as const,
  secure: process.env.NODE_ENV === "production", path: "/api/auth/nexus", maxAge: 300};

/** Query safe availability without revealing application credentials. */
export async function GET() {
  try {
    const upstream = await paFetch("/auth/nexus/options", {}, "");
    if (!upstream.ok) return errorResponse(503, "nexus_unavailable", "Nexus 登录暂不可用。");
    const body = await upstream.json();
    return NextResponse.json({enabled: body.enabled === true}, {headers:{"Cache-Control":"no-store"}});
  } catch { return errorResponse(503, "nexus_unavailable", "Nexus 登录暂不可用。"); }
}

/** Start a browser-bound flow, explicit account link, or consume the callback once. */
export async function POST(request: NextRequest) {
  const rejected = sameOriginFailure(request);
  if (rejected) return rejected;
  let body;
  try { body = await request.json(); } catch { return errorResponse(400, "invalid_request", "请求无效。"); }
  try {
    if (body.action === "link") {
      const token = readToken();
      if (!token) return errorResponse(401, "login_required", "请先使用助手账号登录。");
      const upstream = await paFetch("/auth/nexus/link", {method:"POST"}, token);
      if (!upstream.ok) { const e = await upstreamError(upstream); return errorResponse(e.status,e.code,e.message); }
      return NextResponse.json(await upstream.json(), {headers:{"Cache-Control":"no-store"}});
    }
    if (body.action === "start") {
      const upstream = await paFetch("/auth/nexus/options", {}, "");
      if (!upstream.ok) return errorResponse(503, "nexus_unavailable", "Nexus 登录暂不可用。");
      const config = await upstream.json();
      if (!config.enabled) return errorResponse(403, "nexus_disabled", "暂未启用 Nexus 登录。");
      const state = randomBytes(24).toString("base64url");
      const verifier = randomBytes(32).toString("base64url");
      const url = new URL(config.authorization_url);
      url.search = new URLSearchParams({application_id:config.application_id,
        redirect_uri:config.callback_url, state, response_mode:"fragment",
        code_challenge:createHash("sha256").update(verifier).digest("base64url")}).toString();
      const result = NextResponse.json({url:url.toString()}, {headers:{"Cache-Control":"no-store"}});
      result.cookies.set(FLOW_COOKIE, JSON.stringify({state,verifier,expires:Date.now()+300000}), flowOptions);
      return result;
    }
    if (body.action !== "exchange" || typeof body.state !== "string" || typeof body.code !== "string"
        || body.state.length > 256 || body.code.length < 32 || body.code.length > 128) {
      return errorResponse(400, "invalid_request", "登录回调无效，请重新发起。");
    }
    let flow;
    try { flow = JSON.parse(request.cookies.get(FLOW_COOKIE)?.value ?? "null"); } catch { flow = null; }
    if (!flow || !Number.isFinite(flow.expires) || flow.expires < Date.now()
      || typeof flow.verifier !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(flow.verifier)
      || typeof flow.state !== "string"
      || Buffer.byteLength(flow.state) !== Buffer.byteLength(body.state)
      || !timingSafeEqual(Buffer.from(flow.state),Buffer.from(body.state))) {
      return errorResponse(400, "invalid_login_state", "登录请求已失效，请重新发起。");
    }
    const upstream = await paFetch("/auth/nexus/exchange", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({code:body.code,code_verifier:flow.verifier})}, "");
    if (!upstream.ok) {
      const e = await upstreamError(upstream);
      const result = NextResponse.json({error:{code:e.code,message:e.message}}, {status:e.status});
      result.cookies.set(FLOW_COOKIE,"",{...flowOptions,maxAge:0});
      result.headers.set("Cache-Control","no-store");
      return result;
    }
    const session = await upstream.json();
    const expires = normalizeUtcTimestamp(session.expires_at);
    if (!expires || typeof session.token !== "string") return errorResponse(502,"invalid_session","会话响应无效。");
    const result = NextResponse.json({ok:true}, {headers:{"Cache-Control":"no-store"}});
    const cookies = sessionCookieOptions(expires,session.token);
    result.cookies.set(cookies.token); result.cookies.set(cookies.expires);
    result.cookies.set(FLOW_COOKIE,"",{...flowOptions,maxAge:0});
    return result;
  } catch { return errorResponse(502, "nexus_unavailable", "登录或关联尚未确认，请重新发起。", true); }
}

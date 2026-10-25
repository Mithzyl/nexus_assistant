/**
 * Logout: revoke the presented PA session, then clear local cookies regardless.
 *
 * Local sign-out is still reported honestly when the remote revoke fails, so the
 * UI can say "本机已退出，服务器撤销未确认".
 */

import { NextRequest, NextResponse } from "next/server";

import { clearSessionCookies } from "@/lib/mobile/session";
import { paFetch, readToken } from "@/lib/mobile/server";
import { sameOriginFailure } from "@/lib/mobile/origin";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const crossSite = sameOriginFailure(request);
  if (crossSite) return crossSite;

  const token = readToken();
  let revoked = false;
  if (token) {
    try {
      const upstream = await paFetch("/auth/logout", { method: "POST" }, token);
      revoked = upstream.status === 204 || upstream.ok;
    } catch {
      revoked = false;
    }
  }
  const response = NextResponse.json({ revoked });
  clearSessionCookies(response.cookies);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Vary", "Cookie");
  return response;
}

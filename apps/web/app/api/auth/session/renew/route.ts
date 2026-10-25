/**
 * Session renewal. The token is not rotated by the current PA contract, so the
 * cookie value stays the same and only the server-confirmed deadline moves.
 */

import { NextRequest, NextResponse } from "next/server";

import {
  applyRenewedCookies,
  clearSessionCookies,
  renewCurrentSession,
} from "@/lib/mobile/session";
import { readToken } from "@/lib/mobile/server";
import { sameOriginFailure } from "@/lib/mobile/origin";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const crossSite = sameOriginFailure(request);
  if (crossSite) return crossSite;

  const result = await renewCurrentSession();
  if (!result.ok) {
    const response = NextResponse.json(
      { error: { code: result.code, message: result.message, retryable: result.retryable } },
      { status: result.status },
    );
    if (result.clear) clearSessionCookies(response.cookies);
    return response;
  }
  const response = NextResponse.json(result.body);
  const token = readToken();
  if (token) applyRenewedCookies(response.cookies, result.body.expires_at, token);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Vary", "Cookie");
  return response;
}

/**
 * Session check used on cold start and after the tab becomes visible again.
 *
 * A known credential failure clears the cookie; transport failures keep it.
 */

import { NextResponse } from "next/server";

import { clearSessionCookies, expiringSession } from "@/lib/mobile/session";
import { readToken } from "@/lib/mobile/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  // A missing cookie is a normal "anonymous" answer, not an error event.
  if (!readToken()) {
    const response = NextResponse.json({ user: null, expires_at: null });
    response.headers.set("Cache-Control", "no-store");
    response.headers.set("Vary", "Cookie");
    return response;
  }
  const result = await expiringSession();
  if (!result.ok) {
    const response = NextResponse.json(
      { error: { code: result.code, message: result.message, retryable: result.retryable } },
      { status: result.status },
    );
    if (result.clear) clearSessionCookies(response.cookies);
    response.headers.set("Cache-Control", "no-store");
    response.headers.set("Vary", "Cookie");
    return response;
  }
  const response = NextResponse.json(result.body);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Vary", "Cookie");
  return response;
}

import assert from "node:assert/strict";
import { test } from "node:test";
import { loginDestination, loginDestinationFromSearch, loginHref } from "../lib/mobile/login-return.mjs";

/** Follow the login link through encoding and decoding as a browser would. */
function roundTrip(path) {
  const loginUrl = new URL(loginHref(path), "https://assistant.test");
  assert.equal(loginUrl.pathname, "/login");
  return loginDestinationFromSearch(loginUrl.search);
}

test("retains the complete commute or reply URL", () => {
  for (const path of [
    "/me/commute/plan?from=shortcut#history",
    "/ask?reply=record&return=%2Ftoday%3Ftab%3Dall#question",
    "/me/memories#new-memory",
  ]) {
    assert.equal(roundTrip(path), path);
  }
});

test("falls back to Today for a missing, external or ambiguous return target", () => {
  assert.equal(loginDestinationFromSearch(""), "/today");
  for (const path of [
    null, "", "ask", "https://other.test/", "//other.test/", "/\\other.test/",
    "/\n/other.test", "/%2fother.test", "/%255cother.test", "/safe/..//other.test/",
    "/login?next=/ask", "/nexus-callback/",
  ]) {
    assert.equal(loginDestination(path), "/today", JSON.stringify(path));
  }
});

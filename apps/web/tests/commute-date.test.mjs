import assert from "node:assert/strict";
import test from "node:test";
import { linkedCommuteDate } from "../lib/mobile/commute-date.mjs";

/** A Bark link opens the original service day, including dates in the past. */
test("commute links select a valid exact service date", () => {
  assert.equal(linkedCommuteDate(["2024-02-29"], () => "2026-11-27"), "2024-02-29");
});

/** Missing, repeated or impossible dates preserve the plan's default selection. */
test("commute links fall back for missing or invalid service dates", () => {
  for (const values of [[], ["2026-02-29"], ["2026-11-31"], ["2026-2-01"],
    ["0000-01-01"], ["https://example.test"], ["2026-11-27", "2026-11-28"]]) {
    assert.equal(linkedCommuteDate(values, () => "2026-11-27"), "2026-11-27");
  }
});

/**
 * Local, per-account view of server advisories this browser has actually loaded.
 * PA has no advisory-history list endpoint, so this is intentionally not a
 * substitute server history: it never invents records and never deletes them.
 */

import type { Advisory } from "./api";
import { readJson, writeJson } from "./storage";

const PREFIX = "pa.web.advisory.history.";

type Entry = { cachedAt: string; advisory: Advisory };

export function advisoryHistoryKey(accountId: string, planId: string): string {
  return `${PREFIX}${accountId}.${planId}`;
}

export function loadAdvisoryHistory(accountId: string | null, planId: string): Advisory[] {
  if (!accountId) return [];
  const entries = readJson<Entry[]>(advisoryHistoryKey(accountId, planId), []);
  if (!Array.isArray(entries)) return [];
  return entries
    .filter((entry) => entry && entry.advisory && typeof entry.advisory.id === "string")
    .map((entry) => entry.advisory)
    .sort((a, b) => Date.parse(b.generated_at) - Date.parse(a.generated_at));
}

export function rememberAdvisory(
  accountId: string | null,
  planId: string,
  advisory: Advisory,
): Advisory[] {
  if (!accountId) return [];
  const key = advisoryHistoryKey(accountId, planId);
  const current = readJson<Entry[]>(key, []);
  const entries = Array.isArray(current) ? current : [];
  const next: Entry[] = [
    { cachedAt: new Date().toISOString(), advisory },
    ...entries.filter((entry) => entry?.advisory?.id !== advisory.id),
  ].slice(0, 10);
  writeJson(key, next);
  return next.map((entry) => entry.advisory);
}

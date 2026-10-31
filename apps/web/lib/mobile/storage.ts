/** Browser persistence helpers. All keys are namespaced by account. */

import { uuid } from "./format";

const INSTALL_KEY = "pa.web.installation.id";

function safeGet(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** One stable browser installation id; used only for device-local scopes. */
export function installationId(): string {
  if (typeof window === "undefined") return "";
  const existing = safeGet(INSTALL_KEY);
  if (existing) return existing;
  const created = uuid();
  try {
    window.localStorage.setItem(INSTALL_KEY, created);
  } catch {
    // Private mode without storage: the id stays in-memory for this page.
  }
  return created;
}

/** Namespaced key; `guest` keeps signed-out local data separate. */
export function accountKey(base: string, accountId: string | null): string {
  return `${base}.${accountId || "guest"}`;
}

export function readJson<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function writeJson(key: string, value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Quota or private mode: the in-memory state remains authoritative.
  }
}

export function removeKey(key: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Ignore.
  }
}

/** Session-scoped draft storage (cleared when the tab closes). */
export function readSession<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function writeSession(key: string, value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Ignore.
  }
}

const COMMUTE_CHECK_PREFIX = "pa.web.commute.check.";
const LAST_ACCOUNT_KEY = "pa.web.lastAccount";

/** Remember the account whose caches may need clearing after an expired cookie. */
export function rememberAccount(accountId: string | null): void {
  if (!accountId) return;
  writeJson(LAST_ACCOUNT_KEY, accountId);
}

export function readLastAccount(): string | null {
  return readJson<string | null>(LAST_ACCOUNT_KEY, null);
}

export function forgetLastAccount(): void {
  removeKey(LAST_ACCOUNT_KEY);
}

/**
 * Drop UI caches that belong to one account on logout or confirmed expiry.
 *
 * Locally saved user content ("交给助手整理/随手记一条") is intentionally kept; it is
 * user data, not account/session cache, and remains keyed by account.
 */
export function clearAccountCaches(accountId: string | null): void {
  if (!accountId || typeof window === "undefined") return;
  removeKey(accountKey("pa.web.asks", accountId));
  try {
    const prefix = `${COMMUTE_CHECK_PREFIX}${accountId}.`;
    const keys: string[] = [];
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (key && key.startsWith(prefix)) keys.push(key);
    }
    keys.forEach((key) => window.localStorage.removeItem(key));
  } catch {
    // Ignore storage failures in private mode.
  }
  try {
    window.sessionStorage.removeItem("pa.web.ask.composer");
    window.sessionStorage.removeItem("pa.web.ask.draft");
  } catch {
    // Ignore.
  }
}

/** Persist/read one plan's re-analysis operation. */
export function commuteCheckKey(accountId: string | null, planId: string): string {
  return `${COMMUTE_CHECK_PREFIX}${accountId || "guest"}.${planId}`;
}

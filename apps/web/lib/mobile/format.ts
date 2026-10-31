/** Small formatting helpers shared by mobile screens. */

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

/** `10月24日 周六` from the current local date. */
export function todayLabel(date = new Date()): string {
  return `${date.getMonth() + 1}月${date.getDate()}日 ${WEEKDAYS[date.getDay()]}`;
}

/** `10月24日 周六` from a server `YYYY-MM-DD` service date. */
export function formatServiceDate(value: string | null | undefined, fallback = new Date()): string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return todayLabel(fallback);
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return `${date.getMonth() + 1}月${date.getDate()}日 ${WEEKDAYS[date.getDay()]}`;
}

/** Persisted service dates stay ISO for the API, not for display. */
export function timezoneLabel(value: string | null | undefined): string | null {
  if (!value) return null;
  if (value === "Asia/Shanghai") return "北京时间 (GMT+8)";
  const city = value.split("/").pop()?.replaceAll("_", " ");
  return city ? `当地时间 · ${city}` : value;
}

/** `2026年10月24日` for an explicit service date. */
export function longDate(date: Date): string {
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

/** `10月24日 19:30` in the browser timezone, or empty for invalid input. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${date.getMonth() + 1}月${date.getDate()}日 ${date.getHours()}:${minutes}`;
}

/** `19:30`. */
export function formatTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

/** `2026-10-24` in the browser timezone. */
export function isoDate(value: Date = new Date()): string {
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${value.getFullYear()}-${month}-${day}`;
}

/** `3 分钟前` / `2 小时前` / `10月23日`. */
export function relativeTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const diff = Date.now() - date.getTime();
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return formatDateTime(value);
}

/** `约 26 分钟` from seconds; null stays null (never invented). */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `约 ${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `约 ${hours} 小时` : `约 ${hours} 小时 ${rest} 分`;
}

/** `¥129.00` only when the server supplied a decimal string. */
export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const text = typeof value === "number" ? value.toFixed(2) : value;
  return `¥${text}`;
}

/** `crypto.randomUUID` with a non-secure-context fallback for old browsers. */
export function uuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const random = (Math.random() * 16) | 0;
    const value = char === "x" ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

/** Stable short hash used for idempotent note external ids. */
export async function sha256Hex(text: string): Promise<string> {
  if (typeof crypto !== "undefined" && crypto.subtle) {
    const bytes = new TextEncoder().encode(text);
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  }
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

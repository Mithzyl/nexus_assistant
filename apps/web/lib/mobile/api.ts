/**
 * Browser-side PA client contract.
 *
 * All calls go through the same-origin `/api/pa` proxy (session cookie auth) or
 * `/api/auth` routes. Types mirror `services/assistant-api`; no identity field
 * is ever sent in a request body.
 */

export type RunStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "unknown";

export interface Me {
  user_id: string;
  email: string;
  display_name: string;
}

export interface LoginResult {
  expires_at: string;
  expires_in: number;
  user: Me;
}

export interface SessionInfo {
  user: Me | null;
  expires_at: string | null;
}

export interface AskRead {
  local_run_id: string;
  nexus_run_id: string | null;
  workflow_execution_id: string | null;
  status: RunStatus;
  replayed: boolean;
  request_text: string;
  last_event_sequence: number;
  error_code: string | null;
  answer: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface AskCreated {
  local_run_id: string;
  nexus_run_id: string | null;
  workflow_execution_id: string | null;
  status: RunStatus;
  replayed: boolean;
}

export interface DataSource {
  source_id: string;
  source_type: "calendar" | "reminders" | "notes";
  device_id: string;
  display_name: string;
  sync_enabled: boolean;
  assistant_enabled: boolean;
  upload_fields: string[];
  scope_keys: string[];
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_error_code: string | null;
  item_count: number;
}

export interface ContentItem {
  item_id: string;
  source_id: string;
  source_type: DataSource["source_type"];
  source_display_name: string;
  kind: "calendar_event" | "reminder" | "note";
  external_id: string;
  scope_key: string;
  version: number;
  title: string;
  body: string;
  starts_at: string | null;
  ends_at: string | null;
  due_at: string | null;
  status: string | null;
  location: string | null;
  tags: string[];
  source_updated_at: string;
  synced_at: string;
}

export interface ContentList {
  items: ContentItem[];
  next_cursor: string | null;
}

export interface SyncResult {
  batch_id: string;
  created: number;
  updated: number;
  unchanged: number;
  removed: number;
  last_sync_at: string;
}

export interface CommuteLocation {
  label: string;
  latitude: number;
  longitude: number;
}

export interface CommutePlan {
  id: string;
  name: string;
  enabled: boolean;
  timezone: string;
  weekdays: number[];
  arrival_local_time: string;
  check_local_time: string;
  transport_mode: "driving" | "walking";
  buffer_minutes: number;
  rain_probability_threshold: number;
  rain_extra_buffer_minutes: number;
  notify_enabled: boolean;
  origin: CommuteLocation;
  destination: CommuteLocation;
  revision: number;
  created_at: string;
  updated_at: string;
}

export type CommutePlanDraft = Omit<
  CommutePlan,
  "id" | "revision" | "created_at" | "updated_at"
>;

export type CheckStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "degraded"
  | "failed"
  | "cancelled";

export interface CommuteCheck {
  check_id: string;
  plan_id: string;
  status: CheckStatus;
  trigger: "manual" | "auto";
  plan_revision: number;
  advisory_id: string | null;
  advisory_created: boolean;
  error: { code: string; message: string; retryable: boolean } | null;
  created_at: string;
  finished_at: string | null;
}

export interface AdvisorySource {
  kind: "weather" | "route";
  provider: string;
  fetched_at: string | null;
  valid_from: string | null;
  valid_to: string | null;
  freshness: "fresh" | "stale" | "unavailable";
  attribution: { text: string; url: string } | null;
  request_depart_at: string | null;
  target_arrival_at: string | null;
  note: string | null;
}

export interface WeatherSummary {
  forecast_start: string | null;
  forecast_end: string | null;
  temperature_c: number | null;
  precipitation_probability_max: number | null;
  precipitation_probability_known: boolean;
  rain_probability_threshold: number;
  rain_risk: boolean | null;
  coverage: "full" | "partial" | "unavailable";
}

export interface Advisory {
  id: string;
  plan_id: string;
  plan_revision: number;
  local_service_date: string;
  generated_at: string;
  expires_at: string;
  quality: "fresh" | "degraded" | "unavailable";
  title: string;
  summary: string;
  target_arrival_at: string;
  recommended_departure_at: string | null;
  departure_status: "upcoming" | "leave_now" | "past_due" | "unknown";
  eta_seconds: number | null;
  buffer_seconds: number;
  weather_extra_buffer_seconds: number;
  weather: WeatherSummary | null;
  sources: AdvisorySource[];
  reason_codes: string[];
  explanation_source: "template" | "model";
  ai_analysis: {
    status: "available" | "unavailable" | "rejected";
    route_source?: "client_snapshot" | "server_provider" | "none" | null;
    used_duration_seconds: number | null;
    duration_source: string;
    map_duration_seconds: number | null;
    base_buffer_seconds?: number | null;
    additional_buffer_seconds?: number | null;
    extra_buffer_seconds: number | null;
    recommended_departure_at: string | null;
    basis: string[];
    missing_information: string[];
    reason: string | null;
  } | null;
}

export interface BarkStatus {
  configured: boolean;
  auto_reminders_enabled: boolean;
  available: boolean;
  state: "unconfigured" | "disabled" | "enabled" | string;
  base_url_host: string;
  last_test_status: string | null;
  last_test_at: string | null;
}

export interface BarkTest {
  request_submitted: boolean;
  status: "accepted" | "failed" | "unknown";
  accepted: boolean;
  message: string;
  tested_at: string;
}

export interface MemoryHit {
  memory_id: string;
  memory_type: string;
  content: string;
  source_reference: string | null;
  score: number | null;
}

export interface MemoryResults {
  items: MemoryHit[];
  next_cursor: string | null;
}

/** Stable error envelope parsed from the PA API or the local proxy. */
export class ApiError extends Error {
  status: number;
  code: string;
  retryable: boolean;
  retryAfter: number | null;

  constructor(
    status: number,
    code: string,
    message: string,
    retryable = false,
    retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.retryAfter = retryAfter;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/** One same-origin request; throws `ApiError` for non-2xx responses. */
export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: {
      Accept: "application/json",
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    credentials: "same-origin",
    cache: "no-store",
    signal: options.signal,
  });

  if (!response.ok) {
    let code = `http_${response.status}`;
    let message = "请求未能完成。";
    let retryable = response.status >= 500;
    try {
      const body = (await response.json()) as {
        error?: { code?: string; message?: string; retryable?: boolean };
      };
      code = body.error?.code || code;
      message = body.error?.message || message;
      retryable = body.error?.retryable ?? retryable;
    } catch {
      // Keep the generic message for non-JSON failures.
    }
    const retryHeader = response.headers.get("Retry-After");
    const retryAfter = retryHeader ? Number(retryHeader) : null;
    throw new ApiError(
      response.status,
      code,
      message,
      retryable,
      Number.isFinite(retryAfter) ? retryAfter : null,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** PA path prefix for the proxy: `/api/pa` + path. */
export function pa(path: string): string {
  return `/api/pa${path.startsWith("/") ? path : `/${path}`}`;
}

/** Login through the server route so the token can be stored httpOnly. */
export function login(email: string, password: string) {
  return apiRequest<LoginResult>("/api/auth/login", {
    method: "POST",
    body: { email, password },
  });
}

/** Ask the server to verify the cookie and return the local identity. */
export function fetchSession() {
  return apiRequest<SessionInfo>("/api/auth/me");
}

/** Renew the current session; returns the server-confirmed deadline. */
export function renewSession() {
  return apiRequest<LoginResult>("/api/auth/session/renew", { method: "POST" });
}

/** Revoke the server-side session and clear the cookie. */
export function logoutSession() {
  return apiRequest<{ revoked: boolean }>("/api/auth/logout", { method: "POST" });
}

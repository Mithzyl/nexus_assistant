import type { CommuteBundleState } from "@/components/mobile/commute-bundle-panel";
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

export interface ShortcutToken {
  token_id: string;
  source_id: string;
  display_name: string;
  enabled: boolean;
  last_used_at: string | null;
  created_at: string;
  revoked_at: string | null;
}

export interface ShortcutTokenCreated extends ShortcutToken {
  credential: string;
  credential_returned_once: true;
}

export interface CalendarSubscription {
  subscription_id: string;
  enabled: boolean;
  matter_ids: string[];
  last_read_at: string | null;
  created_at: string;
  updated_at: string;
  feed_url: string | null;
  link_returned_once: boolean;
  note: string | null;
}

export interface CalendarSubscriptionStatus {
  exists: boolean;
  subscription: CalendarSubscription | null;
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
  transport_mode: "driving" | "walking" | "transit";
  effective_from?: string | null;
  matter_id?: string | null;
  first_schedule_id?: string | null;
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
  "id" | "revision" | "created_at" | "updated_at" | "matter_id" | "first_schedule_id"
>;

export type CheckStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "degraded"
  | "failed"
  | "cancelled"
  | "unknown";

export type CheckPhase =
  | "queued"
  | "fetching_route"
  | "fetching_weather"
  | "analyzing"
  | "saving"
  | "retry_wait"
  | "done"
  | "failed"
  | "cancelled";

export interface CommuteCheck {
  check_id: string;
  plan_id: string;
  status: CheckStatus;
  phase: CheckPhase | null;
  analysis_mode: "full" | "weather_only" | null;
  phase_updated_at: string | null;
  attempt_count: number | null;
  next_attempt_at: string | null;
  trigger: "manual" | "auto";
  plan_revision: number;
  advisory_id: string | null;
  advisory_created: boolean;
  error: { code: string; message: string; retryable: boolean } | null;
  created_at: string;
  finished_at: string | null;
}

/** Server process liveness; it is not the progress of one check. */
export interface CommuteRuntime {
  /** Server clock at response time; used to age heartbeats on the client. */
  server_time: string;
  scheduler: { state: "online" | "stale" | "not_seen"; last_seen_at: string | null };
  worker: { state: "online" | "stale" | "not_seen"; last_seen_at: string | null };
  stale_after_seconds: number;
  queue: { queued?: number; running?: number };
  latest_check: {
    check_id: string;
    status: CheckStatus;
    phase: CheckPhase | null;
    phase_updated_at: string | null;
  } | null;
  last_progress_at: string | null;
  route_provider_available: boolean;
  auto_analysis_enabled: boolean;
  auto_analysis_mode: "full" | "weather_only";
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

/** Latest stored provider weather; never borrowed for an older advisory. */
export interface PlanWeather {
  plan_id: string;
  plan_revision: number;
  local_service_date: string;
  check_id: string;
  provider: string;
  fetched_at: string | null;
  valid_from: string | null;
  valid_to: string | null;
  quality: "fresh" | "degraded" | "unavailable";
  freshness: "fresh" | "stale" | "unavailable";
  coverage: "full" | "partial" | "unavailable";
  summary: WeatherSummary | null;
  stale_after_seconds: number;
  is_stale: boolean;
  matches_current_plan: boolean;
  current_plan_revision: number;
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
    status: "available" | "unavailable" | "rejected" | "unknown";
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
  /** Read-time annotations added 2026-10-25; optional for cached older records. */
  matches_current_plan?: boolean;
  is_current_service_date?: boolean;
  is_expired?: boolean;
  current_plan_revision?: number | null;
}

export type AskClassification = "chat" | "not_chat" | "unknown";

export interface AskVerifyItem {
  local_run_id: string;
  classification: AskClassification;
}

export interface AskVerifyResponse {
  results: AskVerifyItem[];
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
  status?: string | null;
  version?: number | null;
  sensitive?: boolean;
  app_data?: { namespace: "personal_assistant"; schema_version: 1; fields: Record<string, unknown> } | null;
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

/* Matters, drafts, today and reminders (backend/dev 3e01ac1a) ---------------- */

export type MatterGroup = "active" | "waiting" | "archived" | "all";
export type MatterStatus = "active" | "waiting" | "archived" | "deleted";
export type MatterScheduleSource = "web_native" | "ios_eventkit" | "ios_reminders";
export type MatterSyncStatus = "local_only" | "pending_ios" | "ios_synced";
export type MatterTodoStatus = "open" | "done";
/** Server reminder states; `due` means it fired but the user has not acknowledged it. */
export type MatterReminderStatus = "scheduled" | "due" | "acknowledged" | "cancelled";

export interface MatterSummary {
  archive_effect?: { cancelled_schedules: number; retained_schedules: number; readonly_schedules: number } | null;
  matter_id: string;
  title: string;
  goal: string;
  status: MatterStatus;
  version: number;
  next_step: string | null;
  last_progress_at: string | null;
  latest_progress: string | null;
  open_todos: number;
  created_at: string;
  updated_at: string;
}

export interface MatterListResponse {
  items: MatterSummary[];
  next_cursor: string | null;
}

export interface MatterSchedule {
  schedule_id: string;
  matter_id: string | null;
  title: string;
  starts_at: string;
  ends_at: string | null;
  location: string | null;
  latitude: number | null;
  longitude: number | null;
  coordinate_system: "gcj02" | "wgs84" | "unknown";
  location_city: string | null;
  location_address: string | null;
  location_source: string | null;
  location_external_id: string | null;
  status: string;
  source: MatterScheduleSource | null;
  sync_status: MatterSyncStatus | null;
  deleted_at: string | null;
}

export interface MatterTodo {
  todo_id: string;
  title: string;
  status: MatterTodoStatus;
  position: number;
  due_at: string | null;
  reminder_at: string | null;
  completed_at: string | null;
}

export interface MatterProgress {
  progress_id: string;
  text: string;
  kind: string;
  created_at: string;
}

export interface MatterReminder {
  reminder_id: string;
  matter_id: string | null;
  todo_id: string | null;
  schedule_id: string | null;
  title: string;
  note: string | null;
  trigger_at: string;
  status: MatterReminderStatus;
  sync_status: MatterSyncStatus | null;
  source: MatterScheduleSource | null;
  completed_at: string | null;
  acknowledged_at: string | null;
  cancelled_at: string | null;
  deleted_at?: string | null;
}

export interface MatterDetail extends MatterSummary {
  schedules: MatterSchedule[];
  todos: MatterTodo[];
  progress: MatterProgress[];
  reminders: MatterReminder[];
}

export interface TodayRoute {
  status: "available" | "unverified" | "unavailable";
  reason_code: string | null;
  message: string | null;
  advisory_id: string | null;
  duration_seconds: number | null;
  mode: "driving" | "walking" | "transit" | null;
  fetched_at: string | null;
  origin_basis: "user_confirmed" | "previous_schedule" | null;
}

export interface TodaySchedule extends MatterSchedule {
  matter_title: string | null;
  route: TodayRoute;
}

export interface TodayReminder extends MatterReminder {
  matter_title: string | null;
}

export interface TodayDeadline {
  todo_id: string;
  matter_id: string;
  matter_title: string;
  title: string;
  due_at: string;
  status: MatterTodoStatus;
  reminder_at: string | null;
}

export interface TodayDraft {
  draft_id: string;
  matter_id: string | null;
  matter_title: string | null;
  kind: "create" | "update";
  version: number;
  updated_at: string;
  schedule: DraftSchedule | null;
  conflict: MatterConflict | null;
  impact: string;
}

export interface TodayChange {
  change_id: string;
  matter_id: string;
  matter_title: string | null;
  change_type: string;
  created_at: string;
  summary: string;
}

export interface TodayView {
  date: string;
  timezone: string;
  schedules: TodaySchedule[];
  reminders: TodayReminder[];
  pending_drafts: number;
  deadlines: TodayDeadline[];
  drafts: TodayDraft[];
  changes: TodayChange[];
}

export interface MatterUpdateInput {
  expected_version: number;
  title?: string;
  goal?: string;
  status?: "active" | "waiting";
  next_step?: string;
}

export interface MatterVersionInput {
  cancel_future_schedules?: boolean;
  expected_version: number;
  cancel_reminders?: boolean;
}

export interface DraftSchedule {
  schedule_id?: string | null;
  title: string;
  starts_at: string;
  ends_at?: string | null;
  location?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  coordinate_system?: "gcj02" | "wgs84" | "unknown";
  location_city?: string | null;
  location_address?: string | null;
  location_source?: string | null;
  location_external_id?: string | null;
}

export interface DraftTodo {
  todo_id?: string | null;
  title: string;
  due_at?: string | null;
  reminder_at?: string | null;
  status?: MatterTodoStatus;
}

export interface DraftRelatedSchedule {
  kind: "existing" | "unverified" | string;
  schedule_id?: string | null;
  matter_id?: string | null;
  title: string;
  starts_at?: string | null;
  ends_at?: string | null;
  location?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  coordinate_system?: "gcj02" | "wgs84" | "unknown";
  location_city?: string | null;
  location_address?: string | null;
  location_source?: string | null;
  location_external_id?: string | null;
}

export interface DraftReminderCancellation {
  reminder_id: string;
  title: string;
  trigger_at: string;
  base_updated_at?: string | null;
  reason?: string | null;
}

/** One future schedule the stop-following preview may cancel; read-only copies stay unselected. */
export interface DraftScheduleCancellation {
  schedule_id: string;
  title: string;
  starts_at: string;
  ends_at: string | null;
  location: string | null;
  selected: boolean;
  cancellable: boolean;
  reason: string | null;
  base_updated_at: string;
}

export interface DraftAction {
  kind: string;
  role?: string | null;
  schedule_id?: string | null;
  todo_id?: string | null;
  reminder_id?: string | null;
  title?: string | null;
  starts_at?: string | null;
  due_at?: string | null;
  reminder_at?: string | null;
  trigger_at?: string | null;
  location?: string | null;
  status?: string | null;
  reason?: string | null;
}

export interface DraftCommunication {
  reply_text: string;
  option_id: string;
  target_title: string;
  target_starts_at: string | null;
  external_change_required: boolean;
  external_change_status: "not_sent" | "pending_external_confirmation";
}

export interface DraftPayload {
  commute?: CommuteBundleState;
  commute_revision?: {status:string; target_arrival_at?:string; buffer_minutes?:number; route?:RouteEstimate};
  commute_plan_cancellations?: {plan_id:string;name:string;revision:number}[];
  kind?: "create" | "update";
  matter_id?: string | null;
  requires_choice?: boolean;
  title?: string | null;
  goal?: string | null;
  changes_note?: string | null;
  next_step?: string | null;
  schedule?: DraftSchedule | null;
  todos?: DraftTodo[];
  complete_todo_ids?: string[];
  selected_option?: "A" | "B" | "C" | null;
  previous_adjustment?: DraftPreviousAdjustment | null;
  affected?: MatterAffectedSchedule[];
  related_schedules?: DraftRelatedSchedule[];
  matter_action?: "none" | "archive";
  proposed_status?: "archived" | null;
  cancel_reminders?: boolean;
  reminder_cancellations?: DraftReminderCancellation[];
  schedule_cancellations?: DraftScheduleCancellation[];
  action_plan?: DraftAction[];
  communication_draft?: DraftCommunication | null;
  source_text?: string | null;
  analysis?: Record<string, unknown> | null;
}

export interface MatterAffectedSchedule {
  type: "schedule" | string;
  matter_id: string | null;
  matter_title: string | null;
  schedule_id: string;
  title: string;
  source: MatterScheduleSource | null;
  before_starts_at: string | null;
  before_ends_at: string | null;
  after_starts_at: string | null;
  after_ends_at: string | null;
}

export interface DraftPreviousAdjustment {
  schedule_id: string;
  matter_id: string | null;
  matter_title: string | null;
  title: string;
  before_starts_at: string | null;
  before_ends_at: string | null;
  after_starts_at: string | null;
  after_ends_at: string | null;
  shift_seconds: number;
  base_updated_at: string;
}

export interface MatterConflictOption {
  option_id: "A" | "B" | "C";
  label: string;
  detail: string;
  recommended: boolean;
  proposed_start: string | null;
  creates_todo: string | null;
  shift_seconds: number;
  target_schedule_id: string | null;
  executable: boolean;
  unavailable_reason: string | null;
  affected: MatterAffectedSchedule[];
}

export interface MatterConflictItem {
  schedule_id: string | null;
  title: string;
  starts_at: string;
  ends_at: string | null;
  gap_seconds: number;
  eta_seconds: number | null;
  eta_status: "available" | "unverified" | "not_applicable";
  relation: "previous" | "next" | "overlap";
  reason: string;
}

export interface MatterConflict {
  has_conflict: boolean;
  severity: "none" | "tight" | "overlap";
  window: Record<string, string>;
  conflicts: MatterConflictItem[];
  options: MatterConflictOption[];
  eta_note: string | null;
}

export interface MatterDraft {
  draft_id: string;
  kind: "create" | "update";
  status: "preview" | "confirmed" | "cancelled";
  version: number;
  matter_id: string | null;
  payload: DraftPayload;
  conflicts: MatterConflict | null;
  action_plan?: DraftAction[];
  communication_draft?: DraftCommunication | null;
  candidates: MatterSummary[] | null;
  ai_run_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface IntakeScheduleCandidate {
  schedule_id: string;
  title: string;
  starts_at: string;
  ends_at: string | null;
  location: string | null;
}

export interface IntakePendingContext {
  missing_information?: string[];
  schedule_candidates?: IntakeScheduleCandidate[];
  pending?: Record<string, unknown>;
  error?: string;
}

export interface MatterIntakeResponse {
  draft: MatterDraft | null;
  replayed: boolean;
  intent: "matter" | "question";
  outcome: "answer" | "draft" | "needs_clarification" | "processing" | "unknown";
  message: string | null;
  answer: string | null;
  clarify: string | null;
  pending_context: IntakePendingContext;
  local_run_id: string | null;
  parent_run_id: string | null;
}

export interface MatterIntakeResult {
  confirm_status: "confirmed" | "unconfirmed" | "unknown";
  target_lifecycle: "active" | "archived" | "deleted" | "source_revoked" | null;
  target_title: string | null;
  history_target_id: string | null;
  local_run_id: string;
  parent_run_id: string | null;
  request_text: string;
  intent: "matter" | "question";
  outcome:
    | "answer"
    | "draft"
    | "needs_clarification"
    | "processing"
    | "unknown"
    | "failed"
    | "cancelled";
  status: string;
  answer: string | null;
  clarify: string | null;
  pending_context: IntakePendingContext;
  matter_id: string | null;
  draft_id: string | null;
  draft: MatterDraft | null;
  created_at: string;
  completed_at: string | null;
}

export interface MatterEditInput {
  idempotency_key: string;
  schedule_id?: string;
  todo_id?: string;
}

export interface PlaceCandidate {
  provider: string;
  provider_place_id: string;
  name: string;
  address: string | null;
  city: string | null;
  latitude: number;
  longitude: number;
  coordinate_system: "gcj02" | "wgs84" | "unknown";
  source: string;
  selection_token: string;
}

export interface PlaceSearchResponse {
  query: string;
  city: string | null;
  search_type: "place" | "address";
  ambiguous: boolean;
  candidates: PlaceCandidate[];
  reason_code: string | null;
}

export interface RouteEstimate {
  status: "available" | "unavailable";
  provider: string;
  mode: "driving" | "walking" | "transit";
  duration_seconds: number | null;
  distance_meters: number | null;
  walking_distance_meters: number | null;
  transfer_count: number | null;
  transfers_summary: string | null;
  fare_cny: number | null;
  fare_kind: string | null;
  fare_notice: string | null;
  fetched_at: string | null;
  request_depart_at: string | null;
  target_arrival_at: string | null;
  source: "server_provider";
  reason_code: string | null;
  timing_basis: string | null;
  accuracy_notice: string | null;
  origin_basis: "user_confirmed" | "previous_schedule";
  origin_schedule_id: string | null;
  schedule_id: string | null;
  origin?: RouteEndpoint | null;
  destination?: RouteEndpoint | null;
  shape?: RouteShape;
}

export interface RoutePoint {
  latitude: number;
  longitude: number;
}

export interface RouteEndpoint extends RoutePoint {
  coordinate_system: "gcj02" | "wgs84" | "unknown";
  city: string | null;
  basis: "user_confirmed" | "previous_schedule" | "destination_schedule";
  schedule_id: string | null;
}

export interface RouteSegment {
  kind: "driving" | "walking" | "transit";
  label: string | null;
  coordinate_system: "gcj02" | "wgs84" | "unknown";
  points: RoutePoint[];
}

export interface RouteShape {
  status: "available" | "partial" | "missing";
  segments: RouteSegment[];
}

export interface ScheduledRoute {
  status: "available" | "unverified";
  reason_code: string | null;
  message: string | null;
  schedule_id: string;
  provider: string | null;
  mode: "driving" | "walking" | "transit" | null;
  duration_seconds: number | null;
  distance_meters: number | null;
  walking_distance_meters: number | null;
  transfer_count: number | null;
  transfers_summary: string | null;
  fare_cny: number | null;
  fare_kind: string | null;
  fare_notice: string | null;
  origin_basis: "user_confirmed" | "previous_schedule" | null;
  origin_schedule_id: string | null;
  fetched_at: string | null;
  requested_depart_at: string | null;
  target_arrival_at: string | null;
  calculated_at: string | null;
  timing_basis: string | null;
  accuracy_notice: string | null;
  origin: RouteEndpoint | null;
  destination: RouteEndpoint | null;
  shape: RouteShape;
}

export interface MapPickLocation {
  provider: string;
  provider_place_id: string | null;
  name: string;
  address: string;
  city: string | null;
  latitude: number;
  longitude: number;
  coordinate_system: "gcj02" | "wgs84" | "unknown";
  source: string;
  selection_token: string;
}

export interface MapPickResponse {
  status: "available" | "unavailable";
  reason_code: string | null;
  address_notice: string | null;
  location: MapPickLocation | null;
}

export interface DraftPatchInput {
  expected_version: number;
  title?: string;
  goal?: string;
  changes_note?: string;
  next_step?: string;
  schedule?: DraftSchedule | null;
  todos?: DraftTodo[] | null;
  complete_todo_ids?: string[] | null;
  /** Final future-schedule selection for a stop-following preview; [] clears every selection. */
  cancel_schedule_ids?: string[] | null;
}

export interface DraftConfirmInput {
  expected_version: number;
  matter_id?: string;
  create_schedule?: boolean;
  sync_to_ios?: boolean;
  acknowledge_conflict?: boolean;
  option_id?: "A" | "B" | "C";
}

export interface MatterChangeSummary {
  change_id: string;
  change_type: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
}

export type MatterChangeReasonCode =
  | "later_changes"
  | "already_reverted"
  | "matter_changed"
  | "schedule_changed"
  | "todo_changed"
  | "reminder_changed"
  | "creation_revert_unsupported"
  | "incomplete_change_snapshot"
  | "revert_from_source_matter"
  | "not_revertable"
  | "not_initial_create"
  | "initial_delete_unavailable"
  | "progress_added"
  | "schedule_added"
  | "todo_added"
  | "schedule_started"
  | "schedule_not_in_stop_preview"
  | "schedule_conflict_in_stop_preview"
  | "reminder_not_due"
  | "reminder_cancelled"
  | string
  | null;

export interface MatterChange extends MatterChangeSummary {
  matter_id: string;
  source_matter_id: string;
  source_matter_title: string | null;
  relation: "primary" | "affected";
  affected_matter_ids: string[];
  draft_id: string | null;
  created_at: string;
  schedule_id: string | null;
  revertable: boolean;
  /** Server-side eligibility for deleting an untouched initial create as one unit. */
  initial_delete_allowed: boolean;
  reason_code: MatterChangeReasonCode;
  reason: string | null;
}

export interface DraftConfirmResponse {
  draft: MatterDraft;
  matter: MatterSummary;
  schedule: MatterSchedule | null;
  reminders: MatterReminder[];
  cancelled_reminder_ids: string[];
  cancelled_schedule_ids: string[];
  notifications_retracted: boolean;
  change_id: string;
  replayed: boolean;
  applied: boolean;
  conflict: MatterConflict | null;
  change: MatterChangeSummary | null;
}

export interface InitialDeleteResponse {
  matter_id: string;
  deleted_at: string;
  schedule_ids: string[];
  todo_ids: string[];
  reminder_ids: string[];
}

export interface RevertResponse {
  matter: MatterSummary;
  change_id: string;
  reverted_at: string;
}

export interface RegistrationInput { email: string; display_name: string; password: string; invite_code?: string }
export function register(input: RegistrationInput) {
  return apiRequest<LoginResult>("/api/auth/register", { method: "POST", body: input });
}
export function registrationOptions() {
  return apiRequest<{ enabled: boolean; invite_required: boolean }>("/api/auth/register");
}

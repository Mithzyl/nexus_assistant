"""Validated runtime settings for the Personal Assistant API.

Settings are read once per process, then handed to the app factory. Nothing in a
request path reads environment variables directly, so tests can build an isolated
app without mutating global state.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Define everything the service needs; refuse to start on short secrets."""

    model_config = SettingsConfigDict(
        env_file=SERVICE_ENV_FILE,
        env_prefix="ASSISTANT_",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    register_enabled: bool = True
    register_invite_code: SecretStr = SecretStr("")

    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./assistant-dev.db"

    # NexusPilot application boundary. The token is an application credential, not
    # a NexusPilot user credential: Personal Assistant users never log in there.
    nexus_base_url: str = "http://127.0.0.1:8000"
    nexus_application_id: str = "personal_assistant"
    nexus_application_token: SecretStr = Field(
        default=SecretStr("dev-application-token-change-me"),
        min_length=24,
    )
    nexus_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    nexus_stream_timeout_seconds: float = Field(default=300.0, gt=0, le=900)
    # Current NexusPilot App Runs require an explicit general-agent workflow
    # definition and role bindings. These are server-side deployment choices;
    # they are never accepted from a client or echoed in an API response.
    nexus_workflow_name: Literal["general_agent"] = "general_agent"
    nexus_workflow_version: Literal["1.0.0"] = "1.0.0"
    nexus_execution_profile: Literal["general_tools_v1"] = "general_tools_v1"
    nexus_review_policy: Literal["always", "on_verification_failure", "never"] = "never"
    nexus_role_bindings_json: SecretStr = SecretStr(
        '{"controller":{"provider":"openai","model":"gpt-4o-mini"}}'
    )
    nexus_workflow_start_timeout_seconds: float = Field(default=20.0, gt=0, le=60)

    # Credential NexusPilot uses when it calls this service's business tools.
    # Only the digest is stored on our side; the plaintext lives in NexusPilot.
    tool_callback_token: SecretStr = Field(
        default=SecretStr("dev-tool-callback-token-change-me"),
        min_length=24,
    )
    tool_callback_max_skew_seconds: int = Field(default=300, ge=30, le=900)

    session_token_ttl_seconds: int = Field(default=604_800, ge=300, le=2_592_000)
    allow_schema_create: bool = False
    tool_result_max_items: int = Field(default=50, ge=1, le=100)
    tool_result_max_chars: int = Field(default=8_000, ge=500, le=64_000)

    # ---- Commute reminders -------------------------------------------------
    # `mock` is for development and tests only. Production startup refuses mock
    # or missing credentials instead of faking weather, ETA or push success.
    commute_provider_mode: Literal["mock", "live"] = "mock"
    commute_mock_fail_weather: bool = False
    commute_mock_fail_eta: bool = False
    commute_mock_rain_probability: float = Field(default=0.2, ge=0, le=1)
    commute_mock_eta_seconds: int = Field(default=1800, ge=60, le=86_400)
    commute_mock_push_result: Literal["accepted", "rejected", "unknown"] = "accepted"

    commute_weather_team_id: str | None = None
    commute_weather_key_id: str | None = None
    commute_weather_service_id: str | None = None
    commute_weather_private_key_path: Path | None = None
    commute_weather_language: str = "zh-Hans"
    commute_maps_team_id: str | None = None
    commute_maps_key_id: str | None = None
    commute_maps_id: str | None = None
    commute_maps_private_key_path: Path | None = None
    commute_maps_base_url: str = "https://maps-api.apple.com"
    commute_amap_api_key: SecretStr | None = None
    commute_amap_base_url: str = "https://restapi.amap.com"
    commute_weather_base_url: str = "https://weatherkit.apple.com"
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    commute_http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    commute_provider_max_attempts: int = Field(default=3, ge=1, le=5)
    commute_provider_backoff_base_seconds: float = Field(default=1.0, ge=0, le=30)
    commute_provider_backoff_max_seconds: float = Field(default=30.0, ge=1, le=300)

    commute_apns_team_id: str | None = None
    commute_apns_key_id: str | None = None
    commute_apns_private_key_path: Path | None = None
    commute_apns_topic: str | None = None
    commute_apns_environment: Literal["sandbox", "production"] = "sandbox"
    commute_apns_host: str | None = None
    commute_apns_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    # Optional model wording. Missing configuration is not an error: template
    # copy is the supported default. No model ever authors the numbers.
    commute_advice_model_url: str | None = None
    commute_advice_model_token: SecretStr | None = None
    commute_advice_model_timeout_seconds: float = Field(default=3.0, gt=0, le=15)
    commute_ai_timeout_seconds: float = Field(default=90.0, ge=10, le=180)

    # Bark is bound per authenticated account and stored in the PA database.
    # This timeout is the only global Bark setting; no owner/key env fallback.
    bark_timeout_seconds: float = Field(default=10.0, gt=0, le=30)

    commute_check_interval_minutes: int = Field(default=10, ge=5, le=60)
    commute_max_auto_reminders_per_day: int = Field(default=2, ge=1, le=2)
    commute_min_reminder_gap_minutes: int = Field(default=15, ge=0, le=120)
    commute_eta_freshness_seconds: int = Field(default=600, ge=60, le=7200)
    commute_weather_freshness_seconds: int = Field(default=3600, ge=300, le=21_600)
    commute_departure_earlier_threshold_minutes: int = Field(default=10, ge=5, le=60)
    commute_scheduler_poll_seconds: float = Field(default=20.0, ge=1, le=300)
    commute_worker_poll_seconds: float = Field(default=3.0, ge=0.5, le=60)
    commute_heartbeat_interval_seconds: float = Field(default=15.0, ge=1, le=60)
    commute_heartbeat_stale_seconds: int = Field(default=60, ge=10, le=600)
    # Timed weather refresh and paid AI analysis are separate switches. Both
    # default to off for AI; starting the scheduler never enables a paid model.
    commute_auto_weather_enabled: bool = True
    commute_auto_ai_enabled: bool = False
    commute_lease_seconds: int = Field(default=90, ge=30, le=600)
    commute_job_lease_seconds: int = Field(default=180, ge=30, le=1800)
    commute_outbox_max_attempts: int = Field(default=4, ge=1, le=10)
    commute_data_retention_days: int = Field(default=30, ge=1, le=365)

    # ---- Matters / work planning ------------------------------------------
    matter_ai_timeout_seconds: float = Field(default=60.0, ge=10, le=180)
    # Conflict scan: schedules within this many hours around the proposal.
    matter_conflict_window_hours: int = Field(default=3, ge=1, le=12)
    # Minimum slack between two appointments before a warning is raised.
    matter_conflict_min_gap_seconds: int = Field(default=900, ge=0, le=3600)
    matter_default_timezone: str = "Asia/Shanghai"
    calendar_public_base_url: str | None = None

    @field_validator(
        "commute_weather_team_id",
        "commute_weather_key_id",
        "commute_weather_service_id",
        "commute_weather_private_key_path",
        "commute_maps_team_id",
        "commute_maps_key_id",
        "commute_maps_id",
        "commute_maps_private_key_path",
        "commute_amap_api_key",
        "commute_apns_team_id",
        "commute_apns_key_id",
        "commute_apns_private_key_path",
        "commute_apns_topic",
        "commute_apns_host",
        "commute_advice_model_url",
        "commute_advice_model_token",
        mode="before",
    )
    @classmethod
    def _blank_optional_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


PRODUCTION_ENVIRONMENTS = {"production", "staging"}


def commute_configuration_errors(settings: Settings) -> list[str]:
    """Reject mock providers in production, but never require Apple secrets.

    Bark is the notification channel; WeatherKit/Maps/APNs are optional legacy
    providers. Missing optional credentials make the corresponding provider
    unavailable at request time instead of preventing service startup.
    """

    if settings.environment.lower() not in PRODUCTION_ENVIRONMENTS:
        return []
    if settings.commute_provider_mode != "live":
        return [
            "ASSISTANT_COMMUTE_PROVIDER_MODE must be 'live' in production; "
            "mock weather/ETA/push is forbidden."
        ]
    return []


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process settings for real deployments and CLI entry points."""

    return Settings()

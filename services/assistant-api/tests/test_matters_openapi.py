"""OpenAPI contract for the matter/iOS-sync surface added in this round."""

from __future__ import annotations

import json
from pathlib import Path

from assistant_api.core.config import Settings
from assistant_api.main import create_app

COMMITTED = Path(__file__).resolve().parents[3] / "docs" / "commute" / "openapi.json"


def _spec() -> dict:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        allow_schema_create=False,
    )
    return create_app(settings).openapi()


def test_committed_openapi_covers_the_new_surface():
    spec = _spec()
    paths = spec["paths"]
    expected = {
        ("/api/v1/matters/intake", "post"),
        ("/api/v1/matters", "get"),
        ("/api/v1/matters/{matter_id}", "get"),
        ("/api/v1/matters/drafts", "get"),
        ("/api/v1/matters/drafts/{draft_id}", "get"),
        ("/api/v1/matters/drafts/{draft_id}/apply-option", "post"),
        ("/api/v1/matters/drafts/{draft_id}/select-matter", "post"),
        ("/api/v1/matters/drafts/{draft_id}/confirm", "post"),
        ("/api/v1/matters/{matter_id}/changes/{change_id}/revert", "post"),
        ("/api/v1/today", "get"),
        ("/api/v1/reminders", "get"),
        ("/api/v1/commute/estimate", "post"),
        ("/api/v1/sync/ios/snapshot", "post"),
        ("/api/v1/sync/ios/pending-actions", "get"),
    }
    for path, method in expected:
        assert path in paths, path
        assert method in paths[path], (path, method)


def test_new_response_models_expose_required_fields():
    schemas = _spec()["components"]["schemas"]
    matter = schemas["MatterRead"]["properties"]
    for field in ("matter_id", "status", "version", "latest_progress", "next_step", "open_todos"):
        assert field in matter, field
    draft = schemas["DraftRead"]["properties"]
    for field in ("draft_id", "kind", "status", "version", "payload", "conflicts", "candidates"):
        assert field in draft, field
    conflict = schemas["ConflictRead"]["properties"]
    assert {"has_conflict", "severity", "conflicts", "options"} <= set(conflict)
    option = schemas["ConflictOptionRead"]["properties"]
    assert {
        "option_id",
        "shift_seconds",
        "target_schedule_id",
        "executable",
        "unavailable_reason",
        "affected",
    } <= set(option)
    schedule = schemas["ScheduleRead"]["properties"]
    assert {"sync_status", "source", "matter_id"} <= set(schedule)
    change = schemas["ChangeRead"]["properties"]
    assert {
        "source_matter_id",
        "source_matter_title",
        "relation",
        "affected_matter_ids",
        "revertable",
        "reason_code",
    } <= set(change)


def test_estimate_is_documented_as_post_and_committed_file_is_current():
    spec = _spec()
    assert "post" in spec["paths"]["/api/v1/commute/estimate"]
    assert "get" not in spec["paths"]["/api/v1/commute/estimate"]
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed == spec

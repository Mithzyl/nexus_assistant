"""The committed OpenAPI is the shared App/backend contract and must not drift."""

from __future__ import annotations

import json
from pathlib import Path

from assistant_api.core.config import Settings
from assistant_api.main import create_app

ROOT = Path(__file__).resolve().parents[3]
COMMITTED = ROOT / "docs" / "commute" / "openapi.json"


def test_committed_openapi_matches_application():
    """Keep the exported contracts identical to those served by the Assistant."""
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        allow_schema_create=False,
    )
    generated = create_app(settings).openapi()
    assert COMMITTED.exists(), "run scripts/export_openapi.py"
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed == generated


def test_commute_contract_exposes_headers_enums_and_no_body_identity():
    """Follow the schemas referenced by real write routes and check location fields."""
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        allow_schema_create=False,
    )
    document = create_app(settings).openapi()
    paths = document["paths"]
    for path in (
        "/api/v1/commute-plans",
        "/api/v1/commute-plans/{plan_id}",
        "/api/v1/commute-plans/{plan_id}/checks",
        "/api/v1/checks/{check_id}",
        "/api/v1/commute-plans/{plan_id}/advisories/latest",
        "/api/v1/advisories/{advisory_id}",
        "/api/v1/advisories/{advisory_id}/actions",
        "/api/v1/push-devices/{installation_id}",
    ):
        assert path in paths

    put = paths["/api/v1/commute-plans/{plan_id}"]["put"]
    if_match = [p for p in put["parameters"] if p["name"] == "If-Match"]
    assert if_match and if_match[0]["required"] is False

    check_post = paths["/api/v1/commute-plans/{plan_id}/checks"]["post"]
    idem = [p for p in check_post["parameters"] if p["name"] == "Idempotency-Key"]
    assert idem and idem[0]["required"] is True

    action_post = paths["/api/v1/advisories/{advisory_id}/actions"]["post"]
    idem = [p for p in action_post["parameters"] if p["name"] == "Idempotency-Key"]
    assert idem and idem[0]["required"] is True

    schemas = document["components"]["schemas"]
    assert schemas["AdvisoryRead"]["properties"]["quality"]["enum"] == [
        "fresh",
        "degraded",
        "unavailable",
    ]
    assert "environment" not in schemas["PushDeviceInput"]["properties"]
    create_definition = paths["/api/v1/commute-plans"]["post"]
    plan_schema_ref = create_definition["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert put["requestBody"]["content"]["application/json"]["schema"]["$ref"] == plan_schema_ref
    plan_schema = schemas[plan_schema_ref.rsplit("/", 1)[-1]]
    assert plan_schema["additionalProperties"] is False
    plan_input = plan_schema["properties"]
    assert "user_id" not in plan_input
    for field in (
        "name",
        "enabled",
        "timezone",
        "weekdays",
        "arrival_local_time",
        "check_local_time",
        "transport_mode",
        "buffer_minutes",
        "rain_probability_threshold",
        "rain_extra_buffer_minutes",
        "notify_enabled",
        "origin",
        "destination",
    ):
        assert field in plan_input
    location_schema_ref = next(item["$ref"] for item in plan_input["origin"]["anyOf"] if "$ref" in item)
    location_fields = schemas[location_schema_ref.rsplit("/", 1)[-1]]["properties"]
    assert {"latitude", "longitude", "city", "coordinate_system"} <= location_fields.keys()

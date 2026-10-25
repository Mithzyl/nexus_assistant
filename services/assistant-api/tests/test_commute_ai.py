"""Deterministic validation tests for the Nexus commute analysis boundary."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from assistant_api.commute.services.ai import AiFailure, _extract_json, _validate, analyze


def snapshot(**overrides):
    value = {
        "provider": "stub", "duration_seconds": 1800,
        "duration_in_traffic_seconds": 2100, "route_reference": "route-a",
    }
    value.update(overrides)
    return value


def valid_result(**overrides):
    value = {
        "title": "建议 10:15 出发",
        "summary": "采用含交通耗时 2100 秒，并额外预留 600 秒。",
        "used_duration_seconds": 2100,
        "duration_source": "duration_in_traffic_seconds",
        "extra_buffer_seconds": 600,
        "recommended_departure_at": "2026-10-24T10:15:00Z",
        "basis": ["地图含交通 ETA 2100 秒"],
        "missing_information": [],
    }
    value.update(overrides)
    return value


def test_ai_validation_accepts_exact_arithmetic():
    result = _validate(valid_result(), plan=SimpleNamespace(), snapshot=snapshot(),
                       target=datetime(2026, 10, 24, 11, 0, tzinfo=UTC))
    assert result["status"] == "available"
    assert result["used_duration_seconds"] == 2100
    assert result["duration_source"] == "duration_in_traffic_seconds"


@pytest.mark.parametrize("payload", [
    valid_result(used_duration_seconds=1800),
    valid_result(extra_buffer_seconds=-1),
    valid_result(recommended_departure_at="2026-10-24T10:20:00Z"),
    valid_result(duration_source="route"),
    valid_result(title=""),
])
def test_ai_validation_rejects_inconsistent_results(payload):
    with pytest.raises(AiFailure):
        _validate(payload, plan=SimpleNamespace(), snapshot=snapshot(),
                  target=datetime(2026, 10, 24, 11, 0, tzinfo=UTC))


def test_ai_json_can_be_fenced_but_invalid_text_is_rejected():
    assert _extract_json('```json\n{"ok": true}\n```') == {"ok": True}
    with pytest.raises(AiFailure):
        _extract_json("这不是 JSON")


@pytest.mark.asyncio
async def test_ai_is_explicitly_unavailable_without_snapshot_or_nexus():
    no_snapshot = await analyze(None, SimpleNamespace(nexus=None), check=SimpleNamespace(user_id="u"), plan=SimpleNamespace(), snapshot=None, weather={}, target=datetime.now(UTC))
    assert no_snapshot["status"] == "unavailable"
    assert no_snapshot["reason"] == "route_provider_unconfigured"
    no_nexus = await analyze(None, SimpleNamespace(nexus=None), check=SimpleNamespace(user_id="u"), plan=SimpleNamespace(), snapshot=snapshot(), weather={}, target=datetime.now(UTC))
    assert no_nexus["status"] == "unavailable"
    assert no_nexus["reason"] == "ai_nexus_not_configured"


def test_traffic_eta_is_normalized_even_when_numbers_match():
    payload = valid_result(duration_source="duration_seconds", used_duration_seconds=2100)
    result = _validate(payload, plan=SimpleNamespace(buffer_minutes=10), snapshot=snapshot(),
                       target=datetime(2026, 10, 24, 11, 0, tzinfo=UTC))
    assert result["duration_source"] == "duration_in_traffic_seconds"
    assert result["additional_buffer_seconds"] == 0


def test_additional_buffer_must_match_total():
    with pytest.raises(AiFailure):
        _validate(valid_result(extra_buffer_seconds=600, additional_buffer_seconds=120),
                  plan=SimpleNamespace(buffer_minutes=10), snapshot=snapshot(),
                  target=datetime(2026, 10, 24, 11, 0, tzinfo=UTC))

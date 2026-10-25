"""Helpers used while consuming the upstream workflow event stream.

Both were referenced by the observer but missing from the module, so every AI
run ended as `unknown` with `nexus_protocol_error` instead of its real outcome.
"""

from __future__ import annotations

from assistant_api.services.run_service import _event_error_code, _sequence_of_event


def test_sequence_of_event_parses_numeric_ids():
    assert _sequence_of_event("5") == 5
    assert _sequence_of_event(" 7 ") == 7
    assert _sequence_of_event(None) is None
    assert _sequence_of_event("") is None


def test_sequence_of_event_tolerates_opaque_ids():
    assert _sequence_of_event("evt-5") is None
    assert _sequence_of_event("not a number") is None


def test_event_error_code_reads_nested_shapes():
    assert _event_error_code({"error_code": "upstream_failed"}) == "upstream_failed"
    assert _event_error_code({"error": {"code": "rate_limited"}}) == "rate_limited"
    assert _event_error_code({"public_payload": {"error_code": "timeout"}}) == "timeout"
    assert _event_error_code({"public_payload": {"error": {"code": "bad_gateway"}}}) == "bad_gateway"


def test_event_error_code_ignores_missing_or_non_string_values():
    assert _event_error_code({}) is None
    assert _event_error_code({"error_code": 42}) is None
    assert _event_error_code({"error": "boom"}) is None

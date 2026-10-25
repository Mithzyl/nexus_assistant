"""DST and local-time rules are pure and must be proven, not documented."""

from datetime import UTC, date, datetime

import pytest

from assistant_api.commute.timeutil import (
    InvalidCommuteTime,
    local_today,
    resolve_local_hhmm,
    slot_index_for,
    validate_same_day,
    validate_timezone,
    validate_weekdays,
)


def test_nonexistent_local_time_shifts_to_first_valid_instant():
    # New York springs forward at 02:00 local on 2026-03-08.
    resolved = resolve_local_hhmm(date(2026, 3, 8), "02:30", "America/New_York")
    assert resolved == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)  # 03:00 EDT


def test_ambiguous_local_time_uses_first_occurrence_only():
    # 01:30 occurs twice on 2026-11-01; fold=0 is the first (EDT) occurrence.
    resolved = resolve_local_hhmm(date(2026, 11, 1), "01:30", "America/New_York")
    assert resolved == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    # The repeated local minute maps to the same automatic slot, so the unique
    # database key executes it once in the automatic flow.
    second = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    assert slot_index_for(resolved, "America/New_York") == slot_index_for(
        second, "America/New_York"
    )


def test_local_today_uses_plan_timezone_not_server_timezone():
    instant = datetime(2026, 10, 22, 16, 30, tzinfo=UTC)  # 00:30 next day Shanghai
    assert local_today(instant, "Asia/Shanghai") == date(2026, 10, 23)
    assert local_today(instant, "America/New_York") == date(2026, 10, 22)


def test_cross_midnight_and_invalid_values_are_rejected():
    with pytest.raises(InvalidCommuteTime):
        validate_same_day("23:30", "07:00")
    with pytest.raises(InvalidCommuteTime):
        validate_timezone("+08:00")
    with pytest.raises(InvalidCommuteTime):
        validate_weekdays([0, 3])
    with pytest.raises(InvalidCommuteTime):
        validate_weekdays([])
    assert validate_weekdays([7, 1, 1, 3]) == [1, 3, 7]

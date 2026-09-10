"""Tests for injectable timezone-aware clocks."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.clock import FixedClock, SystemClock, require_aware_datetime
from app.core.errors import DataValidationError


def test_fixed_clock_returns_configured_datetime() -> None:
    instant = datetime(2026, 9, 2, 10, 30, tzinfo=UTC)

    assert FixedClock(instant).now() == instant


def test_fixed_clock_is_deterministic_across_calls() -> None:
    clock = FixedClock(datetime(2026, 9, 2, 10, 30, tzinfo=UTC))

    assert clock.now() is clock.now()


def test_fixed_clock_preserves_explicit_timezone() -> None:
    moscow = timezone(timedelta(hours=3))
    instant = datetime(2026, 9, 2, 13, 30, tzinfo=moscow)

    assert FixedClock(instant).now().utcoffset() == timedelta(hours=3)


def test_fixed_clock_rejects_naive_datetime() -> None:
    with pytest.raises(DataValidationError) as raised:
        FixedClock(datetime(2026, 9, 2, 10, 30))

    assert raised.value.code == "clock.naive_datetime"


def test_system_clock_returns_aware_datetime_in_requested_timezone() -> None:
    moscow = timezone(timedelta(hours=3))

    result = SystemClock(moscow).now()

    assert result.tzinfo is moscow
    assert result.utcoffset() == timedelta(hours=3)


def test_aware_datetime_validator_returns_original_value() -> None:
    instant = datetime(2026, 9, 2, 10, 30, tzinfo=UTC)

    assert require_aware_datetime(instant) is instant

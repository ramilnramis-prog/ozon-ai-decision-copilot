"""Injectable, timezone-aware clocks for reproducible analyses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Protocol

from app.core.errors import DataValidationError


class Clock(Protocol):
    """Clock boundary used by services instead of calling datetime.now directly."""

    def now(self) -> datetime:
        """Return the current timezone-aware analysis time."""


def require_aware_datetime(value: datetime, *, field_name: str = "datetime") -> datetime:
    """Validate and return a timezone-aware datetime."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise DataValidationError(
            f"{field_name} must be timezone-aware",
            code="clock.naive_datetime",
            scope=field_name,
        )
    return value


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Production clock using an explicit timezone (UTC by default)."""

    zone: tzinfo = UTC

    def __post_init__(self) -> None:
        try:
            require_aware_datetime(datetime.now(self.zone), field_name="clock timezone")
        except (TypeError, ValueError) as exc:
            raise DataValidationError(
                "clock timezone must produce timezone-aware datetimes",
                code="clock.invalid_timezone",
                scope="clock timezone",
            ) from exc

    def now(self) -> datetime:
        return require_aware_datetime(datetime.now(self.zone), field_name="clock time")


@dataclass(frozen=True, slots=True)
class FixedClock:
    """Test clock that always returns the same explicit instant."""

    instant: datetime

    def __post_init__(self) -> None:
        require_aware_datetime(self.instant, field_name="fixed clock instant")

    def now(self) -> datetime:
        return self.instant

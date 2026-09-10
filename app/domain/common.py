"""Source-independent shared domain values, statuses, and validation helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import TypeVar

from app.core.clock import require_aware_datetime
from app.core.errors import DataValidationError
from app.core.money import MoneyInput, to_decimal


class Currency(str, Enum):
    """Currencies explicitly supported by the current domain."""

    RUB = "RUB"


class AvailabilityStatus(str, Enum):
    """Whether a deterministic result can be produced from known inputs."""

    AVAILABLE = "available"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT_DATA = "insufficient_data"
    INVALID = "invalid"


class SafetyStatus(str, Enum):
    """Deterministic safety classification, separate from availability."""

    SAFE = "safe"
    UNSAFE = "unsafe"
    UNAVAILABLE = "unavailable"


class Severity(str, Enum):
    """Shared deterministic severity labels."""

    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class Urgency(str, Enum):
    """Shared deterministic urgency labels."""

    MONITOR = "monitor"
    SOON = "soon"
    IMMEDIATE = "immediate"


class RiskStatus(str, Enum):
    """Inventory risk outcomes anticipated by the decision layer."""

    HEALTHY = "healthy"
    REORDER_DUE_SOON = "reorder_due_soon"
    CRITICAL = "critical"
    OVERSTOCK_CANDIDATE = "overstock_candidate"
    INSUFFICIENT_DATA = "insufficient_data"


class SourceType(str, Enum):
    """Generic provenance types without claiming a live marketplace source."""

    DEMO = "demo"
    MOCK = "mock"
    MANUAL = "manual"
    IMPORTED = "imported"
    CALCULATED = "calculated"
    EXTERNAL_API = "external_api"


T = TypeVar("T")


def require_instance(value: object, expected_type: type[T], *, field_name: str) -> T:
    if not isinstance(value, expected_type):
        raise DataValidationError(
            f"{field_name} has an invalid type",
            code="domain.invalid_type",
            scope=field_name,
        )
    return value


def require_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(
            f"{field_name} must be a non-empty string",
            code="domain.invalid_text",
            scope=field_name,
        )
    return value.strip()


def optional_text(value: object | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    return require_text(value, field_name=field_name)


def require_calendar_date(value: object, *, field_name: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise DataValidationError(
            f"{field_name} must be a calendar date",
            code="domain.invalid_date",
            scope=field_name,
        )
    return value


def require_non_negative_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DataValidationError(
            f"{field_name} must be a non-negative integer",
            code="domain.invalid_non_negative_integer",
            scope=field_name,
        )
    return value


def require_positive_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DataValidationError(
            f"{field_name} must be a positive integer",
            code="domain.invalid_positive_integer",
            scope=field_name,
        )
    return value


def require_decimal(value: MoneyInput, *, field_name: str) -> Decimal:
    return to_decimal(value, field_name=field_name)


def require_non_negative_decimal(value: MoneyInput, *, field_name: str) -> Decimal:
    result = require_decimal(value, field_name=field_name)
    if result < 0:
        raise DataValidationError(
            f"{field_name} must be non-negative",
            code="domain.invalid_non_negative_decimal",
            scope=field_name,
        )
    return result


def require_positive_decimal(value: MoneyInput, *, field_name: str) -> Decimal:
    result = require_decimal(value, field_name=field_name)
    if result <= 0:
        raise DataValidationError(
            f"{field_name} must be greater than zero",
            code="domain.invalid_positive_decimal",
            scope=field_name,
        )
    return result


def require_unit_rate(
    value: MoneyInput,
    *,
    field_name: str,
    allow_one: bool = True,
) -> Decimal:
    result = require_non_negative_decimal(value, field_name=field_name)
    outside_upper_bound = result > 1 if allow_one else result >= 1
    if outside_upper_bound:
        boundary = "at most one" if allow_one else "less than one"
        raise DataValidationError(
            f"{field_name} must be {boundary}",
            code="domain.invalid_rate",
            scope=field_name,
        )
    return result


def normalize_text_tuple(
    values: Iterable[str],
    *,
    field_name: str,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if isinstance(values, str):
        raise DataValidationError(
            f"{field_name} must be a collection of strings",
            code="domain.invalid_text_collection",
            scope=field_name,
        )
    normalized = tuple(require_text(value, field_name=field_name) for value in values)
    if not allow_empty and not normalized:
        raise DataValidationError(
            f"{field_name} must not be empty",
            code="domain.empty_collection",
            scope=field_name,
        )
    if len(set(normalized)) != len(normalized):
        raise DataValidationError(
            f"{field_name} must not contain duplicates",
            code="domain.duplicate_value",
            scope=field_name,
        )
    return normalized


@dataclass(frozen=True, slots=True)
class SkuId:
    """Stable, source-independent SKU identifier."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", require_text(self.value, field_name="sku"))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DateRange:
    """Inclusive calendar-date period."""

    start: date
    end: date

    def __post_init__(self) -> None:
        start = require_calendar_date(self.start, field_name="period start")
        end = require_calendar_date(self.end, field_name="period end")
        if end < start:
            raise DataValidationError(
                "period end must not be before period start",
                code="domain.invalid_date_range",
                scope="period",
            )


@dataclass(frozen=True, slots=True)
class Provenance:
    """Origin and timing metadata attached to source and calculated records."""

    source_type: SourceType
    provider: str
    ingested_at: datetime
    source_timestamp: datetime | None = None
    source_record_id: str | None = None

    def __post_init__(self) -> None:
        require_instance(self.source_type, SourceType, field_name="source_type")
        object.__setattr__(self, "provider", require_text(self.provider, field_name="provider"))
        require_aware_datetime(self.ingested_at, field_name="ingested_at")
        if self.source_timestamp is not None:
            require_aware_datetime(self.source_timestamp, field_name="source_timestamp")
        object.__setattr__(
            self,
            "source_record_id",
            optional_text(self.source_record_id, field_name="source_record_id"),
        )


@dataclass(frozen=True, slots=True)
class PolicyIdentity:
    """Stable identity/version recorded alongside policy-dependent results."""

    policy_id: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "policy_id", require_text(self.policy_id, field_name="policy_id")
        )
        object.__setattr__(self, "version", require_text(self.version, field_name="version"))


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """Safe, structured data-quality issue that can be attached to a scope."""

    code: str
    message: str
    severity: Severity
    scope: str
    sku: SkuId | None = None
    field_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", require_text(self.code, field_name="issue code"))
        object.__setattr__(
            self, "message", require_text(self.message, field_name="issue message")
        )
        require_instance(self.severity, Severity, field_name="issue severity")
        object.__setattr__(self, "scope", require_text(self.scope, field_name="issue scope"))
        if self.sku is not None:
            require_instance(self.sku, SkuId, field_name="issue sku")
        object.__setattr__(
            self, "field_name", optional_text(self.field_name, field_name="issue field")
        )

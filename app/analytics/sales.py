"""Pure deterministic sales-window metrics over normalized observations.

The ``as_of`` date starts a new, incomplete business day and is excluded. The
current window therefore ends on ``as_of - 1 day``; the comparison window is
the immediately preceding, non-overlapping calendar period.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Context, Decimal, MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, localcontext
from enum import Enum
from fractions import Fraction

from app.core.errors import CalculationPreconditionError, DataValidationError
from app.domain.catalog import SalesObservation, SalesPolicy
from app.domain.common import (
    AvailabilityStatus,
    DateRange,
    SkuId,
    normalize_text_tuple,
    require_calendar_date,
    require_decimal,
    require_instance,
    require_non_negative_decimal,
    require_non_negative_int,
    require_positive_int,
)


SALES_RATIO_PRECISION = 28
_SALES_RATIO_CONTEXT = Context(
    prec=SALES_RATIO_PRECISION,
    rounding=ROUND_HALF_EVEN,
    Emin=MIN_EMIN,
    Emax=MAX_EMAX,
)


class SalesChangeDirection(str, Enum):
    """Factual change classification; it is not a recommendation or forecast."""

    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"


def _sales_periods(policy: SalesPolicy, as_of: date) -> tuple[DateRange, DateRange]:
    require_instance(policy, SalesPolicy, field_name="sales policy")
    analysis_date = require_calendar_date(as_of, field_name="sales as_of date")
    try:
        current = DateRange(
            start=analysis_date - timedelta(days=policy.averaging_window_days),
            end=analysis_date - timedelta(days=1),
        )
        comparison = DateRange(
            start=current.start - timedelta(days=policy.comparison_window_days),
            end=current.start - timedelta(days=1),
        )
    except OverflowError as exc:
        raise CalculationPreconditionError(
            "sales windows cannot be represented for the supplied as-of date",
            code="sales.invalid_window_boundaries",
            scope="sales as_of date",
        ) from exc
    return current, comparison


def _decimal_ratio(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        raise CalculationPreconditionError(
            "sales ratio denominator must be positive",
            code="sales.invalid_ratio_denominator",
            scope="sales ratio",
        )
    with localcontext(_SALES_RATIO_CONTEXT):
        return Decimal(numerator) / Decimal(denominator)


@dataclass(frozen=True, slots=True)
class SalesWindowMetrics:
    """Facts and completeness for one inclusive calendar window."""

    period: DateRange
    expected_days: int
    observed_days: int
    total_units: int
    average_daily_sales: Decimal | None
    status: AvailabilityStatus
    missing_dates: tuple[date, ...]
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_instance(self.period, DateRange, field_name="sales window period")
        require_positive_int(self.expected_days, field_name="expected sales days")
        require_non_negative_int(self.observed_days, field_name="observed sales days")
        require_non_negative_int(self.total_units, field_name="sales window units")
        require_instance(self.status, AvailabilityStatus, field_name="sales window status")

        period_days = (self.period.end - self.period.start).days + 1
        if period_days != self.expected_days or self.observed_days > self.expected_days:
            raise DataValidationError(
                "sales window counts are inconsistent with its calendar period",
                code="sales.inconsistent_window_counts",
                scope="sales window",
            )

        missing_dates = tuple(self.missing_dates)
        for missing_date in missing_dates:
            require_calendar_date(missing_date, field_name="missing sales date")
            if not self.period.start <= missing_date <= self.period.end:
                raise DataValidationError(
                    "missing sales date must belong to its window",
                    code="sales.missing_date_outside_window",
                    scope="missing sales dates",
                )
        if (
            missing_dates != tuple(sorted(set(missing_dates)))
            or len(missing_dates) != self.expected_days - self.observed_days
        ):
            raise DataValidationError(
                "missing sales dates must exactly describe incomplete calendar coverage",
                code="sales.inconsistent_missing_dates",
                scope="missing sales dates",
            )
        object.__setattr__(self, "missing_dates", missing_dates)
        object.__setattr__(
            self,
            "source_refs",
            normalize_text_tuple(self.source_refs, field_name="sales source refs"),
        )

        if self.status is AvailabilityStatus.AVAILABLE:
            if self.observed_days != self.expected_days or missing_dates:
                raise DataValidationError(
                    "available sales window requires complete calendar coverage",
                    code="sales.incomplete_available_window",
                    scope="sales window",
                )
            if self.average_daily_sales is None:
                raise DataValidationError(
                    "available sales window requires an average",
                    code="sales.missing_window_average",
                    scope="average daily sales",
                )
            object.__setattr__(
                self,
                "average_daily_sales",
                require_non_negative_decimal(
                    self.average_daily_sales,
                    field_name="average daily sales",
                ),
            )
        elif self.status is AvailabilityStatus.INSUFFICIENT_DATA:
            if self.observed_days == self.expected_days or self.average_daily_sales is not None:
                raise DataValidationError(
                    "insufficient sales window must be incomplete and have no average",
                    code="sales.invalid_incomplete_window",
                    scope="sales window",
                )
        else:
            raise DataValidationError(
                "sales window status must be available or insufficient data",
                code="sales.invalid_window_status",
                scope="sales window status",
            )


@dataclass(frozen=True, slots=True)
class SalesMetrics:
    """Complete deterministic sales facts for current and comparison windows."""

    sku: SkuId
    as_of: date
    policy: SalesPolicy
    current: SalesWindowMetrics
    comparison: SalesWindowMetrics
    status: AvailabilityStatus
    change_status: AvailabilityStatus
    relative_change: Decimal | None
    direction: SalesChangeDirection | None

    def __post_init__(self) -> None:
        require_instance(self.sku, SkuId, field_name="sales metrics sku")
        require_calendar_date(self.as_of, field_name="sales metrics as_of")
        require_instance(self.policy, SalesPolicy, field_name="sales metrics policy")
        require_instance(self.current, SalesWindowMetrics, field_name="current sales window")
        require_instance(
            self.comparison,
            SalesWindowMetrics,
            field_name="comparison sales window",
        )
        require_instance(self.status, AvailabilityStatus, field_name="sales metrics status")
        require_instance(
            self.change_status,
            AvailabilityStatus,
            field_name="sales change status",
        )
        if self.direction is not None:
            require_instance(
                self.direction,
                SalesChangeDirection,
                field_name="sales change direction",
            )
        if self.relative_change is not None:
            object.__setattr__(
                self,
                "relative_change",
                require_decimal(self.relative_change, field_name="relative sales change"),
            )

        expected_current, expected_comparison = _sales_periods(self.policy, self.as_of)
        if self.current.period != expected_current or self.comparison.period != expected_comparison:
            raise DataValidationError(
                "sales metric windows do not match policy and as-of date",
                code="sales.inconsistent_result_windows",
                scope="sales metrics",
            )

        windows_complete = (
            self.current.status is AvailabilityStatus.AVAILABLE
            and self.comparison.status is AvailabilityStatus.AVAILABLE
        )
        expected_status = (
            AvailabilityStatus.AVAILABLE
            if windows_complete
            else AvailabilityStatus.INSUFFICIENT_DATA
        )
        if self.status is not expected_status:
            raise DataValidationError(
                "sales metrics status conflicts with window completeness",
                code="sales.conflicting_metrics_status",
                scope="sales metrics status",
            )

        if not windows_complete:
            if (
                self.change_status is not AvailabilityStatus.INSUFFICIENT_DATA
                or self.relative_change is not None
                or self.direction is not None
            ):
                raise DataValidationError(
                    "incomplete windows cannot produce comparative sales metrics",
                    code="sales.change_on_incomplete_windows",
                    scope="sales change",
                )
        elif self.change_status is AvailabilityStatus.AVAILABLE:
            if self.relative_change is None or self.direction is None:
                raise DataValidationError(
                    "available sales change requires a value and direction",
                    code="sales.incomplete_available_change",
                    scope="sales change",
                )
        elif self.change_status is AvailabilityStatus.NOT_APPLICABLE:
            if self.relative_change is not None or self.direction is not SalesChangeDirection.INCREASING:
                raise DataValidationError(
                    "zero-baseline increase requires no percentage and increasing direction",
                    code="sales.invalid_zero_baseline_change",
                    scope="sales change",
                )
        else:
            raise DataValidationError(
                "complete windows require an available or not-applicable sales change",
                code="sales.invalid_change_status",
                scope="sales change status",
            )


def _window_metrics(
    period: DateRange,
    observations_by_date: Mapping[date, SalesObservation],
) -> SalesWindowMetrics:
    expected_dates = tuple(
        period.start + timedelta(days=offset)
        for offset in range((period.end - period.start).days + 1)
    )
    observed = tuple(
        observations_by_date[observed_on]
        for observed_on in expected_dates
        if observed_on in observations_by_date
    )
    missing_dates = tuple(
        observed_on for observed_on in expected_dates if observed_on not in observations_by_date
    )
    total_units = sum(item.units_sold for item in observed)
    complete = not missing_dates
    source_refs = tuple(
        dict.fromkeys(
            item.provenance.source_record_id
            for item in observed
            if item.provenance.source_record_id is not None
        )
    )
    return SalesWindowMetrics(
        period=period,
        expected_days=len(expected_dates),
        observed_days=len(observed),
        total_units=total_units,
        average_daily_sales=(
            _decimal_ratio(total_units, len(observed)) if complete else None
        ),
        status=(
            AvailabilityStatus.AVAILABLE
            if complete
            else AvailabilityStatus.INSUFFICIENT_DATA
        ),
        missing_dates=missing_dates,
        source_refs=source_refs,
    )


def _material_direction(
    relative_change: Fraction,
    threshold: Decimal,
) -> SalesChangeDirection:
    exact_threshold = Fraction(threshold)
    if relative_change >= exact_threshold:
        return SalesChangeDirection.INCREASING
    if relative_change <= -exact_threshold:
        return SalesChangeDirection.DECREASING
    return SalesChangeDirection.STABLE


def calculate_sales_metrics(
    sku: SkuId,
    observations: Iterable[SalesObservation],
    policy: SalesPolicy,
    as_of: date,
) -> SalesMetrics:
    """Calculate complete-day sales metrics for one SKU without side effects.

    Missing calendar dates make their window insufficient; they are never filled
    with zero. When both complete windows have zero sales, relative change is
    exactly zero. When only the comparison baseline is zero, relative percentage
    is ``NOT_APPLICABLE`` while the factual direction remains ``INCREASING``.
    """

    requested_sku = require_instance(sku, SkuId, field_name="sales metrics sku")
    require_instance(policy, SalesPolicy, field_name="sales policy")
    analysis_date = require_calendar_date(as_of, field_name="sales as_of date")
    current_period, comparison_period = _sales_periods(policy, analysis_date)

    if isinstance(observations, (str, bytes, Mapping)):
        raise CalculationPreconditionError(
            "sales observations must be an iterable of normalized observations",
            code="sales.invalid_observation_collection",
            scope="sales observations",
        )
    try:
        normalized_observations = tuple(observations)
    except TypeError as exc:
        raise CalculationPreconditionError(
            "sales observations must be an iterable of normalized observations",
            code="sales.invalid_observation_collection",
            scope="sales observations",
        ) from exc

    observations_by_date: dict[date, SalesObservation] = {}
    for observation in normalized_observations:
        if not isinstance(observation, SalesObservation):
            raise CalculationPreconditionError(
                "sales analytics requires normalized SalesObservation values",
                code="sales.invalid_observation_type",
                scope="sales observations",
            )
        if observation.sku != requested_sku:
            raise CalculationPreconditionError(
                "sales observations must all match the requested SKU",
                code="sales.mixed_skus",
                scope="sales observations",
            )
        if observation.observed_on in observations_by_date:
            raise CalculationPreconditionError(
                "sales observations must be unique by SKU and calendar date",
                code="sales.duplicate_observation_date",
                scope="sales observations",
            )
        observations_by_date[observation.observed_on] = observation

    current = _window_metrics(current_period, observations_by_date)
    comparison = _window_metrics(comparison_period, observations_by_date)
    if (
        current.status is not AvailabilityStatus.AVAILABLE
        or comparison.status is not AvailabilityStatus.AVAILABLE
    ):
        return SalesMetrics(
            sku=requested_sku,
            as_of=analysis_date,
            policy=policy,
            current=current,
            comparison=comparison,
            status=AvailabilityStatus.INSUFFICIENT_DATA,
            change_status=AvailabilityStatus.INSUFFICIENT_DATA,
            relative_change=None,
            direction=None,
        )

    if comparison.total_units == 0:
        if current.total_units == 0:
            relative_change = Decimal(0)
            change_status = AvailabilityStatus.AVAILABLE
            direction = SalesChangeDirection.STABLE
        else:
            relative_change = None
            change_status = AvailabilityStatus.NOT_APPLICABLE
            direction = SalesChangeDirection.INCREASING
    else:
        exact_change = Fraction(
            current.total_units * comparison.expected_days
            - comparison.total_units * current.expected_days,
            comparison.total_units * current.expected_days,
        )
        relative_change = _decimal_ratio(
            exact_change.numerator,
            exact_change.denominator,
        )
        change_status = AvailabilityStatus.AVAILABLE
        direction = _material_direction(
            exact_change,
            policy.material_change_threshold,
        )

    return SalesMetrics(
        sku=requested_sku,
        as_of=analysis_date,
        policy=policy,
        current=current,
        comparison=comparison,
        status=AvailabilityStatus.AVAILABLE,
        change_status=change_status,
        relative_change=relative_change,
        direction=direction,
    )

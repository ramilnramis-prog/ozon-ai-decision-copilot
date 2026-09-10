"""Unit tests for pure deterministic sales analytics."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, localcontext

import pytest

from app.analytics.sales import (
    SalesChangeDirection,
    SalesMetrics,
    calculate_sales_metrics,
)
from app.core.errors import CalculationPreconditionError
from app.domain.catalog import SalesObservation, SalesPolicy
from app.domain.common import (
    AvailabilityStatus,
    DateRange,
    PolicyIdentity,
    Provenance,
    SkuId,
    SourceType,
)


AS_OF = date(2026, 9, 2)
SKU = SkuId("SKU-001")
OTHER_SKU = SkuId("SKU-002")
POLICY = SalesPolicy(
    identity=PolicyIdentity("sales-test", "1"),
    averaging_window_days=14,
    comparison_window_days=14,
    material_change_threshold=Decimal("0.25"),
)


def observation(sku: SkuId, observed_on: date, units: int) -> SalesObservation:
    return SalesObservation(
        sku=sku,
        observed_on=observed_on,
        units_sold=units,
        provenance=Provenance(
            SourceType.DEMO,
            "sales_test",
            datetime(2026, 9, 2, tzinfo=UTC),
            source_record_id=f"{sku}:{observed_on.isoformat()}",
        ),
    )


def units_with_total(total: int, days: int = 14) -> tuple[int, ...]:
    quotient, remainder = divmod(total, days)
    return tuple(
        quotient + (1 if index < remainder else 0)
        for index in range(days)
    )


def complete_history(
    previous_units: tuple[int, ...],
    current_units: tuple[int, ...],
    *,
    sku: SkuId = SKU,
    as_of: date = AS_OF,
) -> tuple[SalesObservation, ...]:
    assert len(previous_units) == POLICY.comparison_window_days
    assert len(current_units) == POLICY.averaging_window_days
    comparison_start = as_of - timedelta(
        days=POLICY.averaging_window_days + POLICY.comparison_window_days
    )
    current_start = as_of - timedelta(days=POLICY.averaging_window_days)
    return tuple(
        observation(sku, comparison_start + timedelta(days=index), units)
        for index, units in enumerate(previous_units)
    ) + tuple(
        observation(sku, current_start + timedelta(days=index), units)
        for index, units in enumerate(current_units)
    )


def calculate(
    previous_units: tuple[int, ...],
    current_units: tuple[int, ...],
) -> SalesMetrics:
    return calculate_sales_metrics(
        SKU,
        complete_history(previous_units, current_units),
        POLICY,
        AS_OF,
    )


def test_stable_complete_windows_have_explicit_boundaries_and_evidence() -> None:
    result = calculate((10,) * 14, (10,) * 14)

    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.current.period == DateRange(date(2026, 8, 19), date(2026, 9, 1))
    assert result.comparison.period == DateRange(date(2026, 8, 5), date(2026, 8, 18))
    assert result.current.observed_days == result.current.expected_days == 14
    assert result.comparison.observed_days == result.comparison.expected_days == 14
    assert result.current.total_units == result.comparison.total_units == 140
    assert result.current.average_daily_sales == Decimal("10")
    assert result.comparison.average_daily_sales == Decimal("10")
    assert result.relative_change == Decimal("0")
    assert result.direction is SalesChangeDirection.STABLE
    assert len(result.current.source_refs) == 14


def test_material_sales_increase_uses_decimal_relative_change() -> None:
    result = calculate((4,) * 14, (6,) * 14)

    assert result.current.average_daily_sales == Decimal("6")
    assert result.comparison.average_daily_sales == Decimal("4")
    assert result.relative_change == Decimal("0.5")
    assert isinstance(result.relative_change, Decimal)
    assert result.direction is SalesChangeDirection.INCREASING


def test_material_sales_decline_uses_negative_decimal_change() -> None:
    result = calculate((8,) * 14, (4,) * 14)

    assert result.relative_change == Decimal("-0.5")
    assert result.direction is SalesChangeDirection.DECREASING


def test_current_zero_and_previous_positive_is_a_valid_full_decline() -> None:
    result = calculate((5,) * 14, (0,) * 14)

    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.relative_change == Decimal("-1")
    assert result.direction is SalesChangeDirection.DECREASING


def test_two_complete_zero_windows_are_stable_not_missing() -> None:
    result = calculate((0,) * 14, (0,) * 14)

    assert result.current.observed_days == result.comparison.observed_days == 14
    assert result.current.missing_dates == result.comparison.missing_dates == ()
    assert result.current.average_daily_sales == Decimal("0")
    assert result.comparison.average_daily_sales == Decimal("0")
    assert result.relative_change == Decimal("0")
    assert result.change_status is AvailabilityStatus.AVAILABLE
    assert result.direction is SalesChangeDirection.STABLE


def test_previous_zero_and_current_positive_has_no_invented_percentage() -> None:
    result = calculate((0,) * 14, (2,) * 14)

    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.relative_change is None
    assert result.change_status is AvailabilityStatus.NOT_APPLICABLE
    assert result.direction is SalesChangeDirection.INCREASING


def test_missing_current_day_makes_only_current_average_unavailable() -> None:
    history = complete_history((5,) * 14, (7,) * 14)

    result = calculate_sales_metrics(SKU, history[:-1], POLICY, AS_OF)

    assert result.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.current.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.current.observed_days == 13
    assert result.current.average_daily_sales is None
    assert result.current.missing_dates == (date(2026, 9, 1),)
    assert result.comparison.status is AvailabilityStatus.AVAILABLE
    assert result.comparison.average_daily_sales == Decimal("5")
    assert result.change_status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.relative_change is None
    assert result.direction is None


def test_missing_comparison_day_preserves_complete_current_average() -> None:
    history = complete_history((5,) * 14, (7,) * 14)

    result = calculate_sales_metrics(SKU, history[1:], POLICY, AS_OF)

    assert result.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.comparison.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.comparison.observed_days == 13
    assert result.comparison.average_daily_sales is None
    assert result.comparison.missing_dates == (date(2026, 8, 5),)
    assert result.current.status is AvailabilityStatus.AVAILABLE
    assert result.current.average_daily_sales == Decimal("7")


def test_empty_history_is_an_expected_insufficient_data_result() -> None:
    result = calculate_sales_metrics(SKU, (), POLICY, AS_OF)

    assert result.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.current.observed_days == result.comparison.observed_days == 0
    assert len(result.current.missing_dates) == len(result.comparison.missing_dates) == 14
    assert result.current.total_units == result.comparison.total_units == 0
    assert result.current.average_daily_sales is None
    assert result.comparison.average_daily_sales is None


def test_duplicate_sku_date_observations_are_rejected() -> None:
    history = complete_history((4,) * 14, (5,) * 14)

    with pytest.raises(CalculationPreconditionError) as raised:
        calculate_sales_metrics(SKU, (*history, history[0]), POLICY, AS_OF)

    assert raised.value.code == "sales.duplicate_observation_date"


def test_mixed_sku_observations_are_rejected_even_outside_windows() -> None:
    history = complete_history((4,) * 14, (5,) * 14)
    unrelated = observation(OTHER_SKU, date(2020, 1, 1), 999)

    with pytest.raises(CalculationPreconditionError) as raised:
        calculate_sales_metrics(SKU, (*history, unrelated), POLICY, AS_OF)

    assert raised.value.code == "sales.mixed_skus"


def test_input_order_does_not_affect_metrics() -> None:
    history = complete_history((4,) * 14, (7,) * 14)

    chronological = calculate_sales_metrics(SKU, history, POLICY, AS_OF)
    reversed_input = calculate_sales_metrics(SKU, reversed(history), POLICY, AS_OF)

    assert reversed_input == chronological


def test_repeated_calls_with_identical_inputs_are_identical() -> None:
    history = complete_history(units_with_total(47), units_with_total(61))

    first = calculate_sales_metrics(SKU, history, POLICY, AS_OF)
    second = calculate_sales_metrics(SKU, history, POLICY, AS_OF)

    assert second == first


def test_as_of_future_and_older_observations_do_not_contaminate_windows() -> None:
    history = complete_history((4,) * 14, (4,) * 14)
    outside = (
        observation(SKU, date(2026, 8, 4), 999),
        observation(SKU, AS_OF, 999),
        observation(SKU, AS_OF + timedelta(days=1), 999),
    )

    result = calculate_sales_metrics(SKU, (*outside, *history), POLICY, AS_OF)

    assert result.comparison.period.start == date(2026, 8, 5)
    assert result.current.period.end == date(2026, 9, 1)
    assert result.current.total_units == result.comparison.total_units == 56
    assert result.direction is SalesChangeDirection.STABLE


@pytest.mark.parametrize(
    ("current_total", "expected_change", "expected_direction"),
    [
        (70, Decimal("0.25"), SalesChangeDirection.INCREASING),
        (69, None, SalesChangeDirection.STABLE),
        (42, Decimal("-0.25"), SalesChangeDirection.DECREASING),
        (43, None, SalesChangeDirection.STABLE),
    ],
)
def test_material_change_threshold_is_inclusive_at_exact_boundaries(
    current_total: int,
    expected_change: Decimal | None,
    expected_direction: SalesChangeDirection,
) -> None:
    result = calculate(units_with_total(56), units_with_total(current_total))

    if expected_change is not None:
        assert result.relative_change == expected_change
    assert result.direction is expected_direction


def test_ratio_results_do_not_depend_on_ambient_decimal_context() -> None:
    history = complete_history(units_with_total(3), units_with_total(5))
    results = []
    for rounding in (ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP):
        with localcontext() as context:
            context.prec = 3
            context.rounding = rounding
            results.append(calculate_sales_metrics(SKU, history, POLICY, AS_OF))

    assert results[0] == results[1] == results[2]
    assert results[0].comparison.average_daily_sales == Decimal(
        "0.2142857142857142857142857143"
    )
    assert results[0].current.average_daily_sales == Decimal(
        "0.3571428571428571428571428571"
    )
    assert results[0].relative_change == Decimal(
        "0.6666666666666666666666666667"
    )


def test_policy_window_lengths_are_used_without_demo_constants() -> None:
    policy = SalesPolicy(
        PolicyIdentity("short-windows", "1"),
        averaging_window_days=3,
        comparison_window_days=2,
        material_change_threshold=Decimal("0.25"),
    )
    observations = (
        observation(SKU, date(2026, 8, 28), 3),
        observation(SKU, date(2026, 8, 29), 5),
        observation(SKU, date(2026, 8, 30), 6),
        observation(SKU, date(2026, 8, 31), 6),
        observation(SKU, date(2026, 9, 1), 6),
    )

    result = calculate_sales_metrics(SKU, observations, policy, AS_OF)

    assert result.comparison.period == DateRange(date(2026, 8, 28), date(2026, 8, 29))
    assert result.current.period == DateRange(date(2026, 8, 30), date(2026, 9, 1))
    assert result.comparison.average_daily_sales == Decimal("4")
    assert result.current.average_daily_sales == Decimal("6")
    assert result.relative_change == Decimal("0.5")


def test_impossible_window_boundaries_fail_explicitly() -> None:
    with pytest.raises(CalculationPreconditionError) as raised:
        calculate_sales_metrics(SKU, (), POLICY, date.min)

    assert raised.value.code == "sales.invalid_window_boundaries"


def test_sales_result_is_immutable() -> None:
    result = calculate((4,) * 14, (4,) * 14)

    with pytest.raises(FrozenInstanceError):
        result.relative_change = Decimal("1")

"""Unit tests for deterministic inventory and replenishment analytics."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, localcontext
from random import Random

import pytest

from app.analytics.inventory import (
    InboundTreatment,
    ReplenishmentTiming,
    calculate_inventory_analysis,
)
from app.analytics.sales import SalesMetrics, calculate_sales_metrics
from app.core.errors import CalculationPreconditionError, DataValidationError
from app.domain.catalog import SalesObservation, SalesPolicy
from app.domain.common import (
    AvailabilityStatus,
    PolicyIdentity,
    Provenance,
    SkuId,
    SourceType,
)
from app.domain.inventory import (
    InboundStatus,
    InboundSupply,
    InventoryPolicy,
    InventorySnapshot,
    LeadTime,
    ReplenishmentConstraints,
)


AS_OF = date(2026, 9, 2)
SKU = SkuId("SKU-001")
OTHER_SKU = SkuId("SKU-002")
STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)


def provenance(source_ref: str) -> Provenance:
    return Provenance(
        SourceType.DEMO,
        "inventory_test",
        STAMP,
        source_record_id=source_ref,
    )


def sales_metrics(
    current_units: tuple[int, ...] = (10,) * 14,
    *,
    comparison_units: tuple[int, ...] | None = None,
    sku: SkuId = SKU,
    omit_dates: tuple[date, ...] = (),
) -> SalesMetrics:
    comparison = comparison_units or current_units
    policy = SalesPolicy(
        PolicyIdentity(f"sales-{len(current_units)}-{len(comparison)}", "1"),
        averaging_window_days=len(current_units),
        comparison_window_days=len(comparison),
        material_change_threshold=Decimal("0.25"),
    )
    comparison_start = AS_OF - timedelta(days=len(current_units) + len(comparison))
    current_start = AS_OF - timedelta(days=len(current_units))
    observations = tuple(
        SalesObservation(
            sku,
            comparison_start + timedelta(days=index),
            units,
            provenance(f"sales:comparison:{index}"),
        )
        for index, units in enumerate(comparison)
    ) + tuple(
        SalesObservation(
            sku,
            current_start + timedelta(days=index),
            units,
            provenance(f"sales:current:{index}"),
        )
        for index, units in enumerate(current_units)
    )
    return calculate_sales_metrics(
        sku,
        tuple(row for row in observations if row.observed_on not in omit_dates),
        policy,
        AS_OF,
    )


def snapshot(stock: int = 500, *, sku: SkuId = SKU, observed_at: datetime = STAMP) -> InventorySnapshot:
    return InventorySnapshot(sku, stock, observed_at, provenance("inventory:snapshot"))


def lead_time(
    production: int | None = 7,
    delivery: int | None = 3,
    buffer: int | None = 3,
) -> LeadTime:
    return LeadTime(production, delivery, buffer, provenance("lead-time:record"))


def constraints(
    minimum_order_quantity: int | None = None,
    pack_size: int | None = None,
    *,
    sku: SkuId = SKU,
) -> ReplenishmentConstraints:
    return ReplenishmentConstraints(
        sku,
        minimum_order_quantity,
        pack_size,
        provenance("constraints:record"),
    )


def inventory_policy(
    selected_lead_time: LeadTime,
    selected_constraints: ReplenishmentConstraints,
    *,
    target_coverage_days: int = 30,
) -> InventoryPolicy:
    return InventoryPolicy(
        PolicyIdentity("inventory-test", "1"),
        selected_lead_time,
        target_coverage_days=target_coverage_days,
        warning_window_days=7,
        overstock_threshold_days=90,
        minimum_order_quantity=selected_constraints.minimum_order_quantity,
        pack_size=selected_constraints.pack_size,
    )


def inbound(
    quantity: int,
    arrival: date | None,
    *,
    status: InboundStatus = InboundStatus.CONFIRMED,
    sku: SkuId = SKU,
    source_ref: str = "inbound:1",
) -> InboundSupply:
    return InboundSupply(sku, quantity, status, arrival, provenance(source_ref))


def analyze(
    *,
    stock: int = 500,
    sales: SalesMetrics | None = None,
    supplies: tuple[InboundSupply, ...] = (),
    selected_lead_time: LeadTime | None = None,
    selected_constraints: ReplenishmentConstraints | None = None,
    target_coverage_days: int = 30,
) -> object:
    sales = sales or sales_metrics()
    selected_lead_time = selected_lead_time or lead_time()
    selected_constraints = selected_constraints or constraints()
    return calculate_inventory_analysis(
        snapshot(stock),
        sales,
        supplies,
        selected_lead_time,
        selected_constraints,
        inventory_policy(
            selected_lead_time,
            selected_constraints,
            target_coverage_days=target_coverage_days,
        ),
        AS_OF,
    )


def test_healthy_stock_has_complete_timing_and_no_replenishment_need() -> None:
    result = analyze(stock=500)

    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.average_daily_sales == Decimal("10")
    assert result.stock_coverage_days == Decimal("50")
    assert result.stockout_date == date(2026, 10, 22)
    assert result.supply_lead_days == 10
    assert result.required_coverage_horizon_days == 13
    assert result.latest_safe_start_date == date(2026, 10, 9)
    assert result.replenishment_timing is ReplenishmentTiming.FUTURE
    assert result.planned_replenishment_arrival_date == date(2026, 9, 12)
    assert result.projected_stock_at_replenishment_arrival == Decimal("400")
    assert result.reorder_point_units == 130
    assert result.target_stock_units == 430
    assert result.raw_required_quantity == Decimal("0")
    assert result.recommended_replenishment_quantity == 0
    assert "inventory:snapshot" in result.source_refs
    assert "sales:current:0" in result.source_refs
    assert "sales:comparison:0" not in result.source_refs
    assert "lead-time:record" in result.source_refs
    assert "constraints:record" in result.source_refs


@pytest.mark.parametrize(
    ("stock", "expected_coverage", "expected_stockout"),
    [
        (10, Decimal("1"), date(2026, 9, 3)),
        (20, Decimal("2"), date(2026, 9, 4)),
        (
            10,
            Decimal("3.333333333333333333333333333"),
            date(2026, 9, 6),
        ),
    ],
)
def test_exact_and_fractional_stockout_boundaries(
    stock: int,
    expected_coverage: Decimal,
    expected_stockout: date,
) -> None:
    sales = sales_metrics((3,) * 14) if stock == 10 and expected_coverage > 1 else sales_metrics()

    result = analyze(stock=stock, sales=sales)

    assert result.stock_coverage_days == expected_coverage
    assert result.stockout_date == expected_stockout


def test_repeating_sales_rate_uses_exact_window_ratio_for_stockout() -> None:
    sales = sales_metrics((1, 0, 0), comparison_units=(1, 0, 0))

    result = analyze(stock=1, sales=sales)

    assert result.average_daily_sales == Decimal("0.3333333333333333333333333333")
    assert result.stock_coverage_days == Decimal("3")
    assert result.stockout_date == AS_OF + timedelta(days=3)


def test_repeating_sales_rate_preserves_exact_inbound_boundary() -> None:
    sales = sales_metrics((2, 0, 0), comparison_units=(2, 0, 0))
    supply = inbound(10, AS_OF + timedelta(days=3))

    result = analyze(stock=2, sales=sales, supplies=(supply,))

    assert result.average_daily_sales == Decimal("0.6666666666666666666666666667")
    assert result.inbound_events[0].treatment is InboundTreatment.APPLIED
    assert result.eligible_confirmed_inbound_units == 10
    assert result.stockout_date == AS_OF + timedelta(days=18)


def test_repeating_sales_rate_preserves_exact_target_ceiling() -> None:
    sales = sales_metrics((2, 0, 0), comparison_units=(2, 0, 0))
    no_lead = lead_time(0, 0, 0)

    result = analyze(
        stock=0,
        sales=sales,
        selected_lead_time=no_lead,
        target_coverage_days=3,
    )

    assert result.required_coverage_horizon_days == 0
    assert result.target_stock_units == 2
    assert result.raw_required_quantity == Decimal("2")
    assert result.base_replenishment_quantity == 2
    assert result.recommended_replenishment_quantity == 2


@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP])
def test_exact_repeating_rate_boundaries_ignore_ambient_context(rounding: str) -> None:
    with localcontext() as context:
        context.prec = 3
        context.rounding = rounding
        one_third = analyze(
            stock=1,
            sales=sales_metrics((1, 0, 0), comparison_units=(1, 0, 0)),
        )
        two_thirds = analyze(
            stock=2,
            sales=sales_metrics((2, 0, 0), comparison_units=(2, 0, 0)),
            supplies=(inbound(10, AS_OF + timedelta(days=3)),),
        )

    assert one_third.stockout_date == AS_OF + timedelta(days=3)
    assert two_thirds.inbound_events[0].treatment is InboundTreatment.APPLIED


def test_zero_stock_is_an_available_already_depleted_state() -> None:
    result = analyze(stock=0)

    assert result.depletion_status is AvailabilityStatus.AVAILABLE
    assert result.stock_coverage_days == Decimal("0")
    assert result.stockout_date == AS_OF
    assert result.latest_safe_start_date == date(2026, 8, 20)
    assert result.replenishment_timing is ReplenishmentTiming.ALREADY_LATE
    assert result.raw_required_quantity == Decimal("430")
    assert result.recommended_replenishment_quantity == 430


def test_complete_zero_sales_is_non_depleting_not_infinite_or_missing() -> None:
    result = analyze(stock=300, sales=sales_metrics((0,) * 14))

    assert result.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.average_daily_sales == Decimal("0")
    assert result.depletion_status is AvailabilityStatus.NOT_APPLICABLE
    assert result.stock_coverage_days is None
    assert result.stockout_date is None
    assert result.timing_status is AvailabilityStatus.NOT_APPLICABLE
    assert result.replenishment_status is AvailabilityStatus.NOT_APPLICABLE
    assert result.raw_required_quantity == Decimal("0")
    assert result.recommended_replenishment_quantity == 0


def test_insufficient_current_sales_preserves_stock_and_lead_time_facts() -> None:
    incomplete_sales = sales_metrics(omit_dates=(date(2026, 9, 1),))

    result = analyze(stock=25, sales=incomplete_sales)

    assert result.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.sellable_stock == 25
    assert result.average_daily_sales is None
    assert result.depletion_status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.stockout_date is None
    assert result.lead_time_status is AvailabilityStatus.AVAILABLE
    assert result.supply_lead_days == 10
    assert result.replenishment_status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.recommended_replenishment_quantity is None


def test_available_current_sales_remains_authoritative_when_comparison_is_incomplete() -> None:
    comparison_start = AS_OF - timedelta(days=28)
    current_available = sales_metrics(omit_dates=(comparison_start,))

    result = analyze(stock=100, sales=current_available)

    assert current_available.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert current_available.current.status is AvailabilityStatus.AVAILABLE
    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.average_daily_sales == Decimal("10")
    assert result.stock_coverage_days == Decimal("10")


def test_confirmed_inbound_at_exact_depletion_boundary_prevents_gap() -> None:
    supply = inbound(100, AS_OF + timedelta(days=2))

    result = analyze(stock=20, supplies=(supply,))

    assert result.stockout_date == AS_OF + timedelta(days=12)
    assert result.eligible_confirmed_inbound_units == 100
    assert result.inbound_events[0].treatment is InboundTreatment.APPLIED
    assert "inbound:1" in result.source_refs


def test_confirmed_inbound_after_depletion_is_late_and_not_counted() -> None:
    supply = inbound(100, AS_OF + timedelta(days=3))

    result = analyze(stock=20, supplies=(supply,))

    assert result.stockout_date == AS_OF + timedelta(days=2)
    assert result.eligible_confirmed_inbound_units == 0
    assert result.inbound_events[0].treatment is InboundTreatment.LATE_AFTER_STOCKOUT
    assert result.raw_required_quantity == Decimal("410")


def test_unconfirmed_inbound_cannot_rescue_stockout_or_reduce_quantity() -> None:
    supply = inbound(
        100,
        AS_OF + timedelta(days=1),
        status=InboundStatus.UNCONFIRMED,
    )

    result = analyze(stock=20, supplies=(supply,))

    assert result.stockout_date == AS_OF + timedelta(days=2)
    assert result.eligible_confirmed_inbound_units == 0
    assert result.inbound_events[0].treatment is InboundTreatment.UNCONFIRMED
    assert result.base_replenishment_quantity == 410
    assert "inbound:1" not in result.source_refs


def test_multiple_confirmed_batches_are_distinct_and_extend_projection() -> None:
    supplies = (
        inbound(10, AS_OF + timedelta(days=2), source_ref="inbound:10"),
        inbound(20, AS_OF + timedelta(days=3), source_ref="inbound:20"),
    )

    result = analyze(stock=20, supplies=supplies)

    assert result.stockout_date == AS_OF + timedelta(days=5)
    assert result.eligible_confirmed_inbound_units == 30
    assert [event.quantity for event in result.inbound_events] == [10, 20]
    assert all(
        event.treatment is InboundTreatment.APPLIED
        for event in result.inbound_events
    )


def test_same_date_batches_arrive_together_and_use_stable_secondary_order() -> None:
    supplies = (
        inbound(20, AS_OF + timedelta(days=2), source_ref="inbound:b"),
        inbound(10, AS_OF + timedelta(days=2), source_ref="inbound:a"),
    )

    result = analyze(stock=20, supplies=supplies)
    reversed_result = analyze(stock=20, supplies=tuple(reversed(supplies)))

    assert result == reversed_result
    assert result.stockout_date == AS_OF + timedelta(days=5)
    assert [event.source_ref for event in result.inbound_events] == [
        "inbound:a",
        "inbound:b",
    ]


def test_missing_lead_time_only_removes_dependent_timing_and_quantity() -> None:
    missing = lead_time(delivery=None)

    result = analyze(stock=100, selected_lead_time=missing)

    assert result.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.stock_coverage_days == Decimal("10")
    assert result.stockout_date == AS_OF + timedelta(days=10)
    assert result.lead_time_status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.supply_lead_days is None
    assert result.latest_safe_start_date is None
    assert result.replenishment_status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.target_stock_units is None


def test_explicit_zero_day_lead_components_are_valid() -> None:
    no_lead = lead_time(0, 0, 0)

    result = analyze(stock=100, selected_lead_time=no_lead)

    assert result.lead_time_status is AvailabilityStatus.AVAILABLE
    assert result.supply_lead_days == 0
    assert result.required_coverage_horizon_days == 0
    assert result.planned_replenishment_arrival_date == AS_OF
    assert result.projected_stock_at_replenishment_arrival == Decimal("100")
    assert result.latest_safe_start_date == result.stockout_date


@pytest.mark.parametrize(
    ("stock", "expected_date", "expected_timing"),
    [
        (140, AS_OF + timedelta(days=1), ReplenishmentTiming.FUTURE),
        (130, AS_OF, ReplenishmentTiming.DUE_NOW),
        (120, AS_OF - timedelta(days=1), ReplenishmentTiming.ALREADY_LATE),
    ],
)
def test_latest_safe_start_boundary_states(
    stock: int,
    expected_date: date,
    expected_timing: ReplenishmentTiming,
) -> None:
    result = analyze(stock=stock)

    assert result.latest_safe_start_date == expected_date
    assert result.replenishment_timing is expected_timing


@pytest.mark.parametrize("stock", [430, 500])
def test_zero_or_negative_raw_need_does_not_trigger_minimum_order(stock: int) -> None:
    constrained = constraints(100, 12)

    result = analyze(stock=stock, selected_constraints=constrained)

    assert result.raw_required_quantity == Decimal("0")
    assert result.base_replenishment_quantity == 0
    assert result.recommended_replenishment_quantity == 0


def test_fractional_raw_need_rounds_up_to_a_whole_unit() -> None:
    sales = sales_metrics((2, 3), comparison_units=(2, 3))

    result = analyze(stock=64, sales=sales)

    assert result.average_daily_sales == Decimal("2.5")
    assert result.raw_required_quantity == Decimal("43.5")
    assert result.target_stock_units == 108
    assert result.base_replenishment_quantity == 44
    assert result.recommended_replenishment_quantity == 44


@pytest.mark.parametrize(
    ("stock", "expected_base", "expected_final"),
    [(400, 30, 50), (350, 80, 80)],
)
def test_minimum_order_quantity_never_reduces_positive_need(
    stock: int,
    expected_base: int,
    expected_final: int,
) -> None:
    result = analyze(stock=stock, selected_constraints=constraints(50, None))

    assert result.base_replenishment_quantity == expected_base
    assert result.recommended_replenishment_quantity == expected_final


@pytest.mark.parametrize(
    ("stock", "expected_base", "expected_final"),
    [(360, 70, 70), (359, 71, 80), (357, 73, 80)],
)
def test_pack_size_rounds_positive_quantity_upward(
    stock: int,
    expected_base: int,
    expected_final: int,
) -> None:
    result = analyze(stock=stock, selected_constraints=constraints(None, 10))

    assert result.base_replenishment_quantity == expected_base
    assert result.recommended_replenishment_quantity == expected_final


def test_moq_then_pack_order_satisfies_both_constraints() -> None:
    result = analyze(stock=387, selected_constraints=constraints(50, 12))

    assert result.raw_required_quantity == Decimal("43")
    assert result.base_replenishment_quantity == 43
    assert result.recommended_replenishment_quantity == 60
    assert result.recommended_replenishment_quantity >= 50
    assert result.recommended_replenishment_quantity % 12 == 0


def test_missing_moq_and_pack_do_not_create_defaults() -> None:
    result = analyze(stock=357, selected_constraints=constraints(None, None))

    assert result.constraints.minimum_order_quantity is None
    assert result.constraints.pack_size is None
    assert result.base_replenishment_quantity == 73
    assert result.recommended_replenishment_quantity == 73


def test_projected_stock_at_planned_arrival_uses_only_timely_applied_inbound() -> None:
    supplies = (
        inbound(50, AS_OF + timedelta(days=5), source_ref="inbound:timely"),
        inbound(500, AS_OF + timedelta(days=20), source_ref="inbound:after-gap"),
    )

    result = analyze(stock=100, supplies=supplies)

    assert result.planned_replenishment_arrival_date == AS_OF + timedelta(days=10)
    assert result.projected_stock_at_replenishment_arrival == Decimal("50")
    assert result.eligible_confirmed_inbound_units == 50
    assert result.inbound_events[0].treatment is InboundTreatment.APPLIED
    assert result.inbound_events[1].treatment is InboundTreatment.LATE_AFTER_STOCKOUT


def test_late_confirmed_inbound_affects_physical_stock_but_not_first_gap() -> None:
    supply = inbound(100, AS_OF + timedelta(days=2), source_ref="inbound:late")

    result = analyze(stock=10, supplies=(supply,))

    assert result.stockout_date == AS_OF + timedelta(days=1)
    assert result.inbound_events[0].treatment is InboundTreatment.LATE_AFTER_STOCKOUT
    assert result.eligible_confirmed_inbound_units == 0
    assert result.projected_stock_at_replenishment_arrival == Decimal("20")
    assert result.raw_required_quantity == Decimal("420")


def test_physical_projection_excludes_supply_after_planned_arrival() -> None:
    supply = inbound(100, AS_OF + timedelta(days=11))

    result = analyze(stock=10, supplies=(supply,))

    assert result.stockout_date == AS_OF + timedelta(days=1)
    assert result.projected_stock_at_replenishment_arrival == Decimal("0")


def test_physical_projection_excludes_unconfirmed_supply_before_arrival() -> None:
    supply = inbound(
        100,
        AS_OF + timedelta(days=2),
        status=InboundStatus.UNCONFIRMED,
    )

    result = analyze(stock=10, supplies=(supply,))

    assert result.stockout_date == AS_OF + timedelta(days=1)
    assert result.projected_stock_at_replenishment_arrival == Decimal("0")


def test_multiple_late_inbound_batches_have_stable_physical_projection() -> None:
    supplies = (
        inbound(50, AS_OF + timedelta(days=2), source_ref="inbound:b"),
        inbound(50, AS_OF + timedelta(days=5), source_ref="inbound:a"),
    )

    result = analyze(stock=10, supplies=supplies)
    reversed_result = analyze(stock=10, supplies=tuple(reversed(supplies)))

    assert result == reversed_result
    assert result.stockout_date == AS_OF + timedelta(days=1)
    assert all(
        event.treatment is InboundTreatment.LATE_AFTER_STOCKOUT
        for event in result.inbound_events
    )
    assert result.eligible_confirmed_inbound_units == 0
    assert result.projected_stock_at_replenishment_arrival == Decimal("20")


def test_past_and_as_of_inbound_are_not_double_counted() -> None:
    supplies = (
        inbound(100, AS_OF - timedelta(days=1), source_ref="inbound:past"),
        inbound(100, AS_OF, source_ref="inbound:as-of"),
    )

    result = analyze(stock=10, supplies=supplies)

    assert result.stockout_date == AS_OF + timedelta(days=1)
    assert result.eligible_confirmed_inbound_units == 0
    assert all(
        event.treatment is InboundTreatment.PAST_OR_AS_OF
        for event in result.inbound_events
    )


def test_missing_arrival_and_zero_quantity_are_visible_but_not_applied() -> None:
    supplies = (
        inbound(100, None, source_ref="inbound:no-date"),
        inbound(0, AS_OF + timedelta(days=1), source_ref="inbound:zero"),
    )

    result = analyze(stock=20, supplies=supplies)

    assert result.eligible_confirmed_inbound_units == 0
    assert {event.treatment for event in result.inbound_events} == {
        InboundTreatment.MISSING_ARRIVAL,
        InboundTreatment.ZERO_QUANTITY,
    }


def test_inbound_evaluation_is_explicit_when_demand_is_zero_or_unavailable() -> None:
    future = inbound(100, AS_OF + timedelta(days=1))

    no_demand = analyze(stock=20, sales=sales_metrics((0,) * 14), supplies=(future,))
    unavailable_demand = analyze(
        stock=20,
        sales=sales_metrics(omit_dates=(date(2026, 9, 1),)),
        supplies=(future,),
    )

    assert no_demand.inbound_events[0].treatment is InboundTreatment.NOT_APPLICABLE_NO_DEMAND
    assert no_demand.eligible_confirmed_inbound_units == 0
    assert unavailable_demand.inbound_events[0].treatment is InboundTreatment.NOT_EVALUATED
    assert unavailable_demand.eligible_confirmed_inbound_units is None


def test_inbound_sorting_and_source_refs_are_order_independent() -> None:
    supplies = [
        inbound(20, AS_OF + timedelta(days=3), source_ref="inbound:b"),
        inbound(10, AS_OF + timedelta(days=2), source_ref="inbound:a"),
        inbound(
            999,
            AS_OF + timedelta(days=1),
            status=InboundStatus.UNCONFIRMED,
            source_ref="inbound:unconfirmed",
        ),
    ]
    shuffled = list(supplies)
    Random(8).shuffle(shuffled)

    first = analyze(stock=20, supplies=tuple(supplies))
    second = analyze(stock=20, supplies=tuple(shuffled))

    assert first == second
    assert first.source_refs == second.source_refs
    assert [event.source_ref for event in first.inbound_events] == [
        "inbound:unconfirmed",
        "inbound:a",
        "inbound:b",
    ]


@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP])
def test_decimal_outputs_and_integer_boundaries_ignore_ambient_context(rounding: str) -> None:
    with localcontext() as context:
        context.prec = 3
        context.rounding = rounding
        result = analyze(stock=10, sales=sales_metrics((3,) * 14))

    assert result.stock_coverage_days == Decimal("3.333333333333333333333333333")
    assert result.stockout_date == date(2026, 9, 6)
    assert result.raw_required_quantity == Decimal("119")
    assert result.recommended_replenishment_quantity == 119


def test_mixed_sku_inputs_are_rejected() -> None:
    selected_lead_time = lead_time()
    selected_constraints = constraints()
    selected_policy = inventory_policy(selected_lead_time, selected_constraints)

    with pytest.raises(CalculationPreconditionError):
        calculate_inventory_analysis(
            snapshot(sku=OTHER_SKU),
            sales_metrics(),
            (),
            selected_lead_time,
            selected_constraints,
            selected_policy,
            AS_OF,
        )
    with pytest.raises(CalculationPreconditionError) as raised:
        calculate_inventory_analysis(
            snapshot(),
            sales_metrics(),
            (inbound(1, AS_OF + timedelta(days=1), sku=OTHER_SKU),),
            selected_lead_time,
            selected_constraints,
            selected_policy,
            AS_OF,
        )
    assert raised.value.code == "inventory.inbound_sku_mismatch"


def test_policy_duplicates_must_agree_with_explicit_inputs() -> None:
    selected_lead_time = lead_time()
    selected_constraints = constraints(50, 10)

    with pytest.raises(CalculationPreconditionError) as raised:
        calculate_inventory_analysis(
            snapshot(),
            sales_metrics(),
            (),
            selected_lead_time,
            selected_constraints,
            inventory_policy(lead_time(1, 1, 1), selected_constraints),
            AS_OF,
        )
    assert raised.value.code == "inventory.policy_lead_time_mismatch"

    conflicting_constraints = constraints(None, None)
    with pytest.raises(CalculationPreconditionError) as raised:
        calculate_inventory_analysis(
            snapshot(),
            sales_metrics(),
            (),
            selected_lead_time,
            selected_constraints,
            inventory_policy(selected_lead_time, conflicting_constraints),
            AS_OF,
        )
    assert raised.value.code == "inventory.policy_constraints_mismatch"


def test_snapshot_and_sales_must_share_explicit_as_of_date() -> None:
    selected_lead_time = lead_time()
    selected_constraints = constraints()
    selected_policy = inventory_policy(selected_lead_time, selected_constraints)

    with pytest.raises(CalculationPreconditionError) as raised:
        calculate_inventory_analysis(
            snapshot(observed_at=datetime(2026, 9, 1, 23, tzinfo=UTC)),
            sales_metrics(),
            (),
            selected_lead_time,
            selected_constraints,
            selected_policy,
            AS_OF,
        )
    assert raised.value.code == "inventory.snapshot_as_of_mismatch"

    with pytest.raises(CalculationPreconditionError) as raised:
        calculate_inventory_analysis(
            snapshot(),
            sales_metrics(),
            (),
            selected_lead_time,
            selected_constraints,
            selected_policy,
            AS_OF + timedelta(days=1),
        )
    assert raised.value.code == "inventory.sales_as_of_mismatch"


def test_result_is_immutable_and_rejects_contradictory_states() -> None:
    result = analyze(stock=100)

    with pytest.raises(FrozenInstanceError):
        result.stockout_date = AS_OF
    with pytest.raises(DataValidationError) as raised:
        replace(result, stockout_date=None)
    assert raised.value.code == "inventory.incomplete_depletion_result"

    no_demand = analyze(stock=100, sales=sales_metrics((0,) * 14))
    with pytest.raises(DataValidationError) as raised:
        replace(no_demand, recommended_replenishment_quantity=1)
    assert raised.value.code == "inventory.inconsistent_zero_demand_result"


@pytest.mark.parametrize(
    ("stock", "wrong_timing"),
    [
        (140, ReplenishmentTiming.ALREADY_LATE),
        (130, ReplenishmentTiming.FUTURE),
        (120, ReplenishmentTiming.FUTURE),
    ],
)
def test_result_rejects_timing_that_conflicts_with_safe_start(
    stock: int,
    wrong_timing: ReplenishmentTiming,
) -> None:
    result = analyze(stock=stock)

    with pytest.raises(DataValidationError) as raised:
        replace(result, replenishment_timing=wrong_timing)

    assert raised.value.code == "inventory.inconsistent_replenishment_timing"


def test_result_rejects_stockout_before_as_of() -> None:
    result = analyze(stock=100)

    with pytest.raises(DataValidationError) as raised:
        replace(result, stockout_date=AS_OF - timedelta(days=1))

    assert raised.value.code == "inventory.stockout_before_as_of"


@pytest.mark.parametrize(
    ("field_name", "wrong_value"),
    [("supply_lead_days", 11), ("required_coverage_horizon_days", 14)],
)
def test_result_rejects_incorrect_lead_time_totals(
    field_name: str,
    wrong_value: int,
) -> None:
    result = analyze(stock=100)

    with pytest.raises(DataValidationError) as raised:
        replace(result, **{field_name: wrong_value})

    assert raised.value.code == "inventory.inconsistent_lead_time_result"


def test_result_rejects_quantity_below_raw_or_base_need() -> None:
    result = analyze(stock=387, selected_constraints=constraints(50, 12))

    with pytest.raises(DataValidationError) as raised:
        replace(result, base_replenishment_quantity=42)
    assert raised.value.code == "inventory.base_quantity_below_raw"

    with pytest.raises(DataValidationError) as raised:
        replace(result, recommended_replenishment_quantity=42)
    assert raised.value.code == "inventory.final_quantity_below_base"


def test_result_rejects_final_quantity_below_moq_or_outside_pack() -> None:
    result = analyze(stock=387, selected_constraints=constraints(50, 12))

    with pytest.raises(DataValidationError) as raised:
        replace(result, recommended_replenishment_quantity=48)
    assert raised.value.code == "inventory.final_quantity_below_moq"

    with pytest.raises(DataValidationError) as raised:
        replace(result, recommended_replenishment_quantity=50)
    assert raised.value.code == "inventory.final_quantity_not_pack_multiple"


def test_result_rejects_positive_order_when_raw_need_is_zero() -> None:
    result = analyze(stock=430, selected_constraints=constraints(50, 12))

    with pytest.raises(DataValidationError) as raised:
        replace(result, recommended_replenishment_quantity=12)

    assert raised.value.code == "inventory.inconsistent_zero_replenishment"


def test_event_rejects_unconfirmed_supply_marked_applied() -> None:
    result = analyze(
        stock=20,
        supplies=(
            inbound(
                100,
                AS_OF + timedelta(days=1),
                status=InboundStatus.UNCONFIRMED,
            ),
        ),
    )

    with pytest.raises(DataValidationError) as raised:
        replace(result.inbound_events[0], treatment=InboundTreatment.APPLIED)

    assert raised.value.code == "inventory.inconsistent_inbound_treatment"


def test_valid_partial_result_remains_constructible() -> None:
    result = analyze(stock=100, selected_lead_time=lead_time(None, None, None))

    assert replace(result) == result


def test_unrepresentable_stockout_date_fails_explicitly() -> None:
    with pytest.raises(CalculationPreconditionError) as raised:
        analyze(stock=10**20, sales=sales_metrics((1,) * 14))

    assert raised.value.code == "inventory.date_out_of_range"

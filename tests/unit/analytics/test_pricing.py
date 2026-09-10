"""Tests for deterministic price-only what-if simulation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, localcontext

import pytest

from app.analytics.pricing import simulate_price, simulate_prices
from app.analytics.unit_economics import calculate_unit_economics
from app.core.errors import DataValidationError
from app.domain.common import (
    AvailabilityStatus,
    Currency,
    DateRange,
    PolicyIdentity,
    Provenance,
    SafetyStatus,
    SkuId,
    SourceType,
)
from app.domain.economics import (
    CostComponent,
    EconomicsPolicy,
    PricingScenario,
    PricingScenarioResult,
    UnitEconomicsInput,
)
from app.providers.local_unit_economics import LocalUnitEconomicsProvider


SKU = SkuId("SKU-PRICING")
STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)


def source_provenance() -> Provenance:
    return Provenance(
        SourceType.DEMO,
        "pricing_test_source",
        STAMP,
        source_record_id="economics:SKU-PRICING",
    )


def economics_input(**overrides: object) -> UnitEconomicsInput:
    values = {
        "sku": SKU,
        "selling_price": Decimal("1000"),
        "currency": Currency.RUB,
        "cost_of_goods": Decimal("300"),
        "logistics_cost_per_unit": Decimal("100"),
        "commission_rate": Decimal("0.15"),
        "commission_per_unit": None,
        "advertising_cost_per_unit": Decimal("80"),
        "drr": Decimal("0.10"),
        "advertising_spend": Decimal("1000"),
        "attributable_revenue": Decimal("10000"),
        "other_variable_costs": (CostComponent("packaging", Decimal("50")),),
        "source_period": DateRange(date(2026, 8, 1), date(2026, 8, 31)),
        "provenance": source_provenance(),
    }
    values.update(overrides)
    return UnitEconomicsInput(**values)


def policy(**overrides: object) -> EconomicsPolicy:
    values = {
        "identity": PolicyIdentity("pricing-test", "1"),
        "currency": Currency.RUB,
        "minimum_profit_per_unit": Decimal("100"),
        "minimum_margin": Decimal("0.20"),
        "price_floor": Decimal("500"),
        "currency_quantum": Decimal("0.01"),
        "price_increment": Decimal("0.05"),
    }
    values.update(overrides)
    return EconomicsPolicy(**values)


def scenario(
    price: str | Decimal,
    scenario_id: str = "price-scenario",
    *,
    sku: SkuId = SKU,
) -> PricingScenario:
    return PricingScenario(
        scenario_id=scenario_id,
        sku=sku,
        hypothetical_price=Decimal(price),
        currency=Currency.RUB,
        as_of=STAMP,
        is_hypothetical=True,
        provenance=Provenance(
            SourceType.MANUAL,
            "pricing_simulator",
            STAMP,
            source_record_id=f"scenario:{scenario_id}",
        ),
    )


def test_same_price_scenario_has_zero_deltas_and_equivalent_economics() -> None:
    result = simulate_price(economics_input(), policy(), scenario("1000", "same"))

    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.price_delta == Decimal(0)
    assert result.profit_per_unit_delta == Decimal(0)
    assert result.margin_delta == Decimal(0)
    assert result.current_economics.profit_per_unit == result.economics.profit_per_unit
    assert result.current_economics.contribution_margin == result.economics.contribution_margin
    assert result.current_economics.minimum_safe == result.economics.minimum_safe


def test_rate_based_price_increase_reuses_unit_economics_engine() -> None:
    source = economics_input()
    selected_scenario = scenario("1200", "increase")
    result = simulate_price(source, policy(), selected_scenario)
    direct = calculate_unit_economics(
        replace(
            source,
            selling_price=Decimal("1200"),
            provenance=selected_scenario.provenance,
        ),
        policy(),
    )

    assert result.economics == direct
    assert result.current_economics.commission_cost == Decimal("150")
    assert result.current_economics.advertising_cost == Decimal("100")
    assert result.current_economics.total_variable_cost == Decimal("700")
    assert result.current_economics.profit_per_unit == Decimal("300")
    assert result.current_economics.contribution_margin == Decimal("0.30")
    assert result.economics.commission_cost == Decimal("180")
    assert result.economics.advertising_cost == Decimal("120")
    assert result.economics.fixed_cost_total == Decimal("450")
    assert result.economics.total_variable_cost == Decimal("750")
    assert result.economics.profit_per_unit == Decimal("450")
    assert result.economics.contribution_margin == Decimal("0.375")
    assert result.price_delta == Decimal("200")
    assert result.profit_per_unit_delta == Decimal("150")
    assert result.margin_delta == Decimal("0.075")


def test_price_decrease_recalculates_rates_and_can_cross_safe_boundary() -> None:
    result = simulate_price(economics_input(), policy(), scenario("800", "decrease"))

    assert result.economics.commission_cost == Decimal("120")
    assert result.economics.advertising_cost == Decimal("80")
    assert result.economics.fixed_cost_total == Decimal("450")
    assert result.economics.total_variable_cost == Decimal("650")
    assert result.economics.profit_per_unit == Decimal("150")
    assert result.economics.contribution_margin == Decimal("0.1875")
    assert result.price_delta == Decimal("-200")
    assert result.profit_per_unit_delta == Decimal("-150")
    assert result.margin_delta == Decimal("-0.1125")
    assert result.economics.minimum_safe_price == Decimal("818.20")
    assert result.distance_from_safe_price == Decimal("-18.20")
    assert result.safety_status is SafetyStatus.UNSAFE


@pytest.mark.parametrize(
    ("source_overrides", "expected_commission", "expected_advertising"),
    [
        ({}, Decimal("180"), Decimal("120")),
        (
            {
                "drr": None,
                "advertising_spend": None,
                "attributable_revenue": None,
                "advertising_cost_per_unit": Decimal("100"),
            },
            Decimal("180"),
            Decimal("100"),
        ),
        (
            {
                "commission_rate": None,
                "commission_per_unit": Decimal("150"),
            },
            Decimal("150"),
            Decimal("120"),
        ),
        (
            {
                "commission_rate": None,
                "commission_per_unit": Decimal("150"),
                "drr": None,
                "advertising_spend": None,
                "attributable_revenue": None,
                "advertising_cost_per_unit": Decimal("100"),
            },
            Decimal("150"),
            Decimal("100"),
        ),
    ],
)
def test_rate_and_absolute_cost_representations_are_preserved(
    source_overrides: dict[str, object],
    expected_commission: Decimal,
    expected_advertising: Decimal,
) -> None:
    result = simulate_price(
        economics_input(**source_overrides),
        policy(),
        scenario("1200", "representations"),
    )

    assert result.economics.commission_cost == expected_commission
    assert result.economics.advertising_cost == expected_advertising


def test_missing_cogs_preserves_price_delta_and_partial_candidate_facts() -> None:
    result = simulate_price(
        economics_input(cost_of_goods=None),
        policy(),
        scenario("1200", "missing-cogs"),
    )

    assert result.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.safety_status is SafetyStatus.UNAVAILABLE
    assert result.price_delta == Decimal("200")
    assert result.economics.commission_cost == Decimal("180")
    assert result.economics.advertising_cost == Decimal("120")
    assert result.economics.total_variable_cost is None
    assert result.economics.profit_per_unit is None
    assert result.economics.contribution_margin is None
    assert result.profit_per_unit_delta is None
    assert result.margin_delta is None
    assert result.distance_from_safe_price is None


def test_impossible_safe_price_keeps_candidate_economics_available() -> None:
    result = simulate_price(
        economics_input(commission_rate=Decimal("0.18"), drr=Decimal("1.20")),
        policy(),
        scenario("5000", "impossible-safe"),
    )

    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.economics.proportional_cost_rate == Decimal("1.38")
    assert result.economics.minimum_safe.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.distance_from_safe_price is None
    assert result.profit_per_unit_delta is not None
    assert result.margin_delta is not None
    assert result.safety_status is SafetyStatus.UNSAFE


def safe_boundary_source() -> UnitEconomicsInput:
    return economics_input(
        selling_price=Decimal("1200"),
        cost_of_goods=Decimal("500"),
        logistics_cost_per_unit=Decimal("100"),
        commission_rate=Decimal("0.20"),
        drr=Decimal("0.20"),
        advertising_cost_per_unit=Decimal(0),
        other_variable_costs=(),
    )


@pytest.mark.parametrize(
    ("candidate_price", "expected_safety", "expected_distance"),
    [
        ("999", SafetyStatus.UNSAFE, Decimal("-1")),
        ("1000", SafetyStatus.SAFE, Decimal(0)),
        ("1001", SafetyStatus.SAFE, Decimal("1")),
    ],
)
def test_candidate_below_equal_and_above_safe_price(
    candidate_price: str,
    expected_safety: SafetyStatus,
    expected_distance: Decimal,
) -> None:
    result = simulate_price(
        safe_boundary_source(),
        policy(
            minimum_profit_per_unit=Decimal(0),
            minimum_margin=Decimal(0),
            price_floor=None,
            price_increment=Decimal(1),
        ),
        scenario(candidate_price, f"safe-{candidate_price}"),
    )

    assert result.economics.minimum_safe_price == Decimal("1000")
    assert result.safety_status is expected_safety
    assert result.distance_from_safe_price == expected_distance


@pytest.mark.parametrize(
    ("candidate_price", "expected_safety"),
    [
        ("499", SafetyStatus.UNSAFE),
        ("500", SafetyStatus.SAFE),
        ("501", SafetyStatus.SAFE),
    ],
)
def test_zero_fixed_cost_equality_preserves_floor_safety(
    candidate_price: str,
    expected_safety: SafetyStatus,
) -> None:
    source = economics_input(
        cost_of_goods=Decimal(0),
        logistics_cost_per_unit=Decimal(0),
        commission_rate=Decimal(0),
        drr=Decimal(1),
        advertising_cost_per_unit=Decimal(0),
        other_variable_costs=(),
    )
    result = simulate_price(
        source,
        policy(
            minimum_profit_per_unit=Decimal(0),
            minimum_margin=Decimal(0),
            price_floor=Decimal("500"),
            price_increment=Decimal(1),
        ),
        scenario(candidate_price, f"zero-fixed-{candidate_price}"),
    )

    assert result.economics.minimum_safe_price == Decimal("500")
    assert result.safety_status is expected_safety


def test_candidate_price_is_not_silently_rounded() -> None:
    result = simulate_price(
        economics_input(),
        policy(),
        scenario("818.181", "exact-input"),
    )

    assert result.candidate_price == Decimal("818.181")
    assert result.economics.selling_price == Decimal("818.181")


def test_original_input_is_unchanged_and_repeated_simulations_are_independent() -> None:
    source = economics_input()
    original = replace(source)

    increase = simulate_price(source, policy(), scenario("1200", "first"))
    decrease = simulate_price(source, policy(), scenario("800", "second"))

    assert source == original
    assert source.selling_price == Decimal("1000")
    assert increase.current_price == decrease.current_price == Decimal("1000")
    assert increase.candidate_price == Decimal("1200")
    assert decrease.candidate_price == Decimal("800")


def test_multiple_scenarios_preserve_input_order_and_are_repeatable() -> None:
    selected = (
        scenario("1200", "third"),
        scenario("800", "first"),
        scenario("1000", "second"),
    )

    first = simulate_prices(economics_input(), policy(), selected)
    second = simulate_prices(economics_input(), policy(), selected)

    assert tuple(result.scenario.scenario_id for result in first) == (
        "third",
        "first",
        "second",
    )
    assert first == second


@pytest.mark.parametrize(
    "candidate_price",
    [Decimal(0), Decimal("-1"), True, 100.5, Decimal("NaN"), Decimal("Infinity")],
)
def test_invalid_candidate_price_is_rejected(candidate_price: object) -> None:
    with pytest.raises(DataValidationError):
        PricingScenario(
            "invalid-price",
            SKU,
            candidate_price,
            Currency.RUB,
            STAMP,
            True,
            source_provenance(),
        )


def test_scenario_identity_must_match_source() -> None:
    with pytest.raises(DataValidationError) as raised:
        simulate_price(
            economics_input(),
            policy(),
            scenario("1200", sku=SkuId("OTHER-SKU")),
        )

    assert raised.value.code == "pricing.scenario_identity_mismatch"


def test_result_and_composed_economics_are_immutable() -> None:
    result = simulate_price(economics_input(), policy(), scenario("1200"))

    with pytest.raises(FrozenInstanceError):
        result.price_delta = Decimal(0)
    with pytest.raises(FrozenInstanceError):
        result.current_economics.profit_per_unit = Decimal(0)
    with pytest.raises(FrozenInstanceError):
        result.economics.profit_per_unit = Decimal(0)


@pytest.mark.parametrize(
    ("field_name", "wrong_value", "expected_code"),
    [
        ("price_delta", Decimal(0), "economics.inconsistent_price_delta"),
        ("profit_per_unit_delta", Decimal(0), "economics.inconsistent_profit_delta"),
        ("margin_delta", Decimal(0), "economics.inconsistent_margin_delta"),
        (
            "distance_from_safe_price",
            Decimal(0),
            "economics.inconsistent_safe_price_distance",
        ),
    ],
)
def test_result_rejects_inconsistent_deltas(
    field_name: str,
    wrong_value: Decimal,
    expected_code: str,
) -> None:
    result = simulate_price(economics_input(), policy(), scenario("1200"))

    with pytest.raises(DataValidationError) as raised:
        replace(result, **{field_name: wrong_value})

    assert raised.value.code == expected_code


def test_result_rejects_inconsistent_candidate_and_current_economics() -> None:
    result = simulate_price(economics_input(), policy(), scenario("1200"))
    other_source = replace(economics_input(), sku=SkuId("OTHER-SKU"))
    other_current = calculate_unit_economics(other_source, policy())

    with pytest.raises(DataValidationError) as wrong_current:
        replace(result, current_economics=other_current)
    with pytest.raises(DataValidationError) as wrong_candidate:
        replace(result, economics=result.current_economics)

    assert wrong_current.value.code == "economics.conflicting_scenario_identity"
    assert wrong_candidate.value.code == "economics.conflicting_scenario_assumptions"


def test_unavailable_comparison_rejects_numeric_profit_or_margin_delta() -> None:
    result = simulate_price(
        economics_input(cost_of_goods=None),
        policy(),
        scenario("1200", "unavailable"),
    )

    with pytest.raises(DataValidationError):
        replace(result, profit_per_unit_delta=Decimal(0))
    with pytest.raises(DataValidationError):
        replace(result, margin_delta=Decimal(0))
    with pytest.raises(DataValidationError):
        replace(result, status=AvailabilityStatus.AVAILABLE)


def test_scenario_provenance_is_distinct_and_source_evidence_is_preserved() -> None:
    selected_scenario = scenario("1200", "traceable")
    result = simulate_price(economics_input(), policy(), selected_scenario)

    assert result.current_economics.source_input.provenance == source_provenance()
    assert result.economics.source_input.provenance == selected_scenario.provenance
    assert result.scenario.is_hypothetical is True
    assert set(result.evidence_refs) == {
        "economics:SKU-PRICING",
        "policy:pricing-test:1",
        "scenario:traceable",
    }


@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP])
@pytest.mark.parametrize("precision", [3, 10, 80])
def test_pricing_results_ignore_ambient_decimal_context(
    precision: int,
    rounding: str,
) -> None:
    source = economics_input(
        selling_price=Decimal("1000.00000000000000000000000000001"),
        commission_rate=Decimal("0.123456789"),
        drr=Decimal("0.076543211"),
    )
    selected_scenario = scenario(
        Decimal("1200.00000000000000000000000000003"),
        "context",
    )
    baseline = simulate_price(source, policy(), selected_scenario)

    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        result = simulate_price(source, policy(), selected_scenario)

    assert result == baseline


def test_result_contract_contains_no_demand_or_forecast_fields() -> None:
    field_names = {field.name for field in fields(PricingScenarioResult)}
    forbidden = {
        "expected_units_sold",
        "forecast_sales",
        "conversion_change",
        "demand_change",
        "predicted_revenue",
        "predicted_profit_total",
        "elasticity",
        "sales_uplift",
        "revenue_uplift",
    }

    assert field_names.isdisjoint(forbidden)


def demo_policy() -> EconomicsPolicy:
    return EconomicsPolicy(
        PolicyIdentity("demo-economics-policy", "1"),
        Currency.RUB,
        Decimal("100"),
        Decimal("0.20"),
        Decimal("300"),
        Decimal("0.01"),
        Decimal("1"),
    )


def demo_scenario(sku: str, price: str, scenario_id: str) -> PricingScenario:
    return PricingScenario(
        scenario_id,
        SkuId(sku),
        Decimal(price),
        Currency.RUB,
        STAMP,
        True,
        Provenance(
            SourceType.MANUAL,
            "demo_pricing_simulator",
            STAMP,
            source_record_id=f"scenario:{scenario_id}",
        ),
    )


def demo_result(sku: str, price: str, scenario_id: str) -> PricingScenarioResult:
    loaded = LocalUnitEconomicsProvider().get_unit_economics(SkuId(sku))
    assert loaded.value is not None
    assert loaded.issues == ()
    return simulate_price(
        loaded.value,
        demo_policy(),
        demo_scenario(sku, price, scenario_id),
    )


def test_demo_014_lower_same_and_higher_price_scenarios() -> None:
    lower = demo_result("DEMO-014", "1000", "demo-014-lower")
    same = demo_result("DEMO-014", "2490", "demo-014-same")
    higher = demo_result("DEMO-014", "3000", "demo-014-higher")

    assert lower.safety_status is SafetyStatus.UNSAFE
    assert same.safety_status is SafetyStatus.SAFE
    assert same.price_delta == Decimal(0)
    assert same.profit_per_unit_delta == Decimal(0)
    assert higher.safety_status is SafetyStatus.SAFE


def test_demo_015_below_equal_and_above_safe_price() -> None:
    below = demo_result("DEMO-015", "6824", "demo-015-below")
    exact = demo_result("DEMO-015", "6825", "demo-015-exact")
    above = demo_result("DEMO-015", "6826", "demo-015-above")

    assert below.economics.minimum_safe_price == Decimal("6825")
    assert below.safety_status is SafetyStatus.UNSAFE
    assert exact.distance_from_safe_price == Decimal(0)
    assert exact.safety_status is SafetyStatus.SAFE
    assert above.safety_status is SafetyStatus.SAFE


def test_demo_018_preserves_infeasible_safe_price() -> None:
    result = demo_result("DEMO-018", "5000", "demo-018")

    assert result.economics.drr == Decimal("1.20")
    assert result.economics.proportional_cost_rate == Decimal("1.38")
    assert result.economics.minimum_safe.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.distance_from_safe_price is None
    assert result.safety_status is SafetyStatus.UNSAFE


def test_demo_019_at_safe_price() -> None:
    result = demo_result("DEMO-019", "3990", "demo-019")

    assert result.economics.minimum_safe_price == Decimal("3990")
    assert result.distance_from_safe_price == Decimal(0)
    assert result.safety_status is SafetyStatus.SAFE


def test_demo_021_preserves_incomplete_economics_and_factual_price_delta() -> None:
    result = demo_result("DEMO-021", "800", "demo-021")

    assert result.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.price_delta == Decimal("110")
    assert result.economics.cost_of_goods is None
    assert result.economics.profit_per_unit is None
    assert result.economics.contribution_margin is None
    assert result.profit_per_unit_delta is None
    assert result.margin_delta is None
    assert result.distance_from_safe_price is None

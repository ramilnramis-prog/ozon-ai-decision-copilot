"""Unit tests for deterministic contribution economics and safe-price boundaries."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, localcontext
from random import Random

import pytest

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
    PriceBoundary,
    UnitEconomicsInput,
)
from app.providers.local_unit_economics import LocalUnitEconomicsProvider


SKU = SkuId("SKU-ECONOMICS")
STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)


def provenance(source_ref: str = "economics:SKU-ECONOMICS") -> Provenance:
    return Provenance(
        SourceType.DEMO,
        "economics_test",
        STAMP,
        source_record_id=source_ref,
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
        "other_variable_costs": (CostComponent("packaging", Decimal("20")),),
        "source_period": DateRange(date(2026, 8, 1), date(2026, 8, 31)),
        "provenance": provenance(),
    }
    values.update(overrides)
    return UnitEconomicsInput(**values)


def policy(**overrides: object) -> EconomicsPolicy:
    values = {
        "identity": PolicyIdentity("economics-test", "1"),
        "currency": Currency.RUB,
        "minimum_profit_per_unit": Decimal("100"),
        "minimum_margin": Decimal("0.20"),
        "price_floor": Decimal("500"),
        "currency_quantum": Decimal("0.01"),
        "price_increment": Decimal("0.05"),
    }
    values.update(overrides)
    return EconomicsPolicy(**values)


def zero_fixed_cost_result(
    *,
    selling_price: str = "1000",
    proportional_rate: str = "1",
    minimum_profit: str = "0",
    minimum_margin: str = "0",
    price_floor: Decimal | None = None,
):
    return calculate_unit_economics(
        economics_input(
            selling_price=Decimal(selling_price),
            cost_of_goods=Decimal(0),
            logistics_cost_per_unit=Decimal(0),
            commission_rate=Decimal(0),
            advertising_cost_per_unit=Decimal(0),
            drr=Decimal(proportional_rate),
            other_variable_costs=(),
        ),
        policy(
            minimum_profit_per_unit=Decimal(minimum_profit),
            minimum_margin=Decimal(minimum_margin),
            price_floor=price_floor,
            price_increment=Decimal(1),
        ),
    )


def test_profitable_product_reconciles_current_contribution_economics() -> None:
    result = calculate_unit_economics(economics_input(), policy())

    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.commission_cost == Decimal("150.00")
    assert result.advertising_cost == Decimal("100.00")
    assert result.other_variable_cost_total == Decimal("20")
    assert result.total_variable_cost == Decimal("670.00")
    assert result.profit_per_unit == Decimal("330.00")
    assert result.contribution_margin == Decimal("0.33")
    assert result.fixed_cost_total == Decimal("420")
    assert result.proportional_cost_rate == Decimal("0.25")
    assert result.safety_status is SafetyStatus.SAFE


def test_margin_is_profit_divided_by_sales_price_not_cogs() -> None:
    result = calculate_unit_economics(economics_input(), policy())

    assert result.contribution_margin == result.profit_per_unit / result.selling_price
    assert result.contribution_margin != result.profit_per_unit / result.cost_of_goods


def test_loss_is_a_valid_available_result() -> None:
    result = calculate_unit_economics(
        economics_input(cost_of_goods=Decimal("900")),
        policy(),
    )

    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.total_variable_cost == Decimal("1270.00")
    assert result.profit_per_unit == Decimal("-270.00")
    assert result.contribution_margin == Decimal("-0.27")
    assert result.safety_status is SafetyStatus.UNSAFE


def test_exact_break_even_is_zero_profit_and_safe_at_equality() -> None:
    source = economics_input(
        selling_price=Decimal("500"),
        cost_of_goods=Decimal("300"),
        logistics_cost_per_unit=Decimal("100"),
        commission_rate=Decimal("0.10"),
        drr=Decimal("0.10"),
        other_variable_costs=(),
    )
    selected_policy = policy(
        minimum_profit_per_unit=Decimal("0"),
        minimum_margin=Decimal("0"),
        price_floor=None,
        price_increment=Decimal("0.01"),
    )

    result = calculate_unit_economics(source, selected_policy)

    assert result.break_even_price == Decimal("500")
    assert result.minimum_safe_price == Decimal("500")
    assert result.profit_per_unit == Decimal("0")
    assert result.contribution_margin == Decimal("0")
    assert result.safety_status is SafetyStatus.SAFE


def test_price_below_break_even_produces_negative_profit() -> None:
    result = calculate_unit_economics(
        economics_input(
            selling_price=Decimal("400"),
            cost_of_goods=Decimal("300"),
            logistics_cost_per_unit=Decimal("100"),
            commission_rate=Decimal("0.10"),
            drr=Decimal("0.10"),
            other_variable_costs=(),
        ),
        policy(minimum_profit_per_unit=Decimal("0"), minimum_margin=Decimal("0")),
    )

    assert result.profit_per_unit == Decimal("-80")
    assert result.contribution_margin == Decimal("-0.2")


def test_fixed_commission_and_fixed_advertising_are_absolute_costs() -> None:
    result = calculate_unit_economics(
        economics_input(
            commission_rate=None,
            commission_per_unit=Decimal("150"),
            drr=None,
            advertising_spend=None,
            attributable_revenue=None,
            advertising_cost_per_unit=Decimal("100"),
        ),
        policy(),
    )

    assert result.commission_cost == Decimal("150")
    assert result.advertising_cost == Decimal("100")
    assert result.proportional_cost_rate == Decimal("0")
    assert result.fixed_cost_total == Decimal("670")
    assert result.total_variable_cost == Decimal("670")
    assert result.profit_per_unit == Decimal("330")


def test_drr_is_derived_from_valid_spend_and_revenue_and_drives_ads() -> None:
    result = calculate_unit_economics(
        economics_input(
            drr=None,
            advertising_spend=Decimal("200"),
            attributable_revenue=Decimal("1000"),
            advertising_cost_per_unit=Decimal("999"),
        ),
        policy(),
    )

    assert result.drr_status is AvailabilityStatus.AVAILABLE
    assert result.drr == Decimal("0.2")
    assert result.advertising_cost == Decimal("200.0")
    assert result.proportional_cost_rate == Decimal("0.35")


def test_direct_normalized_drr_takes_precedence_without_buyout_adjustment() -> None:
    result = calculate_unit_economics(
        economics_input(
            selling_price=Decimal("1530"),
            drr=Decimal("0.20"),
            advertising_cost_per_unit=Decimal("1"),
        ),
        policy(),
    )

    assert result.drr == Decimal("0.20")
    assert result.advertising_cost == Decimal("306.00")


def test_missing_drr_uses_explicit_per_unit_advertising() -> None:
    result = calculate_unit_economics(
        economics_input(
            drr=None,
            advertising_spend=None,
            attributable_revenue=None,
            advertising_cost_per_unit=Decimal("80"),
        ),
        policy(),
    )

    assert result.drr_status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.drr is None
    assert result.advertising_cost == Decimal("80")
    assert result.status is AvailabilityStatus.AVAILABLE


def test_zero_ad_revenue_makes_drr_not_applicable_but_fixed_ads_remain_valid() -> None:
    result = calculate_unit_economics(
        economics_input(
            drr=None,
            advertising_spend=Decimal("0"),
            attributable_revenue=Decimal("0"),
            advertising_cost_per_unit=Decimal("0"),
        ),
        policy(),
    )

    assert result.drr_status is AvailabilityStatus.NOT_APPLICABLE
    assert result.drr is None
    assert result.advertising_cost == Decimal("0")
    assert result.status is AvailabilityStatus.AVAILABLE


def test_missing_advertising_cost_makes_totals_unavailable() -> None:
    result = calculate_unit_economics(
        economics_input(
            drr=None,
            advertising_spend=None,
            attributable_revenue=None,
            advertising_cost_per_unit=None,
        ),
        policy(),
    )

    assert result.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.advertising_cost is None
    assert result.total_variable_cost is None
    assert result.minimum_safe_price is None


def test_missing_cogs_preserves_known_facts_without_fake_totals() -> None:
    result = calculate_unit_economics(
        economics_input(cost_of_goods=None),
        policy(),
    )

    assert result.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.selling_price == Decimal("1000")
    assert result.cost_of_goods is None
    assert result.commission_cost == Decimal("150.00")
    assert result.logistics_cost == Decimal("100")
    assert result.advertising_cost == Decimal("100.00")
    assert result.other_variable_cost_total == Decimal("20")
    assert result.total_variable_cost is None
    assert result.profit_per_unit is None
    assert result.contribution_margin is None
    assert result.break_even_price is None
    assert result.minimum_safe_price is None
    assert result.safety_status is SafetyStatus.UNAVAILABLE


@pytest.mark.parametrize(
    ("overrides", "expected_cost"),
    [
        ({"commission_rate": Decimal("0")}, Decimal("0")),
        ({"drr": Decimal("0")}, Decimal("0")),
        ({"logistics_cost_per_unit": Decimal("0")}, Decimal("0")),
    ],
)
def test_zero_cost_inputs_remain_valid(
    overrides: dict[str, object],
    expected_cost: Decimal,
) -> None:
    result = calculate_unit_economics(economics_input(**overrides), policy())
    field = (
        result.commission_cost
        if "commission_rate" in overrides
        else result.advertising_cost
        if "drr" in overrides
        else result.logistics_cost
    )

    assert field == expected_cost
    assert result.status is AvailabilityStatus.AVAILABLE


def test_empty_other_cost_collection_has_exact_zero_total() -> None:
    result = calculate_unit_economics(
        economics_input(other_variable_costs=()),
        policy(),
    )

    assert result.other_variable_costs == ()
    assert result.other_variable_cost_total == Decimal("0")


def test_break_even_and_minimum_profit_boundaries_follow_linear_model() -> None:
    result = calculate_unit_economics(economics_input(), policy())

    assert result.break_even.status is AvailabilityStatus.AVAILABLE
    assert result.break_even_price == Decimal("560")
    assert result.minimum_profit.status is AvailabilityStatus.AVAILABLE
    assert result.minimum_profit_price * Decimal("0.75") == pytest.approx(
        Decimal("520"), abs=Decimal("1e-36")
    )


def test_exact_minimum_profit_boundary_uses_inclusive_equality() -> None:
    selected_policy = policy(
        minimum_profit_per_unit=Decimal("100"),
        minimum_margin=Decimal("0"),
        price_floor=None,
        price_increment=Decimal("0.01"),
    )
    result = calculate_unit_economics(
        economics_input(
            cost_of_goods=Decimal("300"),
            logistics_cost_per_unit=Decimal("100"),
            commission_rate=Decimal("0.10"),
            drr=Decimal("0.10"),
            other_variable_costs=(),
        ),
        selected_policy,
    )

    assert result.minimum_profit_price == Decimal("625")
    assert result.minimum_safe_price == Decimal("625")
    assert result.minimum_safe_price * Decimal("0.80") - Decimal("400") == Decimal("100")


def test_exact_minimum_margin_boundary_uses_sales_margin_equality() -> None:
    selected_policy = policy(
        minimum_profit_per_unit=Decimal("0"),
        minimum_margin=Decimal("0.20"),
        price_floor=None,
        price_increment=Decimal("1"),
    )
    result = calculate_unit_economics(
        economics_input(
            cost_of_goods=Decimal("380"),
            logistics_cost_per_unit=Decimal("100"),
            commission_rate=Decimal("0.10"),
            drr=Decimal("0.10"),
            other_variable_costs=(),
        ),
        selected_policy,
    )

    assert result.fixed_cost_total == Decimal("480")
    assert result.minimum_margin_price == Decimal("800")
    assert result.minimum_safe_price == Decimal("800")
    assert (Decimal("800") * Decimal("0.8") - Decimal("480")) / Decimal("800") == Decimal("0.20")


def test_maximum_of_profit_margin_and_floor_constraints_wins() -> None:
    margin_wins = calculate_unit_economics(economics_input(), policy(price_floor=None))
    profit_wins = calculate_unit_economics(
        economics_input(),
        policy(
            minimum_profit_per_unit=Decimal("500"),
            minimum_margin=Decimal("0"),
            price_floor=None,
        ),
    )
    floor_wins = calculate_unit_economics(
        economics_input(),
        policy(price_floor=Decimal("1200")),
    )

    assert margin_wins.minimum_safe_price == Decimal("763.65")
    assert profit_wins.minimum_safe_price == Decimal("1226.70")
    assert floor_wins.minimum_safe_price == Decimal("1200")


def test_safe_price_rounds_strictly_up_to_increment_and_revalidates() -> None:
    result = calculate_unit_economics(economics_input(), policy())
    safe_price = result.minimum_safe_price
    assert safe_price is not None

    assert safe_price == Decimal("763.65")
    assert safe_price % Decimal("0.05") == 0
    safe_profit = safe_price * (Decimal(1) - result.proportional_cost_rate) - result.fixed_cost_total
    assert safe_profit >= result.policy.minimum_profit_per_unit
    assert safe_profit / safe_price >= result.policy.minimum_margin
    previous_step = safe_price - Decimal("0.05")
    previous_profit = previous_step * (Decimal(1) - result.proportional_cost_rate) - result.fixed_cost_total
    assert previous_profit / previous_step < result.policy.minimum_margin


@pytest.mark.parametrize(
    ("fixed_cost", "expected_safe"),
    [
        (Decimal("100.00"), Decimal("100.00")),
        (
            Decimal("100.00000000000000000000000000001"),
            Decimal("100.05"),
        ),
    ],
)
def test_safe_price_preserves_exact_and_tiny_above_step_boundaries(
    fixed_cost: Decimal,
    expected_safe: Decimal,
) -> None:
    result = calculate_unit_economics(
        economics_input(
            selling_price=Decimal("200"),
            cost_of_goods=fixed_cost,
            logistics_cost_per_unit=Decimal("0"),
            commission_rate=Decimal("0"),
            drr=Decimal("0"),
            other_variable_costs=(),
        ),
        policy(
            minimum_profit_per_unit=Decimal("0"),
            minimum_margin=Decimal("0"),
            price_floor=None,
            price_increment=Decimal("0.05"),
        ),
    )

    assert result.minimum_safe_price == expected_safe


@pytest.mark.parametrize(
    ("commission_rate", "drr"),
    [
        (Decimal("0.20"), Decimal("0.80")),
        (Decimal("0.18"), Decimal("1.20")),
    ],
)
def test_rate_at_or_above_one_has_no_finite_price_boundaries(
    commission_rate: Decimal,
    drr: Decimal,
) -> None:
    result = calculate_unit_economics(
        economics_input(commission_rate=commission_rate, drr=drr),
        policy(),
    )

    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.proportional_cost_rate == commission_rate + drr
    assert result.break_even.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.minimum_profit.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.minimum_margin.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.minimum_safe.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.minimum_safe_price is None
    assert result.safety_status is SafetyStatus.UNSAFE


def test_requested_margin_can_be_impossible_while_break_even_exists() -> None:
    result = calculate_unit_economics(
        economics_input(commission_rate=Decimal("0.45"), drr=Decimal("0.40")),
        policy(minimum_margin=Decimal("0.20")),
    )

    assert result.break_even.status is AvailabilityStatus.AVAILABLE
    assert result.minimum_profit.status is AvailabilityStatus.AVAILABLE
    assert result.minimum_margin.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.minimum_safe.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.safety_status is SafetyStatus.UNSAFE


def test_zero_fixed_cost_at_unit_rate_has_universally_satisfied_boundaries() -> None:
    result = zero_fixed_cost_result()

    assert result.fixed_cost_total == Decimal(0)
    assert result.proportional_cost_rate == Decimal(1)
    assert result.profit_per_unit == Decimal(0)
    assert result.contribution_margin == Decimal(0)
    for boundary in (
        result.break_even,
        result.minimum_profit,
        result.minimum_margin,
        result.minimum_safe,
    ):
        assert boundary.status is AvailabilityStatus.AVAILABLE
        assert boundary.price == Decimal(0)
    assert result.safety_status is SafetyStatus.SAFE


def test_zero_fixed_cost_at_unit_rate_cannot_meet_positive_minimum_profit() -> None:
    result = zero_fixed_cost_result(minimum_profit="1")

    assert result.break_even == PriceBoundary(
        AvailabilityStatus.AVAILABLE,
        Decimal(0),
    )
    assert result.minimum_profit.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.minimum_profit.price is None
    assert result.minimum_safe.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.minimum_safe.price is None
    assert result.safety_status is SafetyStatus.UNSAFE


def test_zero_fixed_cost_accepts_exact_attainable_margin_at_zero_denominator() -> None:
    result = zero_fixed_cost_result(
        proportional_rate="0.80",
        minimum_margin="0.20",
    )

    assert result.contribution_margin == Decimal("0.20")
    assert result.minimum_margin == PriceBoundary(
        AvailabilityStatus.AVAILABLE,
        Decimal(0),
    )
    assert result.minimum_safe.status is AvailabilityStatus.AVAILABLE
    assert result.safety_status is SafetyStatus.SAFE


def test_zero_fixed_cost_rejects_margin_above_attainable_rate() -> None:
    result = zero_fixed_cost_result(
        proportional_rate="0.81",
        minimum_margin="0.20",
    )

    assert result.contribution_margin == Decimal("0.19")
    assert result.minimum_margin.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.minimum_margin.price is None
    assert result.minimum_safe.status is AvailabilityStatus.NOT_APPLICABLE
    assert result.safety_status is SafetyStatus.UNSAFE


@pytest.mark.parametrize(
    ("selling_price", "expected_safety"),
    [
        ("499", SafetyStatus.UNSAFE),
        ("500", SafetyStatus.SAFE),
        ("501", SafetyStatus.SAFE),
    ],
)
def test_universally_satisfied_economics_still_enforces_price_floor(
    selling_price: str,
    expected_safety: SafetyStatus,
) -> None:
    result = zero_fixed_cost_result(
        selling_price=selling_price,
        price_floor=Decimal("500"),
    )

    assert result.break_even_price == Decimal(0)
    assert result.minimum_profit_price == Decimal(0)
    assert result.minimum_margin_price == Decimal(0)
    assert result.minimum_safe_price == Decimal("500")
    assert result.safety_status is expected_safety


@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP])
@pytest.mark.parametrize("precision", [3, 10, 80])
def test_results_ignore_ambient_decimal_context(precision: int, rounding: str) -> None:
    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        result = calculate_unit_economics(
            economics_input(
                selling_price=Decimal("1000.00000000000000000000000000001"),
                commission_rate=Decimal("0.123456789"),
                drr=Decimal("0.076543211"),
            ),
            policy(price_increment=Decimal("0.05")),
        )

    assert result.commission_cost == Decimal("123.45678900000000000000000000000123456789")
    assert result.advertising_cost == Decimal("76.54321100000000000000000000000076543211")
    assert result.proportional_cost_rate == Decimal("0.200000000")
    assert result.minimum_safe_price == Decimal("700.00")


def test_result_is_immutable_and_source_traceable() -> None:
    source = economics_input()
    selected_policy = policy()
    result = calculate_unit_economics(source, selected_policy)

    assert result.source_input is source
    assert result.policy is selected_policy
    assert result.evidence_refs == (
        "economics:SKU-ECONOMICS",
        "policy:economics-test:1",
    )
    with pytest.raises(FrozenInstanceError):
        result.profit_per_unit = Decimal("0")


@pytest.mark.parametrize(
    ("field_name", "wrong_value", "expected_code"),
    [
        ("profit_per_unit", None, "economics.incomplete_available_result"),
        ("total_variable_cost", Decimal("1"), "economics.inconsistent_total_cost"),
        ("profit_per_unit", Decimal("1"), "economics.inconsistent_profit"),
        ("contribution_margin", Decimal("0.1"), "economics.inconsistent_margin"),
        ("fixed_cost_total", Decimal("1"), "economics.inconsistent_fixed_cost_total"),
    ],
)
def test_result_rejects_inconsistent_authoritative_values(
    field_name: str,
    wrong_value: Decimal | None,
    expected_code: str,
) -> None:
    result = calculate_unit_economics(economics_input(), policy())

    with pytest.raises(DataValidationError) as raised:
        replace(result, **{field_name: wrong_value})

    assert raised.value.code == expected_code


def test_result_rejects_boundary_price_that_does_not_match_formula() -> None:
    result = calculate_unit_economics(economics_input(), policy())

    with pytest.raises(DataValidationError) as raised:
        replace(
            result,
            break_even=PriceBoundary(AvailabilityStatus.AVAILABLE, Decimal("561")),
        )

    assert raised.value.code == "economics.inconsistent_boundary_price"


def test_price_boundary_rejects_numeric_value_on_impossible_status() -> None:
    with pytest.raises(DataValidationError) as raised:
        PriceBoundary(AvailabilityStatus.NOT_APPLICABLE, Decimal("999999999"))

    assert raised.value.code == "economics.price_on_unavailable_boundary"


def test_result_rejects_safe_price_below_boundary_or_off_increment() -> None:
    result = calculate_unit_economics(economics_input(), policy())

    with pytest.raises(DataValidationError) as below:
        replace(
            result,
            minimum_safe=PriceBoundary(AvailabilityStatus.AVAILABLE, Decimal("700")),
        )
    with pytest.raises(DataValidationError) as unaligned:
        replace(
            result,
            minimum_safe=PriceBoundary(AvailabilityStatus.AVAILABLE, Decimal("763.64")),
        )

    assert below.value.code == "economics.safe_price_below_boundary"
    assert unaligned.value.code == "economics.safe_price_not_increment_aligned"


def test_result_rejects_feasible_boundary_on_impossible_denominator() -> None:
    result = calculate_unit_economics(
        economics_input(commission_rate=Decimal("0.20"), drr=Decimal("0.80")),
        policy(),
    )

    with pytest.raises(DataValidationError) as raised:
        replace(
            result,
            break_even=PriceBoundary(AvailabilityStatus.AVAILABLE, Decimal("1")),
        )

    assert raised.value.code == "economics.inconsistent_boundary_feasibility"


def test_currency_mismatch_fails_clearly() -> None:
    with pytest.raises(DataValidationError) as raised:
        calculate_unit_economics(
            economics_input(),
            replace(policy(), currency="RUB"),
        )

    assert raised.value.code == "domain.invalid_type"


def test_other_cost_input_order_does_not_change_calculated_values() -> None:
    components = [
        CostComponent("packaging", Decimal("20")),
        CostComponent("handling", Decimal("30")),
        CostComponent("defect allowance", Decimal("5")),
    ]
    shuffled = list(components)
    Random(9).shuffle(shuffled)

    first = calculate_unit_economics(
        economics_input(other_variable_costs=tuple(components)),
        policy(),
    )
    second = calculate_unit_economics(
        economics_input(other_variable_costs=tuple(shuffled)),
        policy(),
    )

    assert first.other_variable_cost_total == second.other_variable_cost_total
    assert first.total_variable_cost == second.total_variable_cost
    assert first.profit_per_unit == second.profit_per_unit
    assert first.minimum_safe_price == second.minimum_safe_price


def demo_policy() -> EconomicsPolicy:
    return EconomicsPolicy(
        PolicyIdentity("demo-economics-policy", "1"),
        Currency.RUB,
        Decimal("100.00"),
        Decimal("0.20"),
        Decimal("300.00"),
        Decimal("0.01"),
        Decimal("1.00"),
    )


def demo_result(sku: str):
    source = LocalUnitEconomicsProvider().get_unit_economics(SkuId(sku))
    assert source.value is not None
    assert source.issues == ()
    return calculate_unit_economics(source.value, demo_policy())


def test_demo_profitable_low_margin_loss_high_drr_and_missing_cogs() -> None:
    profitable = demo_result("DEMO-014")
    low_margin = demo_result("DEMO-019")
    loss = demo_result("DEMO-015")
    high_drr = demo_result("DEMO-018")
    missing_cogs = demo_result("DEMO-021")

    assert profitable.profit_per_unit == Decimal("1227.20")
    assert profitable.safety_status is SafetyStatus.SAFE
    assert low_margin.profit_per_unit == Decimal("28.00")
    assert low_margin.contribution_margin < Decimal("0.01")
    assert low_margin.safety_status is SafetyStatus.UNSAFE
    assert loss.profit_per_unit == Decimal("-649.70")
    assert loss.safety_status is SafetyStatus.UNSAFE
    assert high_drr.drr == Decimal("1.20")
    assert high_drr.proportional_cost_rate == Decimal("1.38")
    assert high_drr.minimum_safe.status is AvailabilityStatus.NOT_APPLICABLE
    assert high_drr.minimum_safe_price is None
    assert missing_cogs.cost_of_goods is None
    assert missing_cogs.commission_cost == Decimal("117.30")
    assert missing_cogs.advertising_cost == Decimal("45.00")
    assert missing_cogs.total_variable_cost is None
    assert missing_cogs.profit_per_unit is None
    assert missing_cogs.contribution_margin is None
    assert missing_cogs.break_even_price is None
    assert missing_cogs.minimum_safe_price is None

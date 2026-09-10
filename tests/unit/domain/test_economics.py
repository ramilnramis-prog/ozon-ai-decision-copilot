"""Tests for economics inputs, policies, result states, and pricing scenarios."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.core.errors import DataValidationError
from app.analytics.pricing import simulate_price
from app.analytics.unit_economics import calculate_unit_economics
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
    PricingScenario,
    PricingScenarioResult,
    UnitEconomicsInput,
    UnitEconomicsResult,
)


def provenance(source_type: SourceType = SourceType.DEMO) -> Provenance:
    return Provenance(
        source_type,
        "demo_economics",
        datetime(2026, 9, 2, 8, tzinfo=UTC),
    )


def economics_input(**overrides: object) -> UnitEconomicsInput:
    values = {
        "sku": SkuId("SKU-001"),
        "selling_price": Decimal("1000"),
        "currency": Currency.RUB,
        "cost_of_goods": Decimal("300"),
        "logistics_cost_per_unit": Decimal("100"),
        "commission_rate": Decimal("0.15"),
        "commission_per_unit": None,
        "advertising_cost_per_unit": None,
        "drr": Decimal("0.10"),
        "advertising_spend": Decimal("1000"),
        "attributable_revenue": Decimal("10000"),
        "other_variable_costs": (CostComponent("packaging", Decimal("20")),),
        "source_period": DateRange(date(2026, 8, 1), date(2026, 8, 31)),
        "provenance": provenance(),
    }
    values.update(overrides)
    return UnitEconomicsInput(**values)


def economics_policy(**overrides: object) -> EconomicsPolicy:
    values = {
        "identity": PolicyIdentity("economics-demo", "v1"),
        "currency": Currency.RUB,
        "minimum_profit_per_unit": Decimal("100"),
        "minimum_margin": Decimal("0.20"),
        "price_floor": Decimal("500"),
        "currency_quantum": Decimal("0.01"),
        "price_increment": Decimal("0.05"),
    }
    values.update(overrides)
    return EconomicsPolicy(**values)


def available_result(**overrides: object) -> UnitEconomicsResult:
    result = calculate_unit_economics(economics_input(), economics_policy())
    return replace(result, **overrides)


def unavailable_result(**overrides: object) -> UnitEconomicsResult:
    result = calculate_unit_economics(
        economics_input(cost_of_goods=None),
        economics_policy(),
    )
    return replace(result, **overrides)


def scenario(**overrides: object) -> PricingScenario:
    values = {
        "scenario_id": "scenario-1",
        "sku": SkuId("SKU-001"),
        "hypothetical_price": Decimal("900"),
        "currency": Currency.RUB,
        "as_of": datetime(2026, 9, 2, 8, tzinfo=UTC),
        "is_hypothetical": True,
        "provenance": provenance(SourceType.MANUAL),
    }
    values.update(overrides)
    return PricingScenario(**values)


def pricing_result(
    *,
    source: UnitEconomicsInput | None = None,
    selected_scenario: PricingScenario | None = None,
) -> PricingScenarioResult:
    return simulate_price(
        source or economics_input(),
        economics_policy(),
        selected_scenario or scenario(),
    )


def test_economics_policy_accepts_explicit_decimal_constraints() -> None:
    policy = EconomicsPolicy(
        PolicyIdentity("economics-demo", "v1"),
        Currency.RUB,
        Decimal("100"),
        Decimal("0.20"),
        Decimal("500"),
        Decimal("0.01"),
        Decimal("0.05"),
    )

    assert policy.minimum_margin == Decimal("0.20")
    assert policy.price_increment == Decimal("0.05")


@pytest.mark.parametrize(
    "overrides",
    [
        {"minimum_profit_per_unit": Decimal("-1")},
        {"minimum_margin": Decimal("-0.01")},
        {"minimum_margin": Decimal("1")},
        {"price_floor": Decimal("-1")},
        {"currency_quantum": Decimal("0")},
        {"price_increment": Decimal("0")},
    ],
)
def test_economics_policy_rejects_invalid_constraints(overrides: dict[str, Decimal]) -> None:
    values = {
        "minimum_profit_per_unit": Decimal("100"),
        "minimum_margin": Decimal("0.20"),
        "price_floor": Decimal("500"),
        "currency_quantum": Decimal("0.01"),
        "price_increment": Decimal("0.05"),
    }
    values.update(overrides)
    with pytest.raises(DataValidationError):
        EconomicsPolicy(
            PolicyIdentity("economics-demo", "v1"), Currency.RUB, **values
        )


def test_economics_input_normalizes_exact_money_and_immutable_cost_tuple() -> None:
    source = economics_input(other_variable_costs=[CostComponent("packaging", "20")])

    assert source.selling_price == Decimal("1000")
    assert source.other_variable_costs == (CostComponent("packaging", Decimal("20")),)


def test_economics_input_distinguishes_missing_from_zero() -> None:
    missing = economics_input(cost_of_goods=None, logistics_cost_per_unit=None)
    zero = economics_input(
        cost_of_goods=Decimal("0"), logistics_cost_per_unit=Decimal("0")
    )

    assert missing.cost_of_goods is None
    assert zero.cost_of_goods == Decimal("0")


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1"), 10.5])
def test_economics_input_rejects_non_positive_or_float_price(price: object) -> None:
    with pytest.raises(DataValidationError):
        economics_input(selling_price=price)


def test_economics_input_rejects_invalid_currency() -> None:
    with pytest.raises(DataValidationError):
        economics_input(currency="RUB")


def test_economics_input_rejects_conflicting_commission_inputs() -> None:
    with pytest.raises(DataValidationError) as raised:
        economics_input(commission_per_unit=Decimal("100"))

    assert raised.value.code == "economics.conflicting_commission_inputs"


@pytest.mark.parametrize(
    "overrides",
    [
        {"commission_rate": Decimal("1.01")},
        {"cost_of_goods": Decimal("-1")},
        {"advertising_spend": Decimal("-1")},
        {"other_variable_costs": (CostComponent("duplicate", 1), CostComponent("duplicate", 2))},
    ],
)
def test_economics_input_rejects_invalid_costs_or_rates(overrides: dict[str, object]) -> None:
    with pytest.raises(DataValidationError):
        economics_input(**overrides)


def test_drr_above_one_is_preserved_as_source_data() -> None:
    source = economics_input(drr=Decimal("1.25"))

    assert source.drr == Decimal("1.25")


def test_available_result_accepts_calculated_negative_profit() -> None:
    result = calculate_unit_economics(
        economics_input(cost_of_goods=Decimal("900")),
        economics_policy(),
    )

    assert result.profit_per_unit == Decimal("-270.00")
    assert result.safety_status is SafetyStatus.UNSAFE


def test_unavailable_result_has_no_calculated_values() -> None:
    result = unavailable_result()

    assert result.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.minimum_safe_price is None
    assert result.total_variable_cost is None
    assert result.commission_cost == Decimal("150.00")
    assert result.evidence_refs


def test_available_result_requires_evidence() -> None:
    with pytest.raises(DataValidationError) as raised:
        available_result(evidence_refs=())

    assert raised.value.code == "economics.missing_result_evidence"


@pytest.mark.parametrize("evidence_refs", [(" ",), ("fact-1", "fact-1")])
def test_economics_result_rejects_blank_or_duplicate_evidence(
    evidence_refs: tuple[str, ...],
) -> None:
    with pytest.raises(DataValidationError):
        available_result(evidence_refs=evidence_refs)


@pytest.mark.parametrize(
    "overrides",
    [
        {"safety_status": SafetyStatus.UNAVAILABLE},
        {"drr_status": AvailabilityStatus.AVAILABLE, "drr": None},
        {"drr_status": AvailabilityStatus.INSUFFICIENT_DATA, "drr": Decimal("0.1")},
        {"total_variable_cost": Decimal("1")},
        {"profit_per_unit": Decimal("1")},
        {"contribution_margin": Decimal("0.1")},
    ],
)
def test_available_result_rejects_contradictory_states(overrides: dict[str, object]) -> None:
    with pytest.raises(DataValidationError):
        available_result(**overrides)


def test_unavailable_result_rejects_values_or_safe_status() -> None:
    with pytest.raises(DataValidationError):
        unavailable_result(profit_per_unit=Decimal("0"))
    with pytest.raises(DataValidationError):
        unavailable_result(safety_status=SafetyStatus.SAFE)


def test_result_rejects_contradictory_boundary_states() -> None:
    result = available_result()

    with pytest.raises(DataValidationError):
        replace(
            result,
            minimum_safe=PriceBoundary(
                AvailabilityStatus.AVAILABLE,
                Decimal("700"),
            ),
        )
    with pytest.raises(DataValidationError):
        replace(
            result,
            break_even=PriceBoundary(AvailabilityStatus.NOT_APPLICABLE, None),
        )


def test_result_accepts_available_zero_boundaries_for_universal_constraints() -> None:
    result = calculate_unit_economics(
        economics_input(
            cost_of_goods=Decimal(0),
            logistics_cost_per_unit=Decimal(0),
            commission_rate=Decimal(0),
            advertising_cost_per_unit=Decimal(0),
            drr=Decimal(1),
            other_variable_costs=(),
        ),
        economics_policy(
            minimum_profit_per_unit=Decimal(0),
            minimum_margin=Decimal(0),
            price_floor=None,
        ),
    )

    rebuilt = replace(result)

    assert rebuilt.break_even == PriceBoundary(
        AvailabilityStatus.AVAILABLE,
        Decimal(0),
    )
    assert rebuilt.minimum_profit.price == Decimal(0)
    assert rebuilt.minimum_margin.price == Decimal(0)
    assert rebuilt.minimum_safe.price == Decimal(0)
    assert rebuilt.safety_status is SafetyStatus.SAFE


def test_result_rejects_available_boundary_for_zero_denominator_positive_numerator() -> None:
    result = calculate_unit_economics(
        economics_input(
            cost_of_goods=Decimal(0),
            logistics_cost_per_unit=Decimal(0),
            commission_rate=Decimal(0),
            advertising_cost_per_unit=Decimal(0),
            drr=Decimal(1),
            other_variable_costs=(),
        ),
        economics_policy(
            minimum_profit_per_unit=Decimal(1),
            minimum_margin=Decimal(0),
            price_floor=None,
        ),
    )

    with pytest.raises(DataValidationError) as raised:
        replace(
            result,
            minimum_profit=PriceBoundary(
                AvailabilityStatus.AVAILABLE,
                Decimal(0),
            ),
        )

    assert raised.value.code == "economics.inconsistent_boundary_feasibility"


def test_result_rejects_available_zero_boundary_for_negative_denominator() -> None:
    result = calculate_unit_economics(
        economics_input(
            cost_of_goods=Decimal(0),
            logistics_cost_per_unit=Decimal(0),
            commission_rate=Decimal(0),
            advertising_cost_per_unit=Decimal(0),
            drr=Decimal("1.01"),
            other_variable_costs=(),
        ),
        economics_policy(
            minimum_profit_per_unit=Decimal(0),
            minimum_margin=Decimal(0),
            price_floor=None,
        ),
    )

    with pytest.raises(DataValidationError) as raised:
        replace(
            result,
            break_even=PriceBoundary(
                AvailabilityStatus.AVAILABLE,
                Decimal(0),
            ),
        )

    assert raised.value.code == "economics.inconsistent_boundary_feasibility"


def test_economics_result_is_immutable_and_preserves_source_and_policy() -> None:
    result = available_result()

    assert result.source_input.provenance == economics_input().provenance
    assert result.policy == economics_policy()
    with pytest.raises(FrozenInstanceError):
        result.total_variable_cost = Decimal("0")


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-0.01"), 100.5])
def test_pricing_scenario_rejects_non_positive_or_float_price(price: object) -> None:
    with pytest.raises(DataValidationError):
        scenario(hypothetical_price=price)


def test_pricing_scenario_requires_aware_time_and_hypothetical_flag() -> None:
    with pytest.raises(DataValidationError):
        scenario(as_of=datetime(2026, 9, 2, 8))
    with pytest.raises(DataValidationError):
        scenario(is_hypothetical=False)


def test_available_pricing_result_requires_matching_economics() -> None:
    result = pricing_result()

    assert result.current_price == Decimal("1000")
    assert result.candidate_price == Decimal("900")
    assert result.price_delta == Decimal("-100")
    assert result.economics is result.candidate_economics
    assert result.evidence_refs == ("policy:economics-demo:v1",)


def test_available_pricing_result_requires_evidence() -> None:
    with pytest.raises(DataValidationError) as raised:
        replace(pricing_result(), evidence_refs=())

    assert raised.value.code == "economics.missing_scenario_evidence"


def test_unavailable_pricing_result_allows_empty_evidence() -> None:
    result = replace(
        pricing_result(source=economics_input(cost_of_goods=None)),
        evidence_refs=(),
    )

    assert result.evidence_refs == ()


@pytest.mark.parametrize("evidence_refs", [(" ",), ("fact-1", "fact-1")])
def test_pricing_result_rejects_blank_or_duplicate_evidence(
    evidence_refs: tuple[str, ...],
) -> None:
    with pytest.raises(DataValidationError):
        replace(pricing_result(), evidence_refs=evidence_refs)


def test_pricing_result_rejects_contradictory_availability() -> None:
    with pytest.raises(DataValidationError):
        replace(pricing_result(), status=AvailabilityStatus.INSUFFICIENT_DATA)
    with pytest.raises(DataValidationError):
        replace(
            pricing_result(source=economics_input(cost_of_goods=None)),
            status=AvailabilityStatus.AVAILABLE,
        )


def test_pricing_result_rejects_invalid_economics_type() -> None:
    with pytest.raises(DataValidationError):
        replace(pricing_result(), economics="not-economics")

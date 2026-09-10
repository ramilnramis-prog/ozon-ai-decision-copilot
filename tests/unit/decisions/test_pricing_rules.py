"""Tests for deterministic profitability and pricing decision rules."""

from datetime import UTC, date, datetime
from decimal import Decimal

from app.analytics.pricing import simulate_price
from app.analytics.unit_economics import calculate_unit_economics
from app.decisions.pricing_rules import (
    RULE_BELOW_MINIMUM_MARGIN,
    RULE_BELOW_MINIMUM_PROFIT,
    RULE_CURRENT_PRICE_UNSAFE,
    RULE_INSUFFICIENT_ECONOMICS,
    RULE_LOSS_MAKING,
    RULE_NO_FINITE_SAFE_PRICE,
    RULE_SCENARIO_BELOW_SAFE,
    RULE_SCENARIO_INSUFFICIENT,
    evaluate_economics_rules,
    evaluate_pricing_scenario_rules,
)
from app.domain.common import Currency, DateRange, PolicyIdentity, Provenance, SkuId, SourceType
from app.domain.economics import CostComponent, EconomicsPolicy, PricingScenario, UnitEconomicsInput


STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)
SKU = SkuId("SKU-PRICING-RULE")


def provenance(ref: str, kind: SourceType = SourceType.DEMO) -> Provenance:
    return Provenance(kind, "pricing_rule_test", STAMP, source_record_id=ref)


def source(**overrides: object) -> UnitEconomicsInput:
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
        "provenance": provenance("economics:1"),
    }
    values.update(overrides)
    return UnitEconomicsInput(**values)


def policy(**overrides: object) -> EconomicsPolicy:
    values = {
        "identity": PolicyIdentity("economics-rule", "1"),
        "currency": Currency.RUB,
        "minimum_profit_per_unit": Decimal("100"),
        "minimum_margin": Decimal("0.20"),
        "price_floor": Decimal("500"),
        "currency_quantum": Decimal("0.01"),
        "price_increment": Decimal("1"),
    }
    values.update(overrides)
    return EconomicsPolicy(**values)


def economics(source_value=None, policy_value=None):
    return calculate_unit_economics(source_value or source(), policy_value or policy())


def rule_codes(result) -> tuple[str, ...]:
    return tuple(item.rule_code for item in result.recommendations)


def test_safe_profitable_economics_is_a_no_action_result() -> None:
    decision = evaluate_economics_rules(economics(), STAMP)

    assert decision.recommendations == ()


def test_loss_making_and_unsafe_price_are_independent_decisions() -> None:
    result = economics(source(cost_of_goods=Decimal("900")))
    decision = evaluate_economics_rules(result, STAMP)

    assert result.profit_per_unit == Decimal("-270.00")
    assert result.contribution_margin == Decimal("-0.27")
    assert rule_codes(decision) == (
        RULE_LOSS_MAKING,
        RULE_BELOW_MINIMUM_PROFIT,
        RULE_BELOW_MINIMUM_MARGIN,
        RULE_CURRENT_PRICE_UNSAFE,
    )
    assert all(item.evidence_refs for item in decision.recommendations)


def test_zero_profit_is_not_loss_making() -> None:
    result = economics(
        source(
            selling_price=Decimal("500"),
            cost_of_goods=Decimal("300"),
            logistics_cost_per_unit=Decimal("100"),
            commission_rate=Decimal("0.10"),
            drr=Decimal("0.10"),
            other_variable_costs=(),
        ),
        policy(minimum_profit_per_unit=Decimal(0), minimum_margin=Decimal(0), price_floor=None),
    )
    decision = evaluate_economics_rules(result, STAMP)

    assert result.profit_per_unit == 0
    assert RULE_LOSS_MAKING not in rule_codes(decision)
    assert decision.recommendations == ()


def test_zero_profit_still_reports_positive_minimum_violations() -> None:
    result = economics(
        source(
            selling_price=Decimal("500"),
            cost_of_goods=Decimal("300"),
            logistics_cost_per_unit=Decimal("100"),
            commission_rate=Decimal("0.10"),
            drr=Decimal("0.10"),
            other_variable_costs=(),
        ),
        policy(
            minimum_profit_per_unit=Decimal("100"),
            minimum_margin=Decimal("0.20"),
            price_floor=None,
        ),
    )

    decision = evaluate_economics_rules(result, STAMP)

    assert result.profit_per_unit == 0
    assert result.contribution_margin == 0
    assert rule_codes(decision) == (
        RULE_BELOW_MINIMUM_PROFIT,
        RULE_BELOW_MINIMUM_MARGIN,
        RULE_CURRENT_PRICE_UNSAFE,
    )
    assert RULE_LOSS_MAKING not in rule_codes(decision)


def test_positive_profit_below_minimum_and_low_margin_are_not_loss() -> None:
    result = economics(source(selling_price=Decimal("600")))
    decision = evaluate_economics_rules(result, STAMP)

    assert result.profit_per_unit > 0
    assert rule_codes(decision) == (
        RULE_BELOW_MINIMUM_PROFIT,
        RULE_BELOW_MINIMUM_MARGIN,
        RULE_CURRENT_PRICE_UNSAFE,
    )
    assert RULE_LOSS_MAKING not in rule_codes(decision)


def test_exact_profit_margin_and_safe_boundaries_do_not_emit_false_violations() -> None:
    result = economics(
        source(
            selling_price=Decimal("1000"),
            cost_of_goods=Decimal("300"),
            logistics_cost_per_unit=Decimal("100"),
            commission_rate=Decimal("0.20"),
            drr=Decimal("0.20"),
            other_variable_costs=(),
        ),
        policy(
            minimum_profit_per_unit=Decimal("200"),
            minimum_margin=Decimal("0.20"),
            price_floor=None,
        ),
    )
    decision = evaluate_economics_rules(result, STAMP)

    assert result.profit_per_unit == Decimal("200")
    assert result.contribution_margin == Decimal("0.20")
    assert result.minimum_safe_price == Decimal("1000")
    assert decision.recommendations == ()


def test_margin_just_below_threshold_is_not_misclassified_as_loss() -> None:
    result = economics(
        source(
            selling_price=Decimal("999.99"),
            cost_of_goods=Decimal("300"),
            logistics_cost_per_unit=Decimal("100"),
            commission_rate=Decimal("0.20"),
            drr=Decimal("0.20"),
            other_variable_costs=(),
        ),
        policy(
            minimum_profit_per_unit=Decimal(0),
            minimum_margin=Decimal("0.20"),
            price_floor=None,
            price_increment=Decimal("0.01"),
        ),
    )
    decision = evaluate_economics_rules(result, STAMP)

    assert Decimal(0) < result.contribution_margin < Decimal("0.20")
    assert rule_codes(decision) == (
        RULE_BELOW_MINIMUM_MARGIN,
        RULE_CURRENT_PRICE_UNSAFE,
    )


def test_impossible_safe_price_has_no_numeric_price_recommendation() -> None:
    result = economics(source(commission_rate=Decimal("0.18"), drr=Decimal("1.20")))
    decision = evaluate_economics_rules(result, STAMP)

    assert rule_codes(decision) == (
        RULE_LOSS_MAKING,
        RULE_BELOW_MINIMUM_PROFIT,
        RULE_BELOW_MINIMUM_MARGIN,
        RULE_NO_FINITE_SAFE_PRICE,
    )
    assert RULE_CURRENT_PRICE_UNSAFE not in rule_codes(decision)
    infeasible = decision.recommendations[-1]
    assert "price" not in infeasible.proposed_action


def test_missing_economics_emits_only_data_quality_rule() -> None:
    decision = evaluate_economics_rules(economics(source(cost_of_goods=None)), STAMP)

    assert rule_codes(decision) == (RULE_INSUFFICIENT_ECONOMICS,)
    assert decision.recommendations[0].category.value == "data_quality"


def pricing_scenario(candidate: str, *, missing_cogs: bool = False):
    source_value = source(cost_of_goods=None) if missing_cogs else source()
    selected = PricingScenario(
        "scenario-1",
        SKU,
        Decimal(candidate),
        Currency.RUB,
        STAMP,
        True,
        provenance("scenario:1", SourceType.MANUAL),
    )
    return simulate_price(source_value, policy(), selected)


def test_unsafe_scenario_uses_approved_guardrail_label() -> None:
    decision = evaluate_pricing_scenario_rules(pricing_scenario("600"), STAMP)

    assert rule_codes(decision) == (RULE_SCENARIO_BELOW_SAFE,)
    assert decision.recommendations[0].proposed_action == "do not decrease below the safe threshold"


def test_safe_and_exact_safe_scenarios_emit_no_warning() -> None:
    safe_price = economics().minimum_safe_price
    assert safe_price is not None

    exact = evaluate_pricing_scenario_rules(pricing_scenario(str(safe_price)), STAMP)
    above = evaluate_pricing_scenario_rules(pricing_scenario(str(safe_price + 1)), STAMP)
    assert exact.recommendations == ()
    assert above.recommendations == ()


def test_incomplete_scenario_emits_only_data_quality_rule() -> None:
    decision = evaluate_pricing_scenario_rules(
        pricing_scenario("1200", missing_cogs=True),
        STAMP,
    )

    assert rule_codes(decision) == (RULE_SCENARIO_INSUFFICIENT,)


def test_rule_order_ids_timestamp_and_evidence_are_repeatable() -> None:
    result = economics(source(selling_price=Decimal("600")))
    first = evaluate_economics_rules(result, STAMP)
    second = evaluate_economics_rules(result, STAMP)

    assert first == second
    assert rule_codes(first) == (
        RULE_BELOW_MINIMUM_PROFIT,
        RULE_BELOW_MINIMUM_MARGIN,
        RULE_CURRENT_PRICE_UNSAFE,
    )
    assert all(item.analysis_timestamp == STAMP for item in first.recommendations)
    assert set().union(*(set(item.evidence_refs) for item in first.recommendations)) == {
        fact.fact_id for fact in first.facts
    }


def test_pricing_actions_stay_within_approved_labels() -> None:
    allowed = {
        "keep price",
        "consider increasing price",
        "consider decreasing price",
        "do not decrease below the safe threshold",
        "insufficient data",
    }
    current = evaluate_economics_rules(economics(source(selling_price=Decimal("600"))), STAMP)
    scenario_result = evaluate_pricing_scenario_rules(pricing_scenario("600"), STAMP)
    pricing_actions = tuple(
        recommendation.proposed_action
        for result in (current, scenario_result)
        for recommendation in result.recommendations
        if recommendation.category.value == "pricing"
    )

    assert pricing_actions
    assert set(pricing_actions) <= allowed

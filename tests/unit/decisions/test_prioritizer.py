"""Focused tests for deterministic Priority Action Center ranking."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.analytics.inventory import calculate_inventory_analysis
from app.analytics.sales import calculate_sales_metrics
from app.analytics.unit_economics import calculate_unit_economics
from app.core.errors import DataValidationError
from app.decisions import inventory_rules, pricing_rules, sales_rules
from app.decisions.common import (
    DecisionEvaluation,
    calculated_provenance,
    empty_evaluation,
    make_fact,
    make_recommendation,
)
from app.decisions.inventory_rules import evaluate_inventory_rules
from app.decisions.pricing_rules import evaluate_economics_rules
from app.decisions.prioritizer import (
    APPROVED_RULE_PRIORITIES,
    APPROVED_SEVERITY_ORDER,
    APPROVED_URGENCY_ORDER,
    RulePriority,
    build_priority_action_center,
)
from app.decisions.sales_rules import evaluate_sales_rules
from app.domain.catalog import SalesPolicy
from app.domain.common import Currency, PolicyIdentity, Severity, SkuId, Urgency
from app.domain.economics import EconomicsPolicy
from app.domain.inventory import InventoryPolicy
from app.domain.recommendations import (
    PriorityPolicy,
    Recommendation,
    RecommendationCategory,
    RecommendationStatus,
)
from app.providers.local_unit_economics import LocalUnitEconomicsProvider
from app.providers.mock_ozon import MockOzonProvider


STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)
AS_OF = date(2026, 9, 2)
POLICY = PriorityPolicy(
    PolicyIdentity("mvp-priority-policy", "1"),
    APPROVED_SEVERITY_ORDER,
    APPROVED_URGENCY_ORDER,
)
EXPECTED_PRIORITIES = (
    ("inventory.out_of_stock", Severity.CRITICAL, Urgency.IMMEDIATE, 1),
    ("inventory.replenishment_already_late", Severity.CRITICAL, Urgency.IMMEDIATE, 2),
    ("profitability.no_finite_safe_price", Severity.CRITICAL, Urgency.IMMEDIATE, 3),
    ("profitability.loss_making", Severity.CRITICAL, Urgency.IMMEDIATE, 4),
    ("inventory.replenishment_due_now", Severity.HIGH, Urgency.IMMEDIATE, 5),
    ("pricing.current_price_unsafe", Severity.HIGH, Urgency.IMMEDIATE, 6),
    ("inventory.replenishment_due_soon", Severity.HIGH, Urgency.SOON, 7),
    ("profitability.below_minimum_profit", Severity.HIGH, Urgency.SOON, 8),
    ("profitability.below_minimum_margin", Severity.HIGH, Urgency.SOON, 9),
    ("pricing.scenario_below_safe_price", Severity.HIGH, Urgency.SOON, 10),
    ("economics.insufficient_data", Severity.WARNING, Urgency.SOON, 11),
    ("inventory.missing_lead_time", Severity.WARNING, Urgency.SOON, 12),
    ("sales.material_decline", Severity.WARNING, Urgency.SOON, 13),
    ("inventory.overstock_candidate", Severity.WARNING, Urgency.MONITOR, 14),
    ("inventory.insufficient_sales_history", Severity.WARNING, Urgency.MONITOR, 15),
    ("sales.insufficient_history", Severity.WARNING, Urgency.MONITOR, 16),
    ("pricing.scenario_insufficient_data", Severity.WARNING, Urgency.MONITOR, 17),
    ("sales.material_increase", Severity.INFO, Urgency.MONITOR, 18),
)


def category_for(rule_code: str) -> RecommendationCategory:
    if "insufficient" in rule_code or rule_code == "inventory.missing_lead_time":
        return RecommendationCategory.DATA_QUALITY
    if rule_code.startswith("inventory."):
        return RecommendationCategory.INVENTORY
    if rule_code.startswith("sales."):
        return RecommendationCategory.SALES_CHANGE
    if rule_code.startswith("pricing."):
        return RecommendationCategory.PRICING
    return RecommendationCategory.PROFITABILITY


def evaluation(
    sku_text: str,
    rule_codes: tuple[str, ...],
    *,
    stamp: datetime = STAMP,
    instance_keys: tuple[str | None, ...] | None = None,
) -> DecisionEvaluation:
    sku = SkuId(sku_text)
    keys = instance_keys or (None,) * len(rule_codes)
    facts = []
    recommendations = []
    for rule_code, instance_key in zip(rule_codes, keys, strict=True):
        fact = make_fact(
            sku=sku,
            rule_code=rule_code,
            suffix="value",
            name="authoritative test fact",
            value="known",
            unit="status",
            source_refs=("source:test", "policy:test:1"),
            analysis_timestamp=stamp,
            instance_key=instance_key,
        )
        recommendation = make_recommendation(
            sku=sku,
            category=category_for(rule_code),
            rule_code=rule_code,
            explanation="The deterministic source rule emitted this recommendation.",
            proposed_action="Review the existing deterministic recommendation.",
            facts=(fact,),
            analysis_timestamp=stamp,
            instance_key=instance_key,
        )
        facts.append(fact)
        recommendations.append(recommendation)
    return DecisionEvaluation(sku, stamp, tuple(facts), tuple(recommendations))


def action_rule_codes(actions) -> tuple[str, ...]:
    return tuple(action.recommendation.rule_code for action in actions)


def production_rule_ids() -> set[str]:
    modules = (sales_rules, inventory_rules, pricing_rules)
    return {
        value
        for module in modules
        for name, value in vars(module).items()
        if name.startswith("RULE_") and isinstance(value, str)
    }


def test_approved_policy_exactly_maps_all_18_production_rules() -> None:
    actual = tuple(
        (rule_code, priority.severity, priority.urgency, priority.precedence)
        for rule_code, priority in APPROVED_RULE_PRIORITIES.items()
    )

    assert actual == EXPECTED_PRIORITIES
    assert set(APPROVED_RULE_PRIORITIES) == production_rule_ids()
    assert len(APPROVED_RULE_PRIORITIES) == len(production_rule_ids()) == 18
    assert {priority.precedence for priority in APPROVED_RULE_PRIORITIES.values()} == set(
        range(1, 19)
    )
    with pytest.raises(TypeError):
        APPROVED_RULE_PRIORITIES["future.rule"] = RulePriority(  # type: ignore[index]
            Severity.INFO, Urgency.MONITOR, 19
        )


def test_no_action_and_single_action_have_no_filler_or_zero_rank() -> None:
    assert build_priority_action_center((), POLICY) == ()
    assert build_priority_action_center((empty_evaluation(SkuId("SKU-EMPTY"), STAMP),), POLICY) == ()

    source = evaluation("SKU-ONE", ("inventory.out_of_stock",))
    actions = build_priority_action_center((source,), POLICY)

    assert len(actions) == 1
    assert actions[0].rank == 1
    assert actions[0].recommendation is source.recommendations[0]


def test_all_rules_are_preserved_and_ordered_by_approved_policy() -> None:
    reversed_rules = tuple(rule_code for rule_code, *_ in reversed(EXPECTED_PRIORITIES))
    source = evaluation("SKU-ALL", reversed_rules)

    actions = build_priority_action_center((source,), POLICY)

    assert len(actions) == 18
    assert action_rule_codes(actions) == tuple(rule_code for rule_code, *_ in EXPECTED_PRIORITIES)
    assert tuple(action.rank for action in actions) == tuple(range(1, 19))
    assert len({action.recommendation.recommendation_id for action in actions}) == 18


@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        (
            (
                "profitability.loss_making",
                "profitability.below_minimum_profit",
                "profitability.below_minimum_margin",
                "pricing.current_price_unsafe",
            ),
            (
                "profitability.loss_making",
                "pricing.current_price_unsafe",
                "profitability.below_minimum_profit",
                "profitability.below_minimum_margin",
            ),
        ),
        (
            ("inventory.missing_lead_time", "inventory.out_of_stock"),
            ("inventory.out_of_stock", "inventory.missing_lead_time"),
        ),
        (
            (
                "pricing.scenario_below_safe_price",
                "profitability.below_minimum_margin",
                "profitability.below_minimum_profit",
                "inventory.replenishment_due_soon",
            ),
            (
                "inventory.replenishment_due_soon",
                "profitability.below_minimum_profit",
                "profitability.below_minimum_margin",
                "pricing.scenario_below_safe_price",
            ),
        ),
        (
            (
                "pricing.scenario_insufficient_data",
                "sales.insufficient_history",
                "inventory.insufficient_sales_history",
                "inventory.overstock_candidate",
            ),
            (
                "inventory.overstock_candidate",
                "inventory.insufficient_sales_history",
                "sales.insufficient_history",
                "pricing.scenario_insufficient_data",
            ),
        ),
    ],
)
def test_same_sku_findings_remain_separate_and_follow_precedence(
    rules: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    actions = build_priority_action_center((evaluation("SKU-OVERLAP", rules),), POLICY)

    assert len(actions) == len(rules)
    assert action_rule_codes(actions) == expected


def test_severity_urgency_and_current_scenario_distinctions_are_preserved() -> None:
    source = evaluation(
        "SKU-DISTINCTIONS",
        (
            "pricing.scenario_insufficient_data",
            "economics.insufficient_data",
            "pricing.scenario_below_safe_price",
            "pricing.current_price_unsafe",
            "sales.material_increase",
            "profitability.loss_making",
        ),
    )

    actions = build_priority_action_center((source,), POLICY)

    assert action_rule_codes(actions) == (
        "profitability.loss_making",
        "pricing.current_price_unsafe",
        "pricing.scenario_below_safe_price",
        "economics.insufficient_data",
        "pricing.scenario_insufficient_data",
        "sales.material_increase",
    )


def test_equal_rule_uses_sku_then_recommendation_id_and_ignores_input_order() -> None:
    sku_a = evaluation(
        "SKU-A",
        ("inventory.out_of_stock", "inventory.out_of_stock"),
        instance_keys=("z", "a"),
    )
    sku_b = evaluation("SKU-B", ("inventory.out_of_stock",))

    first = build_priority_action_center((sku_b, sku_a), POLICY)
    second = build_priority_action_center((replace(sku_a, recommendations=tuple(reversed(sku_a.recommendations)), facts=tuple(reversed(sku_a.facts))), sku_b), POLICY)

    assert tuple(str(action.recommendation.sku) for action in first) == (
        "SKU-A",
        "SKU-A",
        "SKU-B",
    )
    assert tuple(action.recommendation.recommendation_id for action in first[:2]) == tuple(
        sorted(action.recommendation.recommendation_id for action in first[:2])
    )
    assert first == second


def test_duplicate_ids_unknown_rules_and_mixed_timestamps_fail_closed() -> None:
    duplicate_a = evaluation("SKU-DUP", ("inventory.out_of_stock",))
    duplicate_b = evaluation("SKU-DUP", ("inventory.out_of_stock",))
    with pytest.raises(DataValidationError) as duplicate_error:
        build_priority_action_center((duplicate_a, duplicate_b), POLICY)
    assert duplicate_error.value.code == "prioritizer.duplicate_recommendation_id"

    unknown = evaluation("SKU-UNKNOWN", ("future.unmapped_rule",))
    with pytest.raises(DataValidationError) as unknown_error:
        build_priority_action_center((unknown,), POLICY)
    assert unknown_error.value.code == "prioritizer.unmapped_rule"

    later = evaluation(
        "SKU-LATER",
        ("sales.material_increase",),
        stamp=STAMP + timedelta(seconds=1),
    )
    with pytest.raises(DataValidationError) as timestamp_error:
        build_priority_action_center((duplicate_a, later), POLICY)
    assert timestamp_error.value.code == "prioritizer.mixed_analysis_timestamps"


def test_snapshot_timestamp_consistency_includes_empty_evaluations() -> None:
    later_stamp = STAMP + timedelta(seconds=1)
    empty_now = empty_evaluation(SkuId("SKU-EMPTY-NOW"), STAMP)
    empty_now_other = empty_evaluation(SkuId("SKU-EMPTY-NOW-OTHER"), STAMP)
    empty_later = empty_evaluation(SkuId("SKU-EMPTY-LATER"), later_stamp)
    action_now = evaluation("SKU-ACTION-NOW", ("inventory.out_of_stock",))
    action_later = evaluation(
        "SKU-ACTION-LATER",
        ("inventory.out_of_stock",),
        stamp=later_stamp,
    )

    for mixed_snapshot in (
        (empty_now, action_later),
        (action_later, empty_now),
        (empty_now, empty_later),
    ):
        with pytest.raises(DataValidationError) as error:
            build_priority_action_center(mixed_snapshot, POLICY)
        assert error.value.code == "prioritizer.mixed_analysis_timestamps"

    assert build_priority_action_center((empty_now, empty_now_other), POLICY) == ()
    actions = build_priority_action_center((empty_now, action_now), POLICY)
    assert action_rule_codes(actions) == ("inventory.out_of_stock",)
    assert actions[0].rank == 1


def test_only_the_approved_severity_and_urgency_orders_are_accepted() -> None:
    source = evaluation("SKU-POLICY", ("inventory.out_of_stock",))
    reversed_severity = PriorityPolicy(
        PolicyIdentity("other-priority-policy", "1"),
        tuple(reversed(APPROVED_SEVERITY_ORDER)),
        APPROVED_URGENCY_ORDER,
    )
    reversed_urgency = PriorityPolicy(
        PolicyIdentity("other-priority-policy", "2"),
        APPROVED_SEVERITY_ORDER,
        tuple(reversed(APPROVED_URGENCY_ORDER)),
    )

    with pytest.raises(DataValidationError) as severity_error:
        build_priority_action_center((source,), reversed_severity)
    assert severity_error.value.code == "prioritizer.unsupported_severity_order"
    with pytest.raises(DataValidationError) as urgency_error:
        build_priority_action_center((source,), reversed_urgency)
    assert urgency_error.value.code == "prioritizer.unsupported_urgency_order"


def test_unavailable_recommendation_is_not_silently_ranked() -> None:
    sku = SkuId("SKU-UNAVAILABLE")
    recommendation_id = "recommendation:SKU-UNAVAILABLE:economics.insufficient_data"
    unavailable = Recommendation(
        recommendation_id=recommendation_id,
        sku=sku,
        category=RecommendationCategory.DATA_QUALITY,
        rule_code="economics.insufficient_data",
        explanation="The recommendation itself is unavailable.",
        evidence_refs=(),
        proposed_action=None,
        status=RecommendationStatus.UNAVAILABLE,
        provenance=calculated_provenance(recommendation_id, STAMP),
        analysis_timestamp=STAMP,
    )
    source = DecisionEvaluation(sku, STAMP, (), (unavailable,))

    with pytest.raises(DataValidationError) as error:
        build_priority_action_center((source,), POLICY)
    assert error.value.code == "prioritizer.unsupported_recommendation_status"


def test_traceability_source_immutability_result_immutability_and_idempotence() -> None:
    source = evaluation(
        "SKU-TRACE",
        ("sales.material_decline", "inventory.overstock_candidate"),
    )
    original_facts = source.facts
    original_recommendations = source.recommendations

    first = build_priority_action_center((source,), POLICY)
    second = build_priority_action_center((source,), POLICY)

    assert first == second
    assert source.facts is original_facts
    assert source.recommendations is original_recommendations
    assert {id(action.recommendation) for action in first} == {
        id(recommendation) for recommendation in source.recommendations
    }
    assert all(
        action.recommendation.evidence_refs
        == next(
            recommendation.evidence_refs
            for recommendation in source.recommendations
            if recommendation.recommendation_id
            == action.recommendation.recommendation_id
        )
        for action in first
    )
    assert isinstance(first, tuple)
    with pytest.raises(FrozenInstanceError):
        first[0].rank = 99


SALES_POLICY = SalesPolicy(
    PolicyIdentity("demo-sales-policy", "1"), 14, 14, Decimal("0.25")
)
ECONOMICS_POLICY = EconomicsPolicy(
    PolicyIdentity("demo-economics-policy", "1"),
    Currency.RUB,
    Decimal("100"),
    Decimal("0.20"),
    Decimal("300"),
    Decimal("0.01"),
    Decimal("1"),
)


@pytest.fixture(scope="module")
def demo_inputs():
    marketplace = MockOzonProvider()
    economics_provider = LocalUnitEconomicsProvider()
    sales = marketplace.get_sales_observations()
    inventory = marketplace.get_inventory_snapshots()
    inbound = marketplace.get_inbound_supplies()
    assert sales.issues == inventory.issues == inbound.issues == ()
    return (
        marketplace,
        economics_provider,
        sales.records,
        inventory.records,
        inbound.records,
    )


def demo_evaluations(sku_text: str, demo_inputs) -> tuple[DecisionEvaluation, ...]:
    marketplace, economics_provider, sales_records, inventory_records, inbound_records = demo_inputs
    sku = SkuId(sku_text)
    sales = calculate_sales_metrics(
        sku,
        tuple(record for record in sales_records if record.sku == sku),
        SALES_POLICY,
        AS_OF,
    )
    lead_time = marketplace.get_lead_time(sku)
    constraints = marketplace.get_replenishment_constraints(sku)
    assert lead_time.value is not None and not lead_time.issues
    assert constraints.value is not None and not constraints.issues
    inventory_policy = InventoryPolicy(
        PolicyIdentity("demo-inventory-policy", "1"),
        lead_time.value,
        target_coverage_days=30,
        warning_window_days=7,
        overstock_threshold_days=90,
        minimum_order_quantity=constraints.value.minimum_order_quantity,
        pack_size=constraints.value.pack_size,
    )
    inventory_snapshot = next(record for record in inventory_records if record.sku == sku)
    inventory = calculate_inventory_analysis(
        inventory_snapshot,
        sales,
        tuple(record for record in inbound_records if record.sku == sku),
        lead_time.value,
        constraints.value,
        inventory_policy,
        AS_OF,
    )
    economics_input = economics_provider.get_unit_economics(sku)
    assert economics_input.value is not None and not economics_input.issues
    economics = calculate_unit_economics(economics_input.value, ECONOMICS_POLICY)
    return (
        evaluate_sales_rules(sales, STAMP),
        evaluate_inventory_rules(inventory, STAMP),
        evaluate_economics_rules(economics, STAMP),
    )


@pytest.mark.parametrize(
    ("sku", "expected"),
    [
        ("DEMO-001", ()),
        ("DEMO-005", ("inventory.out_of_stock",)),
        (
            "DEMO-006",
            (
                "pricing.current_price_unsafe",
                "profitability.below_minimum_profit",
                "profitability.below_minimum_margin",
                "sales.material_decline",
                "inventory.overstock_candidate",
            ),
        ),
        (
            "DEMO-012",
            (
                "pricing.current_price_unsafe",
                "profitability.below_minimum_margin",
                "inventory.missing_lead_time",
            ),
        ),
        (
            "DEMO-015",
            (
                "profitability.loss_making",
                "pricing.current_price_unsafe",
                "profitability.below_minimum_profit",
                "profitability.below_minimum_margin",
            ),
        ),
        (
            "DEMO-018",
            (
                "profitability.no_finite_safe_price",
                "profitability.loss_making",
                "profitability.below_minimum_profit",
                "profitability.below_minimum_margin",
            ),
        ),
        (
            "DEMO-019",
            (
                "pricing.current_price_unsafe",
                "profitability.below_minimum_profit",
                "profitability.below_minimum_margin",
            ),
        ),
        (
            "DEMO-021",
            ("inventory.replenishment_due_soon", "economics.insufficient_data"),
        ),
        (
            "DEMO-037",
            ("inventory.insufficient_sales_history", "sales.insufficient_history"),
        ),
    ],
)
def test_representative_demo_priority_order(
    sku: str, expected: tuple[str, ...], demo_inputs
) -> None:
    actions = build_priority_action_center(demo_evaluations(sku, demo_inputs), POLICY)

    assert action_rule_codes(actions) == expected


def test_cross_sku_demo_center_is_complete_and_shuffle_independent(demo_inputs) -> None:
    skus = ("DEMO-005", "DEMO-015", "DEMO-018", "DEMO-019", "DEMO-021", "DEMO-037")
    evaluations = tuple(
        item for sku in skus for item in demo_evaluations(sku, demo_inputs)
    )

    first = build_priority_action_center(evaluations, POLICY)
    shuffled = build_priority_action_center(tuple(reversed(evaluations)), POLICY)

    expected = (
        ("inventory.out_of_stock", "DEMO-005"),
        ("profitability.no_finite_safe_price", "DEMO-018"),
        ("profitability.loss_making", "DEMO-015"),
        ("profitability.loss_making", "DEMO-018"),
        ("pricing.current_price_unsafe", "DEMO-015"),
        ("pricing.current_price_unsafe", "DEMO-019"),
        ("inventory.replenishment_due_soon", "DEMO-021"),
        ("profitability.below_minimum_profit", "DEMO-015"),
        ("profitability.below_minimum_profit", "DEMO-018"),
        ("profitability.below_minimum_profit", "DEMO-019"),
        ("profitability.below_minimum_margin", "DEMO-015"),
        ("profitability.below_minimum_margin", "DEMO-018"),
        ("profitability.below_minimum_margin", "DEMO-019"),
        ("economics.insufficient_data", "DEMO-021"),
        ("inventory.insufficient_sales_history", "DEMO-037"),
        ("sales.insufficient_history", "DEMO-037"),
    )
    actual = tuple(
        (action.recommendation.rule_code, str(action.recommendation.sku))
        for action in first
    )
    assert actual == expected
    assert first == shuffled
    assert tuple(action.rank for action in first) == tuple(range(1, len(first) + 1))


def test_prioritizer_has_no_analytics_adapter_ai_clock_or_scoring_dependency() -> None:
    source = (
        Path(__file__).resolve().parents[3]
        / "app"
        / "decisions"
        / "prioritizer.py"
    ).read_text(encoding="utf-8").lower()
    forbidden = (
        "app.analytics",
        "app.providers",
        "app.ai",
        "openai",
        "streamlit",
        "fastapi",
        "datetime.now",
        "datetime.utcnow",
        "date.today",
        "uuid",
        "random",
        "hash(",
        "priority_score",
        "weighted_score",
        "profit_per_unit",
        "contribution_margin",
        "stockout_date",
        "average_daily_sales",
        "minimum_safe_price",
    )

    assert all(value not in source for value in forbidden)

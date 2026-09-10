"""Tests for deterministic sales-change decision rules."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.analytics.sales import calculate_sales_metrics
from app.decisions.sales_rules import (
    RULE_INSUFFICIENT_HISTORY,
    RULE_MATERIAL_DECLINE,
    RULE_MATERIAL_INCREASE,
    evaluate_sales_rules,
)
from app.domain.catalog import SalesObservation, SalesPolicy
from app.domain.common import PolicyIdentity, Provenance, SkuId, SourceType
from app.domain.recommendations import RecommendationCategory


AS_OF = date(2026, 9, 2)
STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)
SKU = SkuId("SKU-SALES-RULE")


def sales_result(
    current: tuple[int, ...],
    comparison: tuple[int, ...],
    *,
    omit: tuple[date, ...] = (),
):
    policy = SalesPolicy(PolicyIdentity("sales-rule", "1"), 4, 4, Decimal("0.25"))
    start = AS_OF - timedelta(days=8)
    values = comparison + current
    observations = tuple(
        SalesObservation(
            SKU,
            start + timedelta(days=index),
            units,
            Provenance(
                SourceType.DEMO,
                "sales_rule_test",
                STAMP,
                source_record_id=f"sales:{index}",
            ),
        )
        for index, units in enumerate(values)
        if start + timedelta(days=index) not in omit
    )
    return calculate_sales_metrics(SKU, observations, policy, AS_OF)


def rule_codes(result) -> tuple[str, ...]:
    return tuple(item.rule_code for item in result.recommendations)


def test_stable_sales_is_a_no_action_result() -> None:
    decision = evaluate_sales_rules(sales_result((10,) * 4, (10,) * 4), STAMP)

    assert decision.facts == ()
    assert decision.recommendations == ()


def test_material_increase_and_decline_use_existing_direction() -> None:
    increase = evaluate_sales_rules(sales_result((15,) * 4, (10,) * 4), STAMP)
    decline = evaluate_sales_rules(sales_result((5,) * 4, (10,) * 4), STAMP)

    assert rule_codes(increase) == (RULE_MATERIAL_INCREASE,)
    assert rule_codes(decline) == (RULE_MATERIAL_DECLINE,)
    assert increase.recommendations[0].category is RecommendationCategory.SALES_CHANGE
    assert decline.recommendations[0].category is RecommendationCategory.SALES_CHANGE
    assert {fact.name for fact in increase.facts} >= {
        "sales change direction",
        "material sales-change threshold",
        "relative sales change",
    }
    facts_by_name = {fact.name: fact for fact in increase.facts}
    assert facts_by_name["current-window units sold"].period == sales_result(
        (15,) * 4, (10,) * 4
    ).current.period
    assert facts_by_name["comparison-window units sold"].period == sales_result(
        (15,) * 4, (10,) * 4
    ).comparison.period


def test_exact_material_threshold_boundaries_emit_signals() -> None:
    increase = evaluate_sales_rules(sales_result((5,) * 4, (4,) * 4), STAMP)
    decline = evaluate_sales_rules(sales_result((3,) * 4, (4,) * 4), STAMP)

    assert rule_codes(increase) == (RULE_MATERIAL_INCREASE,)
    assert rule_codes(decline) == (RULE_MATERIAL_DECLINE,)


def test_zero_baseline_increase_does_not_fabricate_relative_percentage() -> None:
    decision = evaluate_sales_rules(sales_result((1,) * 4, (0,) * 4), STAMP)

    assert rule_codes(decision) == (RULE_MATERIAL_INCREASE,)
    assert "relative sales change" not in {fact.name for fact in decision.facts}


def test_insufficient_history_emits_explicit_data_quality_recommendation() -> None:
    missing_date = AS_OF - timedelta(days=1)
    decision = evaluate_sales_rules(
        sales_result((10,) * 4, (10,) * 4, omit=(missing_date,)),
        STAMP,
    )

    assert rule_codes(decision) == (RULE_INSUFFICIENT_HISTORY,)
    assert decision.recommendations[0].category is RecommendationCategory.DATA_QUALITY
    assert "incomplete" in decision.recommendations[0].explanation


def test_sales_rule_ids_evidence_and_output_are_repeatable() -> None:
    metrics = sales_result((15,) * 4, (10,) * 4)
    first = evaluate_sales_rules(metrics, STAMP)
    second = evaluate_sales_rules(metrics, STAMP)

    assert first == second
    assert first.recommendations[0].analysis_timestamp == STAMP
    assert set(first.recommendations[0].evidence_refs) == {
        fact.fact_id for fact in first.facts
    }
    assert all(fact.source_refs for fact in first.facts)

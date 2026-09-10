"""Real demo analysis-to-grounding integration coverage for MVP-014."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.ai.brief_input import build_grounded_brief_input
from app.bootstrap import build_application
from app.core.clock import FixedClock
from app.decisions.common import DecisionEvaluation, make_fact, make_recommendation
from app.domain.briefs import GroundedValueType
from app.domain.common import AvailabilityStatus, Severity, SkuId, Urgency
from app.domain.recommendations import PriorityAction, RecommendationCategory


STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)


@pytest.fixture(scope="module")
def analysis_service():
    return build_application(clock=FixedClock(STAMP)).analysis


@pytest.mark.parametrize(
    ("sku", "expected_rules"),
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
            "DEMO-021",
            ("inventory.replenishment_due_soon", "economics.insufficient_data"),
        ),
        (
            "DEMO-037",
            ("inventory.insufficient_sales_history", "sales.insufficient_history"),
        ),
    ],
)
def test_representative_demo_action_and_fact_closure(
    analysis_service,
    sku: str,
    expected_rules: tuple[str, ...],
) -> None:
    snapshot = analysis_service.analyze((SkuId(sku),))
    grounded = build_grounded_brief_input(snapshot)

    assert tuple(action.rule_id for action in grounded.actions) == expected_rules
    assert tuple(action.rank for action in grounded.actions) == tuple(
        action.rank for action in snapshot.priority_actions
    )
    expected_fact_ids = {
        fact_ref
        for action in snapshot.priority_actions
        for fact_ref in action.recommendation.evidence_refs
    }
    assert {fact.fact_id for fact in grounded.facts} == expected_fact_ids
    assert all(
        fact_ref in expected_fact_ids
        for action in grounded.actions
        for fact_ref in action.fact_refs
    )


def test_unavailable_demo_cases_do_not_receive_fabricated_values(analysis_service) -> None:
    economics = build_grounded_brief_input(
        analysis_service.analyze((SkuId("DEMO-021"),))
    )
    no_finite_price = build_grounded_brief_input(
        analysis_service.analyze((SkuId("DEMO-018"),))
    )
    insufficient_sales = build_grounded_brief_input(
        analysis_service.analyze((SkuId("DEMO-037"),))
    )
    missing_lead = build_grounded_brief_input(
        analysis_service.analyze((SkuId("DEMO-012"),))
    )

    economics_names = {fact.name for fact in economics.facts}
    assert "contribution profit per unit" not in economics_names
    assert "contribution margin" not in economics_names
    assert "minimum safe price" not in economics_names
    assert any(
        fact.name == "unit-economics availability"
        and fact.value == "insufficient_data"
        and fact.value_type is GroundedValueType.TEXT
        for fact in economics.facts
    )

    assert not any(
        fact.name == "minimum safe price" for fact in no_finite_price.facts
    )
    assert any(
        fact.name == "minimum safe-price availability"
        and fact.value == "not_applicable"
        for fact in no_finite_price.facts
    )

    insufficient_names = {fact.name for fact in insufficient_sales.facts}
    assert "average daily sales" not in insufficient_names
    assert "estimated stockout date" not in insufficient_names
    assert "recommended replenishment quantity" not in insufficient_names
    assert {fact.value for fact in insufficient_sales.facts} == {"insufficient_data"}

    missing_lead_names = {fact.name for fact in missing_lead.facts}
    assert "latest safe start date" not in missing_lead_names
    assert any(
        fact.name == "lead-time availability"
        and fact.value == "insufficient_data"
        for fact in missing_lead.facts
    )


def test_multi_sku_traceability_matches_source_objects(analysis_service) -> None:
    snapshot = analysis_service.analyze(
        tuple(SkuId(sku) for sku in ("DEMO-005", "DEMO-015", "DEMO-021"))
    )
    grounded = build_grounded_brief_input(snapshot)
    source_fact_by_id = {
        fact.fact_id: fact
        for result in snapshot.sku_results
        for evaluation in result.decision_evaluations
        for fact in evaluation.facts
    }

    assert tuple(action.action_ref for action in grounded.actions) == tuple(
        action.recommendation.recommendation_id for action in snapshot.priority_actions
    )
    for action in grounded.actions:
        source_action = next(
            item
            for item in snapshot.priority_actions
            if item.recommendation.recommendation_id == action.action_ref
        )
        assert action.fact_refs == source_action.recommendation.evidence_refs
        assert all(ref in source_fact_by_id for ref in action.fact_refs)
    for fact in grounded.facts:
        source_fact = source_fact_by_id[fact.fact_id]
        assert fact.source_refs == source_fact.source_refs
        assert source_fact.provenance.source_record_id == source_fact.fact_id


def test_authoritative_cross_rule_evidence_reaches_grounded_input(
    analysis_service,
) -> None:
    source_snapshot = analysis_service.analyze((SkuId("DEMO-005"),))
    source_result = source_snapshot.sku_results[0]
    fact = make_fact(
        sku=source_result.sku,
        rule_code="unit_economics.minimum_safe_price",
        suffix="value",
        name="minimum safe price",
        value=Decimal("1233.00"),
        unit="RUB",
        source_refs=("source:economics:DEMO-005", "policy:economics:1"),
        analysis_timestamp=STAMP,
    )
    recommendation = make_recommendation(
        sku=source_result.sku,
        category=RecommendationCategory.PRICING,
        rule_code="pricing.current_price_unsafe",
        explanation="The current price is below the calculated safe boundary.",
        proposed_action="Review the current price against the safe boundary.",
        facts=(fact,),
        analysis_timestamp=STAMP,
    )
    evaluation = DecisionEvaluation(
        source_result.sku,
        STAMP,
        (fact,),
        (recommendation,),
    )
    priority_action = PriorityAction(
        recommendation=recommendation,
        severity=Severity.HIGH,
        urgency=Urgency.IMMEDIATE,
        status=AvailabilityStatus.AVAILABLE,
        rank=1,
        tie_break_key="qa-cross-rule-evidence",
    )
    snapshot = replace(
        source_snapshot,
        sku_results=(replace(source_result, decision_evaluations=(evaluation,)),),
        priority_actions=(priority_action,),
    )

    grounded = build_grounded_brief_input(snapshot)

    assert grounded.actions[0].action_ref == recommendation.recommendation_id
    assert grounded.actions[0].rule_id == "pricing.current_price_unsafe"
    assert grounded.actions[0].fact_refs == (fact.fact_id,)
    assert grounded.facts[0].fact_id == fact.fact_id
    assert grounded.facts[0].sku == fact.sku
    assert grounded.facts[0].formula_or_rule_id == "unit_economics.minimum_safe_price"
    assert grounded.facts[0].value == "1233.00"
    assert grounded.facts[0].source_refs == fact.source_refs

"""Tests for shared deterministic decision-output invariants."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.errors import DataValidationError
from app.decisions.common import (
    DECISION_PROVIDER,
    DecisionEvaluation,
    empty_evaluation,
    make_fact,
    make_recommendation,
)
from app.domain.common import Provenance, SkuId, SourceType
from app.domain.recommendations import RecommendationCategory


STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)
SKU = SkuId("SKU-DECISION")


def decision_parts():
    fact = make_fact(
        sku=SKU,
        rule_code="test.rule",
        suffix="value",
        name="test value",
        value=Decimal("1.25"),
        unit="units",
        source_refs=("source:1", "policy:test:1"),
        analysis_timestamp=STAMP,
    )
    recommendation = make_recommendation(
        sku=SKU,
        category=RecommendationCategory.INVENTORY,
        rule_code="test.rule",
        explanation="The explicit test condition is true.",
        proposed_action="Review the source fact.",
        facts=(fact,),
        analysis_timestamp=STAMP,
    )
    return fact, recommendation


def test_evaluation_is_traceable_and_immutable() -> None:
    fact, recommendation = decision_parts()
    result = DecisionEvaluation(SKU, STAMP, (fact,), (recommendation,))

    assert recommendation.evidence_refs == (fact.fact_id,)
    assert fact.provenance.source_type is SourceType.CALCULATED
    assert fact.provenance.provider == DECISION_PROVIDER
    assert recommendation.provenance.source_record_id == recommendation.recommendation_id
    with pytest.raises(FrozenInstanceError):
        result.recommendations = ()


def test_empty_evaluation_is_a_valid_no_action_result() -> None:
    result = empty_evaluation(SKU, STAMP)

    assert result.facts == ()
    assert result.recommendations == ()


def test_evaluation_rejects_naive_timestamp_and_sku_mismatch() -> None:
    fact, recommendation = decision_parts()
    with pytest.raises(DataValidationError):
        DecisionEvaluation(SKU, datetime(2026, 9, 2, 9), (fact,), (recommendation,))
    with pytest.raises(DataValidationError):
        DecisionEvaluation(SkuId("OTHER"), STAMP, (fact,), (recommendation,))


def test_evaluation_rejects_duplicate_ids_and_inconsistent_evidence() -> None:
    fact, recommendation = decision_parts()
    with pytest.raises(DataValidationError):
        DecisionEvaluation(SKU, STAMP, (fact, fact), (recommendation,))
    with pytest.raises(DataValidationError):
        DecisionEvaluation(SKU, STAMP, (fact,), (recommendation, recommendation))
    with pytest.raises(DataValidationError):
        DecisionEvaluation(
            SKU,
            STAMP,
            (fact,),
            (replace(recommendation, evidence_refs=("fact:missing",)),),
        )
    unrelated_fact = make_fact(
        sku=SKU,
        rule_code="other.rule",
        suffix="value",
        name="unrelated value",
        value=Decimal("2.50"),
        unit="units",
        source_refs=("source:2", "policy:test:1"),
        analysis_timestamp=STAMP,
    )
    with pytest.raises(DataValidationError):
        DecisionEvaluation(SKU, STAMP, (fact, unrelated_fact), (recommendation,))


def test_evaluation_accepts_explicit_same_sku_cross_rule_evidence() -> None:
    fact = make_fact(
        sku=SKU,
        rule_code="unit_economics.minimum_safe_price",
        suffix="value",
        name="minimum safe price",
        value=Decimal("1233.00"),
        unit="RUB",
        source_refs=("source:economics:1", "policy:economics:1"),
        analysis_timestamp=STAMP,
    )
    recommendation = make_recommendation(
        sku=SKU,
        category=RecommendationCategory.PRICING,
        rule_code="pricing.current_price_unsafe",
        explanation="The current price is below the calculated safe boundary.",
        proposed_action="Review the current price against the safe boundary.",
        facts=(fact,),
        analysis_timestamp=STAMP,
    )

    result = DecisionEvaluation(SKU, STAMP, (fact,), (recommendation,))

    assert result.facts[0].formula_or_rule_id == "unit_economics.minimum_safe_price"
    assert result.recommendations[0].rule_code == "pricing.current_price_unsafe"
    assert result.recommendations[0].evidence_refs == (fact.fact_id,)


def test_evaluation_rejects_noncalculated_or_mismatched_provenance() -> None:
    fact, recommendation = decision_parts()
    source_provenance = Provenance(
        SourceType.DEMO,
        "demo",
        STAMP,
        source_record_id=fact.fact_id,
    )
    with pytest.raises(DataValidationError):
        DecisionEvaluation(
            SKU,
            STAMP,
            (replace(fact, provenance=source_provenance),),
            (recommendation,),
        )
    with pytest.raises(DataValidationError):
        DecisionEvaluation(
            SKU,
            STAMP,
            (fact,),
            (replace(recommendation, analysis_timestamp=datetime(2026, 9, 2, 10, tzinfo=UTC)),),
        )


def test_decision_output_has_no_priority_fields() -> None:
    assert {field.name for field in fields(DecisionEvaluation)}.isdisjoint(
        {"priority_score", "rank", "urgency_score", "weighted_score", "top_action"}
    )


def test_decision_modules_have_no_forbidden_adapter_or_clock_dependencies() -> None:
    decision_root = Path(__file__).resolve().parents[3] / "app" / "decisions"
    source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in decision_root.glob("*.py")
    )

    forbidden = (
        "app.providers",
        "mockozonprovider",
        "app.ai",
        "openai",
        "streamlit",
        "fastapi",
        "datetime.now",
        "datetime.utcnow",
        "date.today",
        "priority_score",
        "weighted_score",
    )
    assert all(value not in source for value in forbidden)

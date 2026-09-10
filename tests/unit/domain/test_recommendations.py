"""Tests for evidence, read-only recommendations, and priority containers."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.errors import DataValidationError
from app.domain.common import (
    AvailabilityStatus,
    PolicyIdentity,
    Provenance,
    Severity,
    SkuId,
    SourceType,
    Urgency,
)
from app.domain.recommendations import (
    CalculatedFact,
    PriorityAction,
    PriorityPolicy,
    Recommendation,
    RecommendationCategory,
    RecommendationStatus,
)


def provenance(source_type: SourceType = SourceType.CALCULATED) -> Provenance:
    return Provenance(
        source_type,
        "deterministic_engine",
        datetime(2026, 9, 2, 8, tzinfo=UTC),
    )


def proposed_recommendation(**overrides: object) -> Recommendation:
    values = {
        "recommendation_id": "rec-1",
        "sku": SkuId("SKU-001"),
        "category": RecommendationCategory.INVENTORY,
        "rule_code": "inventory.reorder_due",
        "explanation": "Replenishment should be reviewed now.",
        "evidence_refs": ("fact-1",),
        "proposed_action": "Review a replenishment order.",
        "status": RecommendationStatus.PROPOSED,
        "provenance": provenance(),
        "analysis_timestamp": datetime(2026, 9, 2, 9, tzinfo=UTC),
    }
    values.update(overrides)
    return Recommendation(**values)


def test_calculated_fact_accepts_exact_decimal_and_source_references() -> None:
    fact = CalculatedFact(
        "fact-1",
        SkuId("SKU-001"),
        "stock coverage",
        Decimal("12.5"),
        "days",
        None,
        "inventory.coverage.v1",
        ("sales-1", "stock-1"),
        provenance(),
    )

    assert fact.value == Decimal("12.5")
    assert fact.source_refs == ("sales-1", "stock-1")


def test_calculated_fact_accepts_one_source_reference() -> None:
    fact = CalculatedFact(
        "fact-1",
        SkuId("SKU-001"),
        "stock coverage",
        Decimal("12.5"),
        "days",
        None,
        "inventory.coverage.v1",
        ("stock-1",),
        provenance(),
    )

    assert fact.source_refs == ("stock-1",)


@pytest.mark.parametrize("source_refs", [(), (" ",), ("stock-1", "stock-1")])
def test_calculated_fact_rejects_missing_blank_or_duplicate_source_references(
    source_refs: tuple[str, ...],
) -> None:
    with pytest.raises(DataValidationError):
        CalculatedFact(
            "fact-1",
            SkuId("SKU-001"),
            "stock coverage",
            Decimal("12.5"),
            "days",
            None,
            "inventory.coverage.v1",
            source_refs,
            provenance(),
        )


def test_non_calculated_fact_may_have_no_source_references() -> None:
    fact = CalculatedFact(
        "fact-1",
        SkuId("SKU-001"),
        "source value",
        Decimal("12.5"),
        "units",
        None,
        "source.direct",
        (),
        provenance(SourceType.DEMO),
    )

    assert fact.source_refs == ()


def test_calculated_fact_rejects_float_or_naive_datetime() -> None:
    common = (
        "fact-1",
        SkuId("SKU-001"),
        "value",
    )
    tail = ("units", None, "source.direct", (), provenance())
    with pytest.raises(DataValidationError):
        CalculatedFact(*common, 1.5, *tail)
    with pytest.raises(DataValidationError):
        CalculatedFact(*common, datetime(2026, 9, 2, 8), *tail)


def test_proposed_recommendation_is_read_only_and_evidence_backed() -> None:
    recommendation = proposed_recommendation()

    assert recommendation.status is RecommendationStatus.PROPOSED
    assert recommendation.provenance == provenance()
    assert recommendation.analysis_timestamp == datetime(2026, 9, 2, 9, tzinfo=UTC)
    assert "executed" not in {status.value for status in RecommendationStatus}
    with pytest.raises(FrozenInstanceError):
        recommendation.status = RecommendationStatus.UNAVAILABLE


def test_recommendation_preserves_provenance_and_is_immutable() -> None:
    source = provenance(SourceType.DEMO)
    recommendation = proposed_recommendation(provenance=source)

    assert recommendation.provenance is source
    with pytest.raises(FrozenInstanceError):
        recommendation.provenance = provenance()


def test_recommendation_rejects_naive_analysis_timestamp() -> None:
    with pytest.raises(DataValidationError):
        proposed_recommendation(analysis_timestamp=datetime(2026, 9, 2, 9))


def test_proposed_recommendation_requires_evidence_and_action() -> None:
    with pytest.raises(DataValidationError):
        proposed_recommendation(evidence_refs=())
    with pytest.raises(DataValidationError):
        proposed_recommendation(proposed_action=None)


def test_unavailable_recommendation_cannot_contain_action() -> None:
    with pytest.raises(DataValidationError):
        proposed_recommendation(
            status=RecommendationStatus.UNAVAILABLE,
            proposed_action="Do something",
        )


def test_priority_policy_requires_complete_unique_enum_order() -> None:
    policy = PriorityPolicy(
        PolicyIdentity("priority-demo", "v1"),
        (
            Severity.CRITICAL,
            Severity.HIGH,
            Severity.WARNING,
            Severity.INFO,
        ),
        (Urgency.IMMEDIATE, Urgency.SOON, Urgency.MONITOR),
    )

    assert policy.severity_order[0] is Severity.CRITICAL
    with pytest.raises(DataValidationError):
        PriorityPolicy(
            PolicyIdentity("priority-demo", "v1"),
            (Severity.CRITICAL, Severity.HIGH, Severity.WARNING, Severity.WARNING),
            (Urgency.IMMEDIATE, Urgency.SOON, Urgency.MONITOR),
        )


def test_available_priority_action_requires_rank_and_stable_key() -> None:
    action = PriorityAction(
        proposed_recommendation(),
        Severity.CRITICAL,
        Urgency.IMMEDIATE,
        AvailabilityStatus.AVAILABLE,
        rank=1,
        tie_break_key="SKU-001:rec-1",
    )

    assert action.rank == 1
    assert action.tie_break_key == "SKU-001:rec-1"


@pytest.mark.parametrize("invalid_rank", [0, -1, True, 1.0, "1"])
def test_available_priority_action_requires_positive_integer_rank(
    invalid_rank: object,
) -> None:
    with pytest.raises(DataValidationError):
        PriorityAction(
            proposed_recommendation(),
            Severity.CRITICAL,
            Urgency.IMMEDIATE,
            AvailabilityStatus.AVAILABLE,
            rank=invalid_rank,
            tie_break_key="SKU-001:rec-1",
        )


def test_priority_action_rejects_contradictory_states() -> None:
    with pytest.raises(DataValidationError):
        PriorityAction(
            proposed_recommendation(),
            Severity.INFO,
            Urgency.MONITOR,
            AvailabilityStatus.INSUFFICIENT_DATA,
            rank=0,
            tie_break_key="rec-1",
        )
    with pytest.raises(DataValidationError):
        PriorityAction(
            proposed_recommendation(),
            Severity.INFO,
            Urgency.MONITOR,
            AvailabilityStatus.INSUFFICIENT_DATA,
            rank=None,
            tie_break_key=None,
        )
    unavailable = proposed_recommendation(
        status=RecommendationStatus.UNAVAILABLE,
        proposed_action=None,
        evidence_refs=(),
    )
    with pytest.raises(DataValidationError):
        PriorityAction(
            unavailable,
            Severity.INFO,
            Urgency.MONITOR,
            AvailabilityStatus.AVAILABLE,
            rank=0,
            tie_break_key="rec-1",
        )

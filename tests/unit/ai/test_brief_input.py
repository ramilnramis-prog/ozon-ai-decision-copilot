"""Focused tests for action-centric snapshot grounding and serialization."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal
import json
from pathlib import Path

import pytest

import app.ai.brief_input as brief_input_module
from app.ai.brief_input import (
    build_grounded_brief_input,
    canonicalize_fact_value,
    grounded_brief_input_payload,
    serialize_grounded_brief_input,
)
from app.bootstrap import build_application
from app.core.clock import FixedClock
from app.core.errors import DataValidationError
from app.decisions.common import DecisionEvaluation, calculated_provenance
from app.domain.briefs import GroundedValueType
from app.domain.common import Severity, SkuId, ValidationIssue


STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)


@pytest.fixture(scope="module")
def analysis_service():
    return build_application(clock=FixedClock(STAMP)).analysis


def snapshot_for(analysis_service, *skus: str):
    return analysis_service.analyze(tuple(SkuId(sku) for sku in skus))


def rules(grounded) -> tuple[str, ...]:
    return tuple(action.rule_id for action in grounded.actions)


def source_facts(snapshot):
    return {
        fact.fact_id: fact
        for result in snapshot.sku_results
        for evaluation in result.decision_evaluations
        for fact in evaluation.facts
    }


def test_empty_and_healthy_snapshots_have_no_filler(analysis_service) -> None:
    empty_snapshot = analysis_service.analyze(())
    healthy_snapshot = snapshot_for(analysis_service, "DEMO-001")

    empty = build_grounded_brief_input(empty_snapshot)
    healthy = build_grounded_brief_input(healthy_snapshot)

    assert empty.analysis_timestamp == STAMP
    assert empty.actions == empty.facts == ()
    assert healthy.actions == healthy.facts == ()
    assert grounded_brief_input_payload(healthy) == {
        "analysis_timestamp": STAMP.isoformat(),
        "actions": [],
        "facts": [],
    }


def test_single_action_preserves_identity_priority_evidence_and_product_name(
    analysis_service,
) -> None:
    snapshot = snapshot_for(analysis_service, "DEMO-005")
    grounded = build_grounded_brief_input(snapshot)
    source = snapshot.priority_actions[0]
    action = grounded.actions[0]

    assert action.action_ref == action.recommendation_id
    assert action.recommendation_id == source.recommendation.recommendation_id
    assert action.rule_id == "inventory.out_of_stock"
    assert action.rank == source.rank == 1
    assert action.severity == source.severity
    assert action.urgency == source.urgency
    assert action.fact_refs == source.recommendation.evidence_refs
    assert action.product_name == snapshot.sku_results[0].product.name
    assert {fact.fact_id for fact in grounded.facts} == set(action.fact_refs)


def test_multiple_actions_keep_exact_source_order_and_evidence_closure(
    analysis_service,
) -> None:
    snapshot = snapshot_for(analysis_service, "DEMO-006")
    grounded = build_grounded_brief_input(snapshot)

    assert rules(grounded) == (
        "pricing.current_price_unsafe",
        "profitability.below_minimum_profit",
        "profitability.below_minimum_margin",
        "sales.material_decline",
        "inventory.overstock_candidate",
    )
    assert tuple(action.action_ref for action in grounded.actions) == tuple(
        action.recommendation.recommendation_id for action in snapshot.priority_actions
    )
    assert tuple(action.rank for action in grounded.actions) == tuple(
        action.rank for action in snapshot.priority_actions
    )
    expected_refs = {
        ref
        for action in snapshot.priority_actions
        for ref in action.recommendation.evidence_refs
    }
    assert {fact.fact_id for fact in grounded.facts} == expected_refs
    assert tuple(fact.fact_id for fact in grounded.facts) == tuple(sorted(expected_refs))


def test_multi_sku_scope_and_global_action_order_do_not_leak_other_skus(
    analysis_service,
) -> None:
    snapshot = snapshot_for(analysis_service, "DEMO-005", "DEMO-015", "DEMO-021")
    grounded = build_grounded_brief_input(snapshot)
    selected = {SkuId("DEMO-005"), SkuId("DEMO-015"), SkuId("DEMO-021")}

    assert tuple(action.action_ref for action in grounded.actions) == tuple(
        action.recommendation.recommendation_id for action in snapshot.priority_actions
    )
    assert {action.sku for action in grounded.actions} <= selected
    assert {fact.sku for fact in grounded.facts} <= selected
    facts_by_id = {fact.fact_id: fact for fact in grounded.facts}
    assert all(
        facts_by_id[ref].sku == action.sku
        for action in grounded.actions
        for ref in action.fact_refs
    )


def test_service_issues_and_unrelated_analytics_are_not_exposed(analysis_service) -> None:
    snapshot = snapshot_for(analysis_service, "DEMO-005")
    issue = ValidationIssue(
        code="services.test_issue",
        message="Application diagnostic only.",
        severity=Severity.WARNING,
        scope="analysis",
        sku=SkuId("DEMO-005"),
    )
    snapshot_with_issue = replace(snapshot, issues=(issue,))

    payload = grounded_brief_input_payload(
        build_grounded_brief_input(snapshot_with_issue)
    )

    assert set(payload) == {"analysis_timestamp", "actions", "facts"}
    assert "issues" not in serialize_grounded_brief_input(
        build_grounded_brief_input(snapshot_with_issue)
    )
    assert "sales" not in payload
    assert "inventory" not in payload
    assert "economics" not in payload
    assert "configuration" not in payload
    assert "provenance" not in payload


def test_repeated_shared_fact_is_deduplicated_and_conflict_is_rejected(
    analysis_service,
) -> None:
    snapshot = snapshot_for(analysis_service, "DEMO-005")
    result = snapshot.sku_results[0]
    evaluation = next(item for item in result.decision_evaluations if item.recommendations)
    shared_fact = evaluation.facts[0]
    source_recommendation = evaluation.recommendations[0]
    copy_id = f"{source_recommendation.recommendation_id}:copy"
    copied_recommendation = replace(
        source_recommendation,
        recommendation_id=copy_id,
        evidence_refs=(shared_fact.fact_id,),
        provenance=calculated_provenance(copy_id, STAMP),
    )
    copied_evaluation = DecisionEvaluation(
        result.sku,
        STAMP,
        (shared_fact,),
        (copied_recommendation,),
    )
    copied_action = replace(
        snapshot.priority_actions[0],
        recommendation=copied_recommendation,
        rank=len(snapshot.priority_actions) + 1,
        tie_break_key="copied-action",
    )
    shared_snapshot = replace(
        snapshot,
        sku_results=(
            replace(
                result,
                decision_evaluations=(*result.decision_evaluations, copied_evaluation),
            ),
        ),
        priority_actions=(*snapshot.priority_actions, copied_action),
    )

    grounded = build_grounded_brief_input(shared_snapshot)
    assert [fact.fact_id for fact in grounded.facts].count(shared_fact.fact_id) == 1
    assert grounded.actions[-1].fact_refs == (shared_fact.fact_id,)

    conflicting_fact = replace(shared_fact, name="conflicting fact content")
    conflicting_evaluation = DecisionEvaluation(
        result.sku,
        STAMP,
        (conflicting_fact,),
        (copied_recommendation,),
    )
    conflicting_snapshot = replace(
        shared_snapshot,
        sku_results=(
            replace(
                result,
                decision_evaluations=(*result.decision_evaluations, conflicting_evaluation),
            ),
        ),
    )
    with pytest.raises(DataValidationError) as error:
        build_grounded_brief_input(conflicting_snapshot)
    assert error.value.code == "brief_input.conflicting_fact_id"


def test_unresolved_source_evidence_is_rejected_without_placeholder(
    analysis_service,
    monkeypatch,
) -> None:
    snapshot = snapshot_for(analysis_service, "DEMO-005")
    original_index = brief_input_module._fact_index(snapshot)
    missing_id = snapshot.priority_actions[0].recommendation.evidence_refs[0]
    incomplete_index = {
        fact_id: fact for fact_id, fact in original_index.items() if fact_id != missing_id
    }
    monkeypatch.setattr(
        brief_input_module,
        "_fact_index",
        lambda source_snapshot: incomplete_index,
    )

    with pytest.raises(DataValidationError) as error:
        build_grounded_brief_input(snapshot)
    assert error.value.code == "brief_input.unresolved_fact_ref"


def test_builder_is_idempotent_and_does_not_mutate_source(analysis_service) -> None:
    snapshot = snapshot_for(analysis_service, "DEMO-006", "DEMO-015")
    source_actions = snapshot.priority_actions
    source_results = snapshot.sku_results

    first = build_grounded_brief_input(snapshot)
    second = build_grounded_brief_input(snapshot)

    assert first == second
    assert serialize_grounded_brief_input(first) == serialize_grounded_brief_input(second)
    assert snapshot.priority_actions is source_actions
    assert snapshot.sku_results is source_results
    with pytest.raises(FrozenInstanceError):
        first.facts = ()


@pytest.mark.parametrize(
    ("source", "expected", "value_type"),
    [
        (Decimal("1233.00"), "1233.00", GroundedValueType.DECIMAL),
        (Decimal("-649.70"), "-649.70", GroundedValueType.DECIMAL),
        (42, 42, GroundedValueType.INTEGER),
        (True, True, GroundedValueType.BOOLEAN),
        ("insufficient_data", "insufficient_data", GroundedValueType.TEXT),
        (date(2026, 9, 4), "2026-09-04", GroundedValueType.DATE),
        (
            datetime(2026, 9, 4, 9, 30, tzinfo=UTC),
            "2026-09-04T09:30:00+00:00",
            GroundedValueType.DATETIME,
        ),
    ],
)
def test_canonical_fact_value_fidelity(source, expected, value_type) -> None:
    assert canonicalize_fact_value(source) == (expected, value_type)


def test_canonicalization_rejects_naive_datetime_and_nonfinite_decimal() -> None:
    with pytest.raises(DataValidationError) as naive:
        canonicalize_fact_value(datetime(2026, 9, 4, 9))
    assert naive.value.code == "clock.naive_datetime"

    with pytest.raises(DataValidationError) as nonfinite:
        canonicalize_fact_value(Decimal("Infinity"))
    assert nonfinite.value.code == "brief_input.nonfinite_fact_value"


def test_json_serialization_is_allow_listed_deterministic_and_float_free(
    analysis_service,
) -> None:
    grounded = build_grounded_brief_input(snapshot_for(analysis_service, "DEMO-015"))
    encoded = serialize_grounded_brief_input(grounded)
    decoded = json.loads(encoded)

    assert encoded == serialize_grounded_brief_input(grounded)
    assert set(decoded) == {"analysis_timestamp", "actions", "facts"}
    assert decoded["analysis_timestamp"] == STAMP.isoformat()
    assert all(
        isinstance(fact["value"], str)
        for fact in decoded["facts"]
        if fact["value_type"] == "decimal"
    )
    assert all(
        set(action)
        == {
            "action_ref",
            "recommendation_id",
            "sku",
            "product_name",
            "rule_id",
            "category",
            "status",
            "rank",
            "severity",
            "urgency",
            "fact_refs",
        }
        for action in decoded["actions"]
    )


def test_real_grounded_values_equal_authoritative_fact_serialization(
    analysis_service,
) -> None:
    snapshot = snapshot_for(analysis_service, "DEMO-005", "DEMO-006")
    grounded = build_grounded_brief_input(snapshot)
    authoritative = source_facts(snapshot)

    for fact in grounded.facts:
        expected_value, expected_type = canonicalize_fact_value(
            authoritative[fact.fact_id].value
        )
        assert fact.value == expected_value
        assert fact.value_type is expected_type
        assert fact.source_refs == authoritative[fact.fact_id].source_refs

    assert any(fact.value_type is GroundedValueType.DECIMAL for fact in grounded.facts)
    assert any(fact.value_type is GroundedValueType.INTEGER for fact in grounded.facts)
    assert any(fact.value_type is GroundedValueType.DATE for fact in grounded.facts)


def test_grounding_module_has_no_forbidden_dependencies_or_policy_copy() -> None:
    source = Path(brief_input_module.__file__).read_text(encoding="utf-8").lower()
    forbidden = (
        "from app.providers",
        "import app.providers",
        "from app.analytics",
        "import app.analytics",
        "from app.decisions",
        "import app.decisions",
        "build_priority_action_center",
        "approved_rule_priorities",
        "approved_severity_order",
        "approved_urgency_order",
        "from app.services.analysis",
        "mockozonprovider",
        "openai",
        "requests",
        "urllib",
        "datetime.now(",
        "datetime.utcnow(",
        "date.today(",
        "uuid",
        "random",
        "open(",
    )
    assert all(token not in source for token in forbidden)

"""Build and serialize the action-centric grounding boundary.

The builder copies only an existing snapshot's ranked actions and their exact
calculated-fact evidence closure.  It performs no provider reads, analytics,
decision evaluation, prioritization, clock access, or model invocation.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import json

from app.core.clock import require_aware_datetime
from app.core.errors import DataValidationError
from app.domain.briefs import (
    GroundedAction,
    GroundedBriefInput,
    GroundedFact,
    GroundedValue,
    GroundedValueType,
)
from app.domain.common import SkuId, require_instance
from app.domain.recommendations import CalculatedFact, Recommendation
from app.services.models import AnalysisSnapshot


def canonicalize_fact_value(value: object) -> tuple[GroundedValue, GroundedValueType]:
    """Return the exact transport scalar and stable type of one fact value."""

    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DataValidationError(
                "calculated fact Decimal must be finite",
                code="brief_input.nonfinite_fact_value",
                scope="calculated fact value",
            )
        return format(value, "f"), GroundedValueType.DECIMAL
    if isinstance(value, bool):
        return value, GroundedValueType.BOOLEAN
    if isinstance(value, int):
        return value, GroundedValueType.INTEGER
    if isinstance(value, datetime):
        require_aware_datetime(value, field_name="calculated fact datetime")
        return value.isoformat(), GroundedValueType.DATETIME
    if isinstance(value, date):
        return value.isoformat(), GroundedValueType.DATE
    if isinstance(value, str) and value.strip():
        return value, GroundedValueType.TEXT
    raise DataValidationError(
        "calculated fact value type is unsupported for grounding",
        code="brief_input.unsupported_fact_value",
        scope="calculated fact value",
    )


def _fact_index(snapshot: AnalysisSnapshot) -> dict[str, CalculatedFact]:
    facts_by_id: dict[str, CalculatedFact] = {}
    for result in snapshot.sku_results:
        for evaluation in result.decision_evaluations:
            for fact in evaluation.facts:
                existing = facts_by_id.get(fact.fact_id)
                if existing is not None and existing != fact:
                    raise DataValidationError(
                        "one fact identifier resolves to conflicting calculated facts",
                        code="brief_input.conflicting_fact_id",
                        scope=fact.fact_id,
                    )
                facts_by_id[fact.fact_id] = fact
    return facts_by_id


def _recommendation_index(snapshot: AnalysisSnapshot) -> dict[str, Recommendation]:
    recommendations: dict[str, Recommendation] = {}
    for result in snapshot.sku_results:
        for evaluation in result.decision_evaluations:
            for recommendation in evaluation.recommendations:
                if recommendation.recommendation_id in recommendations:
                    raise DataValidationError(
                        "snapshot contains duplicate recommendation identifiers",
                        code="brief_input.duplicate_recommendation_id",
                        scope=recommendation.recommendation_id,
                    )
                recommendations[recommendation.recommendation_id] = recommendation
    return recommendations


def _ground_fact(fact: CalculatedFact) -> GroundedFact:
    if fact.sku is None:
        raise DataValidationError(
            "priority action evidence requires a SKU",
            code="brief_input.missing_fact_sku",
            scope=fact.fact_id,
        )
    value, value_type = canonicalize_fact_value(fact.value)
    return GroundedFact(
        fact_id=fact.fact_id,
        sku=fact.sku,
        name=fact.name,
        value=value,
        value_type=value_type,
        unit=fact.unit,
        period=fact.period,
        formula_or_rule_id=fact.formula_or_rule_id,
        source_refs=fact.source_refs,
    )


def build_grounded_brief_input(snapshot: AnalysisSnapshot) -> GroundedBriefInput:
    """Copy one completed snapshot into the approved minimal grounding schema."""

    snapshot = require_instance(snapshot, AnalysisSnapshot, field_name="analysis snapshot")
    facts_by_id = _fact_index(snapshot)
    recommendations = _recommendation_index(snapshot)
    results_by_sku = {result.sku: result for result in snapshot.sku_results}

    actions: list[GroundedAction] = []
    referenced_fact_ids: set[str] = set()
    for source_action in snapshot.priority_actions:
        recommendation = source_action.recommendation
        authoritative = recommendations.get(recommendation.recommendation_id)
        if authoritative is None:
            raise DataValidationError(
                "priority action references an unknown recommendation",
                code="brief_input.unresolved_recommendation",
                scope=recommendation.recommendation_id,
            )
        if authoritative != recommendation:
            raise DataValidationError(
                "priority action recommendation conflicts with snapshot decisions",
                code="brief_input.conflicting_recommendation",
                scope=recommendation.recommendation_id,
            )
        if recommendation.analysis_timestamp != snapshot.analysis_timestamp:
            raise DataValidationError(
                "priority action does not share the snapshot timestamp",
                code="brief_input.mixed_analysis_timestamps",
                scope=recommendation.recommendation_id,
            )
        if recommendation.sku is None or recommendation.sku not in results_by_sku:
            raise DataValidationError(
                "priority action SKU is outside the analysis snapshot",
                code="brief_input.action_outside_snapshot",
                scope=recommendation.recommendation_id,
            )
        if source_action.rank is None:
            raise DataValidationError(
                "priority action requires an authoritative rank",
                code="brief_input.missing_action_rank",
                scope=recommendation.recommendation_id,
            )

        for fact_ref in recommendation.evidence_refs:
            fact = facts_by_id.get(fact_ref)
            if fact is None:
                raise DataValidationError(
                    "priority action evidence cannot be resolved",
                    code="brief_input.unresolved_fact_ref",
                    scope=fact_ref,
                )
            if fact.sku != recommendation.sku:
                raise DataValidationError(
                    "priority action evidence belongs to another SKU",
                    code="brief_input.cross_sku_evidence",
                    scope=fact_ref,
                )
            referenced_fact_ids.add(fact_ref)

        source_result = results_by_sku[recommendation.sku]
        product_name = source_result.product.name if source_result.product is not None else None
        actions.append(
            GroundedAction(
                action_ref=recommendation.recommendation_id,
                recommendation_id=recommendation.recommendation_id,
                sku=recommendation.sku,
                product_name=product_name,
                rule_id=recommendation.rule_code,
                category=recommendation.category,
                status=recommendation.status,
                rank=source_action.rank,
                severity=source_action.severity,
                urgency=source_action.urgency,
                fact_refs=recommendation.evidence_refs,
            )
        )

    facts = tuple(
        _ground_fact(facts_by_id[fact_id]) for fact_id in sorted(referenced_fact_ids)
    )
    return GroundedBriefInput(snapshot.analysis_timestamp, tuple(actions), facts)


def grounded_brief_input_payload(brief_input: GroundedBriefInput) -> dict[str, object]:
    """Return a fresh allow-listed JSON-compatible payload."""

    brief_input = require_instance(
        brief_input,
        GroundedBriefInput,
        field_name="grounded brief input",
    )
    return {
        "analysis_timestamp": brief_input.analysis_timestamp.isoformat(),
        "actions": [
            {
                "action_ref": action.action_ref,
                "recommendation_id": action.recommendation_id,
                "sku": str(action.sku),
                "product_name": action.product_name,
                "rule_id": action.rule_id,
                "category": action.category.value,
                "status": action.status.value,
                "rank": action.rank,
                "severity": action.severity.value,
                "urgency": action.urgency.value,
                "fact_refs": list(action.fact_refs),
            }
            for action in brief_input.actions
        ],
        "facts": [
            {
                "fact_id": fact.fact_id,
                "sku": str(fact.sku),
                "name": fact.name,
                "value": fact.value,
                "value_type": fact.value_type.value,
                "unit": fact.unit,
                "period": (
                    None
                    if fact.period is None
                    else {
                        "start": fact.period.start.isoformat(),
                        "end": fact.period.end.isoformat(),
                    }
                ),
                "formula_or_rule_id": fact.formula_or_rule_id,
                "source_refs": list(fact.source_refs),
            }
            for fact in brief_input.facts
        ],
    }


def serialize_grounded_brief_input(brief_input: GroundedBriefInput) -> str:
    """Serialize grounding deterministically without a framework or float coercion."""

    return json.dumps(
        grounded_brief_input_payload(brief_input),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

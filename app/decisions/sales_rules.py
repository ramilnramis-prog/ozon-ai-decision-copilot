"""Deterministic recommendations derived only from completed sales metrics."""

from __future__ import annotations

from datetime import datetime

from app.analytics.sales import SalesChangeDirection, SalesMetrics
from app.domain.common import AvailabilityStatus, require_instance
from app.domain.recommendations import RecommendationCategory
from app.decisions.common import (
    DecisionEvaluation,
    empty_evaluation,
    evidence_source_refs,
    make_fact,
    make_recommendation,
)


RULE_INSUFFICIENT_HISTORY = "sales.insufficient_history"
RULE_MATERIAL_INCREASE = "sales.material_increase"
RULE_MATERIAL_DECLINE = "sales.material_decline"


def evaluate_sales_rules(
    metrics: SalesMetrics,
    analysis_timestamp: datetime,
) -> DecisionEvaluation:
    """Emit material-change or data-quality recommendations without recalculation."""

    metrics = require_instance(metrics, SalesMetrics, field_name="sales decision input")
    refs = evidence_source_refs(
        (*metrics.current.source_refs, *metrics.comparison.source_refs),
        metrics.policy.identity,
    )
    if metrics.status is AvailabilityStatus.INSUFFICIENT_DATA:
        facts = (
            make_fact(
                sku=metrics.sku,
                rule_code=RULE_INSUFFICIENT_HISTORY,
                suffix="availability",
                name="sales metrics availability",
                value=metrics.status.value,
                unit="status",
                source_refs=refs,
                analysis_timestamp=analysis_timestamp,
            ),
        )
        recommendation = make_recommendation(
            sku=metrics.sku,
            category=RecommendationCategory.DATA_QUALITY,
            rule_code=RULE_INSUFFICIENT_HISTORY,
            explanation="Sales trend is unavailable because the configured windows are incomplete.",
            proposed_action="Complete the missing daily sales history.",
            facts=facts,
            analysis_timestamp=analysis_timestamp,
        )
        return DecisionEvaluation(metrics.sku, analysis_timestamp, facts, (recommendation,))

    direction = metrics.direction
    if direction is SalesChangeDirection.STABLE:
        return empty_evaluation(metrics.sku, analysis_timestamp)
    if direction is SalesChangeDirection.INCREASING:
        rule_code = RULE_MATERIAL_INCREASE
        direction_name = "increase"
        action = "Review the observed material sales increase."
    else:
        rule_code = RULE_MATERIAL_DECLINE
        direction_name = "decline"
        action = "Review the observed material sales decline."

    facts = [
        make_fact(
            sku=metrics.sku,
            rule_code=rule_code,
            suffix="direction",
            name="sales change direction",
            value=direction.value,
            unit="status",
            source_refs=refs,
            analysis_timestamp=analysis_timestamp,
        ),
        make_fact(
            sku=metrics.sku,
            rule_code=rule_code,
            suffix="current_units",
            name="current-window units sold",
            value=metrics.current.total_units,
            unit="units",
            source_refs=refs,
            analysis_timestamp=analysis_timestamp,
            period=metrics.current.period,
        ),
        make_fact(
            sku=metrics.sku,
            rule_code=rule_code,
            suffix="comparison_units",
            name="comparison-window units sold",
            value=metrics.comparison.total_units,
            unit="units",
            source_refs=refs,
            analysis_timestamp=analysis_timestamp,
            period=metrics.comparison.period,
        ),
        make_fact(
            sku=metrics.sku,
            rule_code=rule_code,
            suffix="threshold",
            name="material sales-change threshold",
            value=metrics.policy.material_change_threshold,
            unit="fraction",
            source_refs=refs,
            analysis_timestamp=analysis_timestamp,
        ),
    ]
    if metrics.relative_change is not None:
        facts.append(
            make_fact(
                sku=metrics.sku,
                rule_code=rule_code,
                suffix="relative_change",
                name="relative sales change",
                value=metrics.relative_change,
                unit="fraction",
                source_refs=refs,
                analysis_timestamp=analysis_timestamp,
            )
        )
    fact_tuple = tuple(facts)
    recommendation = make_recommendation(
        sku=metrics.sku,
        category=RecommendationCategory.SALES_CHANGE,
        rule_code=rule_code,
        explanation=(
            f"Completed sales windows show a material {direction_name} under policy "
            f"{metrics.policy.identity.policy_id}:{metrics.policy.identity.version}."
        ),
        proposed_action=action,
        facts=fact_tuple,
        analysis_timestamp=analysis_timestamp,
    )
    return DecisionEvaluation(metrics.sku, analysis_timestamp, fact_tuple, (recommendation,))

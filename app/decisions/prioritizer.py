"""Deterministic Priority Action Center ordering for completed decisions.

This module adds ranking metadata to existing recommendations.  It does not
re-evaluate rules, inspect business values, or create recommendations.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.core.errors import DataValidationError
from app.decisions.common import DecisionEvaluation
from app.domain.common import AvailabilityStatus, Severity, Urgency, require_instance
from app.domain.recommendations import (
    PriorityAction,
    PriorityPolicy,
    Recommendation,
    RecommendationStatus,
)


APPROVED_SEVERITY_ORDER = (
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.WARNING,
    Severity.INFO,
)
APPROVED_URGENCY_ORDER = (
    Urgency.IMMEDIATE,
    Urgency.SOON,
    Urgency.MONITOR,
)


@dataclass(frozen=True, slots=True)
class RulePriority:
    """Approved categorical priority for one production rule identifier."""

    severity: Severity
    urgency: Urgency
    precedence: int


APPROVED_RULE_PRIORITIES: Mapping[str, RulePriority] = MappingProxyType(
    {
        "inventory.out_of_stock": RulePriority(Severity.CRITICAL, Urgency.IMMEDIATE, 1),
        "inventory.replenishment_already_late": RulePriority(
            Severity.CRITICAL, Urgency.IMMEDIATE, 2
        ),
        "profitability.no_finite_safe_price": RulePriority(
            Severity.CRITICAL, Urgency.IMMEDIATE, 3
        ),
        "profitability.loss_making": RulePriority(
            Severity.CRITICAL, Urgency.IMMEDIATE, 4
        ),
        "inventory.replenishment_due_now": RulePriority(
            Severity.HIGH, Urgency.IMMEDIATE, 5
        ),
        "pricing.current_price_unsafe": RulePriority(
            Severity.HIGH, Urgency.IMMEDIATE, 6
        ),
        "inventory.replenishment_due_soon": RulePriority(
            Severity.HIGH, Urgency.SOON, 7
        ),
        "profitability.below_minimum_profit": RulePriority(
            Severity.HIGH, Urgency.SOON, 8
        ),
        "profitability.below_minimum_margin": RulePriority(
            Severity.HIGH, Urgency.SOON, 9
        ),
        "pricing.scenario_below_safe_price": RulePriority(
            Severity.HIGH, Urgency.SOON, 10
        ),
        "economics.insufficient_data": RulePriority(
            Severity.WARNING, Urgency.SOON, 11
        ),
        "inventory.missing_lead_time": RulePriority(
            Severity.WARNING, Urgency.SOON, 12
        ),
        "sales.material_decline": RulePriority(
            Severity.WARNING, Urgency.SOON, 13
        ),
        "inventory.overstock_candidate": RulePriority(
            Severity.WARNING, Urgency.MONITOR, 14
        ),
        "inventory.insufficient_sales_history": RulePriority(
            Severity.WARNING, Urgency.MONITOR, 15
        ),
        "sales.insufficient_history": RulePriority(
            Severity.WARNING, Urgency.MONITOR, 16
        ),
        "pricing.scenario_insufficient_data": RulePriority(
            Severity.WARNING, Urgency.MONITOR, 17
        ),
        "sales.material_increase": RulePriority(
            Severity.INFO, Urgency.MONITOR, 18
        ),
    }
)


def _validate_policy(policy: PriorityPolicy) -> PriorityPolicy:
    policy = require_instance(policy, PriorityPolicy, field_name="priority policy")
    if policy.severity_order != APPROVED_SEVERITY_ORDER:
        raise DataValidationError(
            "priority policy does not use the approved severity order",
            code="prioritizer.unsupported_severity_order",
            scope="priority policy",
        )
    if policy.urgency_order != APPROVED_URGENCY_ORDER:
        raise DataValidationError(
            "priority policy does not use the approved urgency order",
            code="prioritizer.unsupported_urgency_order",
            scope="priority policy",
        )
    return policy


def _priority_for(recommendation: Recommendation) -> RulePriority:
    priority = APPROVED_RULE_PRIORITIES.get(recommendation.rule_code)
    if priority is None:
        raise DataValidationError(
            "recommendation rule is not present in the approved priority policy",
            code="prioritizer.unmapped_rule",
            scope="recommendation rule",
        )
    return priority


def _tie_break_key(
    sort_key: tuple[int, int, int, str, str],
) -> str:
    severity_position, urgency_position, precedence, sku, recommendation_id = sort_key
    return (
        f"{severity_position:02d}|{urgency_position:02d}|"
        f"{precedence:02d}|{len(sku)}:{sku}|"
        f"{len(recommendation_id)}:{recommendation_id}"
    )


def build_priority_action_center(
    evaluations: Iterable[DecisionEvaluation],
    policy: PriorityPolicy,
) -> tuple[PriorityAction, ...]:
    """Rank every recommendation from one coherent decision snapshot.

    The returned tuple is a complete, immutable ordered view.  No input is
    mutated, grouped, suppressed, or truncated.
    """

    policy = _validate_policy(policy)
    evaluation_items = tuple(evaluations)
    for evaluation in evaluation_items:
        require_instance(
            evaluation,
            DecisionEvaluation,
            field_name="priority decision evaluation",
        )

    if evaluation_items:
        analysis_timestamp = evaluation_items[0].analysis_timestamp
        if any(
            evaluation.analysis_timestamp != analysis_timestamp
            for evaluation in evaluation_items[1:]
        ):
            raise DataValidationError(
                "priority inputs must share one analysis timestamp",
                code="prioritizer.mixed_analysis_timestamps",
                scope="priority decision evaluations",
            )

    recommendations = tuple(
        recommendation
        for evaluation in evaluation_items
        for recommendation in evaluation.recommendations
    )
    recommendation_ids = tuple(
        recommendation.recommendation_id for recommendation in recommendations
    )
    if len(set(recommendation_ids)) != len(recommendation_ids):
        raise DataValidationError(
            "priority inputs contain duplicate recommendation identifiers",
            code="prioritizer.duplicate_recommendation_id",
            scope="priority recommendations",
        )

    severity_positions = {
        severity: position for position, severity in enumerate(policy.severity_order)
    }
    urgency_positions = {
        urgency: position for position, urgency in enumerate(policy.urgency_order)
    }
    candidates: list[
        tuple[tuple[int, int, int, str, str], Recommendation, RulePriority]
    ] = []
    for recommendation in recommendations:
        if recommendation.status is not RecommendationStatus.PROPOSED:
            raise DataValidationError(
                "only proposed recommendations can be ranked as priority actions",
                code="prioritizer.unsupported_recommendation_status",
                scope="priority recommendation",
            )
        if recommendation.sku is None:
            raise DataValidationError(
                "priority recommendation requires a SKU",
                code="prioritizer.missing_sku",
                scope="priority recommendation",
            )
        priority = _priority_for(recommendation)
        sort_key = (
            severity_positions[priority.severity],
            urgency_positions[priority.urgency],
            priority.precedence,
            str(recommendation.sku),
            recommendation.recommendation_id,
        )
        candidates.append((sort_key, recommendation, priority))

    ordered = sorted(candidates, key=lambda candidate: candidate[0])
    return tuple(
        PriorityAction(
            recommendation=recommendation,
            severity=priority.severity,
            urgency=priority.urgency,
            status=AvailabilityStatus.AVAILABLE,
            rank=rank,
            tie_break_key=_tie_break_key(sort_key),
        )
        for rank, (sort_key, recommendation, priority) in enumerate(ordered, start=1)
    )

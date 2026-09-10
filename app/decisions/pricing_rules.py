"""Deterministic profitability and price-safety recommendations."""

from __future__ import annotations

from datetime import datetime

from app.domain.common import AvailabilityStatus, SafetyStatus, require_instance
from app.domain.economics import PricingScenarioResult, UnitEconomicsResult
from app.domain.recommendations import CalculatedFact, Recommendation, RecommendationCategory
from app.decisions.common import (
    DecisionEvaluation,
    empty_evaluation,
    evidence_source_refs,
    make_fact,
    make_recommendation,
)


RULE_INSUFFICIENT_ECONOMICS = "economics.insufficient_data"
RULE_LOSS_MAKING = "profitability.loss_making"
RULE_BELOW_MINIMUM_PROFIT = "profitability.below_minimum_profit"
RULE_BELOW_MINIMUM_MARGIN = "profitability.below_minimum_margin"
RULE_NO_FINITE_SAFE_PRICE = "profitability.no_finite_safe_price"
RULE_CURRENT_PRICE_UNSAFE = "pricing.current_price_unsafe"
RULE_SCENARIO_INSUFFICIENT = "pricing.scenario_insufficient_data"
RULE_SCENARIO_BELOW_SAFE = "pricing.scenario_below_safe_price"


def _fact(
    result: UnitEconomicsResult,
    analysis_timestamp: datetime,
    rule_code: str,
    suffix: str,
    name: str,
    value: object,
    unit: str,
) -> CalculatedFact:
    return make_fact(
        sku=result.sku,
        rule_code=rule_code,
        suffix=suffix,
        name=name,
        value=value,
        unit=unit,
        source_refs=evidence_source_refs(result.evidence_refs, result.policy.identity),
        analysis_timestamp=analysis_timestamp,
        period=result.source_input.source_period,
    )


def _recommendation_evaluation(
    result: UnitEconomicsResult,
    analysis_timestamp: datetime,
    *,
    rule_code: str,
    category: RecommendationCategory,
    explanation: str,
    proposed_action: str,
    values: tuple[tuple[str, str, object, str], ...],
) -> tuple[tuple[CalculatedFact, ...], Recommendation]:
    facts = tuple(
        _fact(result, analysis_timestamp, rule_code, suffix, name, value, unit)
        for suffix, name, value, unit in values
    )
    recommendation = make_recommendation(
        sku=result.sku,
        category=category,
        rule_code=rule_code,
        explanation=explanation,
        proposed_action=proposed_action,
        facts=facts,
        analysis_timestamp=analysis_timestamp,
    )
    return facts, recommendation


def evaluate_economics_rules(
    result: UnitEconomicsResult,
    analysis_timestamp: datetime,
) -> DecisionEvaluation:
    """Evaluate complete economics without recomputing any financial value."""

    result = require_instance(
        result,
        UnitEconomicsResult,
        field_name="economics decision input",
    )
    if result.status is AvailabilityStatus.INSUFFICIENT_DATA:
        facts, recommendation = _recommendation_evaluation(
            result,
            analysis_timestamp,
            rule_code=RULE_INSUFFICIENT_ECONOMICS,
            category=RecommendationCategory.DATA_QUALITY,
            explanation="Profitability and price safety are unavailable because economics inputs are incomplete.",
            proposed_action="Complete the missing unit-economics inputs.",
            values=(("status", "unit-economics availability", result.status.value, "status"),),
        )
        return DecisionEvaluation(result.sku, analysis_timestamp, facts, (recommendation,))

    assert result.profit_per_unit is not None
    assert result.contribution_margin is not None
    assert result.total_variable_cost is not None
    evaluations: list[tuple[tuple[CalculatedFact, ...], Recommendation]] = []
    if result.profit_per_unit < 0:
        evaluations.append(
            _recommendation_evaluation(
                result,
                analysis_timestamp,
                rule_code=RULE_LOSS_MAKING,
                category=RecommendationCategory.PROFITABILITY,
                explanation="Calculated contribution profit per unit is below zero.",
                proposed_action="Review the SKU cost structure and selling price.",
                values=(
                    ("price", "selling price", result.selling_price, result.currency.value),
                    ("cost", "total variable cost", result.total_variable_cost, result.currency.value),
                    ("profit", "contribution profit per unit", result.profit_per_unit, result.currency.value),
                ),
            )
        )
    if result.profit_per_unit < result.policy.minimum_profit_per_unit:
        evaluations.append(
            _recommendation_evaluation(
                result,
                analysis_timestamp,
                rule_code=RULE_BELOW_MINIMUM_PROFIT,
                category=RecommendationCategory.PROFITABILITY,
                explanation="Contribution profit per unit is below the configured minimum.",
                proposed_action="Review the SKU cost structure and selling price.",
                values=(
                    ("profit", "contribution profit per unit", result.profit_per_unit, result.currency.value),
                    ("threshold", "minimum profit per unit", result.policy.minimum_profit_per_unit, result.currency.value),
                ),
            )
        )
    if result.contribution_margin < result.policy.minimum_margin:
        evaluations.append(
            _recommendation_evaluation(
                result,
                analysis_timestamp,
                rule_code=RULE_BELOW_MINIMUM_MARGIN,
                category=RecommendationCategory.PROFITABILITY,
                explanation="Contribution margin is below the configured minimum.",
                proposed_action="Review the SKU cost structure and selling price.",
                values=(
                    ("margin", "contribution margin", result.contribution_margin, "fraction"),
                    ("threshold", "minimum contribution margin", result.policy.minimum_margin, "fraction"),
                ),
            )
        )

    if result.minimum_safe.status is AvailabilityStatus.NOT_APPLICABLE:
        evaluations.append(
            _recommendation_evaluation(
                result,
                analysis_timestamp,
                rule_code=RULE_NO_FINITE_SAFE_PRICE,
                category=RecommendationCategory.PROFITABILITY,
                explanation="No finite safe price exists under the supplied normalized cost assumptions.",
                proposed_action="Review the normalized costs and proportional rates.",
                values=(
                    ("safe_status", "minimum safe-price availability", result.minimum_safe.status.value, "status"),
                    ("rate", "total proportional cost rate", result.proportional_cost_rate, "fraction"),
                ),
            )
        )
    elif result.safety_status is SafetyStatus.UNSAFE:
        assert result.minimum_safe_price is not None
        evaluations.append(
            _recommendation_evaluation(
                result,
                analysis_timestamp,
                rule_code=RULE_CURRENT_PRICE_UNSAFE,
                category=RecommendationCategory.PRICING,
                explanation="Current selling price is below the calculated minimum safe price.",
                proposed_action="consider increasing price",
                values=(
                    ("price", "current selling price", result.selling_price, result.currency.value),
                    ("safe_price", "minimum safe price", result.minimum_safe_price, result.currency.value),
                    ("profit", "contribution profit per unit", result.profit_per_unit, result.currency.value),
                    ("margin", "contribution margin", result.contribution_margin, "fraction"),
                ),
            )
        )

    facts = tuple(fact for fact_group, _ in evaluations for fact in fact_group)
    recommendations = tuple(recommendation for _, recommendation in evaluations)
    return DecisionEvaluation(result.sku, analysis_timestamp, facts, recommendations)


def evaluate_pricing_scenario_rules(
    result: PricingScenarioResult,
    analysis_timestamp: datetime,
) -> DecisionEvaluation:
    """Explain an unsafe hypothetical price without recommending or executing a price."""

    result = require_instance(
        result,
        PricingScenarioResult,
        field_name="pricing scenario decision input",
    )
    candidate = result.candidate_economics
    instance_key = result.scenario.scenario_id
    refs = evidence_source_refs(result.evidence_refs, candidate.policy.identity)
    if result.status is AvailabilityStatus.INSUFFICIENT_DATA:
        rule_code = RULE_SCENARIO_INSUFFICIENT
        facts = (
            make_fact(
                sku=candidate.sku,
                rule_code=rule_code,
                suffix="status",
                name="pricing scenario availability",
                value=result.status.value,
                unit="status",
                source_refs=refs,
                analysis_timestamp=analysis_timestamp,
                instance_key=instance_key,
            ),
        )
        recommendation = make_recommendation(
            sku=candidate.sku,
            category=RecommendationCategory.DATA_QUALITY,
            rule_code=rule_code,
            explanation="The hypothetical price cannot be fully evaluated because economics inputs are incomplete.",
            proposed_action="Complete the missing unit-economics inputs.",
            facts=facts,
            analysis_timestamp=analysis_timestamp,
            instance_key=instance_key,
        )
        return DecisionEvaluation(candidate.sku, analysis_timestamp, facts, (recommendation,))
    if result.safety_status is not SafetyStatus.UNSAFE or result.distance_from_safe_price is None:
        return empty_evaluation(candidate.sku, analysis_timestamp)

    assert candidate.minimum_safe_price is not None
    rule_code = RULE_SCENARIO_BELOW_SAFE
    facts = tuple(
        make_fact(
            sku=candidate.sku,
            rule_code=rule_code,
            suffix=suffix,
            name=name,
            value=value,
            unit=unit,
            source_refs=refs,
            analysis_timestamp=analysis_timestamp,
            instance_key=instance_key,
        )
        for suffix, name, value, unit in (
            ("candidate", "hypothetical candidate price", result.candidate_price, candidate.currency.value),
            ("safe_price", "minimum safe price", candidate.minimum_safe_price, candidate.currency.value),
            ("distance", "distance from minimum safe price", result.distance_from_safe_price, candidate.currency.value),
        )
    )
    recommendation = make_recommendation(
        sku=candidate.sku,
        category=RecommendationCategory.PRICING,
        rule_code=rule_code,
        explanation="The hypothetical candidate price is below the calculated minimum safe price.",
        proposed_action="do not decrease below the safe threshold",
        facts=facts,
        analysis_timestamp=analysis_timestamp,
        instance_key=instance_key,
    )
    return DecisionEvaluation(candidate.sku, analysis_timestamp, facts, (recommendation,))

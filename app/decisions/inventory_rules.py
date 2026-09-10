"""Deterministic inventory recommendations over completed MVP-008 results."""

from __future__ import annotations

from datetime import datetime

from app.analytics.inventory import InventoryAnalysisResult, ReplenishmentTiming
from app.domain.common import AvailabilityStatus, RiskStatus, require_instance
from app.domain.recommendations import RecommendationCategory
from app.decisions.common import (
    DecisionEvaluation,
    empty_evaluation,
    evidence_source_refs,
    make_fact,
    make_recommendation,
)


RULE_INSUFFICIENT_SALES = "inventory.insufficient_sales_history"
RULE_MISSING_LEAD_TIME = "inventory.missing_lead_time"
RULE_OUT_OF_STOCK = "inventory.out_of_stock"
RULE_REPLENISHMENT_LATE = "inventory.replenishment_already_late"
RULE_REPLENISHMENT_DUE_NOW = "inventory.replenishment_due_now"
RULE_REPLENISHMENT_DUE_SOON = "inventory.replenishment_due_soon"
RULE_OVERSTOCK = "inventory.overstock_candidate"


def _evaluation(
    result: InventoryAnalysisResult,
    analysis_timestamp: datetime,
    *,
    rule_code: str,
    category: RecommendationCategory,
    explanation: str,
    proposed_action: str,
    fact_values: tuple[tuple[str, str, object, str], ...],
) -> DecisionEvaluation:
    refs = evidence_source_refs(result.source_refs, result.policy.identity)
    facts = tuple(
        make_fact(
            sku=result.sku,
            rule_code=rule_code,
            suffix=suffix,
            name=name,
            value=value,
            unit=unit,
            source_refs=refs,
            analysis_timestamp=analysis_timestamp,
        )
        for suffix, name, value, unit in fact_values
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
    return DecisionEvaluation(result.sku, analysis_timestamp, facts, (recommendation,))


def evaluate_inventory_rules(
    result: InventoryAnalysisResult,
    analysis_timestamp: datetime,
) -> DecisionEvaluation:
    """Apply explicit inventory-risk rules without recalculating inventory facts."""

    result = require_instance(
        result,
        InventoryAnalysisResult,
        field_name="inventory decision input",
    )
    if result.average_daily_sales is None:
        return _evaluation(
            result,
            analysis_timestamp,
            rule_code=RULE_INSUFFICIENT_SALES,
            category=RecommendationCategory.DATA_QUALITY,
            explanation="Inventory timing is unavailable because sales history is incomplete.",
            proposed_action="Complete the missing daily sales history.",
            fact_values=(("status", "inventory availability", result.status.value, "status"),),
        )
    if result.average_daily_sales == 0:
        return empty_evaluation(result.sku, analysis_timestamp)
    if result.lead_time_status is AvailabilityStatus.INSUFFICIENT_DATA:
        missing_lead = _evaluation(
            result,
            analysis_timestamp,
            rule_code=RULE_MISSING_LEAD_TIME,
            category=RecommendationCategory.DATA_QUALITY,
            explanation="Replenishment timing is unavailable because lead-time inputs are incomplete.",
            proposed_action="Complete the production, delivery, and safety-buffer inputs.",
            fact_values=(
                ("lead_time_status", "lead-time availability", result.lead_time_status.value, "status"),
            ),
        )
        if result.sellable_stock != 0:
            return missing_lead

        assert result.stockout_date is not None
        stockout = _evaluation(
            result,
            analysis_timestamp,
            rule_code=RULE_OUT_OF_STOCK,
            category=RecommendationCategory.INVENTORY,
            explanation=(
                "Sellable stock is zero while authoritative average daily sales are positive."
            ),
            proposed_action="Review immediate stock recovery.",
            fact_values=(
                ("risk", "inventory risk", RiskStatus.CRITICAL.value, "status"),
                ("sellable_stock", "current sellable stock", result.sellable_stock, "units"),
                (
                    "average_daily_sales",
                    "average daily sales",
                    result.average_daily_sales,
                    "units_per_day",
                ),
                ("stockout_date", "estimated stockout date", result.stockout_date, "date"),
            ),
        )
        return DecisionEvaluation(
            result.sku,
            analysis_timestamp,
            (*stockout.facts, *missing_lead.facts),
            (*stockout.recommendations, *missing_lead.recommendations),
        )

    quantity = result.recommended_replenishment_quantity
    assert quantity is not None
    assert result.stockout_date is not None
    assert result.stock_coverage_days is not None
    assert result.latest_safe_start_date is not None
    assert result.replenishment_timing is not None
    assert result.supply_lead_days is not None
    assert result.lead_time.safety_buffer_days is not None

    base_facts = (
        ("sellable_stock", "current sellable stock", result.sellable_stock, "units"),
        ("average_daily_sales", "average daily sales", result.average_daily_sales, "units_per_day"),
        ("stockout_date", "estimated stockout date", result.stockout_date, "date"),
        ("quantity", "recommended replenishment quantity", quantity, "units"),
        ("supply_lead_days", "supply lead time", result.supply_lead_days, "days"),
        (
            "safety_buffer_days",
            "inventory safety buffer",
            result.lead_time.safety_buffer_days,
            "days",
        ),
    )
    if result.sellable_stock == 0:
        action = (
            f"Review immediate stock recovery and a replenishment plan for {quantity} units."
            if quantity > 0
            else "Review immediate stock recovery and confirmed inbound coverage."
        )
        return _evaluation(
            result,
            analysis_timestamp,
            rule_code=RULE_OUT_OF_STOCK,
            category=RecommendationCategory.INVENTORY,
            explanation=(
                "Sellable stock is zero while authoritative average daily sales are positive."
            ),
            proposed_action=action,
            fact_values=(("risk", "inventory risk", RiskStatus.CRITICAL.value, "status"), *base_facts),
        )

    if quantity > 0 and result.replenishment_timing is ReplenishmentTiming.ALREADY_LATE:
        return _evaluation(
            result,
            analysis_timestamp,
            rule_code=RULE_REPLENISHMENT_LATE,
            category=RecommendationCategory.INVENTORY,
            explanation=(
                f"Latest safe replenishment start {result.latest_safe_start_date.isoformat()} "
                f"is before analysis date {result.as_of.isoformat()}."
            ),
            proposed_action=f"Review a replenishment plan for {quantity} units immediately.",
            fact_values=(
                ("risk", "inventory risk", RiskStatus.CRITICAL.value, "status"),
                *base_facts,
                ("latest_safe_start", "latest safe start date", result.latest_safe_start_date, "date"),
            ),
        )
    if quantity > 0 and result.replenishment_timing is ReplenishmentTiming.DUE_NOW:
        return _evaluation(
            result,
            analysis_timestamp,
            rule_code=RULE_REPLENISHMENT_DUE_NOW,
            category=RecommendationCategory.INVENTORY,
            explanation="The calculated latest safe replenishment start is the analysis date.",
            proposed_action=f"Review a replenishment plan for {quantity} units today.",
            fact_values=(
                ("risk", "inventory risk", RiskStatus.CRITICAL.value, "status"),
                *base_facts,
                ("latest_safe_start", "latest safe start date", result.latest_safe_start_date, "date"),
            ),
        )

    days_until_safe_start = (result.latest_safe_start_date - result.as_of).days
    if (
        quantity > 0
        and result.replenishment_timing is ReplenishmentTiming.FUTURE
        and days_until_safe_start <= result.policy.warning_window_days
    ):
        return _evaluation(
            result,
            analysis_timestamp,
            rule_code=RULE_REPLENISHMENT_DUE_SOON,
            category=RecommendationCategory.INVENTORY,
            explanation=(
                f"Latest safe replenishment start {result.latest_safe_start_date.isoformat()} "
                f"falls within the configured {result.policy.warning_window_days}-day warning window."
            ),
            proposed_action=f"Plan a review of replenishment for {quantity} units.",
            fact_values=(
                ("risk", "inventory risk", RiskStatus.REORDER_DUE_SOON.value, "status"),
                *base_facts,
                ("latest_safe_start", "latest safe start date", result.latest_safe_start_date, "date"),
                ("warning_days", "replenishment warning window", result.policy.warning_window_days, "days"),
            ),
        )
    if result.stock_coverage_days > result.policy.overstock_threshold_days:
        return _evaluation(
            result,
            analysis_timestamp,
            rule_code=RULE_OVERSTOCK,
            category=RecommendationCategory.INVENTORY,
            explanation=(
                f"Stock coverage exceeds the configured {result.policy.overstock_threshold_days}-day "
                "overstock attention threshold."
            ),
            proposed_action="Review stock levels before planning additional replenishment.",
            fact_values=(
                ("risk", "inventory risk", RiskStatus.OVERSTOCK_CANDIDATE.value, "status"),
                ("coverage", "stock coverage", result.stock_coverage_days, "days"),
                ("threshold", "overstock threshold", result.policy.overstock_threshold_days, "days"),
            ),
        )
    return empty_evaluation(result.sku, analysis_timestamp)

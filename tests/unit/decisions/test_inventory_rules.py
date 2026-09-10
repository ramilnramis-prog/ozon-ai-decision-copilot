"""Tests for deterministic inventory decision rules."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.analytics.inventory import calculate_inventory_analysis
from app.analytics.sales import calculate_sales_metrics
from app.decisions.inventory_rules import (
    RULE_INSUFFICIENT_SALES,
    RULE_MISSING_LEAD_TIME,
    RULE_OUT_OF_STOCK,
    RULE_OVERSTOCK,
    RULE_REPLENISHMENT_DUE_NOW,
    RULE_REPLENISHMENT_DUE_SOON,
    RULE_REPLENISHMENT_LATE,
    evaluate_inventory_rules,
)
from app.domain.catalog import SalesObservation, SalesPolicy
from app.domain.common import PolicyIdentity, Provenance, SkuId, SourceType
from app.domain.inventory import (
    InboundSupply,
    InboundStatus,
    InventoryPolicy,
    InventorySnapshot,
    LeadTime,
    ReplenishmentConstraints,
)


AS_OF = date(2026, 9, 2)
STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)
SKU = SkuId("SKU-INVENTORY-RULE")


def provenance(ref: str) -> Provenance:
    return Provenance(SourceType.DEMO, "inventory_rule_test", STAMP, source_record_id=ref)


def sales_result(units: int = 10, *, omit_last: bool = False):
    policy = SalesPolicy(PolicyIdentity("sales-rule", "1"), 14, 14, Decimal("0.25"))
    start = AS_OF - timedelta(days=28)
    observations = tuple(
        SalesObservation(SKU, start + timedelta(days=index), units, provenance(f"sales:{index}"))
        for index in range(28 - int(omit_last))
    )
    return calculate_sales_metrics(SKU, observations, policy, AS_OF)


def inventory_result(
    stock: int,
    *,
    units: int = 10,
    omit_sales: bool = False,
    lead: LeadTime | None = None,
    inbound: tuple[InboundSupply, ...] = (),
):
    selected_lead = lead or LeadTime(7, 3, 3, provenance("lead:1"))
    constraints = ReplenishmentConstraints(SKU, None, None, provenance("constraints:1"))
    policy = InventoryPolicy(
        PolicyIdentity("inventory-rule", "1"),
        selected_lead,
        target_coverage_days=30,
        warning_window_days=7,
        overstock_threshold_days=90,
        minimum_order_quantity=None,
        pack_size=None,
    )
    return calculate_inventory_analysis(
        InventorySnapshot(SKU, stock, STAMP, provenance("inventory:1")),
        sales_result(units, omit_last=omit_sales),
        inbound,
        selected_lead,
        constraints,
        policy,
        AS_OF,
    )


def rule_codes(result) -> tuple[str, ...]:
    return tuple(item.rule_code for item in result.recommendations)


def test_healthy_and_zero_replenishment_produce_no_filler_action() -> None:
    decision = evaluate_inventory_rules(inventory_result(500), STAMP)

    assert decision.recommendations == ()
    assert decision.facts == ()


def test_out_of_stock_with_positive_demand_emits_stockout_rule() -> None:
    decision = evaluate_inventory_rules(inventory_result(0), STAMP)

    assert rule_codes(decision) == (RULE_OUT_OF_STOCK,)
    assert {fact.name for fact in decision.facts} >= {
        "current sellable stock",
        "average daily sales",
        "estimated stockout date",
        "recommended replenishment quantity",
        "supply lead time",
        "inventory safety buffer",
    }


def test_zero_stock_and_missing_lead_emit_independent_findings() -> None:
    result = inventory_result(
        0,
        lead=LeadTime(None, 3, 3, provenance("lead:missing")),
    )

    decision = evaluate_inventory_rules(result, STAMP)

    assert rule_codes(decision) == (RULE_OUT_OF_STOCK, RULE_MISSING_LEAD_TIME)
    assert result.replenishment_timing is None
    assert result.recommended_replenishment_quantity is None
    stockout = decision.recommendations[0]
    stockout_facts = {
        fact.name
        for fact in decision.facts
        if fact.fact_id in stockout.evidence_refs
    }
    missing_lead_facts = {
        fact.name
        for fact in decision.facts
        if fact.fact_id in decision.recommendations[1].evidence_refs
    }
    assert stockout_facts == {
        "inventory risk",
        "current sellable stock",
        "average daily sales",
        "estimated stockout date",
    }
    assert missing_lead_facts == {"lead-time availability"}
    assert "units" not in stockout.proposed_action


def test_zero_stock_without_authoritative_positive_demand_is_not_stockout() -> None:
    missing_lead = LeadTime(None, 3, 3, provenance("lead:missing"))

    zero_demand = evaluate_inventory_rules(
        inventory_result(0, units=0, lead=missing_lead),
        STAMP,
    )
    insufficient_demand = evaluate_inventory_rules(
        inventory_result(0, omit_sales=True, lead=missing_lead),
        STAMP,
    )

    assert zero_demand.recommendations == ()
    assert rule_codes(insufficient_demand) == (RULE_INSUFFICIENT_SALES,)
    assert RULE_OUT_OF_STOCK not in rule_codes(insufficient_demand)


def test_late_due_now_and_due_soon_use_authoritative_timing_states() -> None:
    yesterday = evaluate_inventory_rules(inventory_result(120), STAMP)
    today = evaluate_inventory_rules(inventory_result(130), STAMP)
    tomorrow = evaluate_inventory_rules(inventory_result(140), STAMP)

    assert rule_codes(yesterday) == (RULE_REPLENISHMENT_LATE,)
    assert rule_codes(today) == (RULE_REPLENISHMENT_DUE_NOW,)
    assert rule_codes(tomorrow) == (RULE_REPLENISHMENT_DUE_SOON,)


def test_future_replenishment_outside_warning_window_is_not_actionable_yet() -> None:
    exact_boundary = evaluate_inventory_rules(inventory_result(200), STAMP)
    outside = evaluate_inventory_rules(inventory_result(210), STAMP)

    assert rule_codes(exact_boundary) == (RULE_REPLENISHMENT_DUE_SOON,)
    assert outside.recommendations == ()


def test_zero_demand_does_not_fabricate_stockout_or_replenishment() -> None:
    decision = evaluate_inventory_rules(inventory_result(100, units=0), STAMP)

    assert decision.recommendations == ()


def test_missing_sales_and_lead_time_emit_distinct_data_quality_rules() -> None:
    missing_sales = evaluate_inventory_rules(inventory_result(100, omit_sales=True), STAMP)
    missing_lead = evaluate_inventory_rules(
        inventory_result(100, lead=LeadTime(None, 3, 3, provenance("lead:missing"))),
        STAMP,
    )

    assert rule_codes(missing_sales) == (RULE_INSUFFICIENT_SALES,)
    assert rule_codes(missing_lead) == (RULE_MISSING_LEAD_TIME,)
    assert all(result.recommendations[0].category.value == "data_quality" for result in (missing_sales, missing_lead))


def test_zero_replenishment_quantity_never_emits_order_action() -> None:
    result = inventory_result(130)
    # A confirmed arrival before depletion can reduce the calculated quantity to zero.
    inbound = (
        InboundSupply(
            SKU,
            500,
            InboundStatus.CONFIRMED,
            AS_OF + timedelta(days=5),
            provenance("inbound:1"),
        ),
    )
    with_inbound = inventory_result(130, inbound=inbound)
    assert result.recommended_replenishment_quantity > 0
    assert with_inbound.recommended_replenishment_quantity == 0

    decision = evaluate_inventory_rules(with_inbound, STAMP)
    assert RULE_REPLENISHMENT_DUE_NOW not in rule_codes(decision)
    assert RULE_REPLENISHMENT_LATE not in rule_codes(decision)


def test_overstock_exceeds_but_does_not_equal_threshold() -> None:
    overstock = evaluate_inventory_rules(inventory_result(1000), STAMP)
    equality = evaluate_inventory_rules(inventory_result(900), STAMP)

    assert rule_codes(overstock) == (RULE_OVERSTOCK,)
    assert equality.recommendations == ()


def test_inventory_output_is_evidence_grounded_and_idempotent() -> None:
    factual_result = inventory_result(120)
    first = evaluate_inventory_rules(factual_result, STAMP)
    second = evaluate_inventory_rules(factual_result, STAMP)

    assert first == second
    assert first.analysis_timestamp == STAMP
    assert set(first.recommendations[0].evidence_refs) == {
        fact.fact_id for fact in first.facts
    }
    assert factual_result.recommended_replenishment_quantity == 310

"""Unit tests for calculation-free UI presentation transformations."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.bootstrap import build_application
from app.core.clock import FixedClock
from app.core.config import AppConfig
from app.core.errors import DataValidationError
from app.domain.common import AvailabilityStatus, Severity, SkuId, ValidationIssue
from app.ui.presenters import (
    ActionView,
    build_action_views,
    build_dashboard_view,
    build_economics_rows,
    build_inventory_rows,
    build_sku_detail_view,
    build_summary_view,
    EvidenceView,
    format_ads,
    format_coverage_days,
    format_evidence_value,
    format_fact_value,
    format_money,
    format_quantity,
    format_ratio,
)


STAMP = datetime(2026, 9, 7, 9, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def analysis_service():
    return build_application(
        AppConfig(ai_enabled=False),
        clock=FixedClock(STAMP),
        environ={},
    ).analysis


@pytest.fixture(scope="module")
def default_snapshot(analysis_service):
    return analysis_service.analyze(None)


def _rules(view) -> tuple[str, ...]:
    return tuple(action.rule_id for action in view.actions)


def _source_state(snapshot) -> tuple:
    return (
        snapshot.analysis_timestamp,
        snapshot.as_of_date,
        snapshot.configuration,
        snapshot.sku_results,
        snapshot.priority_actions,
        snapshot.issues,
        snapshot.provenance,
        tuple(
            (
                action.recommendation.recommendation_id,
                action.recommendation.rule_code,
                action.recommendation.evidence_refs,
                action.rank,
                action.severity,
                action.urgency,
            )
            for action in snapshot.priority_actions
        ),
        tuple(
            (
                fact.fact_id,
                fact.value,
                fact.formula_or_rule_id,
                fact.source_refs,
            )
            for result in snapshot.sku_results
            for evaluation in result.decision_evaluations
            for fact in evaluation.facts
        ),
    )


def test_summary_copies_timestamp_and_counts_authoritative_collections(
    default_snapshot,
) -> None:
    summary = build_summary_view(default_snapshot)

    assert summary.analyzed_sku_count == len(default_snapshot.sku_results) == 37
    assert summary.priority_action_count == len(default_snapshot.priority_actions)
    assert summary.critical_count == sum(
        action.severity is Severity.CRITICAL
        for action in default_snapshot.priority_actions
    )
    assert summary.high_count == sum(
        action.severity is Severity.HIGH
        for action in default_snapshot.priority_actions
    )
    assert summary.analysis_timestamp is default_snapshot.analysis_timestamp
    assert summary.as_of_date is default_snapshot.as_of_date


def test_empty_scope_has_neutral_zero_action_summary(analysis_service) -> None:
    view = build_dashboard_view(analysis_service.analyze(()))

    assert view.summary.analyzed_sku_count == 0
    assert view.summary.priority_action_count == 0
    assert view.summary.critical_count == 0
    assert view.summary.high_count == 0
    assert view.actions == ()
    assert view.issues == ()
    assert view.inventory_rows == ()
    assert view.economics_rows == ()
    assert view.sku_details == ()


def test_action_views_preserve_count_order_rank_and_authoritative_fields(
    default_snapshot,
) -> None:
    actions = build_action_views(default_snapshot)

    assert len(actions) == len(default_snapshot.priority_actions)
    assert tuple(action.recommendation_id for action in actions) == tuple(
        action.recommendation.recommendation_id
        for action in default_snapshot.priority_actions
    )
    assert tuple(action.rank for action in actions) == tuple(
        action.rank for action in default_snapshot.priority_actions
    )
    assert tuple(action.severity for action in actions) == tuple(
        action.severity for action in default_snapshot.priority_actions
    )
    assert tuple(action.urgency for action in actions) == tuple(
        action.urgency for action in default_snapshot.priority_actions
    )
    assert tuple(action.rule_id for action in actions) == tuple(
        action.recommendation.rule_code
        for action in default_snapshot.priority_actions
    )
    assert tuple(action.sku for action in actions) == tuple(
        str(action.recommendation.sku)
        for action in default_snapshot.priority_actions
    )


def test_actions_copy_only_their_existing_evidence(default_snapshot) -> None:
    actions = build_action_views(default_snapshot)
    facts_by_id = {
        fact.fact_id: fact
        for result in default_snapshot.sku_results
        for evaluation in result.decision_evaluations
        for fact in evaluation.facts
    }

    for source, view in zip(default_snapshot.priority_actions, actions, strict=True):
        assert view.evidence_refs == source.recommendation.evidence_refs
        assert tuple(fact.fact_id for fact in view.evidence) == view.evidence_refs
        assert tuple(fact.formula_or_rule_id for fact in view.evidence) == tuple(
            facts_by_id[fact_id].formula_or_rule_id
            for fact_id in source.recommendation.evidence_refs
        )


def test_explicit_same_sku_cross_rule_evidence_is_preserved_exactly(
    analysis_service,
) -> None:
    snapshot = analysis_service.analyze((SkuId("DEMO-006"),))
    source_action = next(
        action
        for action in snapshot.priority_actions
        if action.recommendation.rule_code == "pricing.current_price_unsafe"
    )
    facts = tuple(
        fact
        for result in snapshot.sku_results
        for evaluation in result.decision_evaluations
        for fact in evaluation.facts
    )
    same_rule_fact = next(
        fact
        for fact in facts
        if fact.formula_or_rule_id == source_action.recommendation.rule_code
    )
    cross_rule_fact = next(
        fact
        for fact in facts
        if fact.formula_or_rule_id
        == "profitability.below_minimum_profit"
    )
    unrelated_fact = next(
        fact
        for fact in facts
        if fact.fact_id not in {same_rule_fact.fact_id, cross_rule_fact.fact_id}
    )
    evidence_refs = (same_rule_fact.fact_id, cross_rule_fact.fact_id)
    changed_recommendation = replace(
        source_action.recommendation,
        evidence_refs=evidence_refs,
    )
    changed_action = replace(source_action, recommendation=changed_recommendation)
    changed_snapshot = replace(
        snapshot,
        priority_actions=tuple(
            changed_action if action is source_action else action
            for action in snapshot.priority_actions
        ),
    )

    action_view = next(
        action
        for action in build_action_views(changed_snapshot)
        if action.recommendation_id == changed_recommendation.recommendation_id
    )

    assert cross_rule_fact.sku == changed_recommendation.sku
    assert cross_rule_fact.formula_or_rule_id != changed_recommendation.rule_code
    assert action_view.rule_id == changed_recommendation.rule_code
    assert action_view.evidence_refs == evidence_refs
    assert tuple(fact.fact_id for fact in action_view.evidence) == evidence_refs
    assert tuple(fact.formula_or_rule_id for fact in action_view.evidence) == (
        same_rule_fact.formula_or_rule_id,
        cross_rule_fact.formula_or_rule_id,
    )
    assert unrelated_fact.fact_id not in {
        fact.fact_id for fact in action_view.evidence
    }


def test_product_name_is_optional_without_changing_action(default_snapshot) -> None:
    source_result = next(
        result
        for result in default_snapshot.sku_results
        if any(
            action.recommendation.sku == result.sku
            for action in default_snapshot.priority_actions
        )
    )
    changed_result = replace(
        source_result,
        product=None,
        status=AvailabilityStatus.INSUFFICIENT_DATA,
    )
    changed_snapshot = replace(
        default_snapshot,
        sku_results=tuple(
            changed_result if result.sku == source_result.sku else result
            for result in default_snapshot.sku_results
        ),
    )

    source_views = tuple(
        action
        for action in build_action_views(default_snapshot)
        if action.sku == str(source_result.sku)
    )
    changed_views = tuple(
        action
        for action in build_action_views(changed_snapshot)
        if action.sku == str(source_result.sku)
    )

    assert source_views
    assert all(action.product_name is not None for action in source_views)
    assert all(action.product_name is None for action in changed_views)
    assert tuple(replace(action, product_name=None) for action in source_views) == changed_views


def test_demo_006_preserves_five_action_authoritative_order(analysis_service) -> None:
    view = build_dashboard_view(analysis_service.analyze((SkuId("DEMO-006"),)))

    assert _rules(view) == (
        "pricing.current_price_unsafe",
        "profitability.below_minimum_profit",
        "profitability.below_minimum_margin",
        "sales.material_decline",
        "inventory.overstock_candidate",
    )
    assert tuple(action.rank for action in view.actions) == (1, 2, 3, 4, 5)


def test_demo_015_preserves_four_distinct_actions(analysis_service) -> None:
    view = build_dashboard_view(analysis_service.analyze((SkuId("DEMO-015"),)))

    assert _rules(view) == (
        "profitability.loss_making",
        "pricing.current_price_unsafe",
        "profitability.below_minimum_profit",
        "profitability.below_minimum_margin",
    )
    assert len({action.recommendation_id for action in view.actions}) == 4


def test_demo_037_presentation_does_not_invent_missing_business_values(
    analysis_service,
) -> None:
    snapshot = analysis_service.analyze((SkuId("DEMO-037"),))
    view = build_dashboard_view(snapshot)

    assert _rules(view) == (
        "inventory.insufficient_sales_history",
        "sales.insufficient_history",
    )
    assert view.summary.analyzed_sku_count == 1
    assert view.summary.priority_action_count == 2
    assert set(field.name for field in fields(ActionView)) == {
        "rank",
        "sku",
        "product_name",
        "severity",
        "urgency",
        "availability",
        "category",
        "recommendation_status",
        "rule_id",
        "recommendation_id",
        "proposed_action",
        "explanation",
        "evidence_refs",
        "evidence",
    }
    assert all(
        tuple(fact.fact_id for fact in action.evidence) == action.evidence_refs
        for action in view.actions
    )


def test_fact_formatting_preserves_exact_values_without_rounding() -> None:
    assert format_fact_value(Decimal("100.0000000000000000001")) == (
        "100.0000000000000000001"
    )
    assert format_fact_value(0) == "0"
    assert format_fact_value(False) == "false"
    assert format_fact_value(STAMP) == STAMP.isoformat()


def test_numeric_display_formatters_apply_semantic_precision_without_float() -> None:
    ads = Decimal("18.14285714285714285714285714")
    coverage = Decimal("2.916666666666666666666666667")
    money = Decimal("232.2000")
    margin = Decimal("0.2387596899224806201550387597")
    source_tuples = tuple(
        value.as_tuple() for value in (ads, coverage, money, margin)
    )

    assert format_ads(ads) == "18.1"
    assert format_ads(Decimal("8")) == "8"
    assert format_coverage_days(coverage) == "2.9"
    assert format_coverage_days(Decimal("62.5")) == "62.5"
    assert format_money(money) == "232.2"
    assert format_money(Decimal("154.8000")) == "154.8"
    assert format_money(Decimal("308.0000")) == "308"
    assert format_money(Decimal("19.27")) == "19.27"
    assert format_ratio(margin, decimal_places=3) == "0.239"
    assert format_ratio(Decimal("0.12")) == "0.12"
    assert format_quantity(35) == "35"
    assert format_quantity(Decimal("481.0")) == "481"
    assert tuple(value.as_tuple() for value in (ads, coverage, money, margin)) == (
        source_tuples
    )


def test_evidence_display_formatting_keeps_exact_fact_identity_and_source_value() -> None:
    fact = EvidenceView(
        fact_id="fact:margin",
        name="contribution margin",
        value="0.2387596899224806201550387597",
        unit="fraction",
        period="2026-09-01 — 2026-09-07",
        formula_or_rule_id="profitability.below_minimum_margin",
        source_refs=("economics:DEMO-001",),
    )

    assert format_evidence_value(fact) == "0.239"
    assert fact.fact_id == "fact:margin"
    assert fact.value == "0.2387596899224806201550387597"
    assert fact.source_refs == ("economics:DEMO-001",)


def test_presentation_transform_is_repeatable_and_immutable(default_snapshot) -> None:
    state_before = _source_state(default_snapshot)
    sku_results_before = default_snapshot.sku_results
    actions_before = default_snapshot.priority_actions
    issues_before = default_snapshot.issues
    first = build_dashboard_view(default_snapshot)
    second = build_dashboard_view(default_snapshot)

    assert first == second
    assert _source_state(default_snapshot) == state_before
    assert default_snapshot.sku_results is sku_results_before
    assert default_snapshot.priority_actions is actions_before
    assert default_snapshot.issues is issues_before
    with pytest.raises(FrozenInstanceError):
        first.actions = ()


def test_service_issue_stays_separate_from_priority_actions_and_summary(
    default_snapshot,
) -> None:
    source_action_order = tuple(
        action.recommendation.recommendation_id
        for action in default_snapshot.priority_actions
    )
    source_summary = build_summary_view(default_snapshot)
    issue = ValidationIssue(
        code="provider.demo_field_unavailable",
        message="A known demo source field is unavailable.",
        severity=Severity.WARNING,
        scope="demo source",
        sku=SkuId("DEMO-037"),
        field_name="sales_history",
    )
    snapshot_with_issue = replace(default_snapshot, issues=(issue,))

    view = build_dashboard_view(snapshot_with_issue)

    assert len(view.issues) == 1
    assert view.issues[0].code == issue.code
    assert view.issues[0].message == issue.message
    assert view.issues[0].severity is issue.severity
    assert view.issues[0].scope == issue.scope
    assert view.issues[0].sku == str(issue.sku)
    assert view.issues[0].field_name == issue.field_name
    assert not hasattr(view.issues[0], "rank")
    assert view.summary.priority_action_count == source_summary.priority_action_count
    assert view.summary.critical_count == source_summary.critical_count
    assert view.summary.high_count == source_summary.high_count
    assert tuple(action.recommendation_id for action in view.actions) == (
        source_action_order
    )


def test_inventory_rows_preserve_scope_order_and_authoritative_values(
    default_snapshot,
) -> None:
    rows = build_inventory_rows(default_snapshot)

    assert tuple(row.sku for row in rows) == tuple(
        str(result.sku) for result in default_snapshot.sku_results
    )
    source = next(
        result.inventory
        for result in default_snapshot.sku_results
        if str(result.sku) == "DEMO-005"
    )
    row = next(row for row in rows if row.sku == "DEMO-005")
    assert source is not None
    assert row.sellable_stock == source.sellable_stock
    assert row.average_daily_sales == source.average_daily_sales
    assert row.stock_coverage_days == source.stock_coverage_days
    assert row.stockout_date == source.stockout_date
    assert row.production_days == source.lead_time.production_days
    assert row.delivery_days == source.lead_time.delivery_days
    assert row.safety_buffer_days == source.lead_time.safety_buffer_days
    assert row.supply_lead_days == source.supply_lead_days
    assert row.latest_safe_start_date == source.latest_safe_start_date
    assert row.recommended_replenishment_quantity == (
        source.recommended_replenishment_quantity
    )
    assert row.eligible_confirmed_inbound_units == (
        source.eligible_confirmed_inbound_units
    )
    assert row.action_rules == ("inventory.out_of_stock",)
    assert row.action_reasons


def test_inventory_rows_preserve_inbound_and_missing_state_distinctions(
    default_snapshot,
) -> None:
    rows = build_inventory_rows(default_snapshot)
    confirmed = next(row for row in rows if row.sku == "DEMO-004")
    unconfirmed = next(row for row in rows if row.sku == "DEMO-006")
    missing_lead = next(row for row in rows if row.sku == "DEMO-012")
    insufficient_sales = next(row for row in rows if row.sku == "DEMO-037")

    assert tuple(event.source_status.value for event in confirmed.inbound_events) == (
        "confirmed",
    )
    assert tuple(event.source_status.value for event in unconfirmed.inbound_events) == (
        "unconfirmed",
    )
    assert confirmed.eligible_confirmed_inbound_units == 100
    assert unconfirmed.eligible_confirmed_inbound_units == 0
    assert missing_lead.inventory_status is AvailabilityStatus.INSUFFICIENT_DATA
    assert missing_lead.production_days is None
    assert missing_lead.delivery_days is None
    assert missing_lead.safety_buffer_days is None
    assert missing_lead.latest_safe_start_date is None
    assert missing_lead.replenishment_timing is None
    assert missing_lead.recommended_replenishment_quantity is None
    assert insufficient_sales.average_daily_sales is None
    assert insufficient_sales.stockout_date is None
    assert insufficient_sales.recommended_replenishment_quantity is None


def test_economics_rows_copy_exact_decimals_boundaries_and_recommendations(
    default_snapshot,
) -> None:
    rows = build_economics_rows(default_snapshot)

    assert tuple(row.sku for row in rows) == tuple(
        str(result.sku) for result in default_snapshot.sku_results
    )
    source = next(
        result.economics
        for result in default_snapshot.sku_results
        if str(result.sku) == "DEMO-015"
    )
    row = next(row for row in rows if row.sku == "DEMO-015")
    assert source is not None
    assert row.selling_price == source.selling_price
    assert row.cost_of_goods == source.cost_of_goods
    assert row.logistics_cost == source.logistics_cost
    assert row.commission_cost == source.commission_cost
    assert row.advertising_cost == source.advertising_cost
    assert row.drr == source.drr
    assert row.total_variable_cost == source.total_variable_cost
    assert row.profit_per_unit == source.profit_per_unit
    assert row.contribution_margin == source.contribution_margin
    assert row.break_even is not None
    assert row.break_even.price == source.break_even.price
    assert row.minimum_safe is not None
    assert row.minimum_safe.price == source.minimum_safe.price
    assert row.action_rules == (
        "profitability.loss_making",
        "pricing.current_price_unsafe",
        "profitability.below_minimum_profit",
        "profitability.below_minimum_margin",
    )
    assert all(isinstance(value, Decimal) for value in (
        row.selling_price,
        row.cost_of_goods,
        row.profit_per_unit,
        row.contribution_margin,
        row.minimum_safe.price,
    ))


def test_economics_rows_preserve_no_finite_price_and_missing_cogs(
    default_snapshot,
) -> None:
    rows = build_economics_rows(default_snapshot)
    impossible = next(row for row in rows if row.sku == "DEMO-018")
    missing = next(row for row in rows if row.sku == "DEMO-021")

    assert impossible.minimum_safe is not None
    assert impossible.minimum_safe.status is AvailabilityStatus.NOT_APPLICABLE
    assert impossible.minimum_safe.price is None
    assert missing.economics_status is AvailabilityStatus.INSUFFICIENT_DATA
    assert missing.cost_of_goods is None
    assert missing.profit_per_unit is None
    assert missing.contribution_margin is None
    assert missing.minimum_safe is not None
    assert missing.minimum_safe.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert missing.minimum_safe.price is None


def test_sku_detail_copies_sections_actions_evidence_and_provenance(
    default_snapshot,
) -> None:
    detail = build_sku_detail_view(default_snapshot, SkuId("DEMO-006"))
    source = next(
        result for result in default_snapshot.sku_results if result.sku == SkuId("DEMO-006")
    )
    expected_actions = tuple(
        action
        for action in default_snapshot.priority_actions
        if action.recommendation.sku == source.sku
    )

    assert detail.sku == str(source.sku)
    assert detail.product_name == source.product.name
    assert detail.marketplace_id == source.product.marketplace_id
    assert detail.active is source.product.active
    assert detail.analysis_status is source.status
    assert detail.sales is not None
    assert detail.sales.current_total_units == source.sales.current.total_units
    assert detail.sales.current_average_daily_sales == (
        source.sales.current.average_daily_sales
    )
    assert detail.inventory.sellable_stock == source.inventory.sellable_stock
    assert detail.economics.profit_per_unit == source.economics.profit_per_unit
    assert tuple(action.recommendation_id for action in detail.actions) == tuple(
        action.recommendation.recommendation_id for action in expected_actions
    )
    assert tuple(action.rank for action in detail.actions) == tuple(
        action.rank for action in expected_actions
    )
    assert all(
        tuple(fact.fact_id for fact in action.evidence) == action.evidence_refs
        for action in detail.actions
    )
    assert tuple(
        (
            item.source_type,
            item.provider,
            item.ingested_at,
            item.source_timestamp,
            item.source_record_id,
        )
        for item in detail.provenance
    ) == tuple(
        (
            item.source_type,
            item.provider,
            item.ingested_at,
            item.source_timestamp,
            item.source_record_id,
        )
        for item in source.provenance
    )


def test_sku_detail_rejects_unknown_without_selecting_a_fallback(
    default_snapshot,
) -> None:
    with pytest.raises(DataValidationError) as error:
        build_sku_detail_view(default_snapshot, SkuId("UNKNOWN-SKU"))

    assert error.value.code == "ui.unknown_sku"


def test_sku_detail_preserves_partial_and_zero_action_states(analysis_service) -> None:
    missing_lead = build_sku_detail_view(
        analysis_service.analyze((SkuId("DEMO-012"),)),
        SkuId("DEMO-012"),
    )
    insufficient_sales = build_sku_detail_view(
        analysis_service.analyze((SkuId("DEMO-037"),)),
        SkuId("DEMO-037"),
    )
    healthy = build_sku_detail_view(
        analysis_service.analyze((SkuId("DEMO-001"),)),
        SkuId("DEMO-001"),
    )

    assert missing_lead.inventory.latest_safe_start_date is None
    assert missing_lead.inventory.recommended_replenishment_quantity is None
    assert insufficient_sales.sales is not None
    assert insufficient_sales.sales.current_average_daily_sales is None
    assert insufficient_sales.inventory.stockout_date is None
    assert insufficient_sales.inventory.recommended_replenishment_quantity is None
    assert healthy.actions == ()


def test_sku_detail_keeps_global_and_matching_issues_outside_actions(
    default_snapshot,
) -> None:
    global_issue = ValidationIssue(
        code="provider.global_notice",
        message="Global demo source notice.",
        severity=Severity.WARNING,
        scope="demo source",
    )
    matching_issue = ValidationIssue(
        code="provider.sku_notice",
        message="Selected SKU source notice.",
        severity=Severity.WARNING,
        scope="demo source",
        sku=SkuId("DEMO-006"),
        field_name="inventory",
    )
    other_issue = ValidationIssue(
        code="provider.other_notice",
        message="Another SKU source notice.",
        severity=Severity.WARNING,
        scope="demo source",
        sku=SkuId("DEMO-007"),
    )
    changed = replace(
        default_snapshot,
        issues=(global_issue, matching_issue, other_issue),
    )

    detail = build_sku_detail_view(changed, SkuId("DEMO-006"))

    assert tuple(issue.code for issue in detail.issues) == (
        global_issue.code,
        matching_issue.code,
    )
    assert all(not hasattr(issue, "rank") for issue in detail.issues)
    assert tuple(action.recommendation_id for action in detail.actions) == tuple(
        action.recommendation.recommendation_id
        for action in changed.priority_actions
        if action.recommendation.sku == SkuId("DEMO-006")
    )


def test_detailed_dashboard_is_repeatable_without_mutating_source(
    default_snapshot,
) -> None:
    before = _source_state(default_snapshot)
    first = build_dashboard_view(default_snapshot)
    second = build_dashboard_view(default_snapshot)

    assert first == second
    assert _source_state(default_snapshot) == before
    assert tuple(row.sku for row in first.inventory_rows) == tuple(
        detail.sku for detail in first.sku_details
    )
    assert tuple(row.sku for row in first.economics_rows) == tuple(
        detail.sku for detail in first.sku_details
    )


def test_pure_presenter_has_no_forbidden_layer_import_or_calls() -> None:
    source = Path("app/ui/presenters.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(
        name.startswith(
            (
                "streamlit",
                "app.providers",
                "app.analytics",
                "app.decisions",
                "app.ai",
            )
        )
        for name in imports
    )
    forbidden_calls = {
        "calculate_sales_metrics",
        "calculate_inventory_analysis",
        "calculate_unit_economics",
        "evaluate_sales_rules",
        "evaluate_inventory_rules",
        "evaluate_economics_rules",
        "build_priority_action_center",
    }
    assert forbidden_calls.isdisjoint(
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    )
    assert "float(" not in source
    business_arithmetic = (
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
    )
    assert not any(
        isinstance(node, ast.BinOp) and isinstance(node.op, business_arithmetic)
        for node in ast.walk(tree)
    )

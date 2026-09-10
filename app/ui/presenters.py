"""Immutable Streamlit-ready views copied from authoritative service results.

This module performs presentation-only formatting and direct collection counts.
It does not read providers, run analytics, evaluate decisions, or reprioritize
actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from app.core.errors import DataValidationError
from app.core.money import quantize_currency, to_decimal
from app.domain.briefs import BriefGenerationResult, BriefGenerationStatus
from app.domain.common import (
    AvailabilityStatus,
    Currency,
    Provenance,
    SafetyStatus,
    Severity,
    SkuId,
    SourceType,
    Urgency,
    ValidationIssue,
)
from app.domain.economics import PriceBoundary, UnitEconomicsResult
from app.domain.recommendations import (
    CalculatedFact,
    RecommendationCategory,
    RecommendationStatus,
)
from app.services.models import AnalysisSnapshot, PricingAnalysis, SkuAnalysis


@dataclass(frozen=True, slots=True)
class SummaryView:
    """Direct counts and timestamps for the executive summary."""

    analyzed_sku_count: int
    priority_action_count: int
    critical_count: int
    high_count: int
    analysis_timestamp: datetime
    as_of_date: date


@dataclass(frozen=True, slots=True)
class EvidenceView:
    """Presentation copy of one already-calculated evidence fact."""

    fact_id: str
    name: str
    value: str
    unit: str
    period: str | None
    formula_or_rule_id: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActionView:
    """Presentation copy of one authoritative PriorityAction."""

    rank: int | None
    sku: str | None
    product_name: str | None
    severity: Severity
    urgency: Urgency
    availability: AvailabilityStatus
    category: RecommendationCategory
    recommendation_status: RecommendationStatus
    rule_id: str
    recommendation_id: str
    proposed_action: str | None
    explanation: str
    evidence_refs: tuple[str, ...]
    evidence: tuple[EvidenceView, ...]


@dataclass(frozen=True, slots=True)
class IssueView:
    """Safe, display-ready copy of one service validation issue."""

    code: str
    message: str
    severity: Severity
    scope: str
    sku: str | None
    field_name: str | None


@dataclass(frozen=True, slots=True)
class InboundEventView:
    """Exact presentation copy of one authoritative inbound treatment event."""

    quantity: int
    source_status: Enum
    expected_arrival: date | None
    treatment: Enum
    source_ref: str | None


@dataclass(frozen=True, slots=True)
class InventoryRow:
    """Inventory and replenishment fields copied from one SKU analysis."""

    sku: str
    product_name: str | None
    analysis_status: AvailabilityStatus
    inventory_status: AvailabilityStatus | None
    inventory_observed_at: datetime | None
    sellable_stock: int | None
    average_daily_sales: Decimal | None
    depletion_status: AvailabilityStatus | None
    stock_coverage_days: Decimal | None
    stockout_date: date | None
    lead_time_status: AvailabilityStatus | None
    production_days: int | None
    delivery_days: int | None
    safety_buffer_days: int | None
    supply_lead_days: int | None
    required_coverage_horizon_days: int | None
    timing_status: AvailabilityStatus | None
    latest_safe_start_date: date | None
    replenishment_timing: Enum | None
    replenishment_status: AvailabilityStatus | None
    planned_replenishment_arrival_date: date | None
    projected_stock_at_replenishment_arrival: Decimal | None
    reorder_point_units: int | None
    target_stock_units: int | None
    raw_required_quantity: Decimal | None
    base_replenishment_quantity: int | None
    recommended_replenishment_quantity: int | None
    eligible_confirmed_inbound_units: int | None
    minimum_order_quantity: int | None
    pack_size: int | None
    inbound_events: tuple[InboundEventView, ...]
    action_rules: tuple[str, ...]
    action_reasons: tuple[str, ...]
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CostComponentView:
    """Exact named per-unit cost copied from normalized economics input."""

    name: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class PriceBoundaryView:
    """Presentation copy retaining both price and its availability state."""

    status: AvailabilityStatus
    price: Decimal | None


@dataclass(frozen=True, slots=True)
class EconomicsRow:
    """Current unit-economics fields copied without recomputation."""

    sku: str
    product_name: str | None
    analysis_status: AvailabilityStatus
    economics_status: AvailabilityStatus | None
    safety_status: SafetyStatus | None
    currency: Currency | None
    selling_price: Decimal | None
    cost_of_goods: Decimal | None
    logistics_cost: Decimal | None
    commission_rate: Decimal | None
    commission_per_unit_input: Decimal | None
    commission_cost: Decimal | None
    advertising_cost_per_unit_input: Decimal | None
    advertising_cost: Decimal | None
    drr_status: AvailabilityStatus | None
    drr: Decimal | None
    other_variable_costs: tuple[CostComponentView, ...]
    other_variable_cost_total: Decimal | None
    total_variable_cost: Decimal | None
    profit_per_unit: Decimal | None
    contribution_margin: Decimal | None
    proportional_cost_rate: Decimal | None
    fixed_cost_total: Decimal | None
    break_even: PriceBoundaryView | None
    minimum_profit: PriceBoundaryView | None
    minimum_margin: PriceBoundaryView | None
    minimum_safe: PriceBoundaryView | None
    evidence_refs: tuple[str, ...]
    action_rules: tuple[str, ...]
    action_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SalesDetailView:
    """Already-calculated sales-window context for one SKU."""

    status: AvailabilityStatus
    change_status: AvailabilityStatus
    current_period_start: date
    current_period_end: date
    current_expected_days: int
    current_observed_days: int
    current_total_units: int
    current_average_daily_sales: Decimal | None
    current_missing_dates: tuple[date, ...]
    current_source_refs: tuple[str, ...]
    comparison_period_start: date
    comparison_period_end: date
    comparison_expected_days: int
    comparison_observed_days: int
    comparison_total_units: int
    comparison_average_daily_sales: Decimal | None
    comparison_missing_dates: tuple[date, ...]
    comparison_source_refs: tuple[str, ...]
    relative_change: Decimal | None
    direction: Enum | None


@dataclass(frozen=True, slots=True)
class ProvenanceView:
    """Safe source identity copied without provider payloads or file paths."""

    source_type: SourceType
    provider: str
    ingested_at: datetime
    source_timestamp: datetime | None
    source_record_id: str | None


@dataclass(frozen=True, slots=True)
class SkuDetailView:
    """Complete presentation-only detail for one selected snapshot SKU."""

    sku: str
    product_name: str | None
    marketplace_id: str | None
    active: bool | None
    analysis_status: AvailabilityStatus
    sales: SalesDetailView | None
    inventory: InventoryRow
    economics: EconomicsRow
    actions: tuple[ActionView, ...]
    issues: tuple[IssueView, ...]
    provenance: tuple[ProvenanceView, ...]

    @property
    def selector_label(self) -> str:
        return self.sku if self.product_name is None else f"{self.sku} — {self.product_name}"


@dataclass(frozen=True, slots=True)
class DashboardView:
    """Complete MVP-017 view prepared from one AnalysisSnapshot."""

    summary: SummaryView
    actions: tuple[ActionView, ...]
    issues: tuple[IssueView, ...]
    inventory_rows: tuple[InventoryRow, ...] = ()
    economics_rows: tuple[EconomicsRow, ...] = ()
    sku_details: tuple[SkuDetailView, ...] = ()


@dataclass(frozen=True, slots=True)
class PricingEconomicsView:
    """Exact presentation copy of one authoritative economics result."""

    status: AvailabilityStatus
    safety_status: SafetyStatus
    currency: Currency
    selling_price: Decimal
    commission_cost: Decimal | None
    logistics_cost: Decimal | None
    advertising_cost: Decimal | None
    other_variable_cost_total: Decimal
    total_variable_cost: Decimal | None
    profit_per_unit: Decimal | None
    contribution_margin: Decimal | None
    break_even: PriceBoundaryView
    minimum_profit: PriceBoundaryView
    minimum_margin: PriceBoundaryView
    minimum_safe: PriceBoundaryView
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PricingScenarioView:
    """One hypothetical comparison copied without recomputing any delta."""

    scenario_id: str
    sku: str
    analysis_timestamp: datetime
    status: AvailabilityStatus
    safety_status: SafetyStatus
    current: PricingEconomicsView
    scenario: PricingEconomicsView
    price_delta: Decimal
    profit_per_unit_delta: Decimal | None
    margin_delta: Decimal | None
    distance_from_safe_price: Decimal | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PricingAnalysisView:
    """Presentation-only aggregate retaining service order and availability."""

    sku: str
    analysis_timestamp: datetime
    status: AvailabilityStatus
    scenarios: tuple[PricingScenarioView, ...]
    issues: tuple[IssueView, ...]


@dataclass(frozen=True, slots=True)
class DailyBriefItemView:
    """Exact validated prose for one authoritative action reference."""

    action_ref: str
    text: str
    fact_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DailyBriefView:
    """Exact validated DailyBrief content safe for presentation."""

    analysis_timestamp: datetime
    grounding_digest: str
    summary_text: str
    summary_action_refs: tuple[str, ...]
    summary_fact_refs: tuple[str, ...]
    items: tuple[DailyBriefItemView, ...]


@dataclass(frozen=True, slots=True)
class BriefStatusView:
    """Safe AI outcome; only GENERATED can carry validated prose."""

    status: BriefGenerationStatus
    brief: DailyBriefView | None


def format_fact_value(value: object) -> str:
    """Format one authoritative fact without rounding or numeric conversion."""

    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _round_decimal_for_display(value: Decimal | int, decimal_places: int) -> Decimal:
    """Use the core half-up Decimal rule at one approved display scale."""

    quanta = {
        1: Decimal("0.1"),
        2: Decimal("0.01"),
        3: Decimal("0.001"),
    }
    try:
        quantum = quanta[decimal_places]
    except KeyError as exc:
        raise ValueError("unsupported display precision") from exc
    return quantize_currency(value, quantum=quantum)


def _trim_decimal(value: Decimal) -> str:
    """Return fixed-point text without redundant fractional zeros."""

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def format_ads(value: Decimal | int) -> str:
    """Format an already-calculated units-per-day rate to one decimal place."""

    return _trim_decimal(_round_decimal_for_display(value, 1))


def format_coverage_days(value: Decimal | int) -> str:
    """Format already-calculated fractional days to one decimal place."""

    return _trim_decimal(_round_decimal_for_display(value, 1))


def format_money(value: Decimal | int) -> str:
    """Format an already-calculated RUB amount to at most two decimal places."""

    return _trim_decimal(_round_decimal_for_display(value, 2))


def format_quantity(value: Decimal | int) -> str:
    """Format a quantity exactly while removing only redundant decimal zeros."""

    return _trim_decimal(to_decimal(value, field_name="display quantity"))


def format_ratio(value: Decimal | int, *, decimal_places: int = 2) -> str:
    """Format an already-calculated canonical fraction without percent conversion."""

    return _trim_decimal(_round_decimal_for_display(value, decimal_places))


def format_evidence_value(evidence: EvidenceView) -> str:
    """Apply the display policy to a fact while preserving its exact stored value."""

    if evidence.unit == "units_per_day":
        return format_ads(to_decimal(evidence.value, field_name="evidence value"))
    if evidence.unit == "days":
        return format_coverage_days(
            to_decimal(evidence.value, field_name="evidence value")
        )
    if evidence.unit == "units":
        return format_quantity(to_decimal(evidence.value, field_name="evidence value"))
    if evidence.unit == Currency.RUB.value:
        return format_money(to_decimal(evidence.value, field_name="evidence value"))
    if evidence.unit == "fraction":
        decimal_places = 3 if "margin" in evidence.name.casefold() else 2
        return format_ratio(
            to_decimal(evidence.value, field_name="evidence value"),
            decimal_places=decimal_places,
        )
    return evidence.value


def parse_candidate_price(raw_value: str) -> Decimal:
    """Parse exact user text without admitting binary floating-point values."""

    if not isinstance(raw_value, str):
        raise DataValidationError(
            "candidate price must be entered as decimal text",
            code="ui.invalid_candidate_price_type",
            scope="candidate price",
        )
    return to_decimal(raw_value, field_name="candidate price")


def _evidence_view(fact: CalculatedFact) -> EvidenceView:
    period = (
        None
        if fact.period is None
        else f"{fact.period.start.isoformat()} — {fact.period.end.isoformat()}"
    )


    return EvidenceView(
        fact_id=fact.fact_id,
        name=fact.name,
        value=format_fact_value(fact.value),
        unit=fact.unit,
        period=period,
        formula_or_rule_id=fact.formula_or_rule_id,
        source_refs=fact.source_refs,
    )


def _issue_view(issue: ValidationIssue) -> IssueView:
    return IssueView(
        code=issue.code,
        message=issue.message,
        severity=issue.severity,
        scope=issue.scope,
        sku=None if issue.sku is None else str(issue.sku),
        field_name=issue.field_name,
    )


def _provenance_view(provenance: Provenance) -> ProvenanceView:
    return ProvenanceView(
        source_type=provenance.source_type,
        provider=provenance.provider,
        ingested_at=provenance.ingested_at,
        source_timestamp=provenance.source_timestamp,
        source_record_id=provenance.source_record_id,
    )


def build_summary_view(snapshot: AnalysisSnapshot) -> SummaryView:
    """Count existing snapshot records and severities without classifying them."""

    return SummaryView(
        analyzed_sku_count=len(snapshot.sku_results),
        priority_action_count=len(snapshot.priority_actions),
        critical_count=sum(
            action.severity is Severity.CRITICAL
            for action in snapshot.priority_actions
        ),
        high_count=sum(
            action.severity is Severity.HIGH for action in snapshot.priority_actions
        ),
        analysis_timestamp=snapshot.analysis_timestamp,
        as_of_date=snapshot.as_of_date,
    )


def build_action_views(snapshot: AnalysisSnapshot) -> tuple[ActionView, ...]:
    """Copy actions in their authoritative order and attach existing evidence."""

    product_names = {
        result.sku: None if result.product is None else result.product.name
        for result in snapshot.sku_results
    }
    facts_by_id = {
        fact.fact_id: fact
        for result in snapshot.sku_results
        for evaluation in result.decision_evaluations
        for fact in evaluation.facts
    }

    views: list[ActionView] = []
    for action in snapshot.priority_actions:
        recommendation = action.recommendation
        evidence = tuple(
            _evidence_view(facts_by_id[fact_id])
            for fact_id in recommendation.evidence_refs
        )
        views.append(
            ActionView(
                rank=action.rank,
                sku=None if recommendation.sku is None else str(recommendation.sku),
                product_name=(
                    None
                    if recommendation.sku is None
                    else product_names.get(recommendation.sku)
                ),
                severity=action.severity,
                urgency=action.urgency,
                availability=action.status,
                category=recommendation.category,
                recommendation_status=recommendation.status,
                rule_id=recommendation.rule_code,
                recommendation_id=recommendation.recommendation_id,
                proposed_action=recommendation.proposed_action,
                explanation=recommendation.explanation,
                evidence_refs=recommendation.evidence_refs,
                evidence=evidence,
            )
        )
    return tuple(views)


def build_issue_views(snapshot: AnalysisSnapshot) -> tuple[IssueView, ...]:
    """Preserve service issue order without converting issues into actions."""

    return tuple(_issue_view(issue) for issue in snapshot.issues)


def _actions_for_sku(
    actions: tuple[ActionView, ...],
    sku: str,
) -> tuple[ActionView, ...]:
    return tuple(action for action in actions if action.sku == sku)


def _actions_with_prefixes(
    actions: tuple[ActionView, ...],
    prefixes: tuple[str, ...],
) -> tuple[ActionView, ...]:
    return tuple(action for action in actions if action.rule_id.startswith(prefixes))


def _inventory_row(result: SkuAnalysis, actions: tuple[ActionView, ...]) -> InventoryRow:
    sku = str(result.sku)
    product_name = None if result.product is None else result.product.name
    inventory = result.inventory
    inventory_actions = _actions_with_prefixes(
        _actions_for_sku(actions, sku),
        ("inventory.",),
    )
    if inventory is None:
        return InventoryRow(
            sku=sku,
            product_name=product_name,
            analysis_status=result.status,
            inventory_status=None,
            inventory_observed_at=None,
            sellable_stock=None,
            average_daily_sales=None,
            depletion_status=None,
            stock_coverage_days=None,
            stockout_date=None,
            lead_time_status=None,
            production_days=None,
            delivery_days=None,
            safety_buffer_days=None,
            supply_lead_days=None,
            required_coverage_horizon_days=None,
            timing_status=None,
            latest_safe_start_date=None,
            replenishment_timing=None,
            replenishment_status=None,
            planned_replenishment_arrival_date=None,
            projected_stock_at_replenishment_arrival=None,
            reorder_point_units=None,
            target_stock_units=None,
            raw_required_quantity=None,
            base_replenishment_quantity=None,
            recommended_replenishment_quantity=None,
            eligible_confirmed_inbound_units=None,
            minimum_order_quantity=None,
            pack_size=None,
            inbound_events=(),
            action_rules=tuple(action.rule_id for action in inventory_actions),
            action_reasons=tuple(action.explanation for action in inventory_actions),
            source_refs=(),
        )

    return InventoryRow(
        sku=sku,
        product_name=product_name,
        analysis_status=result.status,
        inventory_status=inventory.status,
        inventory_observed_at=inventory.inventory_observed_at,
        sellable_stock=inventory.sellable_stock,
        average_daily_sales=inventory.average_daily_sales,
        depletion_status=inventory.depletion_status,
        stock_coverage_days=inventory.stock_coverage_days,
        stockout_date=inventory.stockout_date,
        lead_time_status=inventory.lead_time_status,
        production_days=inventory.lead_time.production_days,
        delivery_days=inventory.lead_time.delivery_days,
        safety_buffer_days=inventory.lead_time.safety_buffer_days,
        supply_lead_days=inventory.supply_lead_days,
        required_coverage_horizon_days=inventory.required_coverage_horizon_days,
        timing_status=inventory.timing_status,
        latest_safe_start_date=inventory.latest_safe_start_date,
        replenishment_timing=inventory.replenishment_timing,
        replenishment_status=inventory.replenishment_status,
        planned_replenishment_arrival_date=(
            inventory.planned_replenishment_arrival_date
        ),
        projected_stock_at_replenishment_arrival=(
            inventory.projected_stock_at_replenishment_arrival
        ),
        reorder_point_units=inventory.reorder_point_units,
        target_stock_units=inventory.target_stock_units,
        raw_required_quantity=inventory.raw_required_quantity,
        base_replenishment_quantity=inventory.base_replenishment_quantity,
        recommended_replenishment_quantity=(
            inventory.recommended_replenishment_quantity
        ),
        eligible_confirmed_inbound_units=(
            inventory.eligible_confirmed_inbound_units
        ),
        minimum_order_quantity=inventory.constraints.minimum_order_quantity,
        pack_size=inventory.constraints.pack_size,
        inbound_events=tuple(
            InboundEventView(
                quantity=event.quantity,
                source_status=event.source_status,
                expected_arrival=event.expected_arrival,
                treatment=event.treatment,
                source_ref=event.source_ref,
            )
            for event in inventory.inbound_events
        ),
        action_rules=tuple(action.rule_id for action in inventory_actions),
        action_reasons=tuple(action.explanation for action in inventory_actions),
        source_refs=inventory.source_refs,
    )


def _boundary_view(boundary: PriceBoundary) -> PriceBoundaryView:
    return PriceBoundaryView(status=boundary.status, price=boundary.price)


def _pricing_economics_view(result: UnitEconomicsResult) -> PricingEconomicsView:
    return PricingEconomicsView(
        status=result.status,
        safety_status=result.safety_status,
        currency=result.currency,
        selling_price=result.selling_price,
        commission_cost=result.commission_cost,
        logistics_cost=result.logistics_cost,
        advertising_cost=result.advertising_cost,
        other_variable_cost_total=result.other_variable_cost_total,
        total_variable_cost=result.total_variable_cost,
        profit_per_unit=result.profit_per_unit,
        contribution_margin=result.contribution_margin,
        break_even=_boundary_view(result.break_even),
        minimum_profit=_boundary_view(result.minimum_profit),
        minimum_margin=_boundary_view(result.minimum_margin),
        minimum_safe=_boundary_view(result.minimum_safe),
        evidence_refs=result.evidence_refs,
    )


def build_pricing_analysis_view(analysis: PricingAnalysis) -> PricingAnalysisView:
    """Copy PricingService output exactly, including its authoritative deltas."""

    if not isinstance(analysis, PricingAnalysis):
        raise DataValidationError(
            "pricing presenter requires a PricingAnalysis result",
            code="ui.invalid_pricing_analysis",
            scope="pricing presenter",
        )
    scenarios = tuple(
        PricingScenarioView(
            scenario_id=result.scenario.scenario_id,
            sku=str(result.scenario.sku),
            analysis_timestamp=result.scenario.as_of,
            status=result.status,
            safety_status=result.safety_status,
            current=_pricing_economics_view(result.current_economics),
            scenario=_pricing_economics_view(result.economics),
            price_delta=result.price_delta,
            profit_per_unit_delta=result.profit_per_unit_delta,
            margin_delta=result.margin_delta,
            distance_from_safe_price=result.distance_from_safe_price,
            evidence_refs=result.evidence_refs,
        )
        for result in analysis.scenario_results
    )
    return PricingAnalysisView(
        sku=str(analysis.sku),
        analysis_timestamp=analysis.analysis_timestamp,
        status=analysis.status,
        scenarios=scenarios,
        issues=tuple(_issue_view(issue) for issue in analysis.issues),
    )


def build_brief_status_view(result: BriefGenerationResult) -> BriefStatusView:
    """Expose only validated DailyBrief content from the service result."""

    if not isinstance(result, BriefGenerationResult):
        raise DataValidationError(
            "brief presenter requires a BriefGenerationResult",
            code="ui.invalid_brief_result",
            scope="brief presenter",
        )
    brief = result.brief
    if brief is None:
        return BriefStatusView(status=result.status, brief=None)
    return BriefStatusView(
        status=result.status,
        brief=DailyBriefView(
            analysis_timestamp=brief.analysis_timestamp,
            grounding_digest=brief.grounding_digest,
            summary_text=brief.summary.text,
            summary_action_refs=brief.summary.action_refs,
            summary_fact_refs=brief.summary.fact_refs,
            items=tuple(
                DailyBriefItemView(
                    action_ref=item.action_ref,
                    text=item.text,
                    fact_refs=item.fact_refs,
                )
                for item in brief.items
            ),
        ),
    )


def _economics_row(result: SkuAnalysis, actions: tuple[ActionView, ...]) -> EconomicsRow:
    sku = str(result.sku)
    product_name = None if result.product is None else result.product.name
    economics = result.economics
    economics_actions = _actions_with_prefixes(
        _actions_for_sku(actions, sku),
        ("economics.", "pricing.", "profitability."),
    )
    if economics is None:
        return EconomicsRow(
            sku=sku,
            product_name=product_name,
            analysis_status=result.status,
            economics_status=None,
            safety_status=None,
            currency=None,
            selling_price=None,
            cost_of_goods=None,
            logistics_cost=None,
            commission_rate=None,
            commission_per_unit_input=None,
            commission_cost=None,
            advertising_cost_per_unit_input=None,
            advertising_cost=None,
            drr_status=None,
            drr=None,
            other_variable_costs=(),
            other_variable_cost_total=None,
            total_variable_cost=None,
            profit_per_unit=None,
            contribution_margin=None,
            proportional_cost_rate=None,
            fixed_cost_total=None,
            break_even=None,
            minimum_profit=None,
            minimum_margin=None,
            minimum_safe=None,
            evidence_refs=(),
            action_rules=tuple(action.rule_id for action in economics_actions),
            action_reasons=tuple(action.explanation for action in economics_actions),
        )

    source = economics.source_input
    return EconomicsRow(
        sku=sku,
        product_name=product_name,
        analysis_status=result.status,
        economics_status=economics.status,
        safety_status=economics.safety_status,
        currency=economics.currency,
        selling_price=economics.selling_price,
        cost_of_goods=economics.cost_of_goods,
        logistics_cost=economics.logistics_cost,
        commission_rate=source.commission_rate,
        commission_per_unit_input=source.commission_per_unit,
        commission_cost=economics.commission_cost,
        advertising_cost_per_unit_input=source.advertising_cost_per_unit,
        advertising_cost=economics.advertising_cost,
        drr_status=economics.drr_status,
        drr=economics.drr,
        other_variable_costs=tuple(
            CostComponentView(component.name, component.amount)
            for component in economics.other_variable_costs
        ),
        other_variable_cost_total=economics.other_variable_cost_total,
        total_variable_cost=economics.total_variable_cost,
        profit_per_unit=economics.profit_per_unit,
        contribution_margin=economics.contribution_margin,
        proportional_cost_rate=economics.proportional_cost_rate,
        fixed_cost_total=economics.fixed_cost_total,
        break_even=_boundary_view(economics.break_even),
        minimum_profit=_boundary_view(economics.minimum_profit),
        minimum_margin=_boundary_view(economics.minimum_margin),
        minimum_safe=_boundary_view(economics.minimum_safe),
        evidence_refs=economics.evidence_refs,
        action_rules=tuple(action.rule_id for action in economics_actions),
        action_reasons=tuple(action.explanation for action in economics_actions),
    )


def build_inventory_rows(snapshot: AnalysisSnapshot) -> tuple[InventoryRow, ...]:
    """Copy inventory results in the authoritative snapshot SKU order."""

    actions = build_action_views(snapshot)
    return tuple(_inventory_row(result, actions) for result in snapshot.sku_results)


def build_economics_rows(snapshot: AnalysisSnapshot) -> tuple[EconomicsRow, ...]:
    """Copy current economics in the authoritative snapshot SKU order."""

    actions = build_action_views(snapshot)
    return tuple(_economics_row(result, actions) for result in snapshot.sku_results)


def _sales_detail(result: SkuAnalysis) -> SalesDetailView | None:
    sales = result.sales
    if sales is None:
        return None
    return SalesDetailView(
        status=sales.status,
        change_status=sales.change_status,
        current_period_start=sales.current.period.start,
        current_period_end=sales.current.period.end,
        current_expected_days=sales.current.expected_days,
        current_observed_days=sales.current.observed_days,
        current_total_units=sales.current.total_units,
        current_average_daily_sales=sales.current.average_daily_sales,
        current_missing_dates=sales.current.missing_dates,
        current_source_refs=sales.current.source_refs,
        comparison_period_start=sales.comparison.period.start,
        comparison_period_end=sales.comparison.period.end,
        comparison_expected_days=sales.comparison.expected_days,
        comparison_observed_days=sales.comparison.observed_days,
        comparison_total_units=sales.comparison.total_units,
        comparison_average_daily_sales=sales.comparison.average_daily_sales,
        comparison_missing_dates=sales.comparison.missing_dates,
        comparison_source_refs=sales.comparison.source_refs,
        relative_change=sales.relative_change,
        direction=sales.direction,
    )


def _sku_detail(
    snapshot: AnalysisSnapshot,
    result: SkuAnalysis,
    inventory: InventoryRow,
    economics: EconomicsRow,
    actions: tuple[ActionView, ...],
) -> SkuDetailView:
    sku = str(result.sku)
    product = result.product
    return SkuDetailView(
        sku=sku,
        product_name=None if product is None else product.name,
        marketplace_id=None if product is None else product.marketplace_id,
        active=None if product is None else product.active,
        analysis_status=result.status,
        sales=_sales_detail(result),
        inventory=inventory,
        economics=economics,
        actions=_actions_for_sku(actions, sku),
        issues=tuple(
            _issue_view(issue)
            for issue in snapshot.issues
            if issue.sku is None or issue.sku == result.sku
        ),
        provenance=tuple(_provenance_view(item) for item in result.provenance),
    )


def build_sku_detail_view(
    snapshot: AnalysisSnapshot,
    sku: SkuId,
) -> SkuDetailView:
    """Build one exact SKU detail or fail instead of selecting another SKU."""

    if not isinstance(sku, SkuId):
        raise DataValidationError(
            "SKU detail selection must use a SkuId value.",
            code="ui.invalid_sku_selection",
            scope="SKU detail",
        )
    result = next((item for item in snapshot.sku_results if item.sku == sku), None)
    if result is None:
        raise DataValidationError(
            "Selected SKU is not present in this analysis snapshot.",
            code="ui.unknown_sku",
            scope="SKU detail",
        )
    actions = build_action_views(snapshot)
    inventory = _inventory_row(result, actions)
    economics = _economics_row(result, actions)
    return _sku_detail(snapshot, result, inventory, economics, actions)


def build_dashboard_view(snapshot: AnalysisSnapshot) -> DashboardView:
    """Prepare all dashboard sections from one immutable snapshot."""

    actions = build_action_views(snapshot)
    inventory_rows = tuple(
        _inventory_row(result, actions) for result in snapshot.sku_results
    )
    economics_rows = tuple(
        _economics_row(result, actions) for result in snapshot.sku_results
    )
    return DashboardView(
        summary=build_summary_view(snapshot),
        actions=actions,
        issues=build_issue_views(snapshot),
        inventory_rows=inventory_rows,
        economics_rows=economics_rows,
        sku_details=tuple(
            _sku_detail(snapshot, result, inventory, economics, actions)
            for result, inventory, economics in zip(
                snapshot.sku_results,
                inventory_rows,
                economics_rows,
                strict=True,
            )
        ),
    )

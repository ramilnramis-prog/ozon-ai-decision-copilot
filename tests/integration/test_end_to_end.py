"""Compact end-to-end protection for the offline employer-demo path.

These tests intentionally assert cross-layer contracts. Detailed formula and UI
edge cases remain in their focused unit/integration suites.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
import socket

import pytest
from streamlit.testing.v1 import AppTest

import app.bootstrap as bootstrap_module
from app.ai.brief_input import build_grounded_brief_input
from app.bootstrap import ApplicationServices, build_application
from app.core.clock import FixedClock
from app.core.config import AI_ENABLED_ENV, OPENAI_API_KEY_ENV, AppConfig
from app.core.errors import AIUnavailableError, ProviderError
from app.domain.briefs import (
    AIBriefDraft,
    AIBriefItem,
    AIBriefSummary,
    BriefGenerationStatus,
    GroundedBriefInput,
)
from app.domain.common import (
    AvailabilityStatus,
    SafetyStatus,
    Severity,
    SkuId,
    SourceType,
    Urgency,
)
from app.providers.contracts import ProviderBatch
from app.providers.local_unit_economics import LocalUnitEconomicsProvider
from app.providers.mock_ozon import MockOzonProvider
from app.services.analysis import AnalysisService
from app.services.briefs import BriefService
from app.services.models import AnalysisSnapshot, PriceScenarioRequest
from app.ui.presenters import build_dashboard_view


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = PROJECT_ROOT / "data" / "demo"
ENTRYPOINT = PROJECT_ROOT / "streamlit_app.py"
# The fixed instant is the documented source value in data/demo/metadata.json.
DEMO_TIMESTAMP = datetime.fromisoformat("2026-09-02T09:00:00+03:00")
DEMO_CLOCK = FixedClock(DEMO_TIMESTAMP)


@dataclass(frozen=True, slots=True)
class ScenarioExpectation:
    """One source-defined walkthrough scenario and its key cross-layer result."""

    scenario_id: str
    sku: str
    required_rule: str | None
    expected_rank: int | None
    expected_severity: Severity | None
    expected_urgency: Urgency | None


NINE_REQUIRED_SCENARIOS = (
    ScenarioExpectation("healthy_inventory", "DEMO-001", None, None, None, None),
    ScenarioExpectation(
        "near_stockout",
        "DEMO-002",
        "inventory.replenishment_already_late",
        2,
        Severity.CRITICAL,
        Urgency.IMMEDIATE,
    ),
    ScenarioExpectation(
        "already_late_replenishment",
        "DEMO-004",
        "inventory.replenishment_already_late",
        4,
        Severity.CRITICAL,
        Urgency.IMMEDIATE,
    ),
    ScenarioExpectation(
        "overstock_candidate",
        "DEMO-006",
        "inventory.overstock_candidate",
        76,
        Severity.WARNING,
        Urgency.MONITOR,
    ),
    ScenarioExpectation(
        "low_margin",
        "DEMO-019",
        "profitability.below_minimum_margin",
        62,
        Severity.HIGH,
        Urgency.SOON,
    ),
    ScenarioExpectation(
        "below_safe_price",
        "DEMO-023",
        "pricing.current_price_unsafe",
        30,
        Severity.HIGH,
        Urgency.IMMEDIATE,
    ),
    ScenarioExpectation(
        "sales_decline",
        "DEMO-006",
        "sales.material_decline",
        73,
        Severity.WARNING,
        Urgency.SOON,
    ),
    ScenarioExpectation(
        "sales_increase",
        "DEMO-010",
        "sales.material_increase",
        81,
        Severity.INFO,
        Urgency.MONITOR,
    ),
    ScenarioExpectation("pricing_opportunity", "DEMO-024", None, None, None, None),
)


@pytest.fixture(scope="module")
def application() -> ApplicationServices:
    return build_application(
        AppConfig(ai_enabled=False),
        clock=DEMO_CLOCK,
        environ={},
    )


@pytest.fixture(scope="module")
def default_snapshot(application: ApplicationServices) -> AnalysisSnapshot:
    return application.analysis.analyze(None)


def _sku(snapshot: AnalysisSnapshot, sku: str):
    return next(result for result in snapshot.sku_results if result.sku == SkuId(sku))


def _sku_actions(snapshot: AnalysisSnapshot, sku: str):
    return tuple(
        action
        for action in snapshot.priority_actions
        if action.recommendation.sku == SkuId(sku)
    )


def _action(snapshot: AnalysisSnapshot, sku: str, rule_code: str):
    return next(
        action
        for action in _sku_actions(snapshot, sku)
        if action.recommendation.rule_code == rule_code
    )


def _manifest_scenarios() -> dict[str, set[str]]:
    payload = json.loads((DEMO_ROOT / "scenarios.json").read_text(encoding="utf-8"))
    return {
        item["scenario_id"]: set(item["skus"])
        for item in payload["scenarios"]
    }


def test_default_demo_runs_provider_to_presenter_with_global_action_integrity(
    default_snapshot: AnalysisSnapshot,
) -> None:
    snapshot = default_snapshot
    view = build_dashboard_view(snapshot)

    assert snapshot.analysis_timestamp == DEMO_TIMESTAMP
    assert snapshot.as_of_date == date(2026, 9, 2)
    assert len(snapshot.sku_results) == view.summary.analyzed_sku_count == 37
    assert len(snapshot.priority_actions) == view.summary.priority_action_count == 82
    assert view.summary.critical_count == 17
    assert view.summary.high_count == 53
    assert tuple(action.rank for action in snapshot.priority_actions) == tuple(range(1, 83))
    assert tuple(result.sku for result in snapshot.sku_results) == tuple(
        sorted((result.sku for result in snapshot.sku_results), key=str)
    )
    assert all(result.product is not None and result.product.active for result in snapshot.sku_results)
    assert SkuId("DEMO-038") not in {result.sku for result in snapshot.sku_results}

    recommendations = {
        recommendation.recommendation_id: (recommendation, evaluation.facts)
        for result in snapshot.sku_results
        for evaluation in result.decision_evaluations
        for recommendation in evaluation.recommendations
    }
    assert set(recommendations) == {
        action.recommendation.recommendation_id for action in snapshot.priority_actions
    }
    for action in snapshot.priority_actions:
        recommendation, facts = recommendations[action.recommendation.recommendation_id]
        facts_by_id = {fact.fact_id: fact for fact in facts}
        assert action.recommendation == recommendation
        assert recommendation.provenance.source_type is SourceType.CALCULATED
        assert recommendation.evidence_refs
        assert set(recommendation.evidence_refs) <= set(facts_by_id)
        assert all(
            facts_by_id[fact_id].provenance.source_type is SourceType.CALCULATED
            for fact_id in recommendation.evidence_refs
        )

    assert tuple(item.recommendation_id for item in view.actions) == tuple(
        action.recommendation.recommendation_id for action in snapshot.priority_actions
    )
    assert tuple(item.rank for item in view.actions) == tuple(range(1, 83))
    assert snapshot.provenance
    assert all(result.provenance for result in snapshot.sku_results)


@pytest.mark.parametrize(
    "expected",
    NINE_REQUIRED_SCENARIOS,
    ids=lambda item: item.scenario_id,
)
def test_demo_end_to_end_scenario_matrix(
    default_snapshot: AnalysisSnapshot,
    expected: ScenarioExpectation,
) -> None:
    """Protect the nine walkthrough scenarios named by MASTER_SPEC section 7.1."""

    manifest = _manifest_scenarios()
    assert expected.sku in manifest[expected.scenario_id]
    result = _sku(default_snapshot, expected.sku)
    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.sales is not None
    assert result.inventory is not None
    assert result.economics is not None

    if expected.required_rule is None:
        assert _sku_actions(default_snapshot, expected.sku) == ()
    else:
        action = _action(default_snapshot, expected.sku, expected.required_rule)
        assert action.rank == expected.expected_rank
        assert action.severity is expected.expected_severity
        assert action.urgency is expected.expected_urgency
        assert action.recommendation.evidence_refs
        assert action.recommendation.recommendation_id == (
            f"recommendation:{expected.sku}:{expected.required_rule}"
        )

    sales = result.sales
    inventory = result.inventory
    economics = result.economics
    if expected.scenario_id == "healthy_inventory":
        assert (sales.current.total_units, sales.current.average_daily_sales) == (
            112,
            Decimal("8"),
        )
        assert (inventory.sellable_stock, inventory.stock_coverage_days) == (
            500,
            Decimal("62.5"),
        )
        # F=420+150+25=595, R=0.18+0.12=0.30: profit=1290*(1-R)-F.
        assert economics.profit_per_unit == Decimal("308.0000")
        assert economics.minimum_safe_price == Decimal("1190.00")
        assert economics.safety_status is SafetyStatus.SAFE
    elif expected.scenario_id == "near_stockout":
        assert sales.current.average_daily_sales == Decimal("12")
        assert inventory.sellable_stock == 35
        assert inventory.stockout_date == date(2026, 9, 5)
        assert inventory.latest_safe_start_date == date(2026, 8, 23)
        # 12 * (13 lead/buffer + 30 target) - 35 stock = 481.
        assert inventory.recommended_replenishment_quantity == 481
    elif expected.scenario_id == "already_late_replenishment":
        assert sales.current.average_daily_sales == Decimal("20")
        assert inventory.stockout_date == date(2026, 9, 11)
        assert inventory.latest_safe_start_date == date(2026, 8, 18)
        assert inventory.eligible_confirmed_inbound_units == 100
        # 20 * (24 lead/buffer + 30 target) - 80 stock - 100 inbound = 900.
        assert inventory.recommended_replenishment_quantity == 900
    elif expected.scenario_id == "overstock_candidate":
        assert inventory.sellable_stock == 900
        assert inventory.stock_coverage_days == Decimal("300")
        assert inventory.eligible_confirmed_inbound_units == 0
        assert any(event.treatment.value == "unconfirmed" for event in inventory.inbound_events)
    elif expected.scenario_id == "low_margin":
        # F=800+1100+95=1995, R=0.18+0.12=0.30.
        assert economics.profit_per_unit == Decimal("28.0000")
        assert economics.minimum_safe_price == Decimal("3990.00")
        assert economics.contribution_margin < Decimal("0.01")
    elif expected.scenario_id == "below_safe_price":
        # F=2700+450+80=3230, R=0.20+0.27=0.47.
        assert economics.selling_price == Decimal("3990.00")
        assert economics.profit_per_unit == Decimal("-1115.3000")
        assert economics.minimum_safe_price == Decimal("9788.00")
        assert economics.safety_status is SafetyStatus.UNSAFE
    elif expected.scenario_id == "sales_decline":
        assert (sales.current.total_units, sales.comparison.total_units) == (42, 168)
        assert sales.relative_change == Decimal("-0.75")
        assert sales.direction is not None and sales.direction.value == "decreasing"
    elif expected.scenario_id == "sales_increase":
        assert (sales.current.total_units, sales.comparison.total_units) == (168, 42)
        assert sales.relative_change == Decimal("3")
        assert sales.direction is not None and sales.direction.value == "increasing"
    elif expected.scenario_id == "pricing_opportunity":
        # Strong headroom is factual; no opportunity action exists without approved evidence/rule.
        assert economics.selling_price == Decimal("12990.00")
        assert economics.profit_per_unit == Decimal("5622.4000")
        assert economics.minimum_safe_price == Decimal("7590.00")
        assert economics.selling_price - economics.minimum_safe_price == Decimal("5400.00")
        assert economics.safety_status is SafetyStatus.SAFE


def test_accepted_edge_skus_remain_truthful_and_distinct_end_to_end(
    default_snapshot: AnalysisSnapshot,
) -> None:
    """Protect accepted cross-layer edge behavior outside the nine walkthroughs."""

    expected_actions = {
        "DEMO-005": ((1, "inventory.out_of_stock"),),
        "DEMO-006": (
            (21, "pricing.current_price_unsafe"),
            (43, "profitability.below_minimum_profit"),
            (55, "profitability.below_minimum_margin"),
            (73, "sales.material_decline"),
            (76, "inventory.overstock_candidate"),
        ),
        "DEMO-012": (
            (23, "pricing.current_price_unsafe"),
            (57, "profitability.below_minimum_margin"),
            (72, "inventory.missing_lead_time"),
        ),
        "DEMO-015": (
            (12, "profitability.loss_making"),
            (24, "pricing.current_price_unsafe"),
            (44, "profitability.below_minimum_profit"),
            (58, "profitability.below_minimum_margin"),
        ),
        "DEMO-018": (
            (11, "profitability.no_finite_safe_price"),
            (15, "profitability.loss_making"),
            (47, "profitability.below_minimum_profit"),
            (61, "profitability.below_minimum_margin"),
        ),
        "DEMO-021": (
            (41, "inventory.replenishment_due_soon"),
            (71, "economics.insufficient_data"),
        ),
        "DEMO-037": (
            (79, "inventory.insufficient_sales_history"),
            (80, "sales.insufficient_history"),
        ),
    }
    for sku, expected in expected_actions.items():
        assert tuple(
            (action.rank, action.recommendation.rule_code)
            for action in _sku_actions(default_snapshot, sku)
        ) == expected

    out_of_stock = _sku(default_snapshot, "DEMO-005").inventory
    decline_overstock = _sku(default_snapshot, "DEMO-006").inventory
    missing_lead = _sku(default_snapshot, "DEMO-012").inventory
    loss = _sku(default_snapshot, "DEMO-015").economics
    no_finite_safe_price = _sku(default_snapshot, "DEMO-018").economics
    missing_economics = _sku(default_snapshot, "DEMO-021")
    insufficient_sales = _sku(default_snapshot, "DEMO-037").inventory
    assert out_of_stock is not None and out_of_stock.sellable_stock == 0
    assert out_of_stock.stockout_date == date(2026, 9, 2)
    assert decline_overstock is not None
    assert tuple(event.treatment.value for event in decline_overstock.inbound_events) == (
        "unconfirmed",
    )
    assert missing_lead is not None
    assert missing_lead.lead_time_status is AvailabilityStatus.INSUFFICIENT_DATA
    assert missing_lead.latest_safe_start_date is None
    assert missing_lead.recommended_replenishment_quantity is None
    assert loss is not None and loss.profit_per_unit == Decimal("-649.7000")
    assert loss.safety_status is SafetyStatus.UNSAFE
    assert no_finite_safe_price is not None
    assert no_finite_safe_price.minimum_safe_price is None
    assert no_finite_safe_price.safety_status is SafetyStatus.UNSAFE
    assert missing_economics.economics is not None
    assert missing_economics.economics.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert missing_economics.economics.profit_per_unit is None
    assert missing_economics.inventory is not None
    assert missing_economics.inventory.recommended_replenishment_quantity == 171
    assert insufficient_sales is not None
    assert insufficient_sales.average_daily_sales is None
    assert insufficient_sales.stockout_date is None
    assert insufficient_sales.recommended_replenishment_quantity is None


def test_fixed_clock_repeated_analysis_and_selection_order_are_deterministic(
    application: ApplicationServices,
    default_snapshot: AnalysisSnapshot,
) -> None:
    repeated = application.analysis.analyze(None)
    left = application.analysis.analyze((SkuId("DEMO-006"), SkuId("DEMO-005")))
    right = application.analysis.analyze((SkuId("DEMO-005"), SkuId("DEMO-006")))
    inactive = application.analysis.analyze((SkuId("DEMO-038"),))

    assert repeated == default_snapshot
    assert left == right
    assert tuple(result.sku for result in left.sku_results) == (
        SkuId("DEMO-005"),
        SkuId("DEMO-006"),
    )
    assert tuple(action.recommendation.recommendation_id for action in left.priority_actions) == tuple(
        action.recommendation.recommendation_id for action in right.priority_actions
    )
    assert inactive.sku_results[0].product is not None
    assert inactive.sku_results[0].product.active is False
    assert SkuId("DEMO-038") not in {result.sku for result in default_snapshot.sku_results}


class ReorderedMarketplace:
    """Alternate test adapter with optional distinctive normalized stock."""

    def __init__(self, *, distinctive_stock: int | None = None) -> None:
        self.delegate = MockOzonProvider()
        self.distinctive_stock = distinctive_stock

    @staticmethod
    def _reversed(batch):
        return ProviderBatch(tuple(reversed(batch.records)), batch.issues)

    def get_products(self):
        return self._reversed(self.delegate.get_products())

    def get_sales_observations(self):
        return self._reversed(self.delegate.get_sales_observations())

    def get_inventory_snapshots(self):
        batch = self.delegate.get_inventory_snapshots()
        records = tuple(reversed(batch.records))
        if self.distinctive_stock is not None:
            records = tuple(
                replace(
                    record,
                    sellable_stock=self.distinctive_stock,
                    provenance=replace(
                        record.provenance,
                        provider="alternate_test_marketplace",
                        source_record_id="alternate:inventory:DEMO-001",
                    ),
                )
                if record.sku == SkuId("DEMO-001")
                else record
                for record in records
            )
        return ProviderBatch(records, batch.issues)

    def get_inbound_supplies(self):
        return self._reversed(self.delegate.get_inbound_supplies())

    def get_lead_time(self, sku):
        return self.delegate.get_lead_time(sku)

    def get_replenishment_constraints(self, sku):
        return self.delegate.get_replenishment_constraints(sku)


def _alternate_analysis(application: ApplicationServices, marketplace) -> AnalysisService:
    return AnalysisService(
        marketplace,
        LocalUnitEconomicsProvider(),
        DEMO_CLOCK,
        application.analysis.configuration,
    )


def test_alternate_provider_reordering_and_distinctive_value_cross_service_boundary(
    application: ApplicationServices,
) -> None:
    requested = (SkuId("DEMO-006"), SkuId("DEMO-001"))
    baseline = application.analysis.analyze(requested)
    reordered = _alternate_analysis(application, ReorderedMarketplace()).analyze(requested)
    distinctive = _alternate_analysis(
        application,
        ReorderedMarketplace(distinctive_stock=640),
    ).analyze((SkuId("DEMO-001"),))

    assert reordered == baseline
    changed = distinctive.sku_results[0]
    assert changed.inventory is not None
    assert changed.inventory.sellable_stock == 640
    assert changed.inventory.stock_coverage_days == Decimal("80")
    assert any(item.provider == "alternate_test_marketplace" for item in changed.provenance)
    service_source = (PROJECT_ROOT / "app" / "services" / "analysis.py").read_text(
        encoding="utf-8"
    )
    assert "ReorderedMarketplace" not in service_source
    assert "alternate_test_marketplace" not in service_source


class DuplicateSalesMarketplace(ReorderedMarketplace):
    def __init__(self) -> None:
        super().__init__()

    def get_sales_observations(self):
        batch = self.delegate.get_sales_observations()
        duplicate = next(item for item in batch.records if item.sku == SkuId("DEMO-005"))
        return ProviderBatch((*batch.records, duplicate), batch.issues)


def test_recoverable_partial_batch_preserves_valid_sku_without_fabrication(
    application: ApplicationServices,
) -> None:
    snapshot = _alternate_analysis(application, DuplicateSalesMarketplace()).analyze(
        (SkuId("DEMO-001"), SkuId("DEMO-005"))
    )
    valid = _sku(snapshot, "DEMO-001")
    affected = _sku(snapshot, "DEMO-005")

    assert valid.status is AvailabilityStatus.AVAILABLE
    assert valid.sales is not None and valid.inventory is not None and valid.economics is not None
    assert affected.status is AvailabilityStatus.INVALID
    assert affected.sales is None
    assert affected.inventory is None
    assert affected.economics is not None
    assert {issue.code for issue in affected.issues} == {"sales.duplicate_observation_date"}
    assert _sku_actions(snapshot, "DEMO-005") == ()


class FailingMarketplace(ReorderedMarketplace):
    def get_products(self):
        raise ProviderError(
            "Marketplace source unavailable.",
            code="provider.test_unavailable",
            scope="marketplace",
        )


def test_provider_wide_failure_remains_distinct_and_propagates(
    application: ApplicationServices,
) -> None:
    with pytest.raises(ProviderError) as error:
        _alternate_analysis(application, FailingMarketplace()).analyze(None)

    assert error.value.code == "provider.test_unavailable"


def test_mock_only_streamlit_startup_and_services_are_offline_without_ai_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OPENAI_API_KEY_ENV, raising=False)
    monkeypatch.setenv(AI_ENABLED_ENV, "false")
    network_calls: list[object] = []

    def forbidden_network(*args, **kwargs):
        network_calls.append((args, kwargs))
        raise AssertionError("MVP-019 acceptance path must remain offline")

    def forbidden_openai(*args, **kwargs):
        raise AssertionError("AI-disabled startup must not construct OpenAI")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(bootstrap_module, "OpenAIBriefProvider", forbidden_openai)
    services = build_application(
        AppConfig(ai_enabled=False),
        clock=DEMO_CLOCK,
        environ={},
    )
    snapshot = services.analysis.analyze(None)
    pricing = services.pricing.simulate(
        SkuId("DEMO-001"),
        (PriceScenarioRequest("offline", Decimal("1190.00")),),
    )
    brief = services.briefs.generate(snapshot)
    view = build_dashboard_view(snapshot)
    app = AppTest.from_file(ENTRYPOINT).run(timeout=30)

    assert not app.exception
    assert len(view.sku_details) == 37
    assert pricing.scenario_results
    assert brief.status is BriefGenerationStatus.DISABLED and brief.brief is None
    assert network_calls == []
    tab_labels = tuple(tab.label for tab in app.tabs)
    assert len(tab_labels) == 6
    assert tab_labels[0].endswith("Priority Actions")
    assert tab_labels[-2:] == ("Price Simulator", "AI Daily Brief")
    assert all(label.strip() for label in tab_labels)
    assert any("SKU" in text for text in tab_labels)


def test_real_pricing_service_preserves_below_equal_above_safe_boundary(
    application: ApplicationServices,
) -> None:
    # DEMO-001 source inputs: F=420+150+25=595 and R=0.18+0.12=0.30.
    # The binding margin boundary is 595/(1-0.30-0.20)=1190 exactly.
    requests = (
        PriceScenarioRequest("below", Decimal("1189.00")),
        PriceScenarioRequest("equal", Decimal("1190.00")),
        PriceScenarioRequest("above", Decimal("1191.00")),
    )
    first = application.pricing.simulate(SkuId("DEMO-001"), requests)
    second = application.pricing.simulate(SkuId("DEMO-001"), requests)
    below, equal, above = first.scenario_results

    assert first == second
    assert tuple(item.candidate_price for item in first.scenario_results) == (
        Decimal("1189.00"),
        Decimal("1190.00"),
        Decimal("1191.00"),
    )
    assert tuple(item.economics.profit_per_unit for item in first.scenario_results) == (
        Decimal("237.3000"),
        Decimal("238.0000"),
        Decimal("238.7000"),
    )
    assert tuple(item.economics.minimum_safe_price for item in first.scenario_results) == (
        Decimal("1190.00"),
        Decimal("1190.00"),
        Decimal("1190.00"),
    )
    assert below.safety_status is SafetyStatus.UNSAFE
    assert equal.safety_status is SafetyStatus.SAFE
    assert above.safety_status is SafetyStatus.SAFE
    assert (below.distance_from_safe_price, equal.distance_from_safe_price, above.distance_from_safe_price) == (
        Decimal("-1.00"),
        Decimal("0.00"),
        Decimal("1.00"),
    )
    assert equal.economics.contribution_margin == Decimal("0.20")
    assert tuple(
        recommendation.rule_code
        for evaluation in first.decision_evaluations
        for recommendation in evaluation.recommendations
    ) == ("pricing.scenario_below_safe_price",)

    missing = application.pricing.simulate(
        SkuId("DEMO-021"),
        (PriceScenarioRequest("missing", Decimal("1233.00")),),
    ).scenario_results[0]
    assert missing.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert missing.economics.profit_per_unit is None
    assert missing.economics.contribution_margin is None
    assert missing.economics.minimum_safe_price is None


class ValidBriefProvider:
    def __init__(self) -> None:
        self.calls = 0

    def create_draft(self, brief_input: GroundedBriefInput) -> AIBriefDraft:
        self.calls += 1
        return AIBriefDraft(
            AIBriefSummary("Grounded management summary.", (), ()),
            tuple(
                AIBriefItem(action.action_ref, "Grounded action explanation.", ())
                for action in brief_input.actions
            ),
        )


class InvalidBriefProvider:
    def create_draft(self, brief_input: GroundedBriefInput) -> AIBriefDraft:
        return AIBriefDraft(
            AIBriefSummary("Unsupported value 999999.", (), ()),
            tuple(
                AIBriefItem(action.action_ref, "Grounded action explanation.", ())
                for action in brief_input.actions
            ),
        )


class UnavailableBriefProvider:
    def create_draft(self, brief_input: GroundedBriefInput) -> AIBriefDraft:
        raise AIUnavailableError(
            "AI provider unavailable.",
            code="ai.test_unavailable",
            scope="AI provider",
        )


def test_ai_statuses_grounding_identity_and_downstream_use_cannot_mutate_truth(
    application: ApplicationServices,
    default_snapshot: AnalysisSnapshot,
) -> None:
    snapshot = default_snapshot
    before = deepcopy(snapshot)
    dashboard = build_dashboard_view(snapshot)
    application.pricing.simulate(
        SkuId("DEMO-001"),
        (PriceScenarioRequest("immutability", Decimal("1190.00")),),
    )

    disabled = application.briefs.generate(snapshot)
    invalid = BriefService(InvalidBriefProvider(), enabled=True).generate(snapshot)
    unavailable = BriefService(UnavailableBriefProvider(), enabled=True).generate(snapshot)
    valid_provider = ValidBriefProvider()
    valid_service = BriefService(valid_provider, enabled=True)
    digest_first = valid_service.grounding_digest(snapshot)
    digest_second = valid_service.grounding_digest(snapshot)
    generated = valid_service.generate(snapshot)

    same_stamp_a = application.analysis.analyze((SkuId("DEMO-005"),))
    same_stamp_b = application.analysis.analyze((SkuId("DEMO-006"),))
    assert same_stamp_a.analysis_timestamp == same_stamp_b.analysis_timestamp
    assert build_grounded_brief_input(same_stamp_a) != build_grounded_brief_input(same_stamp_b)
    assert valid_service.grounding_digest(same_stamp_a) != valid_service.grounding_digest(
        same_stamp_b
    )

    assert disabled.status is BriefGenerationStatus.DISABLED and disabled.brief is None
    assert invalid.status is BriefGenerationStatus.INVALID and invalid.brief is None
    assert unavailable.status is BriefGenerationStatus.UNAVAILABLE and unavailable.brief is None
    assert generated.status is BriefGenerationStatus.GENERATED
    assert generated.brief is not None
    assert generated.brief.grounding_digest == digest_first == digest_second
    assert valid_provider.calls == 1
    assert len(generated.brief.items) == len(snapshot.priority_actions)
    assert tuple(item.action_ref for item in generated.brief.items) == tuple(
        action.recommendation.recommendation_id for action in snapshot.priority_actions
    )
    assert len(dashboard.actions) == len(snapshot.priority_actions)
    assert snapshot == before


def test_mvp019_scope_remains_portable_read_only_and_architecturally_narrow() -> None:
    test_source = Path(__file__).read_text(encoding="utf-8")
    production_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "app").rglob("*.py")
    ).lower()
    analytics_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "app" / "analytics").glob("*.py")
    ).lower()
    decision_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "app" / "decisions").glob("*.py")
    ).lower()
    project_metadata = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert re.search(r"[A-Za-z]:[\\/](?:Users|home)[\\/]", test_source) is None
    assert "fastapi" not in production_source
    assert "ozonsellerapiprovider" not in production_source
    assert "openai>=2,<3" in project_metadata
    assert "streamlit>=1.40,<2" in project_metadata
    assert "app.providers" not in analytics_source
    assert "app.ai" not in analytics_source
    assert "streamlit" not in analytics_source
    assert "app.providers" not in decision_source
    assert "app.ai" not in decision_source
    assert "streamlit" not in decision_source
    assert not (PROJECT_ROOT / "app" / "api").exists()
    assert not any("ozon_seller" in path.name.lower() for path in (PROJECT_ROOT / "app").rglob("*.py"))
    assert "update_price" not in production_source
    assert "elasticity" not in production_source

"""Focused tests for deterministic analysis orchestration."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime

import pytest

import app.services.analysis as analysis_module
from app.core.clock import FixedClock
from app.core.errors import (
    CalculationPreconditionError,
    DataValidationError,
    ProviderError,
)
from app.decisions.prioritizer import APPROVED_SEVERITY_ORDER, APPROVED_URGENCY_ORDER
from app.domain.common import (
    AvailabilityStatus,
    PolicyIdentity,
    Severity,
    SkuId,
    ValidationIssue,
)
from app.domain.recommendations import PriorityPolicy
from app.providers.contracts import ProviderBatch, ProviderValue
from app.providers.local_unit_economics import LocalUnitEconomicsProvider
from app.providers.mock_ozon import MockOzonProvider
from app.services.analysis import AnalysisService
from app.services.models import AnalysisSnapshot


STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)
CLOCK = FixedClock(STAMP)


def _configuration():
    from app.bootstrap import build_application

    return build_application(clock=CLOCK).analysis.configuration


def _service(marketplace=None, economics=None, *, configuration=None):
    return AnalysisService(
        marketplace or MockOzonProvider(),
        economics or LocalUnitEconomicsProvider(),
        CLOCK,
        configuration or _configuration(),
    )


def _result(snapshot: AnalysisSnapshot, sku: str):
    return next(result for result in snapshot.sku_results if str(result.sku) == sku)


def _rules(snapshot: AnalysisSnapshot, sku: str) -> tuple[str, ...]:
    return tuple(
        action.recommendation.rule_code
        for action in snapshot.priority_actions
        if str(action.recommendation.sku) == sku
    )


class DelegatingMarketplace:
    def __init__(self, delegate=None):
        self.delegate = delegate or MockOzonProvider()

    def get_products(self):
        return self.delegate.get_products()

    def get_sales_observations(self):
        return self.delegate.get_sales_observations()

    def get_inventory_snapshots(self):
        return self.delegate.get_inventory_snapshots()

    def get_inbound_supplies(self):
        return self.delegate.get_inbound_supplies()

    def get_lead_time(self, sku):
        return self.delegate.get_lead_time(sku)

    def get_replenishment_constraints(self, sku):
        return self.delegate.get_replenishment_constraints(sku)


class DelegatingEconomics:
    def __init__(self, delegate=None):
        self.delegate = delegate or LocalUnitEconomicsProvider()

    def get_unit_economics(self, sku):
        return self.delegate.get_unit_economics(sku)


def test_healthy_sku_returns_complete_immutable_zero_action_snapshot() -> None:
    snapshot = _service().analyze((SkuId("DEMO-001"),))
    result = snapshot.sku_results[0]

    assert snapshot.analysis_timestamp == STAMP
    assert snapshot.as_of_date == date(2026, 9, 2)
    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.sales is not None
    assert result.inventory is not None
    assert result.economics is not None
    assert result.decision_evaluations
    assert snapshot.priority_actions == ()
    assert snapshot.configuration == _configuration()
    assert snapshot.provenance
    assert result.provenance
    with pytest.raises(FrozenInstanceError):
        snapshot.sku_results = ()


def test_one_timestamp_flows_through_empty_and_nonempty_decision_families() -> None:
    snapshot = _service().analyze((SkuId("DEMO-005"),))
    result = snapshot.sku_results[0]

    assert {item.analysis_timestamp for item in result.decision_evaluations} == {STAMP}
    assert any(not item.recommendations for item in result.decision_evaluations)
    assert any(item.recommendations for item in result.decision_evaluations)
    assert {
        action.recommendation.analysis_timestamp for action in snapshot.priority_actions
    } == {STAMP}
    assert _rules(snapshot, "DEMO-005") == ("inventory.out_of_stock",)


def test_explicit_sku_selection_is_sorted_and_input_order_independent() -> None:
    service = _service()
    left = service.analyze((SkuId("DEMO-006"), SkuId("DEMO-005")))
    right = service.analyze((SkuId("DEMO-005"), SkuId("DEMO-006")))

    assert left == right
    assert tuple(str(result.sku) for result in left.sku_results) == (
        "DEMO-005",
        "DEMO-006",
    )


def test_default_selection_is_active_only_and_empty_selection_stays_empty() -> None:
    service = _service()

    default_snapshot = service.analyze(None)
    empty_snapshot = service.analyze(())

    assert len(default_snapshot.sku_results) == 37
    assert all(
        result.product is not None and result.product.active
        for result in default_snapshot.sku_results
    )
    assert SkuId("DEMO-038") not in {
        result.sku for result in default_snapshot.sku_results
    }
    assert empty_snapshot.analysis_timestamp == STAMP
    assert empty_snapshot.sku_results == ()
    assert empty_snapshot.priority_actions == ()
    assert empty_snapshot.issues == ()
    assert empty_snapshot.provenance == ()
    assert default_snapshot != empty_snapshot


def test_explicit_selection_includes_active_and_inactive_products_exactly() -> None:
    snapshot = _service().analyze(
        (SkuId("DEMO-038"), SkuId("DEMO-001"))
    )

    assert tuple(str(result.sku) for result in snapshot.sku_results) == (
        "DEMO-001",
        "DEMO-038",
    )
    assert snapshot.sku_results[0].product is not None
    assert snapshot.sku_results[0].product.active is True
    assert snapshot.sku_results[1].product is not None
    assert snapshot.sku_results[1].product.active is False


def test_unknown_and_duplicate_requested_skus_fail_clearly() -> None:
    service = _service()
    with pytest.raises(DataValidationError, match="does not exist") as unknown:
        service.analyze((SkuId("UNKNOWN"),))
    assert unknown.value.code == "services.unknown_sku"

    with pytest.raises(DataValidationError, match="must not contain duplicates"):
        service.analyze((SkuId("DEMO-001"), SkuId("DEMO-001")))


def test_alternate_structural_provider_works_without_concrete_service_coupling() -> None:
    service = _service(DelegatingMarketplace(), DelegatingEconomics())

    snapshot = service.analyze((SkuId("DEMO-001"),))

    assert snapshot.sku_results[0].product is not None
    source = __import__("pathlib").Path(analysis_module.__file__).read_text(encoding="utf-8")
    assert "mockozonprovider" not in source.lower()
    assert "localuniteconomicsprovider" not in source.lower()


def test_missing_economics_does_not_block_sales_or_inventory() -> None:
    class MissingEconomics(DelegatingEconomics):
        def get_unit_economics(self, sku):
            return ProviderValue(None)

    snapshot = _service(economics=MissingEconomics()).analyze((SkuId("DEMO-001"),))
    result = snapshot.sku_results[0]

    assert result.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert result.sales is not None
    assert result.inventory is not None
    assert result.economics is None
    assert any(issue.code == "services.missing_unit_economics" for issue in result.issues)


def test_invalid_inventory_record_is_not_healthy_and_does_not_block_valid_sku() -> None:
    bad_sku = SkuId("DEMO-005")

    class NegativeStockBoundary(DelegatingMarketplace):
        def get_inventory_snapshots(self):
            batch = self.delegate.get_inventory_snapshots()
            issue = ValidationIssue(
                code="providers.invalid_negative_stock",
                message="Sellable stock must be non-negative.",
                severity=Severity.WARNING,
                scope="inventory",
                sku=bad_sku,
                field_name="sellable_stock",
            )
            return ProviderBatch(
                tuple(record for record in batch.records if record.sku != bad_sku),
                (*batch.issues, issue),
            )

    snapshot = _service(marketplace=NegativeStockBoundary()).analyze(
        (SkuId("DEMO-001"), bad_sku)
    )

    assert _result(snapshot, "DEMO-001").status is AvailabilityStatus.AVAILABLE
    bad = _result(snapshot, "DEMO-005")
    assert bad.status is AvailabilityStatus.INVALID
    assert bad.inventory is None
    assert any(issue.code == "providers.invalid_negative_stock" for issue in bad.issues)


def test_per_sku_calculation_validation_does_not_block_valid_sku() -> None:
    bad_sku = SkuId("DEMO-005")

    class DuplicateSalesBoundary(DelegatingMarketplace):
        def get_sales_observations(self):
            batch = self.delegate.get_sales_observations()
            duplicate = next(record for record in batch.records if record.sku == bad_sku)
            return ProviderBatch((*batch.records, duplicate), batch.issues)

    snapshot = _service(marketplace=DuplicateSalesBoundary()).analyze(
        (SkuId("DEMO-001"), bad_sku)
    )

    assert _result(snapshot, "DEMO-001").status is AvailabilityStatus.AVAILABLE
    bad = _result(snapshot, "DEMO-005")
    assert bad.status is AvailabilityStatus.INVALID
    assert bad.sales is None
    assert bad.inventory is None
    assert bad.economics is not None
    assert any(issue.code == "sales.duplicate_observation_date" for issue in bad.issues)


def test_invalid_global_sales_window_propagates_without_partial_success() -> None:
    configuration = replace(_configuration(), as_of_date=date.min)

    with pytest.raises(CalculationPreconditionError) as error:
        _service(configuration=configuration).analyze(
            (SkuId("DEMO-001"), SkuId("DEMO-005"))
        )

    assert error.value.code == "sales.invalid_window_boundaries"


def test_internal_typed_analytics_invariant_error_propagates(monkeypatch) -> None:
    def fail_economics(*args, **kwargs):
        raise DataValidationError(
            "Internal economics invariant failed.",
            code="economics.inconsistent_profit",
            scope="economics result",
        )

    monkeypatch.setattr(
        analysis_module,
        "calculate_unit_economics",
        fail_economics,
    )

    with pytest.raises(DataValidationError) as error:
        _service().analyze((SkuId("DEMO-001"), SkuId("DEMO-005")))

    assert error.value.code == "economics.inconsistent_profit"


def test_unexpected_analytics_runtime_error_propagates(monkeypatch) -> None:
    def fail_sales(*args, **kwargs):
        raise RuntimeError("unexpected analytics failure")

    monkeypatch.setattr(analysis_module, "calculate_sales_metrics", fail_sales)

    with pytest.raises(RuntimeError, match="unexpected analytics failure"):
        _service().analyze((SkuId("DEMO-001"),))


def test_existing_partial_business_states_flow_through_without_service_failure() -> None:
    snapshot = _service().analyze(
        (SkuId("DEMO-012"), SkuId("DEMO-021"), SkuId("DEMO-037"))
    )

    missing_lead = _result(snapshot, "DEMO-012")
    assert missing_lead.inventory is not None
    assert missing_lead.inventory.lead_time_status is AvailabilityStatus.INSUFFICIENT_DATA
    assert "inventory.missing_lead_time" in _rules(snapshot, "DEMO-012")

    missing_cogs = _result(snapshot, "DEMO-021")
    assert missing_cogs.inventory is not None
    assert missing_cogs.economics is not None
    assert missing_cogs.economics.profit_per_unit is None
    assert _rules(snapshot, "DEMO-021") == (
        "inventory.replenishment_due_soon",
        "economics.insufficient_data",
    )

    insufficient_sales = _result(snapshot, "DEMO-037")
    assert insufficient_sales.inventory is not None
    assert insufficient_sales.inventory.average_daily_sales is None
    assert _rules(snapshot, "DEMO-037") == (
        "inventory.insufficient_sales_history",
        "sales.insufficient_history",
    )


@pytest.mark.parametrize(
    ("sku", "expected"),
    [
        ("DEMO-006", ("pricing.current_price_unsafe", "profitability.below_minimum_profit", "profitability.below_minimum_margin", "sales.material_decline", "inventory.overstock_candidate")),
        ("DEMO-015", ("profitability.loss_making", "pricing.current_price_unsafe", "profitability.below_minimum_profit", "profitability.below_minimum_margin")),
        ("DEMO-018", ("profitability.no_finite_safe_price", "profitability.loss_making", "profitability.below_minimum_profit", "profitability.below_minimum_margin")),
        ("DEMO-019", ("pricing.current_price_unsafe", "profitability.below_minimum_profit", "profitability.below_minimum_margin")),
    ],
)
def test_overlapping_authoritative_decisions_are_preserved(sku, expected) -> None:
    snapshot = _service().analyze((SkuId(sku),))

    assert _rules(snapshot, sku) == expected


def test_service_reuses_completed_analytics_decisions_and_prioritizer(monkeypatch) -> None:
    calls = {name: 0 for name in ("sales", "inventory", "economics", "sales_rules", "inventory_rules", "economics_rules", "priority")}

    def wrap(name, original):
        def recorded(*args, **kwargs):
            calls[name] += 1
            return original(*args, **kwargs)
        return recorded

    for name, attribute in (
        ("sales", "calculate_sales_metrics"),
        ("inventory", "calculate_inventory_analysis"),
        ("economics", "calculate_unit_economics"),
        ("sales_rules", "evaluate_sales_rules"),
        ("inventory_rules", "evaluate_inventory_rules"),
        ("economics_rules", "evaluate_economics_rules"),
        ("priority", "build_priority_action_center"),
    ):
        original = getattr(analysis_module, attribute)
        monkeypatch.setattr(analysis_module, attribute, wrap(name, original))

    _service().analyze((SkuId("DEMO-001"),))

    assert calls == {name: 1 for name in calls}


def test_provider_wide_failure_and_unsupported_global_priority_policy_propagate() -> None:
    class FailingMarketplace(DelegatingMarketplace):
        def get_products(self):
            raise ProviderError("Marketplace snapshot unavailable.")

    with pytest.raises(ProviderError, match="snapshot unavailable"):
        _service(marketplace=FailingMarketplace()).analyze()

    unsupported = PriorityPolicy(
        PolicyIdentity("unsupported", "1"),
        tuple(reversed(APPROVED_SEVERITY_ORDER)),
        APPROVED_URGENCY_ORDER,
    )
    configuration = replace(_configuration(), priority_policy=unsupported)
    with pytest.raises(DataValidationError) as error:
        _service(configuration=configuration).analyze((SkuId("DEMO-001"),))
    assert error.value.code == "prioritizer.unsupported_severity_order"


def test_cross_sku_economics_contamination_is_rejected() -> None:
    class WrongEconomics(DelegatingEconomics):
        def get_unit_economics(self, sku):
            return self.delegate.get_unit_economics(SkuId("DEMO-002"))

    with pytest.raises(DataValidationError) as error:
        _service(economics=WrongEconomics()).analyze((SkuId("DEMO-001"),))
    assert error.value.code == "services.sku_mismatch"


def test_source_collections_are_not_mutated() -> None:
    marketplace = MockOzonProvider()
    sales_before = marketplace.get_sales_observations().records
    inventory_before = marketplace.get_inventory_snapshots().records

    _service(marketplace=marketplace).analyze((SkuId("DEMO-006"),))

    assert marketplace.get_sales_observations().records == sales_before
    assert marketplace.get_inventory_snapshots().records == inventory_before


def test_analysis_service_has_no_formula_ai_ui_api_or_write_dependency() -> None:
    source = __import__("pathlib").Path(analysis_module.__file__).read_text(encoding="utf-8")
    forbidden = (
        "streamlit",
        "fastapi",
        "openai",
        "app.ai",
        "approved_rule_priorities",
        "minimum_safe_price =",
        "average_daily_sales =",
        "requests.",
        "http",
    )
    assert all(token not in source.lower() for token in forbidden)

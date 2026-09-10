"""Focused tests for pricing-service composition."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import app.services.pricing as pricing_module
from app.analytics.pricing import simulate_prices
from app.core.clock import FixedClock
from app.core.errors import DataValidationError, ProviderError
from app.domain.common import AvailabilityStatus, SkuId
from app.providers.contracts import ProviderValue
from app.providers.local_unit_economics import LocalUnitEconomicsProvider
from app.services.models import PriceScenarioRequest
from app.services.pricing import PricingService


STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)


def _service(economics=None):
    from app.bootstrap import build_application

    services = build_application(clock=FixedClock(STAMP))
    if economics is None:
        return services.pricing
    config = services.analysis.configuration
    return PricingService(
        economics,
        FixedClock(STAMP),
        config.economics_policy,
        config.priority_policy,
    )


def test_pricing_service_reuses_exact_what_if_engine_and_is_read_only() -> None:
    provider = LocalUnitEconomicsProvider()
    source_before = provider.get_unit_economics(SkuId("DEMO-001")).value
    requests = (
        PriceScenarioRequest("too-low", Decimal("100")),
        PriceScenarioRequest("higher", Decimal("2500")),
    )

    analysis = _service(provider).simulate(SkuId("DEMO-001"), requests)
    source_after = provider.get_unit_economics(SkuId("DEMO-001")).value

    assert analysis.status is AvailabilityStatus.AVAILABLE
    assert analysis.analysis_timestamp == STAMP
    assert analysis.source_input == source_before == source_after
    assert tuple(result.scenario.scenario_id for result in analysis.scenario_results) == (
        "too-low",
        "higher",
    )
    assert all(result.scenario.is_hypothetical for result in analysis.scenario_results)
    assert analysis.scenario_results == simulate_prices(
        source_before,
        analysis.economics_policy,
        tuple(result.scenario for result in analysis.scenario_results),
    )
    assert tuple(action.recommendation.rule_code for action in analysis.priority_actions) == (
        "pricing.scenario_below_safe_price",
    )
    with pytest.raises(FrozenInstanceError):
        analysis.scenario_results = ()


def test_no_candidate_price_means_no_invented_scenario_or_action() -> None:
    analysis = _service().simulate(SkuId("DEMO-001"), ())

    assert analysis.status is AvailabilityStatus.AVAILABLE
    assert analysis.scenario_results == ()
    assert analysis.decision_evaluations == ()
    assert analysis.priority_actions == ()


def test_pricing_scenario_ids_must_be_unique() -> None:
    request = PriceScenarioRequest("same", Decimal("100"))
    with pytest.raises(DataValidationError) as error:
        _service().simulate(SkuId("DEMO-001"), (request, request))
    assert error.value.code == "services.duplicate_pricing_scenario"


def test_missing_economics_returns_typed_partial_pricing_result() -> None:
    class MissingEconomics:
        def get_unit_economics(self, sku):
            return ProviderValue(None)

    analysis = _service(MissingEconomics()).simulate(
        SkuId("DEMO-001"),
        (PriceScenarioRequest("candidate", Decimal("1000")),),
    )

    assert analysis.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert analysis.source_input is None
    assert analysis.scenario_results == ()
    assert analysis.priority_actions == ()
    assert analysis.issues[0].code == "services.missing_unit_economics"


def test_provider_error_and_sku_contamination_propagate() -> None:
    class FailingEconomics:
        def get_unit_economics(self, sku):
            raise ProviderError("Economics source unavailable.")

    with pytest.raises(ProviderError):
        _service(FailingEconomics()).simulate(SkuId("DEMO-001"), ())

    class WrongEconomics:
        def get_unit_economics(self, sku):
            return LocalUnitEconomicsProvider().get_unit_economics(SkuId("DEMO-002"))

    with pytest.raises(DataValidationError) as error:
        _service(WrongEconomics()).simulate(SkuId("DEMO-001"), ())
    assert error.value.code == "services.sku_mismatch"


def test_pricing_service_calls_completed_simulator_and_scenario_rules(monkeypatch) -> None:
    calls = {"simulate": 0, "rules": 0, "priority": 0}

    def wrap(name, original):
        def recorded(*args, **kwargs):
            calls[name] += 1
            return original(*args, **kwargs)
        return recorded

    monkeypatch.setattr(
        pricing_module,
        "simulate_prices",
        wrap("simulate", pricing_module.simulate_prices),
    )
    monkeypatch.setattr(
        pricing_module,
        "evaluate_pricing_scenario_rules",
        wrap("rules", pricing_module.evaluate_pricing_scenario_rules),
    )
    monkeypatch.setattr(
        pricing_module,
        "build_priority_action_center",
        wrap("priority", pricing_module.build_priority_action_center),
    )

    _service().simulate(
        SkuId("DEMO-001"),
        (PriceScenarioRequest("candidate", Decimal("100")),),
    )

    assert calls == {"simulate": 1, "rules": 1, "priority": 1}


def test_authoritative_money_request_rejects_float() -> None:
    with pytest.raises(DataValidationError):
        PriceScenarioRequest("candidate", 100.0)


def test_pricing_service_contains_no_formula_or_external_dependency() -> None:
    source = __import__("pathlib").Path(pricing_module.__file__).read_text(encoding="utf-8").lower()
    for token in ("streamlit", "fastapi", "openai", "app.ai", "http", "requests."):
        assert token not in source
    assert "calculate_unit_economics" not in source
    assert "minimum_safe_price =" not in source

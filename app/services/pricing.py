"""Read-only orchestration for hypothetical pricing scenarios."""

from __future__ import annotations

from collections.abc import Iterable

from app.analytics.pricing import simulate_prices
from app.core.clock import Clock, require_aware_datetime
from app.core.errors import DataValidationError
from app.decisions.pricing_rules import evaluate_pricing_scenario_rules
from app.decisions.prioritizer import build_priority_action_center
from app.domain.common import (
    AvailabilityStatus,
    Provenance,
    Severity,
    SkuId,
    SourceType,
    ValidationIssue,
    require_instance,
)
from app.domain.economics import EconomicsPolicy, PricingScenario, UnitEconomicsInput
from app.domain.recommendations import PriorityPolicy
from app.providers.contracts import ProviderValue, UnitEconomicsProvider
from app.services.models import PriceScenarioRequest, PricingAnalysis


class PricingService:
    """Load one economics input and reuse the completed what-if engine."""

    __slots__ = ("_clock", "_economics", "_economics_policy", "_priority_policy")

    def __init__(
        self,
        economics: UnitEconomicsProvider,
        clock: Clock,
        economics_policy: EconomicsPolicy,
        priority_policy: PriorityPolicy,
    ) -> None:
        if not isinstance(economics, UnitEconomicsProvider):
            raise DataValidationError(
                "economics provider does not implement the read contract",
                code="services.invalid_economics_provider",
                scope="economics provider",
            )
        if not callable(getattr(clock, "now", None)):
            raise DataValidationError(
                "clock must provide now()",
                code="services.invalid_clock",
                scope="clock",
            )
        require_instance(economics_policy, EconomicsPolicy, field_name="economics policy")
        require_instance(priority_policy, PriorityPolicy, field_name="priority policy")
        self._economics = economics
        self._clock = clock
        self._economics_policy = economics_policy
        self._priority_policy = priority_policy

    def simulate(
        self,
        sku: SkuId,
        requests: Iterable[PriceScenarioRequest],
    ) -> PricingAnalysis:
        """Evaluate supplied hypothetical prices without changing source data."""

        require_instance(sku, SkuId, field_name="pricing SKU")
        if isinstance(requests, (str, bytes, PriceScenarioRequest)):
            raise DataValidationError(
                "pricing requests must be a collection",
                code="services.invalid_pricing_requests",
                scope="pricing requests",
            )
        try:
            normalized_requests = tuple(requests)
        except TypeError as exc:
            raise DataValidationError(
                "pricing requests must be a collection",
                code="services.invalid_pricing_requests",
                scope="pricing requests",
            ) from exc
        for request in normalized_requests:
            require_instance(request, PriceScenarioRequest, field_name="pricing request")
        scenario_ids = tuple(request.scenario_id for request in normalized_requests)
        if len(set(scenario_ids)) != len(scenario_ids):
            raise DataValidationError(
                "pricing scenario identifiers must be unique",
                code="services.duplicate_pricing_scenario",
                scope="pricing requests",
            )

        analysis_timestamp = require_aware_datetime(
            self._clock.now(),
            field_name="pricing clock timestamp",
        )
        provider_value = self._economics.get_unit_economics(sku)
        provider_value = require_instance(
            provider_value,
            ProviderValue,
            field_name="economics provider value",
        )
        issues = tuple(provider_value.issues)
        if provider_value.value is None:
            missing = ValidationIssue(
                code="services.missing_unit_economics",
                message="Unit economics is unavailable for this SKU.",
                severity=Severity.WARNING,
                scope="unit_economics",
                sku=sku,
            )
            return PricingAnalysis(
                sku=sku,
                analysis_timestamp=analysis_timestamp,
                status=AvailabilityStatus.INSUFFICIENT_DATA,
                source_input=None,
                economics_policy=self._economics_policy,
                priority_policy=self._priority_policy,
                scenario_results=(),
                decision_evaluations=(),
                priority_actions=(),
                issues=tuple(dict.fromkeys((*issues, missing))),
                provenance=(),
            )
        source = require_instance(
            provider_value.value,
            UnitEconomicsInput,
            field_name="unit economics input",
        )
        if source.sku != sku:
            raise DataValidationError(
                "unit-economics input does not match requested SKU",
                code="services.sku_mismatch",
                scope="unit economics",
            )

        scenarios = tuple(
            PricingScenario(
                scenario_id=request.scenario_id,
                sku=sku,
                hypothetical_price=request.hypothetical_price,
                currency=source.currency,
                as_of=analysis_timestamp,
                is_hypothetical=True,
                provenance=Provenance(
                    source_type=SourceType.MANUAL,
                    provider="pricing_service",
                    ingested_at=analysis_timestamp,
                    source_timestamp=analysis_timestamp,
                    source_record_id=f"pricing-scenario:{request.scenario_id}",
                ),
            )
            for request in normalized_requests
        )
        results = simulate_prices(source, self._economics_policy, scenarios)
        evaluations = tuple(
            evaluate_pricing_scenario_rules(result, analysis_timestamp)
            for result in results
        )
        actions = build_priority_action_center(evaluations, self._priority_policy)
        provenance = (source.provenance, *(scenario.provenance for scenario in scenarios))
        return PricingAnalysis(
            sku=sku,
            analysis_timestamp=analysis_timestamp,
            status=AvailabilityStatus.AVAILABLE,
            source_input=source,
            economics_policy=self._economics_policy,
            priority_policy=self._priority_policy,
            scenario_results=results,
            decision_evaluations=evaluations,
            priority_actions=actions,
            issues=tuple(dict.fromkeys(issues)),
            provenance=tuple(dict.fromkeys(provenance)),
        )

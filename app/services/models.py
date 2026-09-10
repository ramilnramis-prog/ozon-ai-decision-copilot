"""Immutable application-service inputs and aggregate results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.analytics.inventory import InventoryAnalysisResult
from app.analytics.sales import SalesMetrics
from app.core.clock import require_aware_datetime
from app.core.errors import DataValidationError
from app.domain.catalog import Product, SalesPolicy
from app.domain.common import (
    AvailabilityStatus,
    PolicyIdentity,
    Provenance,
    SkuId,
    ValidationIssue,
    require_calendar_date,
    require_instance,
    require_non_negative_int,
    require_positive_decimal,
    require_positive_int,
    require_text,
)
from app.domain.economics import (
    EconomicsPolicy,
    PricingScenarioResult,
    UnitEconomicsInput,
    UnitEconomicsResult,
)
from app.domain.inventory import InventoryPolicy, LeadTime, ReplenishmentConstraints
from app.domain.recommendations import PriorityAction, PriorityPolicy
from app.decisions.common import DecisionEvaluation


def _normalized_tuple(values: object, expected_type: type, *, field_name: str) -> tuple:
    if isinstance(values, (str, bytes)):
        raise DataValidationError(
            f"{field_name} must be a collection",
            code="services.invalid_collection",
            scope=field_name,
        )
    try:
        normalized = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise DataValidationError(
            f"{field_name} must be a collection",
            code="services.invalid_collection",
            scope=field_name,
        ) from exc
    for value in normalized:
        require_instance(value, expected_type, field_name=field_name)
    return normalized


@dataclass(frozen=True, slots=True)
class InventoryPolicyConfig:
    """Run-wide inventory policy values combined with provider-owned SKU inputs."""

    identity: PolicyIdentity
    target_coverage_days: int
    warning_window_days: int
    overstock_threshold_days: int

    def __post_init__(self) -> None:
        require_instance(self.identity, PolicyIdentity, field_name="inventory policy identity")
        require_positive_int(self.target_coverage_days, field_name="target coverage days")
        require_non_negative_int(self.warning_window_days, field_name="warning window days")
        require_positive_int(
            self.overstock_threshold_days,
            field_name="overstock threshold days",
        )
        if self.overstock_threshold_days <= self.target_coverage_days:
            raise DataValidationError(
                "overstock threshold must exceed target coverage",
                code="inventory.inverted_coverage_thresholds",
                scope="overstock_threshold_days",
            )

    def for_sku(
        self,
        lead_time: LeadTime,
        constraints: ReplenishmentConstraints,
    ) -> InventoryPolicy:
        """Build the existing domain policy using explicit provider-owned values."""

        require_instance(lead_time, LeadTime, field_name="lead time")
        require_instance(
            constraints,
            ReplenishmentConstraints,
            field_name="replenishment constraints",
        )
        return InventoryPolicy(
            identity=self.identity,
            lead_time=lead_time,
            target_coverage_days=self.target_coverage_days,
            warning_window_days=self.warning_window_days,
            overstock_threshold_days=self.overstock_threshold_days,
            minimum_order_quantity=constraints.minimum_order_quantity,
            pack_size=constraints.pack_size,
        )


@dataclass(frozen=True, slots=True)
class AnalysisConfiguration:
    """Exact dated policies used by one analysis service."""

    as_of_date: date
    sales_policy: SalesPolicy
    inventory_policy: InventoryPolicyConfig
    economics_policy: EconomicsPolicy
    priority_policy: PriorityPolicy

    def __post_init__(self) -> None:
        require_calendar_date(self.as_of_date, field_name="analysis as-of date")
        require_instance(self.sales_policy, SalesPolicy, field_name="sales policy")
        require_instance(
            self.inventory_policy,
            InventoryPolicyConfig,
            field_name="inventory policy configuration",
        )
        require_instance(
            self.economics_policy,
            EconomicsPolicy,
            field_name="economics policy",
        )
        require_instance(self.priority_policy, PriorityPolicy, field_name="priority policy")


@dataclass(frozen=True, slots=True)
class SkuAnalysis:
    """One SKU's composed source, calculation, and decision results."""

    sku: SkuId
    analysis_timestamp: datetime
    status: AvailabilityStatus
    product: Product | None
    sales: SalesMetrics | None
    inventory: InventoryAnalysisResult | None
    economics: UnitEconomicsResult | None
    decision_evaluations: tuple[DecisionEvaluation, ...]
    issues: tuple[ValidationIssue, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        require_instance(self.sku, SkuId, field_name="SKU analysis sku")
        require_aware_datetime(
            self.analysis_timestamp,
            field_name="SKU analysis timestamp",
        )
        require_instance(self.status, AvailabilityStatus, field_name="SKU analysis status")
        for field_name, value, expected_type in (
            ("product", self.product, Product),
            ("sales", self.sales, SalesMetrics),
            ("inventory", self.inventory, InventoryAnalysisResult),
            ("economics", self.economics, UnitEconomicsResult),
        ):
            if value is not None:
                require_instance(value, expected_type, field_name=field_name)
                if value.sku != self.sku:
                    raise DataValidationError(
                        "SKU analysis components must belong to the same SKU",
                        code="services.sku_mismatch",
                        scope=field_name,
                    )

        evaluations = _normalized_tuple(
            self.decision_evaluations,
            DecisionEvaluation,
            field_name="decision evaluations",
        )
        for evaluation in evaluations:
            if evaluation.sku != self.sku:
                raise DataValidationError(
                    "decision evaluation does not match the analyzed SKU",
                    code="services.sku_mismatch",
                    scope="decision evaluations",
                )
            if evaluation.analysis_timestamp != self.analysis_timestamp:
                raise DataValidationError(
                    "decision evaluations must use the analysis timestamp",
                    code="services.mixed_analysis_timestamps",
                    scope="decision evaluations",
                )

        issues = _normalized_tuple(
            self.issues,
            ValidationIssue,
            field_name="SKU analysis issues",
        )
        if any(issue.sku not in (None, self.sku) for issue in issues):
            raise DataValidationError(
                "SKU analysis issue belongs to another SKU",
                code="services.sku_mismatch",
                scope="SKU analysis issues",
            )
        provenance = _normalized_tuple(
            self.provenance,
            Provenance,
            field_name="SKU analysis provenance",
        )
        if len(set(provenance)) != len(provenance):
            raise DataValidationError(
                "SKU analysis provenance must not contain duplicates",
                code="services.duplicate_provenance",
                scope="SKU analysis provenance",
            )

        complete = all(
            value is not None
            for value in (self.product, self.sales, self.inventory, self.economics)
        )
        if self.status is AvailabilityStatus.AVAILABLE and not complete:
            raise DataValidationError(
                "available SKU analysis requires every primary result",
                code="services.incomplete_available_analysis",
                scope="SKU analysis",
            )
        if self.status is AvailabilityStatus.INVALID and not issues:
            raise DataValidationError(
                "invalid SKU analysis requires a validation issue",
                code="services.invalid_analysis_without_issue",
                scope="SKU analysis",
            )

        object.__setattr__(self, "decision_evaluations", evaluations)
        object.__setattr__(self, "issues", issues)
        object.__setattr__(self, "provenance", provenance)


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    """Immutable aggregate returned by one full, coherent analysis run."""

    analysis_timestamp: datetime
    as_of_date: date
    configuration: AnalysisConfiguration
    sku_results: tuple[SkuAnalysis, ...]
    priority_actions: tuple[PriorityAction, ...]
    issues: tuple[ValidationIssue, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        require_aware_datetime(self.analysis_timestamp, field_name="analysis timestamp")
        require_calendar_date(self.as_of_date, field_name="analysis as-of date")
        require_instance(
            self.configuration,
            AnalysisConfiguration,
            field_name="analysis configuration",
        )
        if self.configuration.as_of_date != self.as_of_date:
            raise DataValidationError(
                "snapshot and configuration as-of dates must match",
                code="services.as_of_mismatch",
                scope="analysis snapshot",
            )
        results = _normalized_tuple(
            self.sku_results,
            SkuAnalysis,
            field_name="SKU analysis results",
        )
        if any(result.analysis_timestamp != self.analysis_timestamp for result in results):
            raise DataValidationError(
                "all SKU results must share the snapshot timestamp",
                code="services.mixed_analysis_timestamps",
                scope="analysis snapshot",
            )
        skus = tuple(result.sku for result in results)
        if len(set(skus)) != len(skus):
            raise DataValidationError(
                "analysis snapshot must contain at most one result per SKU",
                code="services.duplicate_sku_result",
                scope="analysis snapshot",
            )
        actions = _normalized_tuple(
            self.priority_actions,
            PriorityAction,
            field_name="priority actions",
        )
        if any(
            action.recommendation.analysis_timestamp != self.analysis_timestamp
            for action in actions
        ):
            raise DataValidationError(
                "priority actions must share the snapshot timestamp",
                code="services.mixed_analysis_timestamps",
                scope="priority actions",
            )
        recommendations = tuple(
            recommendation
            for result in results
            for evaluation in result.decision_evaluations
            for recommendation in evaluation.recommendations
        )
        if tuple(
            sorted(action.recommendation.recommendation_id for action in actions)
        ) != tuple(
            sorted(recommendation.recommendation_id for recommendation in recommendations)
        ):
            raise DataValidationError(
                "priority actions must preserve every snapshot recommendation",
                code="services.broken_action_traceability",
                scope="priority actions",
            )
        issues = _normalized_tuple(
            self.issues,
            ValidationIssue,
            field_name="analysis issues",
        )
        provenance = _normalized_tuple(
            self.provenance,
            Provenance,
            field_name="analysis provenance",
        )
        if len(set(provenance)) != len(provenance):
            raise DataValidationError(
                "analysis provenance must not contain duplicates",
                code="services.duplicate_provenance",
                scope="analysis provenance",
            )
        object.__setattr__(self, "sku_results", results)
        object.__setattr__(self, "priority_actions", actions)
        object.__setattr__(self, "issues", issues)
        object.__setattr__(self, "provenance", provenance)


@dataclass(frozen=True, slots=True)
class PriceScenarioRequest:
    """Validated, transport-neutral request for one hypothetical price."""

    scenario_id: str
    hypothetical_price: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "scenario_id",
            require_text(self.scenario_id, field_name="scenario id"),
        )
        object.__setattr__(
            self,
            "hypothetical_price",
            require_positive_decimal(
                self.hypothetical_price,
                field_name="hypothetical price",
            ),
        )


@dataclass(frozen=True, slots=True)
class PricingAnalysis:
    """Read-only aggregate for requested pricing scenarios."""

    sku: SkuId
    analysis_timestamp: datetime
    status: AvailabilityStatus
    source_input: UnitEconomicsInput | None
    economics_policy: EconomicsPolicy
    priority_policy: PriorityPolicy
    scenario_results: tuple[PricingScenarioResult, ...]
    decision_evaluations: tuple[DecisionEvaluation, ...]
    priority_actions: tuple[PriorityAction, ...]
    issues: tuple[ValidationIssue, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        require_instance(self.sku, SkuId, field_name="pricing analysis sku")
        require_aware_datetime(self.analysis_timestamp, field_name="pricing timestamp")
        require_instance(self.status, AvailabilityStatus, field_name="pricing status")
        if self.source_input is not None:
            require_instance(
                self.source_input,
                UnitEconomicsInput,
                field_name="pricing source input",
            )
            if self.source_input.sku != self.sku:
                raise DataValidationError(
                    "pricing source input does not match requested SKU",
                    code="services.sku_mismatch",
                    scope="pricing source input",
                )
        elif self.status is AvailabilityStatus.AVAILABLE:
            raise DataValidationError(
                "available pricing analysis requires economics input",
                code="services.incomplete_available_analysis",
                scope="pricing analysis",
            )
        require_instance(self.economics_policy, EconomicsPolicy, field_name="economics policy")
        require_instance(self.priority_policy, PriorityPolicy, field_name="priority policy")

        results = _normalized_tuple(
            self.scenario_results,
            PricingScenarioResult,
            field_name="pricing scenario results",
        )
        if any(
            result.scenario.sku != self.sku
            or result.scenario.as_of != self.analysis_timestamp
            for result in results
        ):
            raise DataValidationError(
                "pricing scenario result does not match the SKU and analysis timestamp",
                code="services.pricing_result_mismatch",
                scope="pricing scenario results",
            )
        evaluations = _normalized_tuple(
            self.decision_evaluations,
            DecisionEvaluation,
            field_name="pricing decision evaluations",
        )
        if any(
            evaluation.sku != self.sku
            or evaluation.analysis_timestamp != self.analysis_timestamp
            for evaluation in evaluations
        ):
            raise DataValidationError(
                "pricing decisions must match the SKU and analysis timestamp",
                code="services.pricing_decision_mismatch",
                scope="pricing decision evaluations",
            )
        actions = _normalized_tuple(
            self.priority_actions,
            PriorityAction,
            field_name="pricing priority actions",
        )
        if any(
            action.recommendation.sku != self.sku
            or action.recommendation.analysis_timestamp != self.analysis_timestamp
            for action in actions
        ):
            raise DataValidationError(
                "pricing actions must match the SKU and analysis timestamp",
                code="services.pricing_action_mismatch",
                scope="pricing priority actions",
            )
        if tuple(
            sorted(action.recommendation.recommendation_id for action in actions)
        ) != tuple(
            sorted(
                recommendation.recommendation_id
                for evaluation in evaluations
                for recommendation in evaluation.recommendations
            )
        ):
            raise DataValidationError(
                "pricing actions must preserve every scenario recommendation",
                code="services.broken_action_traceability",
                scope="pricing priority actions",
            )
        issues = _normalized_tuple(
            self.issues,
            ValidationIssue,
            field_name="pricing issues",
        )
        provenance = _normalized_tuple(
            self.provenance,
            Provenance,
            field_name="pricing provenance",
        )
        if len(set(provenance)) != len(provenance):
            raise DataValidationError(
                "pricing provenance must not contain duplicates",
                code="services.duplicate_provenance",
                scope="pricing provenance",
            )
        object.__setattr__(self, "scenario_results", results)
        object.__setattr__(self, "decision_evaluations", evaluations)
        object.__setattr__(self, "priority_actions", actions)
        object.__setattr__(self, "issues", issues)
        object.__setattr__(self, "provenance", provenance)

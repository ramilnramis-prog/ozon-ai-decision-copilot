"""Deterministic orchestration for marketplace analysis snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from app.analytics.inventory import InventoryAnalysisResult, calculate_inventory_analysis
from app.analytics.sales import SalesMetrics, calculate_sales_metrics
from app.analytics.unit_economics import calculate_unit_economics
from app.core.clock import Clock, require_aware_datetime
from app.core.errors import CalculationPreconditionError, DataValidationError
from app.decisions.common import DecisionEvaluation
from app.decisions.inventory_rules import evaluate_inventory_rules
from app.decisions.pricing_rules import evaluate_economics_rules
from app.decisions.prioritizer import build_priority_action_center
from app.decisions.sales_rules import evaluate_sales_rules
from app.domain.catalog import Product, SalesObservation
from app.domain.common import (
    AvailabilityStatus,
    Provenance,
    Severity,
    SkuId,
    ValidationIssue,
    require_instance,
)
from app.domain.economics import UnitEconomicsInput, UnitEconomicsResult
from app.domain.inventory import (
    InventorySnapshot,
    InboundSupply,
    LeadTime,
    ReplenishmentConstraints,
)
from app.providers.contracts import (
    MarketplaceReadProvider,
    ProviderBatch,
    ProviderValue,
    UnitEconomicsProvider,
)
from app.services.models import AnalysisConfiguration, AnalysisSnapshot, SkuAnalysis


RECOVERABLE_PER_SKU_ERROR_CODES = frozenset(
    {
        "sales.duplicate_observation_date",
    }
)


def _issue_key(issue: ValidationIssue) -> tuple[str, str, str, str, str, str]:
    return (
        issue.code,
        issue.scope,
        "" if issue.sku is None else str(issue.sku),
        issue.field_name or "",
        issue.severity.value,
        issue.message,
    )


def _normalized_issues(issues: Iterable[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    unique: dict[ValidationIssue, None] = {}
    for issue in issues:
        require_instance(issue, ValidationIssue, field_name="provider issue")
        unique.setdefault(issue, None)
    return tuple(sorted(unique, key=_issue_key))


def _provenance_key(value: Provenance) -> tuple[str, str, str, str, str]:
    return (
        value.source_type.value,
        value.provider,
        value.ingested_at.isoformat(),
        "" if value.source_timestamp is None else value.source_timestamp.isoformat(),
        value.source_record_id or "",
    )


def _normalized_provenance(values: Iterable[Provenance]) -> tuple[Provenance, ...]:
    unique: dict[Provenance, None] = {}
    for value in values:
        require_instance(value, Provenance, field_name="source provenance")
        unique.setdefault(value, None)
    return tuple(sorted(unique, key=_provenance_key))


def _require_batch(value: object, *, field_name: str) -> ProviderBatch:
    return require_instance(value, ProviderBatch, field_name=field_name)


def _require_value(value: object, *, field_name: str) -> ProviderValue:
    return require_instance(value, ProviderValue, field_name=field_name)


def _index_unique(records: Iterable[object], expected_type: type, *, field_name: str) -> dict:
    indexed: dict = {}
    for record in records:
        require_instance(record, expected_type, field_name=field_name)
        sku = record.sku
        if sku in indexed:
            raise DataValidationError(
                f"provider returned duplicate {field_name} for one SKU",
                code="services.duplicate_provider_record",
                scope=field_name,
            )
        indexed[sku] = record
    return indexed


def _group_by_sku(records: Iterable[object], expected_type: type, *, field_name: str) -> dict:
    grouped: dict[SkuId, list] = {}
    for record in records:
        require_instance(record, expected_type, field_name=field_name)
        grouped.setdefault(record.sku, []).append(record)
    return {sku: tuple(values) for sku, values in grouped.items()}


def _missing_issue(sku: SkuId, component: str) -> ValidationIssue:
    return ValidationIssue(
        code=f"services.missing_{component}",
        message=f"{component.replace('_', ' ').capitalize()} is unavailable for this SKU.",
        severity=Severity.WARNING,
        scope=component,
        sku=sku,
        field_name=None,
    )


def _recoverable_calculation_issue(
    error: CalculationPreconditionError,
    sku: SkuId,
    component: str,
) -> ValidationIssue:
    """Convert only explicitly approved per-SKU source failures to issues."""

    if error.code not in RECOVERABLE_PER_SKU_ERROR_CODES:
        raise error

    return ValidationIssue(
        code=error.code,
        message=error.safe_message,
        severity=Severity.WARNING,
        scope=error.scope or component,
        sku=sku,
    )


def _assert_catalog_membership(
    grouped_or_indexed: dict[SkuId, object],
    catalog_skus: set[SkuId],
    *,
    field_name: str,
) -> None:
    if any(sku not in catalog_skus for sku in grouped_or_indexed):
        raise DataValidationError(
            f"provider returned {field_name} for an unknown catalog SKU",
            code="services.unknown_provider_sku",
            scope=field_name,
        )


@dataclass(frozen=True, slots=True)
class _SkuWork:
    result: SkuAnalysis
    evaluations: tuple[DecisionEvaluation, ...]
    issues: tuple[ValidationIssue, ...]
    provenance: tuple[Provenance, ...]


class AnalysisService:
    """Compose normalized reads and completed deterministic components."""

    __slots__ = ("_clock", "_configuration", "_economics", "_marketplace")

    def __init__(
        self,
        marketplace: MarketplaceReadProvider,
        economics: UnitEconomicsProvider,
        clock: Clock,
        configuration: AnalysisConfiguration,
    ) -> None:
        if not isinstance(marketplace, MarketplaceReadProvider):
            raise DataValidationError(
                "marketplace provider does not implement the read contract",
                code="services.invalid_marketplace_provider",
                scope="marketplace provider",
            )
        if not isinstance(economics, UnitEconomicsProvider):
            raise DataValidationError(
                "economics provider does not implement the read contract",
                code="services.invalid_economics_provider",
                scope="economics provider",
            )
        require_instance(
            configuration,
            AnalysisConfiguration,
            field_name="analysis configuration",
        )
        if not callable(getattr(clock, "now", None)):
            raise DataValidationError(
                "clock must provide now()",
                code="services.invalid_clock",
                scope="clock",
            )
        self._marketplace = marketplace
        self._economics = economics
        self._clock = clock
        self._configuration = configuration

    @property
    def configuration(self) -> AnalysisConfiguration:
        return self._configuration

    def analyze(self, sku_ids: Iterable[SkuId] | None = None) -> AnalysisSnapshot:
        """Analyze active products by default, or an exact explicit selection."""

        analysis_timestamp = require_aware_datetime(
            self._clock.now(),
            field_name="analysis clock timestamp",
        )
        products_batch = _require_batch(
            self._marketplace.get_products(),
            field_name="products batch",
        )
        sales_batch = _require_batch(
            self._marketplace.get_sales_observations(),
            field_name="sales batch",
        )
        inventory_batch = _require_batch(
            self._marketplace.get_inventory_snapshots(),
            field_name="inventory batch",
        )
        inbound_batch = _require_batch(
            self._marketplace.get_inbound_supplies(),
            field_name="inbound batch",
        )

        products = _index_unique(
            products_batch.records,
            Product,
            field_name="product",
        )
        sales = _group_by_sku(
            sales_batch.records,
            SalesObservation,
            field_name="sales observation",
        )
        inventories = _index_unique(
            inventory_batch.records,
            InventorySnapshot,
            field_name="inventory snapshot",
        )
        inbound = _group_by_sku(
            inbound_batch.records,
            InboundSupply,
            field_name="inbound supply",
        )
        catalog_skus = set(products)
        _assert_catalog_membership(sales, catalog_skus, field_name="sales observation")
        _assert_catalog_membership(inventories, catalog_skus, field_name="inventory snapshot")
        _assert_catalog_membership(inbound, catalog_skus, field_name="inbound supply")

        selected = self._selected_skus(sku_ids, products)
        snapshot_issues: list[ValidationIssue] = [
            *products_batch.issues,
            *sales_batch.issues,
            *inventory_batch.issues,
            *inbound_batch.issues,
        ]
        work_items: list[_SkuWork] = []
        for sku in selected:
            work = self._analyze_sku(
                sku=sku,
                product=products[sku],
                observations=sales.get(sku, ()),
                inventory_snapshot=inventories.get(sku),
                inbound_supplies=inbound.get(sku, ()),
                inherited_issues=tuple(
                    issue
                    for issue in snapshot_issues
                    if issue.sku is not None and issue.sku == sku
                ),
                analysis_timestamp=analysis_timestamp,
            )
            work_items.append(work)
            snapshot_issues.extend(work.issues)

        evaluations = tuple(
            evaluation
            for work in work_items
            for evaluation in work.evaluations
        )
        actions = build_priority_action_center(
            evaluations,
            self._configuration.priority_policy,
        )
        provenance = _normalized_provenance(
            value
            for work in work_items
            for value in work.provenance
        )
        return AnalysisSnapshot(
            analysis_timestamp=analysis_timestamp,
            as_of_date=self._configuration.as_of_date,
            configuration=self._configuration,
            sku_results=tuple(work.result for work in work_items),
            priority_actions=actions,
            issues=_normalized_issues(snapshot_issues),
            provenance=provenance,
        )

    def _selected_skus(
        self,
        requested: Iterable[SkuId] | None,
        products: dict[SkuId, Product],
    ) -> tuple[SkuId, ...]:
        if requested is None:
            return tuple(
                sorted(
                    (sku for sku, product in products.items() if product.active),
                    key=str,
                )
            )
        if isinstance(requested, (str, bytes, SkuId)):
            raise DataValidationError(
                "requested SKUs must be a collection of SkuId values",
                code="services.invalid_sku_selection",
                scope="requested SKUs",
            )
        try:
            selected = tuple(requested)
        except TypeError as exc:
            raise DataValidationError(
                "requested SKUs must be a collection of SkuId values",
                code="services.invalid_sku_selection",
                scope="requested SKUs",
            ) from exc
        for sku in selected:
            require_instance(sku, SkuId, field_name="requested SKU")
        if len(set(selected)) != len(selected):
            raise DataValidationError(
                "requested SKUs must not contain duplicates",
                code="services.duplicate_requested_sku",
                scope="requested SKUs",
            )
        unknown = tuple(sku for sku in selected if sku not in products)
        if unknown:
            raise DataValidationError(
                "requested SKU does not exist in the product catalog",
                code="services.unknown_sku",
                scope="requested SKUs",
                context={"skus": tuple(str(sku) for sku in unknown)},
            )
        return tuple(sorted(selected, key=str))

    def _analyze_sku(
        self,
        *,
        sku: SkuId,
        product: Product,
        observations: tuple[SalesObservation, ...],
        inventory_snapshot: InventorySnapshot | None,
        inbound_supplies: tuple[InboundSupply, ...],
        inherited_issues: tuple[ValidationIssue, ...],
        analysis_timestamp: datetime,
    ) -> _SkuWork:
        config = self._configuration
        local_issues: list[ValidationIssue] = list(inherited_issues)
        source_provenance: list[Provenance] = [
            product.provenance,
            *(record.provenance for record in observations),
            *(record.provenance for record in inbound_supplies),
        ]

        sales_result: SalesMetrics | None = None
        sales_decision: DecisionEvaluation | None = None
        try:
            sales_result = calculate_sales_metrics(
                sku,
                observations,
                config.sales_policy,
                config.as_of_date,
            )
        except CalculationPreconditionError as error:
            local_issues.append(
                _recoverable_calculation_issue(error, sku, "sales")
            )
        if sales_result is not None:
            sales_decision = evaluate_sales_rules(sales_result, analysis_timestamp)

        lead_value = _require_value(
            self._marketplace.get_lead_time(sku),
            field_name="lead-time provider value",
        )
        constraint_value = _require_value(
            self._marketplace.get_replenishment_constraints(sku),
            field_name="replenishment-constraints provider value",
        )
        local_issues.extend(lead_value.issues)
        local_issues.extend(constraint_value.issues)
        lead_time = (
            None
            if lead_value.value is None
            else require_instance(lead_value.value, LeadTime, field_name="lead time")
        )
        constraints = (
            None
            if constraint_value.value is None
            else require_instance(
                constraint_value.value,
                ReplenishmentConstraints,
                field_name="replenishment constraints",
            )
        )

        inventory_result: InventoryAnalysisResult | None = None
        inventory_decision: DecisionEvaluation | None = None
        if inventory_snapshot is None:
            local_issues.append(_missing_issue(sku, "inventory_snapshot"))
        else:
            source_provenance.append(inventory_snapshot.provenance)
        if lead_time is None:
            local_issues.append(_missing_issue(sku, "lead_time"))
        else:
            source_provenance.append(lead_time.provenance)
        if constraints is None:
            local_issues.append(_missing_issue(sku, "replenishment_constraints"))
        else:
            if constraints.sku != sku:
                raise DataValidationError(
                    "replenishment constraints do not match requested SKU",
                    code="services.sku_mismatch",
                    scope="replenishment constraints",
                )
            source_provenance.append(constraints.provenance)

        if (
            sales_result is not None
            and inventory_snapshot is not None
            and lead_time is not None
            and constraints is not None
        ):
            inventory_policy = config.inventory_policy.for_sku(
                lead_time,
                constraints,
            )
            inventory_result = calculate_inventory_analysis(
                inventory_snapshot,
                sales_result,
                inbound_supplies,
                lead_time,
                constraints,
                inventory_policy,
                config.as_of_date,
            )
            if inventory_result is not None:
                inventory_decision = evaluate_inventory_rules(
                    inventory_result,
                    analysis_timestamp,
                )

        economics_value = _require_value(
            self._economics.get_unit_economics(sku),
            field_name="economics provider value",
        )
        local_issues.extend(economics_value.issues)
        economics_result: UnitEconomicsResult | None = None
        economics_decision: DecisionEvaluation | None = None
        if economics_value.value is None:
            local_issues.append(_missing_issue(sku, "unit_economics"))
        else:
            economics_input = require_instance(
                economics_value.value,
                UnitEconomicsInput,
                field_name="unit economics input",
            )
            if economics_input.sku != sku:
                raise DataValidationError(
                    "unit-economics input does not match requested SKU",
                    code="services.sku_mismatch",
                    scope="unit economics",
                )
            source_provenance.append(economics_input.provenance)
            economics_result = calculate_unit_economics(
                economics_input,
                config.economics_policy,
            )
            if economics_result is not None:
                economics_decision = evaluate_economics_rules(
                    economics_result,
                    analysis_timestamp,
                )

        evaluations = tuple(
            evaluation
            for evaluation in (sales_decision, inventory_decision, economics_decision)
            if evaluation is not None
        )
        normalized_local_issues = _normalized_issues(
            issue
            for issue in local_issues
            if issue.sku in (None, sku)
        )
        has_source_error = bool(inherited_issues) or any(
            issue.sku == sku and not issue.code.startswith("services.missing_")
            for issue in normalized_local_issues
        )
        complete = (
            sales_result is not None
            and inventory_result is not None
            and economics_result is not None
        )
        status = (
            AvailabilityStatus.INVALID
            if has_source_error
            else AvailabilityStatus.AVAILABLE
            if complete
            else AvailabilityStatus.INSUFFICIENT_DATA
        )
        provenance = _normalized_provenance(source_provenance)
        result = SkuAnalysis(
            sku=sku,
            analysis_timestamp=analysis_timestamp,
            status=status,
            product=product,
            sales=sales_result,
            inventory=inventory_result,
            economics=economics_result,
            decision_evaluations=evaluations,
            issues=normalized_local_issues,
            provenance=provenance,
        )
        return _SkuWork(result, evaluations, normalized_local_issues, provenance)

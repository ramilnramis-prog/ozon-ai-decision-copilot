"""Source-independent, read-only provider contracts.

Implementations return normalized domain models plus recoverable ``ValidationIssue``
objects. A provider-wide inability to read its source is reported by raising
``ProviderError``; malformed records may raise ``DataValidationError`` at the
boundary or be represented as per-record issues when a partial batch is usable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, runtime_checkable

from app.core.errors import DataValidationError
from app.domain.catalog import Product, SalesObservation
from app.domain.common import SkuId, ValidationIssue, require_instance
from app.domain.economics import UnitEconomicsInput
from app.domain.inventory import (
    InboundSupply,
    InventorySnapshot,
    LeadTime,
    ReplenishmentConstraints,
)


RecordT = TypeVar("RecordT")


def _normalize_issues(issues: tuple[ValidationIssue, ...]) -> tuple[ValidationIssue, ...]:
    if isinstance(issues, (str, bytes)):
        raise DataValidationError(
            "provider issues must be a collection",
            code="providers.invalid_issue_collection",
            scope="provider issues",
        )
    try:
        normalized = tuple(issues)
    except TypeError as exc:
        raise DataValidationError(
            "provider issues must be a collection",
            code="providers.invalid_issue_collection",
            scope="provider issues",
        ) from exc
    for issue in normalized:
        require_instance(issue, ValidationIssue, field_name="provider issue")
    return normalized


@dataclass(frozen=True, slots=True)
class ProviderBatch(Generic[RecordT]):
    """Immutable normalized records and any recoverable per-record issues."""

    records: tuple[RecordT, ...]
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.records, (str, bytes, Mapping)):
            raise DataValidationError(
                "provider records must be a collection of domain models",
                code="providers.invalid_record_collection",
                scope="provider records",
            )
        try:
            records = tuple(self.records)
        except TypeError as exc:
            raise DataValidationError(
                "provider records must be a collection of domain models",
                code="providers.invalid_record_collection",
                scope="provider records",
            ) from exc
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "issues", _normalize_issues(self.issues))


@dataclass(frozen=True, slots=True)
class ProviderValue(Generic[RecordT]):
    """Immutable optional normalized value and recoverable lookup issues."""

    value: RecordT | None
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", _normalize_issues(self.issues))


@runtime_checkable
class MarketplaceReadProvider(Protocol):
    """Read normalized marketplace operations data without source details."""

    def get_products(self) -> ProviderBatch[Product]:
        """Return product catalog records and recoverable record issues."""

        ...

    def get_sales_observations(self) -> ProviderBatch[SalesObservation]:
        """Return dated normalized sales records."""

        ...

    def get_inventory_snapshots(self) -> ProviderBatch[InventorySnapshot]:
        """Return normalized current inventory observations."""

        ...

    def get_inbound_supplies(self) -> ProviderBatch[InboundSupply]:
        """Return normalized known inbound supplies."""

        ...

    def get_lead_time(self, sku: SkuId) -> ProviderValue[LeadTime]:
        """Return provider-owned lead time for one SKU, when available."""

        ...

    def get_replenishment_constraints(
        self,
        sku: SkuId,
    ) -> ProviderValue[ReplenishmentConstraints]:
        """Return provider-owned per-SKU ordering constraints, when available."""

        ...


@runtime_checkable
class UnitEconomicsProvider(Protocol):
    """Read normalized economics inputs independently of marketplace data."""

    def get_unit_economics(self, sku: SkuId) -> ProviderValue[UnitEconomicsInput]:
        """Return normalized unit-economics inputs for one SKU, when available."""

        ...

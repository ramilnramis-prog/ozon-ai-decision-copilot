"""Catalog records, dated sales inputs, and sales-analysis policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.errors import DataValidationError
from app.domain.common import (
    Currency,
    PolicyIdentity,
    Provenance,
    SkuId,
    optional_text,
    require_calendar_date,
    require_instance,
    require_non_negative_decimal,
    require_non_negative_int,
    require_positive_decimal,
    require_positive_int,
    require_text,
)


@dataclass(frozen=True, slots=True)
class Product:
    """Product identity and display metadata, without calculated fields."""

    sku: SkuId
    name: str
    active: bool
    provenance: Provenance
    marketplace_id: str | None = None

    def __post_init__(self) -> None:
        require_instance(self.sku, SkuId, field_name="product sku")
        object.__setattr__(self, "name", require_text(self.name, field_name="product name"))
        if not isinstance(self.active, bool):
            raise DataValidationError(
                "product active status must be boolean",
                code="catalog.invalid_active_status",
                scope="active",
            )
        require_instance(self.provenance, Provenance, field_name="product provenance")
        object.__setattr__(
            self,
            "marketplace_id",
            optional_text(self.marketplace_id, field_name="marketplace_id"),
        )


@dataclass(frozen=True, slots=True)
class SalesObservation:
    """Dated units sold and optional exact revenue; no metrics are calculated here."""

    sku: SkuId
    observed_on: date
    units_sold: int
    provenance: Provenance
    revenue: Decimal | None = None
    currency: Currency | None = None

    def __post_init__(self) -> None:
        require_instance(self.sku, SkuId, field_name="sales sku")
        require_calendar_date(self.observed_on, field_name="sales observation date")
        require_non_negative_int(self.units_sold, field_name="units sold")
        require_instance(self.provenance, Provenance, field_name="sales provenance")

        if (self.revenue is None) != (self.currency is None):
            raise DataValidationError(
                "sales revenue and currency must be present or missing together",
                code="catalog.incomplete_revenue",
                scope="revenue",
            )
        if self.revenue is not None:
            object.__setattr__(
                self,
                "revenue",
                require_non_negative_decimal(self.revenue, field_name="sales revenue"),
            )
            require_instance(self.currency, Currency, field_name="sales currency")


@dataclass(frozen=True, slots=True)
class SalesPolicy:
    """Explicit sales-window and material-change inputs for future analytics."""

    identity: PolicyIdentity
    averaging_window_days: int
    comparison_window_days: int
    material_change_threshold: Decimal

    def __post_init__(self) -> None:
        require_instance(self.identity, PolicyIdentity, field_name="sales policy identity")
        require_positive_int(
            self.averaging_window_days, field_name="averaging window days"
        )
        require_positive_int(
            self.comparison_window_days, field_name="comparison window days"
        )
        object.__setattr__(
            self,
            "material_change_threshold",
            require_positive_decimal(
                self.material_change_threshold,
                field_name="material change threshold",
            ),
        )

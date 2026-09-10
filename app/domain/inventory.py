"""Inventory and supply inputs plus explicit replenishment policy values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from app.core.clock import require_aware_datetime
from app.core.errors import DataValidationError
from app.domain.common import (
    PolicyIdentity,
    Provenance,
    SkuId,
    require_calendar_date,
    require_instance,
    require_non_negative_int,
    require_positive_int,
)


class InboundStatus(str, Enum):
    """Whether inbound stock is confirmed by its source."""

    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    """Sellable stock observed at one explicit instant."""

    sku: SkuId
    sellable_stock: int
    observed_at: datetime
    provenance: Provenance

    def __post_init__(self) -> None:
        require_instance(self.sku, SkuId, field_name="inventory sku")
        require_non_negative_int(self.sellable_stock, field_name="sellable stock")
        require_aware_datetime(self.observed_at, field_name="inventory observed_at")
        require_instance(self.provenance, Provenance, field_name="inventory provenance")


@dataclass(frozen=True, slots=True)
class InboundSupply:
    """Known inbound quantity with explicit confirmation and arrival information."""

    sku: SkuId
    quantity: int
    status: InboundStatus
    expected_arrival: date | None
    provenance: Provenance

    def __post_init__(self) -> None:
        require_instance(self.sku, SkuId, field_name="inbound sku")
        require_non_negative_int(self.quantity, field_name="inbound quantity")
        require_instance(self.status, InboundStatus, field_name="inbound status")
        if self.expected_arrival is not None:
            require_calendar_date(
                self.expected_arrival, field_name="inbound expected arrival"
            )
        require_instance(self.provenance, Provenance, field_name="inbound provenance")


@dataclass(frozen=True, slots=True)
class LeadTime:
    """Supply timing inputs; None explicitly means the source value is unavailable."""

    production_days: int | None
    delivery_days: int | None
    safety_buffer_days: int | None
    provenance: Provenance

    def __post_init__(self) -> None:
        for field_name in ("production_days", "delivery_days", "safety_buffer_days"):
            value = getattr(self, field_name)
            if value is not None:
                require_non_negative_int(value, field_name=field_name)
        require_instance(self.provenance, Provenance, field_name="lead-time provenance")


@dataclass(frozen=True, slots=True)
class ReplenishmentConstraints:
    """Per-SKU source inputs that constrain a future replenishment quantity."""

    sku: SkuId
    minimum_order_quantity: int | None
    pack_size: int | None
    provenance: Provenance

    def __post_init__(self) -> None:
        require_instance(self.sku, SkuId, field_name="replenishment constraints sku")
        if self.minimum_order_quantity is not None:
            require_positive_int(
                self.minimum_order_quantity, field_name="minimum order quantity"
            )
        if self.pack_size is not None:
            require_positive_int(self.pack_size, field_name="pack size")
        require_instance(
            self.provenance,
            Provenance,
            field_name="replenishment constraints provenance",
        )


@dataclass(frozen=True, slots=True)
class InventoryPolicy:
    """Explicit supply-risk and replenishment constraints, with no hidden defaults."""

    identity: PolicyIdentity
    lead_time: LeadTime
    target_coverage_days: int
    warning_window_days: int
    overstock_threshold_days: int
    minimum_order_quantity: int | None
    pack_size: int | None

    def __post_init__(self) -> None:
        require_instance(self.identity, PolicyIdentity, field_name="inventory policy identity")
        require_instance(self.lead_time, LeadTime, field_name="inventory lead time")
        require_positive_int(self.target_coverage_days, field_name="target coverage days")
        require_non_negative_int(self.warning_window_days, field_name="warning window days")
        require_positive_int(
            self.overstock_threshold_days, field_name="overstock threshold days"
        )
        if self.overstock_threshold_days <= self.target_coverage_days:
            raise DataValidationError(
                "overstock threshold must exceed target coverage",
                code="inventory.inverted_coverage_thresholds",
                scope="overstock_threshold_days",
            )
        if self.minimum_order_quantity is not None:
            require_positive_int(
                self.minimum_order_quantity, field_name="minimum order quantity"
            )
        if self.pack_size is not None:
            require_positive_int(self.pack_size, field_name="pack size")

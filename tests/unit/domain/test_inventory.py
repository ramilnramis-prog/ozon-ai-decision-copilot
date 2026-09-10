"""Tests for inventory, inbound supply, lead time, and inventory policy."""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest

from app.core.errors import DataValidationError
from app.domain.common import PolicyIdentity, Provenance, SkuId, SourceType
from app.domain.inventory import (
    InboundStatus,
    InboundSupply,
    InventoryPolicy,
    InventorySnapshot,
    LeadTime,
)


def provenance() -> Provenance:
    return Provenance(
        SourceType.MOCK,
        "mock_marketplace",
        datetime(2026, 9, 2, 8, tzinfo=UTC),
    )


def lead_time(**overrides: int | None) -> LeadTime:
    values = {
        "production_days": 10,
        "delivery_days": 4,
        "safety_buffer_days": 3,
    }
    values.update(overrides)
    return LeadTime(provenance=provenance(), **values)


def test_inventory_snapshot_accepts_zero_stock_and_is_immutable() -> None:
    snapshot = InventorySnapshot(
        SkuId("SKU-001"),
        0,
        datetime(2026, 9, 2, 8, tzinfo=UTC),
        provenance(),
    )

    assert snapshot.sellable_stock == 0
    with pytest.raises(FrozenInstanceError):
        snapshot.sellable_stock = 1


@pytest.mark.parametrize("stock", [-1, True])
def test_inventory_snapshot_rejects_invalid_stock(stock: object) -> None:
    with pytest.raises(DataValidationError):
        InventorySnapshot(
            SkuId("SKU-001"),
            stock,
            datetime(2026, 9, 2, 8, tzinfo=UTC),
            provenance(),
        )


def test_inventory_snapshot_rejects_naive_timestamp() -> None:
    with pytest.raises(DataValidationError):
        InventorySnapshot(
            SkuId("SKU-001"), 1, datetime(2026, 9, 2, 8), provenance()
        )


def test_inbound_supply_preserves_confirmation_and_missing_arrival() -> None:
    inbound = InboundSupply(
        SkuId("SKU-001"),
        10,
        InboundStatus.UNCONFIRMED,
        None,
        provenance(),
    )

    assert inbound.status is InboundStatus.UNCONFIRMED
    assert inbound.expected_arrival is None


def test_inbound_supply_accepts_confirmed_arrival_date() -> None:
    inbound = InboundSupply(
        SkuId("SKU-001"),
        10,
        InboundStatus.CONFIRMED,
        date(2026, 9, 10),
        provenance(),
    )

    assert inbound.expected_arrival == date(2026, 9, 10)


def test_inbound_supply_rejects_negative_quantity_or_datetime_arrival() -> None:
    with pytest.raises(DataValidationError):
        InboundSupply(
            SkuId("SKU-001"), -1, InboundStatus.CONFIRMED, None, provenance()
        )
    with pytest.raises(DataValidationError):
        InboundSupply(
            SkuId("SKU-001"),
            1,
            InboundStatus.CONFIRMED,
            datetime(2026, 9, 10, tzinfo=UTC),
            provenance(),
        )


def test_missing_lead_time_values_remain_none() -> None:
    missing = LeadTime(None, None, None, provenance())

    assert missing.production_days is None
    assert missing.delivery_days is None
    assert missing.safety_buffer_days is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"production_days": -1},
        {"delivery_days": -1},
        {"safety_buffer_days": -1},
    ],
)
def test_lead_time_rejects_negative_values(overrides: dict[str, int]) -> None:
    with pytest.raises(DataValidationError):
        lead_time(**overrides)


def test_inventory_policy_accepts_explicit_constraints() -> None:
    policy = InventoryPolicy(
        PolicyIdentity("inventory-demo", "v1"),
        lead_time(),
        target_coverage_days=30,
        warning_window_days=5,
        overstock_threshold_days=90,
        minimum_order_quantity=20,
        pack_size=5,
    )

    assert policy.minimum_order_quantity == 20
    assert policy.pack_size == 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_coverage_days": 0},
        {"warning_window_days": -1},
        {"overstock_threshold_days": 30},
        {"minimum_order_quantity": 0},
        {"pack_size": 0},
    ],
)
def test_inventory_policy_rejects_invalid_or_inverted_values(
    overrides: dict[str, int]
) -> None:
    values = {
        "target_coverage_days": 30,
        "warning_window_days": 5,
        "overstock_threshold_days": 90,
        "minimum_order_quantity": 20,
        "pack_size": 5,
    }
    values.update(overrides)

    with pytest.raises(DataValidationError):
        InventoryPolicy(
            PolicyIdentity("inventory-demo", "v1"), lead_time(), **values
        )

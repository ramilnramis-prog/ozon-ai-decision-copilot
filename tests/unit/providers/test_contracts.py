"""Tests for source-independent provider read contracts."""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.core.errors import DataValidationError, ProviderError
from app.domain.catalog import Product, SalesObservation
from app.domain.common import (
    Currency,
    Provenance,
    Severity,
    SkuId,
    SourceType,
    ValidationIssue,
)
from app.domain.economics import UnitEconomicsInput
from app.domain.inventory import (
    InboundStatus,
    InboundSupply,
    InventorySnapshot,
    LeadTime,
    ReplenishmentConstraints,
)
from app.providers.contracts import (
    MarketplaceReadProvider,
    ProviderBatch,
    ProviderValue,
    UnitEconomicsProvider,
)


NOW = datetime(2026, 9, 2, 8, tzinfo=UTC)
SKU = SkuId("SKU-001")


def provenance() -> Provenance:
    return Provenance(SourceType.DEMO, "fixture_provider", NOW)


class FakeMarketplaceProvider:
    def get_products(self) -> ProviderBatch[Product]:
        return ProviderBatch((Product(SKU, "Demo product", True, provenance()),))

    def get_sales_observations(self) -> ProviderBatch[SalesObservation]:
        return ProviderBatch((SalesObservation(SKU, date(2026, 9, 1), 2, provenance()),))

    def get_inventory_snapshots(self) -> ProviderBatch[InventorySnapshot]:
        return ProviderBatch((InventorySnapshot(SKU, 10, NOW, provenance()),))

    def get_inbound_supplies(self) -> ProviderBatch[InboundSupply]:
        return ProviderBatch(
            (
                InboundSupply(
                    SKU,
                    5,
                    InboundStatus.CONFIRMED,
                    date(2026, 9, 5),
                    provenance(),
                ),
            )
        )

    def get_lead_time(self, sku: SkuId) -> ProviderValue[LeadTime]:
        assert sku == SKU
        return ProviderValue(LeadTime(2, 1, 1, provenance()))

    def get_replenishment_constraints(
        self,
        sku: SkuId,
    ) -> ProviderValue[ReplenishmentConstraints]:
        assert sku == SKU
        return ProviderValue(ReplenishmentConstraints(SKU, 10, 5, provenance()))


class FakeUnitEconomicsProvider:
    def get_unit_economics(self, sku: SkuId) -> ProviderValue[UnitEconomicsInput]:
        assert sku == SKU
        return ProviderValue(
            UnitEconomicsInput(
                SKU,
                Decimal("1000"),
                Currency.RUB,
                Decimal("300"),
                Decimal("100"),
                Decimal("0.15"),
                None,
                Decimal("50"),
                None,
                None,
                None,
                (),
                None,
                provenance(),
            )
        )


def test_minimal_fake_structurally_satisfies_marketplace_contract() -> None:
    provider = FakeMarketplaceProvider()

    assert isinstance(provider, MarketplaceReadProvider)
    assert isinstance(provider.get_products().records[0], Product)
    assert isinstance(provider.get_sales_observations().records[0], SalesObservation)
    assert isinstance(provider.get_inventory_snapshots().records[0], InventorySnapshot)
    assert isinstance(provider.get_inbound_supplies().records[0], InboundSupply)
    assert isinstance(provider.get_lead_time(SKU).value, LeadTime)
    constraints = provider.get_replenishment_constraints(SKU).value
    assert isinstance(constraints, ReplenishmentConstraints)
    assert constraints.minimum_order_quantity == 10
    assert constraints.pack_size == 5


def test_minimal_fake_structurally_satisfies_economics_contract() -> None:
    provider = FakeUnitEconomicsProvider()

    assert isinstance(provider, UnitEconomicsProvider)
    result = provider.get_unit_economics(SKU)
    assert isinstance(result.value, UnitEconomicsInput)
    assert isinstance(result.value.selling_price, Decimal)


def test_marketplace_and_economics_contracts_remain_separate() -> None:
    assert not isinstance(FakeMarketplaceProvider(), UnitEconomicsProvider)
    assert not isinstance(FakeUnitEconomicsProvider(), MarketplaceReadProvider)


def test_provider_results_are_immutable_and_preserve_typed_issues() -> None:
    issue = ValidationIssue(
        "source.missing",
        "One source record was unavailable.",
        Severity.WARNING,
        "SKU-002",
        SkuId("SKU-002"),
    )
    batch = ProviderBatch([Product(SKU, "Demo product", True, provenance())], [issue])
    value = ProviderValue(None, [issue])

    assert isinstance(batch.records, tuple)
    assert batch.issues == (issue,)
    assert value.issues == (issue,)
    with pytest.raises(FrozenInstanceError):
        batch.records = ()


def test_provider_results_reject_invalid_issue_or_record_collections() -> None:
    with pytest.raises(DataValidationError):
        ProviderBatch({"raw": "record"})
    with pytest.raises(DataValidationError):
        ProviderBatch((), ("not-an-issue",))
    with pytest.raises(DataValidationError):
        ProviderValue(None, "not-an-issue-collection")


def test_provider_wide_failure_uses_typed_provider_error() -> None:
    class UnavailableMarketplaceProvider(FakeMarketplaceProvider):
        def get_products(self) -> ProviderBatch[Product]:
            raise ProviderError("Marketplace source is unavailable")

    provider: MarketplaceReadProvider = UnavailableMarketplaceProvider()
    with pytest.raises(ProviderError):
        provider.get_products()

"""Tests for the read-only deterministic marketplace demo provider."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.core.errors import DataValidationError, ProviderError
from app.domain.catalog import Product, SalesObservation
from app.domain.common import SkuId, SourceType
from app.domain.inventory import (
    InboundStatus,
    InboundSupply,
    InventorySnapshot,
    LeadTime,
    ReplenishmentConstraints,
)
from app.providers.contracts import MarketplaceReadProvider
from app.providers.mock_ozon import MockOzonProvider


DEMO_ROOT = Path(__file__).resolve().parents[3] / "data" / "demo"
EXPECTED_SKUS = tuple(f"DEMO-{number:03d}" for number in range(1, 39))


def _load_source(relative_path: str) -> dict[str, object]:
    return json.loads((DEMO_ROOT / relative_path).read_text(encoding="utf-8"))


def _write_source(
    root: Path,
    relative_path: str,
    document: dict[str, object],
) -> None:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document), encoding="utf-8")


def test_mock_provider_structurally_satisfies_marketplace_contract() -> None:
    provider = MockOzonProvider()

    assert isinstance(provider, MarketplaceReadProvider)
    assert not hasattr(provider, "get_unit_economics")


def test_catalog_returns_normalized_products_in_stable_order() -> None:
    batch = MockOzonProvider().get_products()

    assert batch.issues == ()
    assert len(batch.records) == 38
    assert all(isinstance(record, Product) for record in batch.records)
    assert tuple(str(record.sku) for record in batch.records) == EXPECTED_SKUS
    assert all(record.provenance.source_type is SourceType.DEMO for record in batch.records)
    assert all(record.provenance.provider == "demo_dataset" for record in batch.records)
    assert all(record.provenance.source_record_id for record in batch.records)


def test_default_demo_root_does_not_depend_on_current_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    batch = MockOzonProvider().get_products()

    assert len(batch.records) == 38
    assert batch.issues == ()


def test_sales_expand_only_explicit_daily_source_observations() -> None:
    batch = MockOzonProvider().get_sales_observations()

    assert batch.issues == ()
    assert len(batch.records) == 1_041
    assert all(isinstance(record, SalesObservation) for record in batch.records)
    assert (str(batch.records[0].sku), batch.records[0].observed_on, batch.records[0].units_sold) == (
        "DEMO-001",
        date(2026, 8, 5),
        8,
    )
    zero_sales = tuple(record for record in batch.records if record.sku == SkuId("DEMO-011"))
    short_history = tuple(record for record in batch.records if record.sku == SkuId("DEMO-037"))
    assert len(zero_sales) == 28
    assert all(record.units_sold == 0 for record in zero_sales)
    assert tuple(record.observed_on for record in short_history) == tuple(
        date(2026, 8, 28) + timedelta(days=offset)
        for offset in range(5)
    )
    assert not hasattr(batch.records[0], "average_daily_sales")
    assert not hasattr(batch.records[0], "sales_change")


def test_inventory_preserves_zero_stock_and_aware_source_timestamps() -> None:
    batch = MockOzonProvider().get_inventory_snapshots()

    assert batch.issues == ()
    assert len(batch.records) == 38
    assert all(isinstance(record, InventorySnapshot) for record in batch.records)
    assert all(record.observed_at.utcoffset() is not None for record in batch.records)
    by_sku = {str(record.sku): record for record in batch.records}
    assert by_sku["DEMO-005"].sellable_stock == 0
    assert by_sku["DEMO-035"].sellable_stock == 0
    assert not hasattr(by_sku["DEMO-005"], "stock_coverage_days")


def test_inbound_preserves_status_and_separate_batches() -> None:
    batch = MockOzonProvider().get_inbound_supplies()

    assert batch.issues == ()
    assert len(batch.records) == 11
    assert all(isinstance(record, InboundSupply) for record in batch.records)
    assert {record.status for record in batch.records} == {
        InboundStatus.CONFIRMED,
        InboundStatus.UNCONFIRMED,
    }
    assert len(tuple(record for record in batch.records if record.sku == SkuId("DEMO-007"))) == 2
    assert len(tuple(record for record in batch.records if record.sku == SkuId("DEMO-028"))) == 2


def test_lead_time_lookup_preserves_missing_values_and_unknown_sku() -> None:
    provider = MockOzonProvider()

    missing_source_values = provider.get_lead_time(SkuId("DEMO-012"))
    unknown = provider.get_lead_time(SkuId("UNKNOWN"))

    assert missing_source_values.issues == ()
    assert isinstance(missing_source_values.value, LeadTime)
    assert missing_source_values.value.production_days is None
    assert missing_source_values.value.delivery_days is None
    assert missing_source_values.value.safety_buffer_days is None
    assert unknown.value is None
    assert unknown.issues == ()


def test_all_38_lead_time_rows_are_available_as_contract_values() -> None:
    provider = MockOzonProvider()
    values = tuple(provider.get_lead_time(SkuId(sku)) for sku in EXPECTED_SKUS)

    assert all(result.issues == () for result in values)
    assert all(isinstance(result.value, LeadTime) for result in values)


def test_replenishment_constraints_are_normalized_and_contract_accessible() -> None:
    provider: MarketplaceReadProvider = MockOzonProvider()

    result = provider.get_replenishment_constraints(SkuId("DEMO-003"))

    assert result.issues == ()
    assert isinstance(result.value, ReplenishmentConstraints)
    assert result.value.sku == SkuId("DEMO-003")
    assert result.value.minimum_order_quantity == 50
    assert result.value.pack_size == 10
    assert result.value.provenance.source_type is SourceType.DEMO
    assert result.value.provenance.provider == "demo_dataset"


def test_missing_and_unknown_replenishment_constraints_remain_explicit() -> None:
    provider = MockOzonProvider()

    missing_source_values = provider.get_replenishment_constraints(SkuId("DEMO-001"))
    unknown = provider.get_replenishment_constraints(SkuId("UNKNOWN"))

    assert missing_source_values.issues == ()
    assert isinstance(missing_source_values.value, ReplenishmentConstraints)
    assert missing_source_values.value.minimum_order_quantity is None
    assert missing_source_values.value.pack_size is None
    assert unknown.value is None
    assert unknown.issues == ()


@pytest.mark.parametrize("invalid_value", [0, -1, "10", True])
def test_invalid_moq_becomes_a_recoverable_source_issue(
    tmp_path: Path,
    invalid_value: object,
) -> None:
    document = _load_source("marketplace/lead_times.json")
    document["records"] = [document["records"][0]]
    document["records"][0]["minimum_order_quantity"] = invalid_value
    _write_source(tmp_path, "marketplace/lead_times.json", document)

    result = MockOzonProvider(tmp_path).get_replenishment_constraints(
        SkuId("DEMO-001")
    )

    assert result.value is None
    assert len(result.issues) == 1
    assert result.issues[0].sku == SkuId("DEMO-001")
    assert result.issues[0].code == "domain.invalid_positive_integer"


@pytest.mark.parametrize("invalid_value", [0, -1, "10", True])
def test_invalid_pack_size_becomes_a_recoverable_source_issue(
    tmp_path: Path,
    invalid_value: object,
) -> None:
    document = _load_source("marketplace/lead_times.json")
    document["records"] = [document["records"][0]]
    document["records"][0]["pack_size"] = invalid_value
    _write_source(tmp_path, "marketplace/lead_times.json", document)

    result = MockOzonProvider(tmp_path).get_replenishment_constraints(
        SkuId("DEMO-001")
    )

    assert result.value is None
    assert len(result.issues) == 1
    assert result.issues[0].sku == SkuId("DEMO-001")
    assert result.issues[0].code == "domain.invalid_positive_integer"


def test_replenishment_constraints_are_repeatable_and_immutable() -> None:
    provider = MockOzonProvider()

    first = provider.get_replenishment_constraints(SkuId("DEMO-003"))
    second = provider.get_replenishment_constraints(SkuId("DEMO-003"))

    assert first == second
    assert isinstance(first.value, ReplenishmentConstraints)
    with pytest.raises(FrozenInstanceError):
        first.value.pack_size = 1


def test_known_zero_lead_time_values_remain_distinct_from_missing(
    tmp_path: Path,
) -> None:
    document = _load_source("marketplace/lead_times.json")
    zero_record = document["records"][0]
    zero_record["production_days"] = 0
    zero_record["delivery_days"] = 0
    zero_record["safety_buffer_days"] = 0
    document["records"] = [zero_record]
    _write_source(tmp_path, "marketplace/lead_times.json", document)

    result = MockOzonProvider(tmp_path).get_lead_time(SkuId("DEMO-001"))

    assert isinstance(result.value, LeadTime)
    assert result.value.production_days == 0
    assert result.value.delivery_days == 0
    assert result.value.safety_buffer_days == 0


def test_repeated_marketplace_reads_are_equal_and_results_are_immutable() -> None:
    provider = MockOzonProvider()
    first = provider.get_products()
    second = provider.get_products()

    assert first == second
    assert isinstance(first.records, tuple)
    with pytest.raises(FrozenInstanceError):
        first.records[0].name = "changed"


def test_one_invalid_product_becomes_an_issue_without_hiding_valid_rows(
    tmp_path: Path,
) -> None:
    document = _load_source("marketplace/products.json")
    document["records"][0]["name"] = ""
    _write_source(tmp_path, "marketplace/products.json", document)

    batch = MockOzonProvider(tmp_path).get_products()

    assert len(batch.records) == 37
    assert len(batch.issues) == 1
    assert batch.issues[0].sku == SkuId("DEMO-001")
    assert batch.issues[0].code == "domain.invalid_text"


def test_optional_marketplace_id_may_be_absent(tmp_path: Path) -> None:
    document = _load_source("marketplace/products.json")
    document["records"] = [document["records"][0]]
    del document["records"][0]["marketplace_id"]
    _write_source(tmp_path, "marketplace/products.json", document)

    batch = MockOzonProvider(tmp_path).get_products()

    assert len(batch.records) == 1
    assert batch.records[0].marketplace_id is None
    assert batch.issues == ()


def test_invalid_sales_row_is_not_partially_emitted(tmp_path: Path) -> None:
    document = _load_source("marketplace/sales.json")
    document["records"][0]["daily_units"][5] = -1
    _write_source(tmp_path, "marketplace/sales.json", document)

    batch = MockOzonProvider(tmp_path).get_sales_observations()

    assert len(batch.records) == 1_041 - 28
    assert len(batch.issues) == 1
    assert all(record.sku != SkuId("DEMO-001") for record in batch.records)


def test_empty_catalog_is_a_valid_empty_batch(tmp_path: Path) -> None:
    document = _load_source("marketplace/products.json")
    document["records"] = []
    _write_source(tmp_path, "marketplace/products.json", document)

    batch = MockOzonProvider(tmp_path).get_products()

    assert batch.records == ()
    assert batch.issues == ()


def test_invalid_inventory_timestamp_becomes_a_record_issue(tmp_path: Path) -> None:
    document = _load_source("marketplace/inventory.json")
    document["records"] = [document["records"][0]]
    document["records"][0]["observed_at"] = "not-a-timestamp"
    _write_source(tmp_path, "marketplace/inventory.json", document)

    batch = MockOzonProvider(tmp_path).get_inventory_snapshots()

    assert batch.records == ()
    assert len(batch.issues) == 1
    assert batch.issues[0].code == "providers.invalid_datetime"


def test_missing_or_malformed_marketplace_file_is_a_safe_provider_error(
    tmp_path: Path,
) -> None:
    missing_provider = MockOzonProvider(tmp_path)
    with pytest.raises(ProviderError) as missing:
        missing_provider.get_products()

    target = tmp_path / "marketplace" / "products.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ProviderError) as malformed:
        MockOzonProvider(tmp_path).get_products()

    assert missing.value.code == "provider.demo_source_unavailable"
    assert malformed.value.code == "provider.demo_invalid_json"
    assert str(tmp_path) not in str(missing.value)
    assert str(tmp_path) not in repr(malformed.value)


def test_unexpected_top_level_records_shape_fails_explicitly(tmp_path: Path) -> None:
    document = _load_source("marketplace/products.json")
    document["records"] = {"sku": "DEMO-001"}
    _write_source(tmp_path, "marketplace/products.json", document)

    with pytest.raises(DataValidationError) as raised:
        MockOzonProvider(tmp_path).get_products()

    assert raised.value.code == "providers.invalid_demo_records"

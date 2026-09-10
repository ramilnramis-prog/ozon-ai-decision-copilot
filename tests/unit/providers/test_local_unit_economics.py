"""Tests for the local deterministic unit-economics provider."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.errors import DataValidationError, ProviderError
from app.domain.common import SkuId, SourceType
from app.domain.economics import UnitEconomicsInput
from app.providers.contracts import (
    MarketplaceReadProvider,
    ProviderValue,
    UnitEconomicsProvider,
)
from app.providers.local_unit_economics import LocalUnitEconomicsProvider


DEMO_ROOT = Path(__file__).resolve().parents[3] / "data" / "demo"
EXPECTED_SKUS = tuple(f"DEMO-{number:03d}" for number in range(1, 39))


class InMemoryUnitEconomicsProvider:
    """Minimal alternate adapter used to prove consumer-side replaceability."""

    def __init__(self, values: dict[SkuId, UnitEconomicsInput]) -> None:
        self._values = dict(values)

    def get_unit_economics(self, sku: SkuId) -> ProviderValue[UnitEconomicsInput]:
        return ProviderValue(self._values.get(sku))


def _read_selling_price(
    provider: UnitEconomicsProvider,
    sku: SkuId,
) -> Decimal | None:
    result = provider.get_unit_economics(sku)
    return None if result.value is None else result.value.selling_price


def _load_economics_source() -> dict[str, object]:
    return json.loads(
        (DEMO_ROOT / "unit_economics" / "economics.json").read_text(
            encoding="utf-8"
        )
    )


def _write_economics_source(root: Path, document: dict[str, object]) -> None:
    target = root / "unit_economics" / "economics.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document), encoding="utf-8")


def test_local_provider_structurally_satisfies_only_economics_contract() -> None:
    provider = LocalUnitEconomicsProvider()

    assert isinstance(provider, UnitEconomicsProvider)
    assert not isinstance(provider, MarketplaceReadProvider)


def test_contract_consumer_can_swap_local_provider_for_in_memory_fake() -> None:
    sku = SkuId("DEMO-001")
    local_provider = LocalUnitEconomicsProvider()
    local_value = local_provider.get_unit_economics(sku).value
    assert isinstance(local_value, UnitEconomicsInput)
    in_memory_provider = InMemoryUnitEconomicsProvider({sku: local_value})

    assert isinstance(in_memory_provider, UnitEconomicsProvider)
    assert _read_selling_price(local_provider, sku) == _read_selling_price(
        in_memory_provider, sku
    )


def test_all_38_economics_rows_are_normalized_domain_values() -> None:
    provider = LocalUnitEconomicsProvider()
    results = tuple(provider.get_unit_economics(SkuId(sku)) for sku in EXPECTED_SKUS)

    assert all(result.issues == () for result in results)
    assert all(isinstance(result.value, UnitEconomicsInput) for result in results)
    assert tuple(str(result.value.sku) for result in results if result.value) == EXPECTED_SKUS
    assert all(result.value.provenance.source_type is SourceType.DEMO for result in results if result.value)
    assert all(result.value.provenance.provider == "demo_dataset" for result in results if result.value)


def test_money_and_rates_are_decimal_normalized_without_float() -> None:
    value = LocalUnitEconomicsProvider().get_unit_economics(SkuId("DEMO-001")).value

    assert isinstance(value, UnitEconomicsInput)
    assert value.selling_price == Decimal("1290.00")
    assert isinstance(value.selling_price, Decimal)
    assert isinstance(value.cost_of_goods, Decimal)
    assert isinstance(value.logistics_cost_per_unit, Decimal)
    assert isinstance(value.commission_rate, Decimal)
    assert isinstance(value.advertising_cost_per_unit, Decimal)
    assert all(isinstance(component.amount, Decimal) for component in value.other_variable_costs)


def test_deliberate_missing_cogs_and_high_drr_are_preserved() -> None:
    provider = LocalUnitEconomicsProvider()

    missing_cogs = provider.get_unit_economics(SkuId("DEMO-021"))
    high_drr = provider.get_unit_economics(SkuId("DEMO-018"))

    assert isinstance(missing_cogs.value, UnitEconomicsInput)
    assert missing_cogs.value.cost_of_goods is None
    assert missing_cogs.value.logistics_cost_per_unit == Decimal("105.00")
    assert isinstance(high_drr.value, UnitEconomicsInput)
    assert high_drr.value.drr == Decimal("1.20")


def test_unknown_economics_sku_returns_explicit_empty_value() -> None:
    result = LocalUnitEconomicsProvider().get_unit_economics(SkuId("UNKNOWN"))

    assert result.value is None
    assert result.issues == ()


def test_repeated_economics_lookups_are_equal_and_immutable() -> None:
    provider = LocalUnitEconomicsProvider()
    first = provider.get_unit_economics(SkuId("DEMO-001"))
    second = provider.get_unit_economics(SkuId("DEMO-001"))

    assert first == second
    assert isinstance(first.value, UnitEconomicsInput)
    with pytest.raises(FrozenInstanceError):
        first.value.selling_price = Decimal("1.00")


def test_binary_float_money_becomes_a_recoverable_record_issue(tmp_path: Path) -> None:
    document = _load_economics_source()
    document["records"] = [document["records"][0]]
    document["records"][0]["selling_price"] = 123.45
    _write_economics_source(tmp_path, document)

    result = LocalUnitEconomicsProvider(tmp_path).get_unit_economics(SkuId("DEMO-001"))

    assert result.value is None
    assert len(result.issues) == 1
    assert result.issues[0].sku == SkuId("DEMO-001")
    assert result.issues[0].code == "money.binary_float_not_allowed"


def test_missing_required_economics_field_becomes_an_issue(tmp_path: Path) -> None:
    document = _load_economics_source()
    document["records"] = [document["records"][0]]
    del document["records"][0]["currency"]
    _write_economics_source(tmp_path, document)

    result = LocalUnitEconomicsProvider(tmp_path).get_unit_economics(SkuId("DEMO-001"))

    assert result.value is None
    assert len(result.issues) == 1
    assert result.issues[0].code == "providers.missing_required_source_field"


def test_domain_incompatible_economics_value_becomes_an_issue(tmp_path: Path) -> None:
    document = _load_economics_source()
    document["records"] = [document["records"][0]]
    document["records"][0]["cost_of_goods"] = "-0.01"
    _write_economics_source(tmp_path, document)

    result = LocalUnitEconomicsProvider(tmp_path).get_unit_economics(SkuId("DEMO-001"))

    assert result.value is None
    assert len(result.issues) == 1
    assert result.issues[0].code == "domain.invalid_non_negative_decimal"


def test_missing_or_malformed_economics_file_is_a_safe_provider_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(ProviderError) as missing:
        LocalUnitEconomicsProvider(tmp_path).get_unit_economics(SkuId("DEMO-001"))

    target = tmp_path / "unit_economics" / "economics.json"
    target.parent.mkdir(parents=True)
    target.write_text("[not-json", encoding="utf-8")
    with pytest.raises(ProviderError) as malformed:
        LocalUnitEconomicsProvider(tmp_path).get_unit_economics(SkuId("DEMO-001"))

    assert missing.value.code == "provider.demo_source_unavailable"
    assert malformed.value.code == "provider.demo_invalid_json"
    assert str(tmp_path) not in str(missing.value)
    assert str(tmp_path) not in repr(malformed.value)


def test_invalid_economics_document_shape_fails_explicitly(tmp_path: Path) -> None:
    document = _load_economics_source()
    document["records"] = "not-an-array"
    _write_economics_source(tmp_path, document)

    with pytest.raises(DataValidationError) as raised:
        LocalUnitEconomicsProvider(tmp_path).get_unit_economics(SkuId("DEMO-001"))

    assert raised.value.code == "providers.invalid_demo_records"

"""Integrity and domain-compatibility tests for the deterministic demo inputs."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.domain.catalog import Product, SalesObservation, SalesPolicy
from app.domain.common import Currency, DateRange, PolicyIdentity, Provenance, SkuId, SourceType
from app.domain.economics import CostComponent, EconomicsPolicy, UnitEconomicsInput
from app.domain.inventory import InboundStatus, InboundSupply, InventoryPolicy, InventorySnapshot, LeadTime


DEMO_ROOT = Path(__file__).resolve().parents[3] / "data" / "demo"
REFERENCE_TIME = datetime.fromisoformat("2026-09-02T09:00:00+03:00")
EXPECTED_SKUS = {f"DEMO-{number:03d}" for number in range(1, 39)}
EXPECTED_SALES_OBSERVATIONS = 1_041


def _reject_json_float(value: str) -> None:
    raise AssertionError(f"binary-float JSON number is not permitted: {value}")


def _load(relative_path: str) -> dict[str, Any]:
    return json.loads(
        (DEMO_ROOT / relative_path).read_text(encoding="utf-8"),
        parse_float=_reject_json_float,
    )


def _records(relative_path: str) -> list[dict[str, Any]]:
    return _load(relative_path)["records"]


def _provenance(document: dict[str, Any]) -> Provenance:
    source = document["source"]
    timestamp = datetime.fromisoformat(source["source_timestamp"])
    return Provenance(
        source_type=SourceType(source["source_type"]),
        provider=source["provider"],
        ingested_at=REFERENCE_TIME,
        source_timestamp=timestamp,
    )


def _walk(value: object):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_metadata_fixes_a_timezone_aware_reference_period() -> None:
    metadata = _load("metadata.json")

    reference_time = datetime.fromisoformat(metadata["reference_time"])

    assert reference_time == REFERENCE_TIME
    assert reference_time.utcoffset() is not None
    assert date.fromisoformat(metadata["sales_history_start"]) == date(2026, 8, 5)
    assert date.fromisoformat(metadata["sales_history_end"]) == date(2026, 9, 1)
    assert date.fromisoformat(metadata["as_of_date"]) == date(2026, 9, 2)
    assert metadata["product_count"] == 38
    assert metadata["currency"] == "RUB"


def test_catalog_has_38_unique_fictional_skus_and_one_inactive_record() -> None:
    records = _records("marketplace/products.json")
    skus = [record["sku"] for record in records]

    assert 30 <= len(records) <= 50
    assert len(records) == 38
    assert set(skus) == EXPECTED_SKUS
    assert len(skus) == len(set(skus))
    assert all(record["name"].strip() for record in records)
    assert all(record["marketplace_id"] is None for record in records)
    assert [record["sku"] for record in records if not record["active"]] == ["DEMO-038"]


def test_cross_file_sku_references_are_complete_and_valid() -> None:
    complete_files = (
        "marketplace/products.json",
        "marketplace/sales.json",
        "marketplace/inventory.json",
        "marketplace/lead_times.json",
        "unit_economics/economics.json",
    )
    for relative_path in complete_files:
        skus = [record["sku"] for record in _records(relative_path)]
        assert set(skus) == EXPECTED_SKUS, relative_path
        assert len(skus) == len(set(skus)), relative_path

    inbound_records = _records("marketplace/inbound.json")
    assert {record["sku"] for record in inbound_records} <= EXPECTED_SKUS
    inbound_keys = {
        (record["sku"], record["expected_arrival"], record["quantity"])
        for record in inbound_records
    }
    assert len(inbound_keys) == len(inbound_records)


def test_sales_are_dated_non_negative_raw_observations_with_known_history_gap() -> None:
    sales_records = _records("marketplace/sales.json")
    observation_count = 0
    latest_observation = date.min

    for record in sales_records:
        start = date.fromisoformat(record["start_date"])
        units = record["daily_units"]
        assert all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in units)
        expected_length = 5 if record["sku"] == "DEMO-037" else 28
        assert len(units) == expected_length
        observation_count += len(units)
        latest_observation = max(latest_observation, start + timedelta(days=len(units) - 1))

    assert observation_count == EXPECTED_SALES_OBSERVATIONS
    assert latest_observation == date(2026, 9, 1)


def test_inventory_inbound_and_lead_time_source_values_are_valid() -> None:
    inventories = _records("marketplace/inventory.json")
    inbound = _records("marketplace/inbound.json")
    lead_times = _records("marketplace/lead_times.json")

    assert all(record["sellable_stock"] >= 0 for record in inventories)
    assert all(datetime.fromisoformat(record["observed_at"]).utcoffset() is not None for record in inventories)
    assert all(record["quantity"] >= 0 for record in inbound)
    assert {record["status"] for record in inbound} == {"confirmed", "unconfirmed"}
    assert all(date.fromisoformat(record["expected_arrival"]) >= date(2026, 9, 2) for record in inbound)

    for record in lead_times:
        for field in ("production_days", "delivery_days", "safety_buffer_days"):
            assert record[field] is None or record[field] >= 0
        for field in ("minimum_order_quantity", "pack_size"):
            assert record[field] is None or record[field] > 0

    missing = next(record for record in lead_times if record["sku"] == "DEMO-012")
    assert (missing["production_days"], missing["delivery_days"], missing["safety_buffer_days"]) == (
        None,
        None,
        None,
    )


def test_money_and_rate_inputs_are_decimal_strings_never_binary_floats() -> None:
    document = _load("unit_economics/economics.json")
    decimal_fields = (
        "selling_price",
        "cost_of_goods",
        "logistics_cost_per_unit",
        "commission_rate",
        "commission_per_unit",
        "advertising_cost_per_unit",
        "drr",
        "advertising_spend",
        "attributable_revenue",
    )

    for record in document["records"]:
        for field in decimal_fields:
            value = record[field]
            assert value is None or isinstance(value, str)
            if value is not None:
                assert Decimal(value).is_finite()
        for component in record["other_variable_costs"]:
            assert isinstance(component["amount"], str)
            assert Decimal(component["amount"]).is_finite()

    assert all(not isinstance(value, float) for value in _walk(document))
    assert next(record for record in document["records"] if record["sku"] == "DEMO-018")["drr"] == "1.20"


def test_all_fixture_records_construct_approved_domain_inputs() -> None:
    products_doc = _load("marketplace/products.json")
    sales_doc = _load("marketplace/sales.json")
    inventory_doc = _load("marketplace/inventory.json")
    inbound_doc = _load("marketplace/inbound.json")
    lead_doc = _load("marketplace/lead_times.json")
    economics_doc = _load("unit_economics/economics.json")
    policies_doc = _load("policies.json")

    products_provenance = _provenance(products_doc)
    products = [
        Product(
            sku=SkuId(record["sku"]),
            name=record["name"],
            active=record["active"],
            marketplace_id=record["marketplace_id"],
            provenance=products_provenance,
        )
        for record in products_doc["records"]
    ]

    sales_provenance = _provenance(sales_doc)
    sales_observations = [
        SalesObservation(
            sku=SkuId(record["sku"]),
            observed_on=date.fromisoformat(record["start_date"]) + timedelta(days=index),
            units_sold=units,
            provenance=sales_provenance,
        )
        for record in sales_doc["records"]
        for index, units in enumerate(record["daily_units"])
    ]

    inventory_provenance = _provenance(inventory_doc)
    inventory_snapshots = [
        InventorySnapshot(
            sku=SkuId(record["sku"]),
            sellable_stock=record["sellable_stock"],
            observed_at=datetime.fromisoformat(record["observed_at"]),
            provenance=inventory_provenance,
        )
        for record in inventory_doc["records"]
    ]

    inbound_provenance = _provenance(inbound_doc)
    inbound_supplies = [
        InboundSupply(
            sku=SkuId(record["sku"]),
            quantity=record["quantity"],
            status=InboundStatus(record["status"]),
            expected_arrival=date.fromisoformat(record["expected_arrival"]),
            provenance=inbound_provenance,
        )
        for record in inbound_doc["records"]
    ]

    policy_source = policies_doc["inventory"]
    lead_provenance = _provenance(lead_doc)
    inventory_policies = [
        InventoryPolicy(
            identity=PolicyIdentity(policy_source["policy_id"], policy_source["version"]),
            lead_time=LeadTime(
                production_days=record["production_days"],
                delivery_days=record["delivery_days"],
                safety_buffer_days=record["safety_buffer_days"],
                provenance=lead_provenance,
            ),
            target_coverage_days=policy_source["target_coverage_days"],
            warning_window_days=policy_source["warning_window_days"],
            overstock_threshold_days=policy_source["overstock_threshold_days"],
            minimum_order_quantity=record["minimum_order_quantity"],
            pack_size=record["pack_size"],
        )
        for record in lead_doc["records"]
    ]

    economics_provenance = _provenance(economics_doc)
    economics_inputs = [
        UnitEconomicsInput(
            sku=SkuId(record["sku"]),
            selling_price=Decimal(record["selling_price"]),
            currency=Currency(record["currency"]),
            cost_of_goods=None if record["cost_of_goods"] is None else Decimal(record["cost_of_goods"]),
            logistics_cost_per_unit=None if record["logistics_cost_per_unit"] is None else Decimal(record["logistics_cost_per_unit"]),
            commission_rate=None if record["commission_rate"] is None else Decimal(record["commission_rate"]),
            commission_per_unit=None if record["commission_per_unit"] is None else Decimal(record["commission_per_unit"]),
            advertising_cost_per_unit=None if record["advertising_cost_per_unit"] is None else Decimal(record["advertising_cost_per_unit"]),
            drr=None if record["drr"] is None else Decimal(record["drr"]),
            advertising_spend=None if record["advertising_spend"] is None else Decimal(record["advertising_spend"]),
            attributable_revenue=None if record["attributable_revenue"] is None else Decimal(record["attributable_revenue"]),
            other_variable_costs=tuple(
                CostComponent(name=component["name"], amount=Decimal(component["amount"]))
                for component in record["other_variable_costs"]
            ),
            source_period=DateRange(
                start=date.fromisoformat(record["source_period"]["start"]),
                end=date.fromisoformat(record["source_period"]["end"]),
            ),
            provenance=economics_provenance,
        )
        for record in economics_doc["records"]
    ]

    sales_source = policies_doc["sales"]
    sales_policy = SalesPolicy(
        identity=PolicyIdentity(sales_source["policy_id"], sales_source["version"]),
        averaging_window_days=sales_source["averaging_window_days"],
        comparison_window_days=sales_source["comparison_window_days"],
        material_change_threshold=Decimal(sales_source["material_change_threshold"]),
    )
    economics_source = policies_doc["economics"]
    economics_policy = EconomicsPolicy(
        identity=PolicyIdentity(economics_source["policy_id"], economics_source["version"]),
        currency=Currency(economics_source["currency"]),
        minimum_profit_per_unit=Decimal(economics_source["minimum_profit_per_unit"]),
        minimum_margin=Decimal(economics_source["minimum_margin_rate"]),
        price_floor=Decimal(economics_source["price_floor"]),
        currency_quantum=Decimal(economics_source["currency_quantum"]),
        price_increment=Decimal(economics_source["price_increment"]),
    )

    assert len(products) == 38
    assert len(sales_observations) == EXPECTED_SALES_OBSERVATIONS
    assert len(inventory_snapshots) == 38
    assert len(inbound_supplies) == 11
    assert len(inventory_policies) == 38
    assert len(economics_inputs) == 38
    assert sales_policy.averaging_window_days == 14
    assert economics_policy.currency is Currency.RUB


def test_scenario_manifest_covers_required_normal_edge_and_incomplete_cases() -> None:
    manifest = _load("scenarios.json")
    scenarios = manifest["scenarios"]
    scenario_ids = [scenario["scenario_id"] for scenario in scenarios]
    required = {
        "healthy_inventory",
        "near_stockout",
        "critical_stockout_risk",
        "zero_stock",
        "overstock_candidate",
        "confirmed_inbound",
        "unconfirmed_inbound",
        "missing_lead_time",
        "moq_pack_size",
        "stable_sales",
        "sales_increase",
        "sales_decline",
        "low_volume_sales",
        "zero_sales_period",
        "lumpy_demand",
        "insufficient_sales_history",
        "strong_profitability",
        "normal_profitability",
        "low_margin",
        "near_break_even",
        "loss_making",
        "high_drr",
        "high_logistics_cost",
        "high_cogs",
        "missing_economics",
        "near_safe_price",
        "below_safe_price",
        "pricing_opportunity",
        "compound_high_sales_low_inventory_long_lead",
        "compound_decline_overstock",
        "compound_healthy_inventory_weak_economics",
        "compound_critical_stock_confirmed_inbound",
    }

    assert required <= set(scenario_ids)
    assert len(scenario_ids) == len(set(scenario_ids))
    assert all(scenario["description"].strip() for scenario in scenarios)
    assert all(set(scenario["skus"]) <= EXPECTED_SKUS for scenario in scenarios)
    assert all(scenario["skus"] for scenario in scenarios)


def test_every_catalog_sku_has_a_documented_scenario_purpose() -> None:
    catalog_skus = {
        record["sku"] for record in _records("marketplace/products.json")
    }
    manifest_skus = {
        sku
        for scenario in _load("scenarios.json")["scenarios"]
        for sku in scenario["skus"]
    }

    assert manifest_skus == catalog_skus


def test_low_margin_manifest_uses_the_reviewed_source_fixture() -> None:
    scenarios = {
        scenario["scenario_id"]: set(scenario["skus"])
        for scenario in _load("scenarios.json")["scenarios"]
    }

    assert scenarios["low_margin"] == {"DEMO-019"}
    assert {"DEMO-015", "DEMO-016"} <= scenarios["loss_making"]


def test_intentional_incomplete_inputs_are_explicit_and_limited() -> None:
    leads = {record["sku"]: record for record in _records("marketplace/lead_times.json")}
    economics = {record["sku"]: record for record in _records("unit_economics/economics.json")}
    sales = {record["sku"]: record for record in _records("marketplace/sales.json")}

    incomplete_leads = {
        sku
        for sku, record in leads.items()
        if any(record[field] is None for field in ("production_days", "delivery_days", "safety_buffer_days"))
    }
    missing_cogs = {sku for sku, record in economics.items() if record["cost_of_goods"] is None}
    short_history = {sku for sku, record in sales.items() if len(record["daily_units"]) < 28}

    assert incomplete_leads == {"DEMO-012"}
    assert missing_cogs == {"DEMO-021"}
    assert short_history == {"DEMO-037"}


def test_records_contain_no_precomputed_business_outputs() -> None:
    raw_documents = (
        _load("marketplace/products.json"),
        _load("marketplace/sales.json"),
        _load("marketplace/inventory.json"),
        _load("marketplace/inbound.json"),
        _load("marketplace/lead_times.json"),
        _load("unit_economics/economics.json"),
    )
    forbidden_keys = {
        "average_daily_sales",
        "sales_change_rate",
        "stock_coverage_days",
        "stockout_date",
        "reorder_date",
        "start_production_date",
        "replenishment_quantity",
        "total_variable_cost",
        "profit_per_unit",
        "contribution_margin",
        "break_even_price",
        "minimum_safe_price",
        "risk_status",
        "recommendation",
        "priority",
        "priority_score",
    }

    for document in raw_documents:
        for value in _walk(document):
            if isinstance(value, dict):
                assert forbidden_keys.isdisjoint(value)


def test_all_sources_are_demo_provenance_with_no_secret_fields() -> None:
    json_paths = sorted(DEMO_ROOT.rglob("*.json"))
    forbidden_secret_keys = {"api_key", "access_token", "client_secret", "password", "token"}

    for path in json_paths:
        document = _load(path.relative_to(DEMO_ROOT).as_posix())
        assert document["source"]["source_type"] == "demo"
        assert document["source"]["provider"] == "demo_dataset"
        assert datetime.fromisoformat(document["source"]["source_timestamp"]).utcoffset() is not None
        for value in _walk(document):
            if isinstance(value, dict):
                assert forbidden_secret_keys.isdisjoint(key.lower() for key in value)


def test_fixture_reads_are_byte_for_byte_repeatable() -> None:
    first = {path.relative_to(DEMO_ROOT): path.read_bytes() for path in sorted(DEMO_ROOT.rglob("*")) if path.is_file()}
    second = {path.relative_to(DEMO_ROOT): path.read_bytes() for path in sorted(DEMO_ROOT.rglob("*")) if path.is_file()}

    assert first == second


@pytest.mark.parametrize(
    "relative_path",
    [
        "metadata.json",
        "policies.json",
        "scenarios.json",
        "marketplace/products.json",
        "marketplace/sales.json",
        "marketplace/inventory.json",
        "marketplace/inbound.json",
        "marketplace/lead_times.json",
        "unit_economics/economics.json",
    ],
)
def test_every_json_document_has_the_expected_schema_version(relative_path: str) -> None:
    assert _load(relative_path)["schema_version"] == "1.0"

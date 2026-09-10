"""Read-only marketplace adapter for the deterministic demo dataset."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import TypeVar

from app.core.errors import DataValidationError
from app.domain.catalog import Product, SalesObservation
from app.domain.common import SkuId, ValidationIssue, require_instance
from app.domain.inventory import (
    InboundStatus,
    InboundSupply,
    InventorySnapshot,
    LeadTime,
    ReplenishmentConstraints,
)
from app.providers.contracts import ProviderBatch, ProviderValue
from app.providers.demo_loader import (
    DEMO_SOURCE_IDENTITY,
    DemoDocument,
    DemoJsonLoader,
    parse_aware_datetime,
    parse_enum,
    parse_iso_date,
    required_value,
    validation_issue_for_record,
)
from app.providers.mapping import FieldMapping, MappedRecord, SourceMapping, map_source_record


RecordT = TypeVar("RecordT")


def _direct_mapping(
    *field_names: str,
    required_fields: tuple[str, ...] | None = None,
) -> SourceMapping:
    return SourceMapping(
        identity=DEMO_SOURCE_IDENTITY,
        fields=tuple(FieldMapping(name, name) for name in field_names),
        required_canonical_fields=field_names if required_fields is None else required_fields,
    )


_PRODUCTS_SOURCE = "marketplace/products.json"
_SALES_SOURCE = "marketplace/sales.json"
_INVENTORY_SOURCE = "marketplace/inventory.json"
_INBOUND_SOURCE = "marketplace/inbound.json"
_LEAD_TIMES_SOURCE = "marketplace/lead_times.json"

_PRODUCT_MAPPING = _direct_mapping(
    "sku",
    "name",
    "active",
    "marketplace_id",
    required_fields=("sku", "name", "active"),
)
_SALES_MAPPING = _direct_mapping("sku", "start_date", "daily_units")
_INVENTORY_MAPPING = _direct_mapping("sku", "sellable_stock", "observed_at")
_INBOUND_MAPPING = _direct_mapping("sku", "quantity", "status", "expected_arrival")
_LEAD_TIME_MAPPING = _direct_mapping(
    "sku",
    "production_days",
    "delivery_days",
    "safety_buffer_days",
    "minimum_order_quantity",
    "pack_size",
)


def _parse_product(mapped: MappedRecord) -> tuple[Product, ...]:
    values = mapped.canonical_values
    return (
        Product(
            sku=SkuId(required_value(values, "sku")),
            name=required_value(values, "name"),
            active=required_value(values, "active"),
            marketplace_id=values.get("marketplace_id"),
            provenance=mapped.provenance,
        ),
    )


def _parse_sales(mapped: MappedRecord) -> tuple[SalesObservation, ...]:
    values = mapped.canonical_values
    sku = SkuId(required_value(values, "sku"))
    start_date = parse_iso_date(
        required_value(values, "start_date"), field_name="sales start date"
    )
    daily_units = required_value(values, "daily_units")
    if isinstance(daily_units, (str, bytes)) or not isinstance(daily_units, Sequence):
        raise DataValidationError(
            "daily units must be an ordered collection",
            code="providers.invalid_daily_units",
            scope="daily_units",
        )
    if not daily_units:
        raise DataValidationError(
            "daily units must contain at least one observation",
            code="providers.empty_daily_units",
            scope="daily_units",
        )

    observations: list[SalesObservation] = []
    for day_offset, units_sold in enumerate(daily_units):
        observations.append(
            SalesObservation(
                sku=sku,
                observed_on=start_date + timedelta(days=day_offset),
                units_sold=units_sold,
                provenance=mapped.provenance,
            )
        )
    return tuple(observations)


def _parse_inventory(mapped: MappedRecord) -> tuple[InventorySnapshot, ...]:
    values = mapped.canonical_values
    return (
        InventorySnapshot(
            sku=SkuId(required_value(values, "sku")),
            sellable_stock=required_value(values, "sellable_stock"),
            observed_at=parse_aware_datetime(
                required_value(values, "observed_at"),
                field_name="inventory observed_at",
            ),
            provenance=mapped.provenance,
        ),
    )


def _parse_inbound(mapped: MappedRecord) -> tuple[InboundSupply, ...]:
    values = mapped.canonical_values
    arrival_source = required_value(values, "expected_arrival")
    expected_arrival = (
        None
        if arrival_source is None
        else parse_iso_date(arrival_source, field_name="inbound expected arrival")
    )
    return (
        InboundSupply(
            sku=SkuId(required_value(values, "sku")),
            quantity=required_value(values, "quantity"),
            status=parse_enum(
                required_value(values, "status"),
                InboundStatus,
                field_name="inbound status",
            ),
            expected_arrival=expected_arrival,
            provenance=mapped.provenance,
        ),
    )


def _parse_supply_inputs(
    mapped: MappedRecord,
) -> tuple[SkuId, LeadTime, ReplenishmentConstraints]:
    values = mapped.canonical_values
    sku = SkuId(required_value(values, "sku"))

    return (
        sku,
        LeadTime(
            production_days=required_value(values, "production_days"),
            delivery_days=required_value(values, "delivery_days"),
            safety_buffer_days=required_value(values, "safety_buffer_days"),
            provenance=mapped.provenance,
        ),
        ReplenishmentConstraints(
            sku=sku,
            minimum_order_quantity=required_value(
                values, "minimum_order_quantity"
            ),
            pack_size=required_value(values, "pack_size"),
            provenance=mapped.provenance,
        ),
    )


def _normalized_supply_inputs(
    loader: DemoJsonLoader,
) -> tuple[
    dict[SkuId, tuple[LeadTime, ReplenishmentConstraints]],
    tuple[ValidationIssue, ...],
]:
    """Normalize each lead-time row once into timing and ordering inputs."""

    document = loader.load_records(_LEAD_TIMES_SOURCE)
    values_by_sku: dict[SkuId, tuple[LeadTime, ReplenishmentConstraints]] = {}
    issues: list[ValidationIssue] = []

    for index, raw_record in enumerate(document.records):
        try:
            mapped = map_source_record(
                raw_record,
                _LEAD_TIME_MAPPING,
                document.provenance_for(index),
            )
            source_sku, lead_time, constraints = _parse_supply_inputs(mapped)
            if source_sku in values_by_sku:
                raise DataValidationError(
                    "lead-time source contains a duplicate SKU",
                    code="providers.duplicate_lead_time_sku",
                    scope="sku",
                )
            values_by_sku[source_sku] = (lead_time, constraints)
        except DataValidationError as exc:
            issues.append(
                validation_issue_for_record(
                    exc,
                    document=document,
                    record_index=index,
                    raw_record=raw_record,
                )
            )

    return values_by_sku, tuple(issues)


def _normalized_batch(
    loader: DemoJsonLoader,
    source_name: str,
    mapping: SourceMapping,
    parser: Callable[[MappedRecord], tuple[RecordT, ...]],
) -> ProviderBatch[RecordT]:
    document = loader.load_records(source_name)
    records: list[RecordT] = []
    issues: list[ValidationIssue] = []

    for index, raw_record in enumerate(document.records):
        try:
            mapped = map_source_record(
                raw_record,
                mapping,
                document.provenance_for(index),
            )
            parsed_records = parser(mapped)
        except DataValidationError as exc:
            issues.append(
                validation_issue_for_record(
                    exc,
                    document=document,
                    record_index=index,
                    raw_record=raw_record,
                )
            )
        else:
            records.extend(parsed_records)

    return ProviderBatch(tuple(records), tuple(issues))


class MockOzonProvider:
    """Concrete local marketplace reader; it performs no Ozon or network calls."""

    __slots__ = ("_loader",)

    def __init__(self, demo_root: str | Path | None = None) -> None:
        self._loader = DemoJsonLoader(demo_root)

    def get_products(self) -> ProviderBatch[Product]:
        return _normalized_batch(
            self._loader, _PRODUCTS_SOURCE, _PRODUCT_MAPPING, _parse_product
        )

    def get_sales_observations(self) -> ProviderBatch[SalesObservation]:
        return _normalized_batch(
            self._loader, _SALES_SOURCE, _SALES_MAPPING, _parse_sales
        )

    def get_inventory_snapshots(self) -> ProviderBatch[InventorySnapshot]:
        return _normalized_batch(
            self._loader,
            _INVENTORY_SOURCE,
            _INVENTORY_MAPPING,
            _parse_inventory,
        )

    def get_inbound_supplies(self) -> ProviderBatch[InboundSupply]:
        return _normalized_batch(
            self._loader, _INBOUND_SOURCE, _INBOUND_MAPPING, _parse_inbound
        )

    def get_lead_time(self, sku: SkuId) -> ProviderValue[LeadTime]:
        require_instance(sku, SkuId, field_name="lead-time lookup sku")
        values_by_sku, issues = _normalized_supply_inputs(self._loader)
        source_inputs = values_by_sku.get(sku)
        return ProviderValue(
            None if source_inputs is None else source_inputs[0],
            issues,
        )

    def get_replenishment_constraints(
        self,
        sku: SkuId,
    ) -> ProviderValue[ReplenishmentConstraints]:
        require_instance(sku, SkuId, field_name="replenishment constraints lookup sku")
        values_by_sku, issues = _normalized_supply_inputs(self._loader)
        source_inputs = values_by_sku.get(sku)
        return ProviderValue(
            None if source_inputs is None else source_inputs[1],
            issues,
        )

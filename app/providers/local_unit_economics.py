"""Read-only unit-economics adapter for the deterministic demo dataset."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from app.core.errors import DataValidationError
from app.domain.common import Currency, DateRange, SkuId, ValidationIssue, require_instance
from app.domain.economics import CostComponent, UnitEconomicsInput
from app.providers.contracts import ProviderValue
from app.providers.demo_loader import (
    DEMO_SOURCE_IDENTITY,
    DemoJsonLoader,
    parse_decimal,
    parse_enum,
    parse_iso_date,
    parse_optional_decimal,
    required_value,
    validation_issue_for_record,
)
from app.providers.mapping import FieldMapping, MappedRecord, SourceMapping, map_source_record


_ECONOMICS_SOURCE = "unit_economics/economics.json"
_ECONOMICS_FIELDS = (
    "sku",
    "selling_price",
    "currency",
    "cost_of_goods",
    "logistics_cost_per_unit",
    "commission_rate",
    "commission_per_unit",
    "advertising_cost_per_unit",
    "drr",
    "advertising_spend",
    "attributable_revenue",
    "other_variable_costs",
    "source_period",
)
_ECONOMICS_MAPPING = SourceMapping(
    identity=DEMO_SOURCE_IDENTITY,
    fields=tuple(FieldMapping(name, name) for name in _ECONOMICS_FIELDS),
    # Explicit nulls are required for unavailable source values; omitted fields
    # are malformed records rather than silently inferred missing values.
    required_canonical_fields=_ECONOMICS_FIELDS,
)


def _parse_cost_components(value: object) -> tuple[CostComponent, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DataValidationError(
            "other variable costs must be an ordered collection",
            code="providers.invalid_cost_components",
            scope="other_variable_costs",
        )

    components: list[CostComponent] = []
    for source_component in value:
        if not isinstance(source_component, Mapping):
            raise DataValidationError(
                "each variable cost must be an object",
                code="providers.invalid_cost_component",
                scope="other_variable_costs",
            )
        if set(source_component) != {"name", "amount"}:
            raise DataValidationError(
                "variable cost fields are invalid",
                code="providers.invalid_cost_component_fields",
                scope="other_variable_costs",
            )
        components.append(
            CostComponent(
                name=source_component["name"],
                amount=parse_decimal(
                    source_component["amount"], field_name="variable cost amount"
                ),
            )
        )
    return tuple(components)


def _parse_source_period(value: object) -> DateRange | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"start", "end"}:
        raise DataValidationError(
            "economics source period must contain start and end dates",
            code="providers.invalid_source_period",
            scope="source_period",
        )
    return DateRange(
        start=parse_iso_date(value["start"], field_name="economics period start"),
        end=parse_iso_date(value["end"], field_name="economics period end"),
    )


def _parse_economics(mapped: MappedRecord) -> UnitEconomicsInput:
    values = mapped.canonical_values
    return UnitEconomicsInput(
        sku=SkuId(required_value(values, "sku")),
        selling_price=parse_decimal(
            required_value(values, "selling_price"), field_name="selling price"
        ),
        currency=parse_enum(
            required_value(values, "currency"),
            Currency,
            field_name="economics currency",
        ),
        cost_of_goods=parse_optional_decimal(
            required_value(values, "cost_of_goods"), field_name="cost of goods"
        ),
        logistics_cost_per_unit=parse_optional_decimal(
            required_value(values, "logistics_cost_per_unit"),
            field_name="logistics cost per unit",
        ),
        commission_rate=parse_optional_decimal(
            required_value(values, "commission_rate"), field_name="commission rate"
        ),
        commission_per_unit=parse_optional_decimal(
            required_value(values, "commission_per_unit"),
            field_name="commission per unit",
        ),
        advertising_cost_per_unit=parse_optional_decimal(
            required_value(values, "advertising_cost_per_unit"),
            field_name="advertising cost per unit",
        ),
        drr=parse_optional_decimal(required_value(values, "drr"), field_name="DRR"),
        advertising_spend=parse_optional_decimal(
            required_value(values, "advertising_spend"),
            field_name="advertising spend",
        ),
        attributable_revenue=parse_optional_decimal(
            required_value(values, "attributable_revenue"),
            field_name="attributable revenue",
        ),
        other_variable_costs=_parse_cost_components(
            required_value(values, "other_variable_costs")
        ),
        source_period=_parse_source_period(required_value(values, "source_period")),
        provenance=mapped.provenance,
    )


class LocalUnitEconomicsProvider:
    """Concrete local economics reader, independent of marketplace operations."""

    __slots__ = ("_loader",)

    def __init__(self, demo_root: str | Path | None = None) -> None:
        self._loader = DemoJsonLoader(demo_root)

    def get_unit_economics(self, sku: SkuId) -> ProviderValue[UnitEconomicsInput]:
        require_instance(sku, SkuId, field_name="economics lookup sku")
        document = self._loader.load_records(_ECONOMICS_SOURCE)
        values_by_sku: dict[SkuId, UnitEconomicsInput] = {}
        issues: list[ValidationIssue] = []

        for index, raw_record in enumerate(document.records):
            try:
                mapped = map_source_record(
                    raw_record,
                    _ECONOMICS_MAPPING,
                    document.provenance_for(index),
                )
                economics = _parse_economics(mapped)
                if economics.sku in values_by_sku:
                    raise DataValidationError(
                        "economics source contains a duplicate SKU",
                        code="providers.duplicate_economics_sku",
                        scope="sku",
                    )
                values_by_sku[economics.sku] = economics
            except DataValidationError as exc:
                issues.append(
                    validation_issue_for_record(
                        exc,
                        document=document,
                        record_index=index,
                        raw_record=raw_record,
                    )
                )

        return ProviderValue(values_by_sku.get(sku), tuple(issues))

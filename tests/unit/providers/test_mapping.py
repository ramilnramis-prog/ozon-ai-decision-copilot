"""Tests for explicit, immutable source-field mapping primitives."""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType

import pytest

from app.core.errors import DataValidationError
from app.domain.common import Provenance, SourceType
from app.providers.mapping import (
    FieldMapping,
    SourceIdentity,
    SourceMapping,
    UnknownFieldPolicy,
    map_source_record,
)


NOW = datetime(2026, 9, 2, 8, tzinfo=UTC)


def identity(source_type: SourceType = SourceType.DEMO) -> SourceIdentity:
    return SourceIdentity(source_type, "fixture_provider")


def provenance(source_type: SourceType = SourceType.DEMO) -> Provenance:
    return Provenance(source_type, "fixture_provider", NOW, source_record_id="row-1")


def mapping(**overrides: object) -> SourceMapping:
    values = {
        "identity": identity(),
        "fields": (
            FieldMapping("offer_id", "sku"),
            FieldMapping("title", "name"),
        ),
        "required_canonical_fields": ("sku",),
        "unknown_field_policy": UnknownFieldPolicy.REJECT,
    }
    values.update(overrides)
    return SourceMapping(**values)


def test_valid_mapping_maps_required_and_optional_fields_with_provenance() -> None:
    source_provenance = provenance()
    result = map_source_record(
        {"offer_id": "SKU-001", "title": "Demo product"},
        mapping(),
        source_provenance,
    )

    assert dict(result.canonical_values) == {"sku": "SKU-001", "name": "Demo product"}
    assert result.provenance is source_provenance
    assert result.provenance.source_record_id == "row-1"


def test_optional_source_field_may_be_absent() -> None:
    result = map_source_record({"offer_id": "SKU-001"}, mapping(), provenance())

    assert dict(result.canonical_values) == {"sku": "SKU-001"}


def test_field_mapping_rejects_blank_source_or_canonical_name() -> None:
    with pytest.raises(DataValidationError):
        FieldMapping(" ", "sku")
    with pytest.raises(DataValidationError):
        FieldMapping("offer_id", " ")


def test_mapping_rejects_duplicate_canonical_target() -> None:
    with pytest.raises(DataValidationError) as raised:
        mapping(
            fields=(
                FieldMapping("offer_id", "sku"),
                FieldMapping("another_id", "sku"),
            )
        )

    assert raised.value.code == "providers.duplicate_canonical_mapping"


def test_mapping_rejects_duplicate_or_conflicting_source_mapping() -> None:
    with pytest.raises(DataValidationError) as duplicate:
        mapping(
            fields=(
                FieldMapping("offer_id", "sku"),
                FieldMapping("offer_id", "sku"),
            )
        )
    with pytest.raises(DataValidationError) as conflicting:
        mapping(
            fields=(
                FieldMapping("offer_id", "sku"),
                FieldMapping("offer_id", "name"),
            )
        )

    assert duplicate.value.code == "providers.duplicate_source_mapping"
    assert conflicting.value.code == "providers.conflicting_source_mapping"


def test_mapping_rejects_missing_required_canonical_mapping() -> None:
    with pytest.raises(DataValidationError) as raised:
        mapping(
            fields=(FieldMapping("title", "name"),),
            required_canonical_fields=("sku",),
        )

    assert raised.value.code == "providers.missing_required_mapping"


def test_record_rejects_missing_required_source_field() -> None:
    with pytest.raises(DataValidationError) as raised:
        map_source_record({"title": "Demo product"}, mapping(), provenance())

    assert raised.value.code == "providers.missing_required_source_field"


def test_unknown_source_fields_are_rejected_by_default() -> None:
    with pytest.raises(DataValidationError) as raised:
        map_source_record(
            {"offer_id": "SKU-001", "unexpected": "value"},
            mapping(),
            provenance(),
        )

    assert raised.value.code == "providers.unknown_source_field"


def test_unknown_source_fields_can_be_explicitly_ignored() -> None:
    result = map_source_record(
        {"offer_id": "SKU-001", "unexpected": "value"},
        mapping(unknown_field_policy=UnknownFieldPolicy.IGNORE),
        provenance(),
    )

    assert dict(result.canonical_values) == {"sku": "SKU-001"}
    assert "unexpected" not in result.canonical_values


def test_record_rejects_conflicting_provenance_identity() -> None:
    with pytest.raises(DataValidationError) as raised:
        map_source_record(
            {"offer_id": "SKU-001"},
            mapping(),
            Provenance(SourceType.MANUAL, "another_provider", NOW),
        )

    assert raised.value.code == "providers.conflicting_source_identity"


def test_mapping_and_canonical_result_are_immutable() -> None:
    specification = mapping(fields=[FieldMapping("offer_id", "sku")])
    result = map_source_record({"offer_id": "SKU-001"}, specification, provenance())

    assert isinstance(specification.fields, tuple)
    with pytest.raises(FrozenInstanceError):
        specification.fields = ()
    with pytest.raises(TypeError):
        result.canonical_values["sku"] = "changed"


def test_mapped_record_defensively_snapshots_source_list() -> None:
    source_tags = ["a", "b"]
    specification = mapping(
        fields=(FieldMapping("tags", "tags"),),
        required_canonical_fields=("tags",),
    )
    result = map_source_record({"tags": source_tags}, specification, provenance())

    source_tags.append("c")

    assert result.canonical_values["tags"] == ("a", "b")
    assert isinstance(result.canonical_values["tags"], tuple)


def test_mapped_record_recursively_snapshots_nested_collections() -> None:
    metadata = {
        "tags": ["a", "b"],
        "flags": {"x", "y"},
        "nested": {"codes": [1, 2]},
    }
    specification = mapping(
        fields=(FieldMapping("metadata", "metadata"),),
        required_canonical_fields=("metadata",),
    )
    result = map_source_record(
        {"metadata": metadata},
        specification,
        provenance(),
    )

    metadata["tags"].append("c")
    metadata["flags"].add("z")
    metadata["nested"]["codes"].append(3)
    metadata["new"] = "value"

    frozen_metadata = result.canonical_values["metadata"]
    assert isinstance(frozen_metadata, MappingProxyType)
    assert frozen_metadata["tags"] == ("a", "b")
    assert frozen_metadata["flags"] == frozenset({"x", "y"})
    assert frozen_metadata["nested"]["codes"] == (1, 2)
    assert "new" not in frozen_metadata


def test_mapped_record_nested_values_cannot_be_mutated_through_result() -> None:
    specification = mapping(
        fields=(FieldMapping("metadata", "metadata"),),
        required_canonical_fields=("metadata",),
    )
    result = map_source_record(
        {"metadata": {"tags": ["a"], "flags": {"x"}}},
        specification,
        provenance(),
    )
    frozen_metadata = result.canonical_values["metadata"]

    with pytest.raises(TypeError):
        frozen_metadata["new"] = "value"
    with pytest.raises(AttributeError):
        frozen_metadata["tags"].append("b")
    with pytest.raises(AttributeError):
        frozen_metadata["flags"].add("y")


def test_mapped_record_preserves_scalar_business_values() -> None:
    observed_on = date(2026, 9, 1)
    observed_at = datetime(2026, 9, 1, 8, tzinfo=UTC)
    exact_money = Decimal("10.50")
    source_provenance = provenance()
    values = {
        "money": exact_money,
        "quantity": 3,
        "text": "10",
        "active": True,
        "observed_on": observed_on,
        "observed_at": observed_at,
        "source_type": SourceType.DEMO,
        "domain_value": source_provenance,
        "binary": b"unchanged",
    }
    specification = mapping(
        fields=tuple(FieldMapping(name, name) for name in values),
        required_canonical_fields=tuple(values),
    )
    result = map_source_record(values, specification, source_provenance)

    assert result.canonical_values["money"] is exact_money
    assert result.canonical_values["quantity"] == 3
    assert result.canonical_values["text"] == "10"
    assert isinstance(result.canonical_values["text"], str)
    assert result.canonical_values["active"] is True
    assert result.canonical_values["observed_on"] is observed_on
    assert result.canonical_values["observed_at"] is observed_at
    assert result.canonical_values["source_type"] is SourceType.DEMO
    assert result.canonical_values["domain_value"] is source_provenance
    assert result.canonical_values["binary"] == b"unchanged"

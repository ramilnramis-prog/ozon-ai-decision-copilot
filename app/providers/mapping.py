"""Generic source-field mapping primitives for future provider adapters.

This module maps an explicitly configured raw record to canonical field names.
It does not parse files, infer semantics, coerce business values, or construct
domain models; concrete provider adapters own those boundary steps.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, is_dataclass
from enum import Enum
from types import MappingProxyType

from app.core.errors import DataValidationError
from app.domain.common import (
    Provenance,
    SourceType,
    normalize_text_tuple,
    require_instance,
    require_text,
)


def _freeze_value(value: object) -> object:
    """Take a recursively immutable snapshot without interpreting the value."""

    if is_dataclass(value):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (str, bytes)):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, Sequence):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, Set):
        return frozenset(_freeze_value(item) for item in value)
    return value


class UnknownFieldPolicy(str, Enum):
    """Explicit treatment of source fields absent from a mapping specification."""

    REJECT = "reject"
    IGNORE = "ignore"


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """Declared provider and provenance type for one mapping specification."""

    source_type: SourceType
    provider: str

    def __post_init__(self) -> None:
        require_instance(self.source_type, SourceType, field_name="mapping source type")
        object.__setattr__(
            self,
            "provider",
            require_text(self.provider, field_name="mapping provider"),
        )


@dataclass(frozen=True, slots=True)
class FieldMapping:
    """One explicit source-field to canonical-field mapping."""

    source_field: str
    canonical_field: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_field",
            require_text(self.source_field, field_name="source field"),
        )
        object.__setattr__(
            self,
            "canonical_field",
            require_text(self.canonical_field, field_name="canonical field"),
        )


@dataclass(frozen=True, slots=True)
class SourceMapping:
    """Validated mapping schema with explicit completeness and unknown-field policy."""

    identity: SourceIdentity
    fields: tuple[FieldMapping, ...]
    required_canonical_fields: tuple[str, ...]
    unknown_field_policy: UnknownFieldPolicy = UnknownFieldPolicy.REJECT

    def __post_init__(self) -> None:
        require_instance(self.identity, SourceIdentity, field_name="mapping source identity")
        require_instance(
            self.unknown_field_policy,
            UnknownFieldPolicy,
            field_name="unknown field policy",
        )
        if isinstance(self.fields, (str, bytes)):
            raise DataValidationError(
                "field mappings must be a collection",
                code="providers.invalid_field_mapping_collection",
                scope="field mappings",
            )
        try:
            fields = tuple(self.fields)
        except TypeError as exc:
            raise DataValidationError(
                "field mappings must be a collection",
                code="providers.invalid_field_mapping_collection",
                scope="field mappings",
            ) from exc
        if not fields:
            raise DataValidationError(
                "at least one field mapping is required",
                code="providers.empty_field_mapping",
                scope="field mappings",
            )

        source_targets: dict[str, str] = {}
        canonical_sources: dict[str, str] = {}
        for field in fields:
            require_instance(field, FieldMapping, field_name="field mapping")
            existing_target = source_targets.get(field.source_field)
            if existing_target is not None:
                code = (
                    "providers.duplicate_source_mapping"
                    if existing_target == field.canonical_field
                    else "providers.conflicting_source_mapping"
                )
                raise DataValidationError(
                    "source field must map exactly once",
                    code=code,
                    scope="field mappings",
                )
            if field.canonical_field in canonical_sources:
                raise DataValidationError(
                    "canonical field must have exactly one source",
                    code="providers.duplicate_canonical_mapping",
                    scope="field mappings",
                )
            source_targets[field.source_field] = field.canonical_field
            canonical_sources[field.canonical_field] = field.source_field

        required = normalize_text_tuple(
            self.required_canonical_fields,
            field_name="required canonical fields",
        )
        missing = tuple(
            field_name for field_name in required if field_name not in canonical_sources
        )
        if missing:
            raise DataValidationError(
                "required canonical fields must have explicit mappings",
                code="providers.missing_required_mapping",
                scope="required canonical fields",
                context={"missing": missing},
            )

        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "required_canonical_fields", required)


@dataclass(frozen=True, slots=True)
class MappedRecord:
    """Canonical boundary values paired with the original record provenance."""

    canonical_values: Mapping[str, object]
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_values, Mapping):
            raise DataValidationError(
                "canonical values must be a mapping",
                code="providers.invalid_canonical_values",
                scope="canonical values",
            )
        normalized: dict[str, object] = {}
        for field_name, value in self.canonical_values.items():
            canonical_name = require_text(field_name, field_name="canonical field")
            if canonical_name in normalized:
                raise DataValidationError(
                    "canonical values must not contain duplicate fields",
                    code="providers.duplicate_canonical_value",
                    scope="canonical values",
                )
            normalized[canonical_name] = _freeze_value(value)
        object.__setattr__(
            self,
            "canonical_values",
            MappingProxyType(normalized),
        )
        require_instance(self.provenance, Provenance, field_name="mapped provenance")


def map_source_record(
    source_record: Mapping[str, object],
    mapping: SourceMapping,
    provenance: Provenance,
) -> MappedRecord:
    """Apply one explicit mapping without guessing, coercing, or leaking source keys."""

    if not isinstance(source_record, Mapping):
        raise DataValidationError(
            "source record must be a mapping",
            code="providers.invalid_source_record",
            scope="source record",
        )
    require_instance(mapping, SourceMapping, field_name="source mapping")
    require_instance(provenance, Provenance, field_name="source provenance")
    if (
        provenance.source_type is not mapping.identity.source_type
        or provenance.provider != mapping.identity.provider
    ):
        raise DataValidationError(
            "record provenance does not match mapping source identity",
            code="providers.conflicting_source_identity",
            scope="source provenance",
        )

    for source_field in source_record:
        if not isinstance(source_field, str) or not source_field.strip():
            raise DataValidationError(
                "source record fields must be non-empty strings",
                code="providers.invalid_source_field",
                scope="source record",
            )

    mapped_sources = {field.source_field for field in mapping.fields}
    unknown_sources = tuple(
        source_field for source_field in source_record if source_field not in mapped_sources
    )
    if unknown_sources and mapping.unknown_field_policy is UnknownFieldPolicy.REJECT:
        raise DataValidationError(
            "source record contains unmapped fields",
            code="providers.unknown_source_field",
            scope="source record",
            context={"fields": unknown_sources},
        )

    required = set(mapping.required_canonical_fields)
    missing_sources = tuple(
        field.source_field
        for field in mapping.fields
        if field.canonical_field in required and field.source_field not in source_record
    )
    if missing_sources:
        raise DataValidationError(
            "source record is missing required mapped fields",
            code="providers.missing_required_source_field",
            scope="source record",
            context={"fields": missing_sources},
        )

    canonical_values = {
        field.canonical_field: source_record[field.source_field]
        for field in mapping.fields
        if field.source_field in source_record
    }
    return MappedRecord(canonical_values, provenance)

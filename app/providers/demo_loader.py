"""Shared local-file boundary helpers for deterministic demo providers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path, PurePosixPath
from typing import TypeVar, cast

from app.core.clock import require_aware_datetime
from app.core.errors import DataValidationError, ProviderError
from app.core.money import MoneyInput, to_decimal
from app.domain.common import (
    Provenance,
    Severity,
    SkuId,
    SourceType,
    ValidationIssue,
    require_text,
)
from app.providers.mapping import SourceIdentity


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEMO_ROOT = PROJECT_ROOT / "data" / "demo"
DEMO_SOURCE_IDENTITY = SourceIdentity(SourceType.DEMO, "demo_dataset")

EnumT = TypeVar("EnumT", bound=Enum)


@dataclass(frozen=True, slots=True)
class DemoDocument:
    """Validated demo document header plus source records kept inside providers."""

    source_name: str
    records: tuple[object, ...]
    source_timestamp: datetime

    def provenance_for(self, record_index: int) -> Provenance:
        """Build deterministic provenance for one source row."""

        return Provenance(
            source_type=DEMO_SOURCE_IDENTITY.source_type,
            provider=DEMO_SOURCE_IDENTITY.provider,
            ingested_at=self.source_timestamp,
            source_timestamp=self.source_timestamp,
            source_record_id=f"{self.source_name}#{record_index + 1}",
        )


class DemoJsonLoader:
    """Read one of the explicitly named UTF-8 JSON demo sources without caching."""

    __slots__ = ("_root",)

    def __init__(self, root: str | Path | None = None) -> None:
        selected = DEFAULT_DEMO_ROOT if root is None else Path(root)
        if not selected.is_absolute():
            selected = PROJECT_ROOT / selected
        self._root = selected.resolve()

    def load_records(self, source_name: str) -> DemoDocument:
        """Load and validate one records document; raw values do not leave providers."""

        relative = PurePosixPath(source_name)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise DataValidationError(
                "demo source name must be a safe relative path",
                code="providers.invalid_demo_source_name",
                scope="demo source",
            )
        safe_source_name = relative.as_posix()
        path = self._root.joinpath(*relative.parts)

        try:
            source_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ProviderError(
                "Demo provider source is unavailable.",
                code="provider.demo_source_unavailable",
                scope=safe_source_name,
            ) from exc

        try:
            document = json.loads(source_text)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "Demo provider source contains invalid JSON.",
                code="provider.demo_invalid_json",
                scope=safe_source_name,
            ) from exc

        if not isinstance(document, Mapping):
            raise DataValidationError(
                "demo source must contain a JSON object",
                code="providers.invalid_demo_document",
                scope=safe_source_name,
            )
        if document.get("schema_version") != "1.0":
            raise DataValidationError(
                "demo source has an unsupported schema version",
                code="providers.unsupported_demo_schema",
                scope=safe_source_name,
            )

        source = document.get("source")
        if not isinstance(source, Mapping):
            raise DataValidationError(
                "demo source metadata must be an object",
                code="providers.invalid_demo_source_metadata",
                scope=safe_source_name,
            )
        try:
            source_type = SourceType(source.get("source_type"))
        except (TypeError, ValueError) as exc:
            raise DataValidationError(
                "demo source type is invalid",
                code="providers.invalid_demo_source_type",
                scope=safe_source_name,
            ) from exc
        provider = require_text(source.get("provider"), field_name="source provider")
        if (
            source_type is not DEMO_SOURCE_IDENTITY.source_type
            or provider != DEMO_SOURCE_IDENTITY.provider
        ):
            raise DataValidationError(
                "demo source identity is inconsistent",
                code="providers.invalid_demo_source_identity",
                scope=safe_source_name,
            )
        source_timestamp = parse_aware_datetime(
            source.get("source_timestamp"),
            field_name="source timestamp",
        )

        records = document.get("records")
        if not isinstance(records, list):
            raise DataValidationError(
                "demo source records must be a JSON array",
                code="providers.invalid_demo_records",
                scope=safe_source_name,
            )

        return DemoDocument(safe_source_name, tuple(records), source_timestamp)


def required_value(values: Mapping[str, object], field_name: str) -> object:
    """Read a mapped required field without allowing an implicit default."""

    if field_name not in values:
        raise DataValidationError(
            f"{field_name} is required",
            code="providers.missing_required_source_field",
            scope=field_name,
        )
    return values[field_name]


def parse_iso_date(value: object, *, field_name: str) -> date:
    """Parse one exact ISO calendar date at the provider boundary."""

    if not isinstance(value, str):
        raise DataValidationError(
            f"{field_name} must be an ISO date string",
            code="providers.invalid_date",
            scope=field_name,
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataValidationError(
            f"{field_name} must be a valid ISO date",
            code="providers.invalid_date",
            scope=field_name,
        ) from exc


def parse_aware_datetime(value: object, *, field_name: str) -> datetime:
    """Parse an ISO timestamp and enforce the domain's awareness rule."""

    if not isinstance(value, str):
        raise DataValidationError(
            f"{field_name} must be an ISO timestamp string",
            code="providers.invalid_datetime",
            scope=field_name,
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DataValidationError(
            f"{field_name} must be a valid ISO timestamp",
            code="providers.invalid_datetime",
            scope=field_name,
        ) from exc
    return require_aware_datetime(parsed, field_name=field_name)


def parse_enum(value: object, enum_type: type[EnumT], *, field_name: str) -> EnumT:
    """Parse a source enum value without coercing unrelated input types."""

    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(
            f"{field_name} is invalid",
            code="providers.invalid_enum",
            scope=field_name,
        ) from exc


def parse_decimal(value: object, *, field_name: str) -> Decimal:
    """Convert source money/rates without permitting binary floats."""

    return to_decimal(cast(MoneyInput, value), field_name=field_name)


def parse_optional_decimal(value: object, *, field_name: str) -> Decimal | None:
    """Preserve source null while Decimal-normalizing a present value."""

    if value is None:
        return None
    return parse_decimal(value, field_name=field_name)


def validation_issue_for_record(
    error: DataValidationError,
    *,
    document: DemoDocument,
    record_index: int,
    raw_record: object,
) -> ValidationIssue:
    """Convert a recoverable row error to a safe, source-scoped issue."""

    sku: SkuId | None = None
    if isinstance(raw_record, Mapping):
        raw_sku = raw_record.get("sku")
        if isinstance(raw_sku, str) and raw_sku.strip():
            try:
                sku = SkuId(raw_sku)
            except DataValidationError:
                sku = None

    return ValidationIssue(
        code=error.code,
        message=error.safe_message,
        severity=Severity.WARNING,
        scope=f"{document.source_name} record {record_index + 1}",
        sku=sku,
        field_name=error.scope,
    )

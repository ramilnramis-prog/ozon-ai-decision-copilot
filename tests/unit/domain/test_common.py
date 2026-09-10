"""Tests for shared domain value objects, statuses, and provenance."""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest

from app.core.errors import DataValidationError
from app.domain.common import (
    Currency,
    DateRange,
    PolicyIdentity,
    Provenance,
    Severity,
    SkuId,
    SourceType,
    ValidationIssue,
)


def test_sku_is_trimmed_and_immutable() -> None:
    sku = SkuId("  SKU-001  ")

    assert sku.value == "SKU-001"
    assert str(sku) == "SKU-001"
    with pytest.raises(FrozenInstanceError):
        sku.value = "OTHER"


@pytest.mark.parametrize("value", ["", "   ", None, 123])
def test_sku_rejects_missing_or_invalid_identifier(value: object) -> None:
    with pytest.raises(DataValidationError):
        SkuId(value)


def test_currency_is_explicit_and_unknown_currency_is_rejected() -> None:
    assert Currency.RUB.value == "RUB"
    with pytest.raises(ValueError):
        Currency("USD")


def test_source_types_cover_current_and_generic_future_provenance() -> None:
    assert {source.value for source in SourceType} == {
        "demo",
        "mock",
        "manual",
        "imported",
        "calculated",
        "external_api",
    }


def test_provenance_accepts_aware_source_timestamps() -> None:
    timestamp = datetime(2026, 9, 2, 8, tzinfo=UTC)
    provenance = Provenance(
        source_type=SourceType.DEMO,
        provider="demo_fixture",
        ingested_at=timestamp,
        source_timestamp=timestamp,
        source_record_id="record-1",
    )

    assert provenance.source_type is SourceType.DEMO
    assert provenance.provider == "demo_fixture"


@pytest.mark.parametrize("field_name", ["ingested_at", "source_timestamp"])
def test_provenance_rejects_naive_timestamps(field_name: str) -> None:
    values = {
        "source_type": SourceType.MOCK,
        "provider": "mock_provider",
        "ingested_at": datetime(2026, 9, 2, 8, tzinfo=UTC),
        "source_timestamp": datetime(2026, 9, 2, 8, tzinfo=UTC),
    }
    values[field_name] = datetime(2026, 9, 2, 8)

    with pytest.raises(DataValidationError) as raised:
        Provenance(**values)

    assert raised.value.code == "clock.naive_datetime"


def test_date_range_rejects_inverted_or_datetime_boundaries() -> None:
    with pytest.raises(DataValidationError):
        DateRange(date(2026, 9, 3), date(2026, 9, 2))
    with pytest.raises(DataValidationError):
        DateRange(datetime(2026, 9, 2, tzinfo=UTC), date(2026, 9, 3))


def test_validation_issue_retains_safe_typed_scope() -> None:
    issue = ValidationIssue(
        code="inventory.negative_stock",
        message="Stock value is invalid",
        severity=Severity.HIGH,
        scope="inventory",
        sku=SkuId("SKU-001"),
        field_name="sellable_stock",
    )

    assert issue.sku == SkuId("SKU-001")
    assert issue.severity is Severity.HIGH


def test_policy_identity_requires_non_empty_values() -> None:
    assert PolicyIdentity("demo-policy", "v1") == PolicyIdentity("demo-policy", "v1")
    with pytest.raises(DataValidationError):
        PolicyIdentity("", "v1")

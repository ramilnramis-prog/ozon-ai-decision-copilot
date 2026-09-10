"""Tests for product, sales-input, and sales-policy models."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.core.errors import DataValidationError
from app.domain.catalog import Product, SalesObservation, SalesPolicy
from app.domain.common import Currency, PolicyIdentity, Provenance, SkuId, SourceType


def provenance() -> Provenance:
    return Provenance(
        source_type=SourceType.DEMO,
        provider="demo_marketplace",
        ingested_at=datetime(2026, 9, 2, 8, tzinfo=UTC),
    )


def test_product_contains_identity_metadata_only() -> None:
    product = Product(
        sku=SkuId("SKU-001"),
        name="Demo product",
        active=True,
        provenance=provenance(),
        marketplace_id="marketplace-1",
    )

    assert product.name == "Demo product"
    assert not hasattr(product, "sales")
    assert not hasattr(product, "inventory")
    assert not hasattr(product, "profit")


def test_product_rejects_invalid_active_status() -> None:
    with pytest.raises(DataValidationError):
        Product(SkuId("SKU-001"), "Demo", 1, provenance())


def test_sales_observation_distinguishes_zero_from_missing_revenue() -> None:
    zero = SalesObservation(
        SkuId("SKU-001"),
        date(2026, 9, 1),
        0,
        provenance(),
        revenue=Decimal("0"),
        currency=Currency.RUB,
    )
    missing = SalesObservation(
        SkuId("SKU-001"), date(2026, 9, 1), 0, provenance()
    )

    assert zero.revenue == Decimal("0")
    assert missing.revenue is None
    assert missing.currency is None


@pytest.mark.parametrize("units", [-1, True])
def test_sales_observation_rejects_invalid_units(units: object) -> None:
    with pytest.raises(DataValidationError):
        SalesObservation(SkuId("SKU-001"), date(2026, 9, 1), units, provenance())


def test_sales_observation_rejects_datetime_as_calendar_date() -> None:
    with pytest.raises(DataValidationError):
        SalesObservation(
            SkuId("SKU-001"),
            datetime(2026, 9, 1, tzinfo=UTC),
            1,
            provenance(),
        )


@pytest.mark.parametrize(
    ("revenue", "currency"),
    [(Decimal("10"), None), (None, Currency.RUB)],
)
def test_sales_observation_rejects_incomplete_revenue_pair(
    revenue: Decimal | None, currency: Currency | None
) -> None:
    with pytest.raises(DataValidationError) as raised:
        SalesObservation(
            SkuId("SKU-001"),
            date(2026, 9, 1),
            1,
            provenance(),
            revenue=revenue,
            currency=currency,
        )

    assert raised.value.code == "catalog.incomplete_revenue"


def test_sales_observation_rejects_negative_or_float_revenue() -> None:
    with pytest.raises(DataValidationError):
        SalesObservation(
            SkuId("SKU-001"),
            date(2026, 9, 1),
            1,
            provenance(),
            revenue=Decimal("-0.01"),
            currency=Currency.RUB,
        )
    with pytest.raises(DataValidationError):
        SalesObservation(
            SkuId("SKU-001"),
            date(2026, 9, 1),
            1,
            provenance(),
            revenue=10.5,
            currency=Currency.RUB,
        )


def test_sales_policy_holds_explicit_inputs_without_calculation() -> None:
    policy = SalesPolicy(
        identity=PolicyIdentity("sales-demo", "v1"),
        averaging_window_days=28,
        comparison_window_days=28,
        material_change_threshold=Decimal("0.20"),
    )

    assert policy.material_change_threshold == Decimal("0.20")


@pytest.mark.parametrize(
    ("averaging", "comparison", "threshold"),
    [(0, 28, Decimal("0.2")), (28, 0, Decimal("0.2")), (28, 28, Decimal("0"))],
)
def test_sales_policy_rejects_invalid_values(
    averaging: int, comparison: int, threshold: Decimal
) -> None:
    with pytest.raises(DataValidationError):
        SalesPolicy(
            PolicyIdentity("sales-demo", "v1"), averaging, comparison, threshold
        )

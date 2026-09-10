"""Tests for stable, safe application error primitives."""

import pytest

from app.core.errors import (
    AIInvalidOutputError,
    AIUnavailableError,
    CalculationPreconditionError,
    ConfigurationError,
    DataValidationError,
    ProviderError,
)


@pytest.mark.parametrize(
    ("error_type", "expected_code"),
    [
        (ConfigurationError, "configuration.invalid"),
        (DataValidationError, "data.invalid"),
        (ProviderError, "provider.error"),
        (CalculationPreconditionError, "calculation.precondition_failed"),
        (AIUnavailableError, "ai.unavailable"),
        (AIInvalidOutputError, "ai.invalid_output"),
    ],
)
def test_error_categories_have_stable_codes(error_type: type[Exception], expected_code: str) -> None:
    error = error_type("Safe message")

    assert error.code == expected_code
    assert str(error) == "Safe message"


def test_error_exposes_scope_and_non_sensitive_context() -> None:
    error = ProviderError(
        "Provider is unavailable",
        scope="marketplace",
        context={"provider": "mock", "sku": "SKU-001"},
    )

    assert error.scope == "marketplace"
    assert error.context["provider"] == "mock"
    assert error.context["sku"] == "SKU-001"


def test_error_redacts_sensitive_context_recursively() -> None:
    secret_value = "must-never-leak"
    error = ProviderError(
        "Provider is unavailable",
        context={
            "api_key": secret_value,
            "nested": {"access-token": secret_value, "safe": "visible"},
        },
    )

    rendered = repr(error)

    assert secret_value not in rendered
    assert error.context["api_key"] == "[REDACTED]"
    assert error.context["nested"]["access-token"] == "[REDACTED]"
    assert error.context["nested"]["safe"] == "visible"


def test_error_code_can_be_specialized_without_exposing_internal_details() -> None:
    error = DataValidationError(
        "Record is invalid",
        code="inventory.negative_stock",
        scope="SKU-001",
    )

    assert error.code == "inventory.negative_stock"
    assert str(error) == "Record is invalid"

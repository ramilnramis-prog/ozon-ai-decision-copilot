"""Tests for Decimal-only money construction and rounding."""

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, localcontext

import pytest

from app.core.errors import DataValidationError
from app.core.money import (
    compare_money,
    quantize_currency,
    quantize_rub,
    round_up_to_increment,
    to_decimal,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (Decimal("12.34"), Decimal("12.34")),
        ("12.34", Decimal("12.34")),
        (12, Decimal("12")),
    ],
)
def test_to_decimal_accepts_exact_input_types(source: object, expected: Decimal) -> None:
    assert to_decimal(source) == expected


@pytest.mark.parametrize("source", [12.34, True])
def test_to_decimal_rejects_unsafe_numeric_types(source: object) -> None:
    with pytest.raises(DataValidationError) as raised:
        to_decimal(source)

    assert raised.value.code in {"money.binary_float_not_allowed", "money.invalid_type"}


@pytest.mark.parametrize("source", ["", "not-a-number", "NaN", "Infinity"])
def test_to_decimal_rejects_invalid_or_non_finite_values(source: str) -> None:
    with pytest.raises(DataValidationError):
        to_decimal(source)


def test_quantize_rub_uses_kopecks() -> None:
    assert quantize_rub("123.456") == Decimal("123.46")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1.005", Decimal("1.01")),
        ("-1.005", Decimal("-1.01")),
    ],
)
def test_quantize_rub_rounds_half_away_from_zero(source: str, expected: Decimal) -> None:
    assert quantize_rub(source) == expected


def test_currency_quantization_accepts_an_explicit_quantum() -> None:
    assert quantize_currency("12.3456", quantum="0.001") == Decimal("12.346")


@pytest.mark.parametrize("quantum", ["0", "-0.01"])
def test_currency_quantization_rejects_non_positive_quantum(quantum: str) -> None:
    with pytest.raises(DataValidationError) as raised:
        quantize_currency("10", quantum=quantum)

    assert raised.value.code == "money.invalid_quantum"


def test_round_up_to_increment_advances_to_next_step() -> None:
    assert round_up_to_increment("100.01", "0.05") == Decimal("100.05")


def test_round_up_to_increment_preserves_exact_boundary() -> None:
    assert round_up_to_increment("100.00", "0.05") == Decimal("100.00")


def test_round_up_to_increment_handles_value_slightly_above_boundary() -> None:
    assert round_up_to_increment("100.0000001", "0.05") == Decimal("100.05")


def test_round_up_to_increment_preserves_high_precision_remainder() -> None:
    value = Decimal("100.00000000000000000000000000001")

    assert round_up_to_increment(value, Decimal("0.05")) == Decimal("100.05")


@pytest.mark.parametrize("ambient_rounding", [ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP])
def test_increment_rounding_is_independent_of_ambient_decimal_rounding(
    ambient_rounding: str,
) -> None:
    value = Decimal("100.00000000000000000000000000001")

    with localcontext() as context:
        context.rounding = ambient_rounding
        result = round_up_to_increment(value, Decimal("0.05"))

    assert result == Decimal("100.05")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("-100.01", Decimal("-100.00")),
        ("-100.00", Decimal("-100.00")),
    ],
)
def test_increment_rounding_for_negative_values_uses_positive_infinity(
    value: str, expected: Decimal
) -> None:
    assert round_up_to_increment(value, "0.05") == expected


@pytest.mark.parametrize("increment", ["0", "-1"])
def test_round_up_to_increment_rejects_non_positive_step(increment: str) -> None:
    with pytest.raises(DataValidationError) as raised:
        round_up_to_increment("10", increment)

    assert raised.value.code == "money.invalid_increment"


def test_money_rounding_is_repeatable_and_ignores_ambient_rounding_mode() -> None:
    with localcontext() as context:
        context.rounding = ROUND_DOWN
        results = {quantize_rub("9.995") for _ in range(10)}

    assert results == {Decimal("10.00")}


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("10.004", "10.00", 0),
        ("10.005", "10.00", 1),
        ("9.99", "10.00", -1),
    ],
)
def test_compare_money_uses_canonical_quantization(
    left: str, right: str, expected: int
) -> None:
    assert compare_money(left, right) == expected

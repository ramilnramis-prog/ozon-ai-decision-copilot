"""Decimal-only construction and rounding primitives for monetary values."""

from __future__ import annotations

from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext
from typing import Final, TypeAlias

from app.core.errors import DataValidationError


MoneyInput: TypeAlias = Decimal | str | int

RUB_QUANTUM: Final = Decimal("0.01")
MONEY_PRECISION: Final = 28


def to_decimal(value: MoneyInput, *, field_name: str = "money") -> Decimal:
    """Construct a finite Decimal without accepting binary floating-point input."""

    if isinstance(value, bool):
        raise DataValidationError(
            f"{field_name} must be a Decimal, string, or integer",
            code="money.invalid_type",
            scope=field_name,
        )
    if isinstance(value, float):
        raise DataValidationError(
            f"{field_name} must not use binary floating-point input",
            code="money.binary_float_not_allowed",
            scope=field_name,
        )

    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            raise DataValidationError(
                f"{field_name} must not be empty",
                code="money.invalid_decimal",
                scope=field_name,
            )
        try:
            result = Decimal(candidate)
        except DecimalException as exc:
            raise DataValidationError(
                f"{field_name} is not a valid decimal value",
                code="money.invalid_decimal",
                scope=field_name,
            ) from exc
    else:
        raise DataValidationError(
            f"{field_name} must be a Decimal, string, or integer",
            code="money.invalid_type",
            scope=field_name,
        )

    if not result.is_finite():
        raise DataValidationError(
            f"{field_name} must be finite",
            code="money.non_finite",
            scope=field_name,
        )
    return result


def quantize_currency(
    value: MoneyInput,
    *,
    quantum: MoneyInput,
) -> Decimal:
    """Round a monetary value to a positive currency quantum, half away from zero."""

    amount = to_decimal(value)
    unit = to_decimal(quantum, field_name="currency quantum")
    if unit <= 0:
        raise DataValidationError(
            "currency quantum must be greater than zero",
            code="money.invalid_quantum",
            scope="currency quantum",
        )

    try:
        with localcontext() as context:
            context.prec = MONEY_PRECISION
            return amount.quantize(unit, rounding=ROUND_HALF_UP)
    except DecimalException as exc:
        raise DataValidationError(
            "money value cannot be represented at the requested currency precision",
            code="money.quantization_failed",
            scope="money",
        ) from exc


def quantize_rub(value: MoneyInput) -> Decimal:
    """Round RUB to kopecks using the project currency rule."""

    return quantize_currency(value, quantum=RUB_QUANTUM)


def _signed_coefficient_and_exponent(value: Decimal) -> tuple[int, int]:
    """Return an exact signed base-10 coefficient and exponent for a finite Decimal."""

    decimal_tuple = value.as_tuple()
    coefficient = 0
    for digit in decimal_tuple.digits:
        coefficient = coefficient * 10 + digit
    if decimal_tuple.sign:
        coefficient = -coefficient

    # ``to_decimal`` rejects non-finite values, whose exponents are non-integers.
    assert isinstance(decimal_tuple.exponent, int)
    return coefficient, decimal_tuple.exponent


def _decimal_from_coefficient(coefficient: int, exponent: int) -> Decimal:
    """Build a Decimal exactly, without consulting the ambient Decimal context."""

    sign = int(coefficient < 0)
    digits = Decimal(abs(coefficient)).as_tuple().digits
    return Decimal((sign, digits, exponent))


def round_up_to_increment(value: MoneyInput, increment: MoneyInput) -> Decimal:
    """Round toward positive infinity to the next configured price increment."""

    amount = to_decimal(value)
    step = to_decimal(increment, field_name="price increment")
    if step <= 0:
        raise DataValidationError(
            "price increment must be greater than zero",
            code="money.invalid_increment",
            scope="price increment",
        )

    amount_coefficient, amount_exponent = _signed_coefficient_and_exponent(amount)
    step_coefficient, step_exponent = _signed_coefficient_and_exponent(step)
    common_exponent = min(amount_exponent, step_exponent)

    amount_units = amount_coefficient * 10 ** (amount_exponent - common_exponent)
    step_units = step_coefficient * 10 ** (step_exponent - common_exponent)
    step_count, remainder = divmod(amount_units, step_units)
    if remainder:
        step_count += 1

    return _decimal_from_coefficient(step_count * step_coefficient, step_exponent)


def compare_money(
    left: MoneyInput,
    right: MoneyInput,
    *,
    quantum: MoneyInput = RUB_QUANTUM,
) -> int:
    """Compare values after applying the same canonical display/decision rounding."""

    normalized_left = quantize_currency(left, quantum=quantum)
    normalized_right = quantize_currency(right, quantum=quantum)
    return (normalized_left > normalized_right) - (normalized_left < normalized_right)

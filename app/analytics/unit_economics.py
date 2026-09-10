"""Pure deterministic contribution-economics and safe-price analytics.

The closed-form price boundaries are authoritative only for the normalized
linear cost model supplied in :class:`UnitEconomicsInput`.  They deliberately
do not model Ozon tariff tiers, buyout adjustments, taxes, or price-dependent
fees.  A future provider may normalize those inputs for a scenario without
changing this calculation boundary.

Current-price components retain their exact normalized Decimal precision; the
policy currency quantum remains available to later presenters.  Only the
minimum-safe boundary is operationally rounded here, always upward to the
configured price increment and then revalidated against the exact inequalities.
"""

from __future__ import annotations

from decimal import (
    Context,
    Decimal,
    MAX_EMAX,
    MIN_EMIN,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    localcontext,
)

from app.core.money import round_up_to_increment
from app.domain.common import AvailabilityStatus, SafetyStatus, require_instance
from app.domain.economics import (
    ECONOMICS_RATIO_PRECISION,
    EconomicsPolicy,
    PriceBoundary,
    UnitEconomicsInput,
    UnitEconomicsResult,
    materialize_economics_ratio,
)


def _exact_sum(*values: Decimal) -> Decimal:
    """Add finite Decimals exactly under an isolated, sufficiently wide context."""

    if not values:
        return Decimal(0)
    exponents = tuple(value.as_tuple().exponent for value in values)
    assert all(isinstance(exponent, int) for exponent in exponents)
    common_exponent = min(exponents)
    aligned_digits = tuple(
        len(value.as_tuple().digits) + exponent - common_exponent
        for value, exponent in zip(values, exponents, strict=True)
    )
    precision = max(aligned_digits) + len(str(len(values))) + 2
    with localcontext(
        Context(
            prec=max(precision, 1),
            rounding=ROUND_HALF_EVEN,
            Emin=MIN_EMIN,
            Emax=MAX_EMAX,
        )
    ):
        return sum(values, Decimal(0))


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    """Multiply finite Decimals exactly without ambient-context rounding."""

    precision = len(left.as_tuple().digits) + len(right.as_tuple().digits) + 2
    with localcontext(
        Context(
            prec=max(precision, 1),
            rounding=ROUND_HALF_EVEN,
            Emin=MIN_EMIN,
            Emax=MAX_EMAX,
        )
    ):
        return left * right


def _difference(left: Decimal, right: Decimal) -> Decimal:
    return _exact_sum(left, right.copy_negate())


def _boundary_precision(
    numerator: Decimal,
    denominator: Decimal,
    price_increment: Decimal,
) -> int:
    if numerator == 0:
        integer_digits = 1
    else:
        integer_digits = max(1, numerator.adjusted() - denominator.adjusted() + 2)
    increment_exponent = price_increment.as_tuple().exponent
    assert isinstance(increment_exponent, int)
    fractional_digits = max(0, -increment_exponent)
    return max(
        ECONOMICS_RATIO_PRECISION,
        integer_digits + fractional_digits + 20,
        len(numerator.as_tuple().digits)
        + len(denominator.as_tuple().digits)
        + len(price_increment.as_tuple().digits)
        + 10,
    )


def _round_ratio_up(
    numerator: Decimal,
    denominator: Decimal,
    price_increment: Decimal,
) -> Decimal:
    """Round an exact non-negative ratio upward and revalidate the inequality."""

    with localcontext(
        Context(
            prec=_boundary_precision(numerator, denominator, price_increment),
            rounding=ROUND_FLOOR,
            Emin=MIN_EMIN,
            Emax=MAX_EMAX,
        )
    ):
        lower_materialization = numerator / denominator
    rounded = round_up_to_increment(lower_materialization, price_increment)
    while _exact_product(rounded, denominator) < numerator:
        rounded = round_up_to_increment(
            _exact_sum(rounded, price_increment),
            price_increment,
        )
    return rounded


def _resolve_drr(
    source: UnitEconomicsInput,
) -> tuple[AvailabilityStatus, Decimal | None]:
    if source.drr is not None:
        return AvailabilityStatus.AVAILABLE, source.drr
    if source.advertising_spend is None or source.attributable_revenue is None:
        return AvailabilityStatus.INSUFFICIENT_DATA, None
    if source.attributable_revenue == 0:
        return AvailabilityStatus.NOT_APPLICABLE, None
    return (
        AvailabilityStatus.AVAILABLE,
        materialize_economics_ratio(
            source.advertising_spend,
            source.attributable_revenue,
        ),
    )


def _evidence_refs(
    source: UnitEconomicsInput,
    policy: EconomicsPolicy,
) -> tuple[str, ...]:
    values = (
        source.provenance.source_record_id,
        f"policy:{policy.identity.policy_id}:{policy.identity.version}",
    )
    return tuple(dict.fromkeys(value for value in values if value is not None))


def _insufficient_boundary() -> PriceBoundary:
    return PriceBoundary(AvailabilityStatus.INSUFFICIENT_DATA, None)


def _impossible_boundary() -> PriceBoundary:
    return PriceBoundary(AvailabilityStatus.NOT_APPLICABLE, None)


def _resolve_lower_price_boundary(
    numerator: Decimal,
    denominator: Decimal,
    price_increment: Decimal,
) -> tuple[PriceBoundary, Decimal | None]:
    """Resolve ``price * denominator >= numerator`` for positive prices.

    A zero numerator and zero denominator make the constraint universally
    satisfied, represented by the project's existing zero lower boundary.
    A negative denominator remains impossible even when the numerator is zero.
    """

    if denominator > 0:
        return (
            PriceBoundary(
                AvailabilityStatus.AVAILABLE,
                materialize_economics_ratio(numerator, denominator),
            ),
            _round_ratio_up(numerator, denominator, price_increment),
        )
    if denominator == 0 and numerator == 0:
        return PriceBoundary(AvailabilityStatus.AVAILABLE, Decimal(0)), Decimal(0)
    return _impossible_boundary(), None


def calculate_unit_economics(
    source: UnitEconomicsInput,
    policy: EconomicsPolicy,
) -> UnitEconomicsResult:
    """Calculate current contribution economics and normalized price boundaries."""

    source = require_instance(
        source,
        UnitEconomicsInput,
        field_name="unit economics input",
    )
    policy = require_instance(
        policy,
        EconomicsPolicy,
        field_name="economics policy",
    )

    drr_status, drr = _resolve_drr(source)
    commission_cost = (
        _exact_product(source.selling_price, source.commission_rate)
        if source.commission_rate is not None
        else source.commission_per_unit
    )
    advertising_cost = (
        _exact_product(source.selling_price, drr)
        if drr_status is AvailabilityStatus.AVAILABLE and drr is not None
        else source.advertising_cost_per_unit
    )
    other_variable_cost_total = _exact_sum(
        *(component.amount for component in source.other_variable_costs)
    )

    if commission_cost is None or advertising_cost is None:
        proportional_cost_rate = None
    else:
        proportional_cost_rate = _exact_sum(
            source.commission_rate or Decimal(0),
            drr if drr_status is AvailabilityStatus.AVAILABLE and drr is not None else Decimal(0),
        )

    required_costs = (
        source.cost_of_goods,
        source.logistics_cost_per_unit,
        commission_cost,
        advertising_cost,
        proportional_cost_rate,
    )
    if any(value is None for value in required_costs):
        unavailable = _insufficient_boundary()
        return UnitEconomicsResult(
            source_input=source,
            policy=policy,
            status=AvailabilityStatus.INSUFFICIENT_DATA,
            safety_status=SafetyStatus.UNAVAILABLE,
            commission_cost=commission_cost,
            advertising_cost=advertising_cost,
            other_variable_cost_total=other_variable_cost_total,
            total_variable_cost=None,
            profit_per_unit=None,
            contribution_margin=None,
            drr_status=drr_status,
            drr=drr,
            proportional_cost_rate=proportional_cost_rate,
            fixed_cost_total=None,
            break_even=unavailable,
            minimum_profit=unavailable,
            minimum_margin=unavailable,
            minimum_safe=unavailable,
            evidence_refs=_evidence_refs(source, policy),
        )

    assert source.cost_of_goods is not None
    assert source.logistics_cost_per_unit is not None
    assert commission_cost is not None
    assert advertising_cost is not None
    assert proportional_cost_rate is not None

    fixed_cost_total = _exact_sum(
        source.cost_of_goods,
        source.logistics_cost_per_unit,
        other_variable_cost_total,
        source.commission_per_unit or Decimal(0),
        (
            source.advertising_cost_per_unit or Decimal(0)
            if drr_status is not AvailabilityStatus.AVAILABLE
            else Decimal(0)
        ),
    )
    total_variable_cost = _exact_sum(
        source.cost_of_goods,
        source.logistics_cost_per_unit,
        commission_cost,
        advertising_cost,
        other_variable_cost_total,
    )
    profit_per_unit = _difference(source.selling_price, total_variable_cost)
    contribution_margin = materialize_economics_ratio(
        profit_per_unit,
        source.selling_price,
    )

    base_denominator = _difference(Decimal(1), proportional_cost_rate)
    margin_denominator = _difference(base_denominator, policy.minimum_margin)
    minimum_profit_numerator = _exact_sum(
        fixed_cost_total,
        policy.minimum_profit_per_unit,
    )
    break_even, rounded_break_even = _resolve_lower_price_boundary(
        fixed_cost_total,
        base_denominator,
        policy.price_increment,
    )
    minimum_profit, rounded_minimum_profit = _resolve_lower_price_boundary(
        minimum_profit_numerator,
        base_denominator,
        policy.price_increment,
    )
    minimum_margin, rounded_minimum_margin = _resolve_lower_price_boundary(
        fixed_cost_total,
        margin_denominator,
        policy.price_increment,
    )

    required_rounded_prices = (
        rounded_break_even,
        rounded_minimum_profit,
        rounded_minimum_margin,
    )
    if any(price is None for price in required_rounded_prices):
        minimum_safe = _impossible_boundary()
        safety_status = SafetyStatus.UNSAFE
    else:
        assert rounded_break_even is not None
        assert rounded_minimum_profit is not None
        assert rounded_minimum_margin is not None
        floor = round_up_to_increment(
            policy.price_floor or Decimal(0),
            policy.price_increment,
        )
        safe_price = max(
            floor,
            rounded_break_even,
            rounded_minimum_profit,
            rounded_minimum_margin,
        )

        # Defense in depth: validate the rounded boundary against exact linear
        # inequalities, not its materialized division strings.
        while True:
            safe_profit = _difference(
                _exact_product(safe_price, base_denominator),
                fixed_cost_total,
            )
            meets_profit = safe_profit >= policy.minimum_profit_per_unit
            meets_margin = safe_profit >= _exact_product(
                safe_price,
                policy.minimum_margin,
            )
            meets_floor = policy.price_floor is None or safe_price >= policy.price_floor
            if meets_profit and meets_margin and meets_floor:
                break
            safe_price = round_up_to_increment(
                _exact_sum(safe_price, policy.price_increment),
                policy.price_increment,
            )

        minimum_safe = PriceBoundary(AvailabilityStatus.AVAILABLE, safe_price)
        safety_status = (
            SafetyStatus.SAFE
            if source.selling_price >= safe_price
            else SafetyStatus.UNSAFE
        )

    return UnitEconomicsResult(
        source_input=source,
        policy=policy,
        status=AvailabilityStatus.AVAILABLE,
        safety_status=safety_status,
        commission_cost=commission_cost,
        advertising_cost=advertising_cost,
        other_variable_cost_total=other_variable_cost_total,
        total_variable_cost=total_variable_cost,
        profit_per_unit=profit_per_unit,
        contribution_margin=contribution_margin,
        drr_status=drr_status,
        drr=drr,
        proportional_cost_rate=proportional_cost_rate,
        fixed_cost_total=fixed_cost_total,
        break_even=break_even,
        minimum_profit=minimum_profit,
        minimum_margin=minimum_margin,
        minimum_safe=minimum_safe,
        evidence_refs=_evidence_refs(source, policy),
    )

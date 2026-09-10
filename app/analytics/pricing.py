"""Pure price-only what-if simulation using the unit-economics engine.

Each candidate is evaluated under the original normalized rates and absolute
per-unit costs.  The simulator does not predict demand, revenue, conversion, or
marketplace tariff changes, and it never mutates the source economics input.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Context, Decimal, MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, localcontext

from app.analytics.unit_economics import calculate_unit_economics
from app.core.errors import DataValidationError
from app.domain.common import AvailabilityStatus, require_instance
from app.domain.economics import (
    EconomicsPolicy,
    PricingScenario,
    PricingScenarioResult,
    UnitEconomicsInput,
    UnitEconomicsResult,
)


def _exact_difference(left: Decimal, right: Decimal) -> Decimal:
    """Subtract finite Decimals exactly without using the ambient context."""

    exponents = (left.as_tuple().exponent, right.as_tuple().exponent)
    assert all(isinstance(exponent, int) for exponent in exponents)
    common_exponent = min(exponents)
    precision = (
        max(
            len(value.as_tuple().digits) + exponent - common_exponent
            for value, exponent in zip((left, right), exponents, strict=True)
        )
        + 3
    )
    with localcontext(
        Context(
            prec=max(precision, 1),
            rounding=ROUND_HALF_EVEN,
            Emin=MIN_EMIN,
            Emax=MAX_EMAX,
        )
    ):
        return left - right


def _scenario_evidence(
    current: UnitEconomicsResult,
    candidate: UnitEconomicsResult,
    scenario: PricingScenario,
) -> tuple[str, ...]:
    values = (
        *current.evidence_refs,
        *candidate.evidence_refs,
        scenario.provenance.source_record_id,
    )
    return tuple(dict.fromkeys(value for value in values if value is not None))


def simulate_price(
    source: UnitEconomicsInput,
    policy: EconomicsPolicy,
    scenario: PricingScenario,
) -> PricingScenarioResult:
    """Evaluate one hypothetical price under unchanged normalized cost assumptions."""

    source = require_instance(source, UnitEconomicsInput, field_name="pricing source input")
    policy = require_instance(policy, EconomicsPolicy, field_name="pricing policy")
    scenario = require_instance(scenario, PricingScenario, field_name="pricing scenario")
    if scenario.sku != source.sku or scenario.currency is not source.currency:
        raise DataValidationError(
            "pricing scenario must match the source SKU and currency",
            code="pricing.scenario_identity_mismatch",
            scope="pricing scenario",
        )

    current = calculate_unit_economics(source, policy)
    candidate_input = replace(
        source,
        selling_price=scenario.hypothetical_price,
        provenance=scenario.provenance,
    )
    candidate = calculate_unit_economics(candidate_input, policy)

    profit_delta = (
        _exact_difference(candidate.profit_per_unit, current.profit_per_unit)
        if candidate.profit_per_unit is not None and current.profit_per_unit is not None
        else None
    )
    margin_delta = (
        _exact_difference(candidate.contribution_margin, current.contribution_margin)
        if candidate.contribution_margin is not None
        and current.contribution_margin is not None
        else None
    )
    distance_from_safe_price = (
        _exact_difference(scenario.hypothetical_price, candidate.minimum_safe_price)
        if candidate.minimum_safe_price is not None
        else None
    )
    status = (
        AvailabilityStatus.AVAILABLE
        if current.status is AvailabilityStatus.AVAILABLE
        and candidate.status is AvailabilityStatus.AVAILABLE
        else AvailabilityStatus.INSUFFICIENT_DATA
    )

    return PricingScenarioResult(
        scenario=scenario,
        status=status,
        safety_status=candidate.safety_status,
        current_economics=current,
        economics=candidate,
        price_delta=_exact_difference(
            scenario.hypothetical_price,
            source.selling_price,
        ),
        profit_per_unit_delta=profit_delta,
        margin_delta=margin_delta,
        distance_from_safe_price=distance_from_safe_price,
        evidence_refs=_scenario_evidence(current, candidate, scenario),
    )


def simulate_prices(
    source: UnitEconomicsInput,
    policy: EconomicsPolicy,
    scenarios: Iterable[PricingScenario],
) -> tuple[PricingScenarioResult, ...]:
    """Evaluate scenarios independently and preserve their supplied order."""

    return tuple(simulate_price(source, policy, scenario) for scenario in scenarios)

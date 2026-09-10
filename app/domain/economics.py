"""Unit-economics inputs, policies, deterministic result containers, and scenarios."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Context, Decimal, MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, localcontext

from app.core.clock import require_aware_datetime
from app.core.errors import DataValidationError
from app.core.money import round_up_to_increment
from app.domain.common import (
    AvailabilityStatus,
    Currency,
    DateRange,
    PolicyIdentity,
    Provenance,
    SafetyStatus,
    SkuId,
    normalize_text_tuple,
    require_decimal,
    require_instance,
    require_non_negative_decimal,
    require_positive_decimal,
    require_text,
    require_unit_rate,
)


ECONOMICS_RATIO_PRECISION = 40


def _exact_decimal_sum(*values: Decimal) -> Decimal:
    """Add finite Decimals exactly without consulting the ambient context."""

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


def _exact_decimal_product(left: Decimal, right: Decimal) -> Decimal:
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


def materialize_economics_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Materialize a business ratio under one deterministic private context."""

    with localcontext(
        Context(
            prec=ECONOMICS_RATIO_PRECISION,
            rounding=ROUND_HALF_EVEN,
            Emin=MIN_EMIN,
            Emax=MAX_EMAX,
        )
    ):
        return numerator / denominator


def _expected_lower_price_boundary(
    numerator: Decimal,
    denominator: Decimal,
) -> tuple[AvailabilityStatus, Decimal | None]:
    """Return the expected boundary for ``price * denominator >= numerator``."""

    if denominator > 0:
        return (
            AvailabilityStatus.AVAILABLE,
            materialize_economics_ratio(numerator, denominator),
        )
    if denominator == 0 and numerator == 0:
        return AvailabilityStatus.AVAILABLE, Decimal(0)
    return AvailabilityStatus.NOT_APPLICABLE, None


def _optional_non_negative(value: Decimal | None, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    return require_non_negative_decimal(value, field_name=field_name)


@dataclass(frozen=True, slots=True)
class CostComponent:
    """Named per-unit variable cost in the parent input's currency."""

    name: str
    amount: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_text(self.name, field_name="cost name"))
        object.__setattr__(
            self,
            "amount",
            require_non_negative_decimal(self.amount, field_name="cost amount"),
        )


@dataclass(frozen=True, slots=True)
class EconomicsPolicy:
    """Explicit profitability and rounding constraints for later analytics."""

    identity: PolicyIdentity
    currency: Currency
    minimum_profit_per_unit: Decimal
    minimum_margin: Decimal
    price_floor: Decimal | None
    currency_quantum: Decimal
    price_increment: Decimal

    def __post_init__(self) -> None:
        require_instance(self.identity, PolicyIdentity, field_name="economics policy identity")
        require_instance(self.currency, Currency, field_name="economics policy currency")
        object.__setattr__(
            self,
            "minimum_profit_per_unit",
            require_non_negative_decimal(
                self.minimum_profit_per_unit, field_name="minimum profit per unit"
            ),
        )
        object.__setattr__(
            self,
            "minimum_margin",
            require_unit_rate(
                self.minimum_margin,
                field_name="minimum margin",
                allow_one=False,
            ),
        )
        if self.price_floor is not None:
            object.__setattr__(
                self,
                "price_floor",
                require_non_negative_decimal(self.price_floor, field_name="price floor"),
            )
        object.__setattr__(
            self,
            "currency_quantum",
            require_positive_decimal(self.currency_quantum, field_name="currency quantum"),
        )
        object.__setattr__(
            self,
            "price_increment",
            require_positive_decimal(self.price_increment, field_name="price increment"),
        )


@dataclass(frozen=True, slots=True)
class UnitEconomicsInput:
    """Normalized cost/rate inputs; None remains distinct from a validated zero."""

    sku: SkuId
    selling_price: Decimal
    currency: Currency
    cost_of_goods: Decimal | None
    logistics_cost_per_unit: Decimal | None
    commission_rate: Decimal | None
    commission_per_unit: Decimal | None
    advertising_cost_per_unit: Decimal | None
    drr: Decimal | None
    advertising_spend: Decimal | None
    attributable_revenue: Decimal | None
    other_variable_costs: tuple[CostComponent, ...]
    source_period: DateRange | None
    provenance: Provenance

    def __post_init__(self) -> None:
        require_instance(self.sku, SkuId, field_name="economics sku")
        object.__setattr__(
            self,
            "selling_price",
            require_positive_decimal(self.selling_price, field_name="selling price"),
        )
        require_instance(self.currency, Currency, field_name="economics currency")

        for field_name in (
            "cost_of_goods",
            "logistics_cost_per_unit",
            "commission_per_unit",
            "advertising_cost_per_unit",
            "advertising_spend",
            "attributable_revenue",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_non_negative(getattr(self, field_name), field_name=field_name),
            )

        if self.commission_rate is not None:
            object.__setattr__(
                self,
                "commission_rate",
                require_unit_rate(self.commission_rate, field_name="commission rate"),
            )
        if self.commission_rate is not None and self.commission_per_unit is not None:
            raise DataValidationError(
                "commission rate and per-unit commission are mutually exclusive",
                code="economics.conflicting_commission_inputs",
                scope="commission",
            )
        if self.drr is not None:
            object.__setattr__(
                self,
                "drr",
                require_non_negative_decimal(self.drr, field_name="DRR"),
            )

        components = tuple(self.other_variable_costs)
        for component in components:
            require_instance(component, CostComponent, field_name="other variable cost")
        names = tuple(component.name for component in components)
        if len(set(names)) != len(names):
            raise DataValidationError(
                "other variable cost names must be unique",
                code="economics.duplicate_cost_component",
                scope="other_variable_costs",
            )
        object.__setattr__(self, "other_variable_costs", components)

        if self.source_period is not None:
            require_instance(self.source_period, DateRange, field_name="economics source period")
        require_instance(self.provenance, Provenance, field_name="economics provenance")


@dataclass(frozen=True, slots=True)
class PriceBoundary:
    """A finite calculated price or an explicit unavailable/impossible state."""

    status: AvailabilityStatus
    price: Decimal | None

    def __post_init__(self) -> None:
        require_instance(self.status, AvailabilityStatus, field_name="price boundary status")
        if self.status not in {
            AvailabilityStatus.AVAILABLE,
            AvailabilityStatus.INSUFFICIENT_DATA,
            AvailabilityStatus.NOT_APPLICABLE,
        }:
            raise DataValidationError(
                "price boundary status is invalid",
                code="economics.invalid_boundary_status",
                scope="price boundary",
            )
        if self.status is AvailabilityStatus.AVAILABLE:
            if self.price is None:
                raise DataValidationError(
                    "available price boundary requires a finite price",
                    code="economics.missing_boundary_price",
                    scope="price boundary",
                )
            object.__setattr__(
                self,
                "price",
                require_non_negative_decimal(self.price, field_name="price boundary"),
            )
        elif self.price is not None:
            raise DataValidationError(
                "unavailable price boundary must not contain a price",
                code="economics.price_on_unavailable_boundary",
                scope="price boundary",
            )


@dataclass(frozen=True, slots=True)
class UnitEconomicsResult:
    """Immutable deterministic contribution-economics result with partial facts."""

    source_input: UnitEconomicsInput
    policy: EconomicsPolicy
    status: AvailabilityStatus
    safety_status: SafetyStatus
    commission_cost: Decimal | None
    advertising_cost: Decimal | None
    other_variable_cost_total: Decimal
    total_variable_cost: Decimal | None
    profit_per_unit: Decimal | None
    contribution_margin: Decimal | None
    drr_status: AvailabilityStatus
    drr: Decimal | None
    proportional_cost_rate: Decimal | None
    fixed_cost_total: Decimal | None
    break_even: PriceBoundary
    minimum_profit: PriceBoundary
    minimum_margin: PriceBoundary
    minimum_safe: PriceBoundary
    evidence_refs: tuple[str, ...]

    @property
    def sku(self) -> SkuId:
        return self.source_input.sku

    @property
    def currency(self) -> Currency:
        return self.source_input.currency

    @property
    def selling_price(self) -> Decimal:
        return self.source_input.selling_price

    @property
    def cost_of_goods(self) -> Decimal | None:
        return self.source_input.cost_of_goods

    @property
    def logistics_cost(self) -> Decimal | None:
        return self.source_input.logistics_cost_per_unit

    @property
    def other_variable_costs(self) -> tuple[CostComponent, ...]:
        return self.source_input.other_variable_costs

    @property
    def break_even_price(self) -> Decimal | None:
        return self.break_even.price

    @property
    def minimum_profit_price(self) -> Decimal | None:
        return self.minimum_profit.price

    @property
    def minimum_margin_price(self) -> Decimal | None:
        return self.minimum_margin.price

    @property
    def minimum_safe_price(self) -> Decimal | None:
        return self.minimum_safe.price

    def __post_init__(self) -> None:
        require_instance(
            self.source_input,
            UnitEconomicsInput,
            field_name="economics result source input",
        )
        require_instance(self.policy, EconomicsPolicy, field_name="economics result policy")
        require_instance(self.status, AvailabilityStatus, field_name="economics result status")
        require_instance(self.safety_status, SafetyStatus, field_name="economics safety status")
        require_instance(self.drr_status, AvailabilityStatus, field_name="DRR status")
        for field_name in (
            "break_even",
            "minimum_profit",
            "minimum_margin",
            "minimum_safe",
        ):
            require_instance(
                getattr(self, field_name),
                PriceBoundary,
                field_name=field_name,
            )
        if self.policy.currency is not self.currency:
            raise DataValidationError(
                "economics input and policy currencies must match",
                code="economics.currency_mismatch",
                scope="economics result",
            )

        object.__setattr__(
            self,
            "evidence_refs",
            normalize_text_tuple(self.evidence_refs, field_name="economics evidence refs"),
        )
        if self.status is AvailabilityStatus.AVAILABLE and not self.evidence_refs:
            raise DataValidationError(
                "available economics result requires evidence",
                code="economics.missing_result_evidence",
                scope="economics evidence refs",
            )
        required_evidence = {
            f"policy:{self.policy.identity.policy_id}:{self.policy.identity.version}"
        }
        if self.source_input.provenance.source_record_id is not None:
            required_evidence.add(self.source_input.provenance.source_record_id)
        if not required_evidence.issubset(self.evidence_refs):
            raise DataValidationError(
                "economics evidence must retain source and policy references",
                code="economics.missing_traceability_evidence",
                scope="economics evidence refs",
            )

        for field_name in (
            "commission_cost",
            "advertising_cost",
            "total_variable_cost",
            "fixed_cost_total",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    require_non_negative_decimal(value, field_name=field_name),
                )
        object.__setattr__(
            self,
            "other_variable_cost_total",
            require_non_negative_decimal(
                self.other_variable_cost_total,
                field_name="other variable cost total",
            ),
        )
        if self.profit_per_unit is not None:
            object.__setattr__(
                self,
                "profit_per_unit",
                require_decimal(self.profit_per_unit, field_name="profit per unit"),
            )
        if self.contribution_margin is not None:
            object.__setattr__(
                self,
                "contribution_margin",
                require_decimal(
                    self.contribution_margin,
                    field_name="contribution margin",
                ),
            )
        if self.proportional_cost_rate is not None:
            object.__setattr__(
                self,
                "proportional_cost_rate",
                require_non_negative_decimal(
                    self.proportional_cost_rate,
                    field_name="proportional cost rate",
                ),
            )

        self._validate_source_derived_facts()
        self._validate_current_economics()
        self._validate_boundaries()

    def _validate_source_derived_facts(self) -> None:
        expected_other_total = _exact_decimal_sum(
            *(component.amount for component in self.other_variable_costs)
        )
        if self.other_variable_cost_total != expected_other_total:
            raise DataValidationError(
                "other variable cost total must reconcile to its components",
                code="economics.inconsistent_other_cost_total",
                scope="other variable costs",
            )

        source = self.source_input
        if source.drr is not None:
            expected_drr_status = AvailabilityStatus.AVAILABLE
            expected_drr = source.drr
        elif (
            source.advertising_spend is not None
            and source.attributable_revenue is not None
        ):
            if source.attributable_revenue == 0:
                expected_drr_status = AvailabilityStatus.NOT_APPLICABLE
                expected_drr = None
            else:
                expected_drr_status = AvailabilityStatus.AVAILABLE
                expected_drr = materialize_economics_ratio(
                    source.advertising_spend,
                    source.attributable_revenue,
                )
        else:
            expected_drr_status = AvailabilityStatus.INSUFFICIENT_DATA
            expected_drr = None
        if self.drr_status is not expected_drr_status or self.drr != expected_drr:
            raise DataValidationError(
                "DRR result must match the normalized source inputs",
                code="economics.inconsistent_drr_result",
                scope="drr",
            )

        expected_commission = (
            _exact_decimal_product(self.selling_price, source.commission_rate)
            if source.commission_rate is not None
            else source.commission_per_unit
        )
        if self.commission_cost != expected_commission:
            raise DataValidationError(
                "commission cost must match the normalized commission input",
                code="economics.inconsistent_commission_cost",
                scope="commission cost",
            )

        expected_advertising = (
            _exact_decimal_product(self.selling_price, self.drr)
            if self.drr_status is AvailabilityStatus.AVAILABLE
            else source.advertising_cost_per_unit
        )
        if self.advertising_cost != expected_advertising:
            raise DataValidationError(
                "advertising cost must match DRR or the per-unit fallback",
                code="economics.inconsistent_advertising_cost",
                scope="advertising cost",
            )

        if expected_commission is None or expected_advertising is None:
            expected_rate = None
        else:
            expected_rate = _exact_decimal_sum(
                source.commission_rate or Decimal(0),
                self.drr if self.drr_status is AvailabilityStatus.AVAILABLE else Decimal(0),
            )
        if self.proportional_cost_rate != expected_rate:
            raise DataValidationError(
                "proportional cost rate must match commission and advertising semantics",
                code="economics.inconsistent_proportional_rate",
                scope="proportional cost rate",
            )

    def _validate_current_economics(self) -> None:
        required_costs = (
            self.cost_of_goods,
            self.logistics_cost,
            self.commission_cost,
            self.advertising_cost,
            self.proportional_cost_rate,
        )
        complete = all(value is not None for value in required_costs)
        if complete:
            if self.status is not AvailabilityStatus.AVAILABLE:
                raise DataValidationError(
                    "complete economics inputs require an available result",
                    code="economics.inconsistent_result_availability",
                    scope="economics result",
                )
            if any(
                value is None
                for value in (
                    self.fixed_cost_total,
                    self.total_variable_cost,
                    self.profit_per_unit,
                    self.contribution_margin,
                )
            ):
                raise DataValidationError(
                    "available economics result requires complete calculated values",
                    code="economics.incomplete_available_result",
                    scope="economics result",
                )
            assert self.cost_of_goods is not None
            assert self.logistics_cost is not None
            assert self.commission_cost is not None
            assert self.advertising_cost is not None
            assert self.fixed_cost_total is not None
            assert self.total_variable_cost is not None
            assert self.profit_per_unit is not None
            assert self.contribution_margin is not None
            source = self.source_input
            expected_fixed = _exact_decimal_sum(
                self.cost_of_goods,
                self.logistics_cost,
                self.other_variable_cost_total,
                source.commission_per_unit or Decimal(0),
                (
                    source.advertising_cost_per_unit or Decimal(0)
                    if self.drr_status is not AvailabilityStatus.AVAILABLE
                    else Decimal(0)
                ),
            )
            expected_total = _exact_decimal_sum(
                self.cost_of_goods,
                self.logistics_cost,
                self.commission_cost,
                self.advertising_cost,
                self.other_variable_cost_total,
            )
            expected_profit = _exact_decimal_sum(
                self.selling_price,
                self.total_variable_cost.copy_negate(),
            )
            expected_margin = materialize_economics_ratio(
                self.profit_per_unit,
                self.selling_price,
            )
            if self.fixed_cost_total != expected_fixed:
                code = "economics.inconsistent_fixed_cost_total"
            elif self.total_variable_cost != expected_total:
                code = "economics.inconsistent_total_cost"
            elif self.profit_per_unit != expected_profit:
                code = "economics.inconsistent_profit"
            elif self.contribution_margin != expected_margin:
                code = "economics.inconsistent_margin"
            else:
                return
            raise DataValidationError(
                "available economics values do not reconcile",
                code=code,
                scope="economics result",
            )

        if self.status is not AvailabilityStatus.INSUFFICIENT_DATA:
            raise DataValidationError(
                "missing required costs require insufficient-data status",
                code="economics.inconsistent_result_availability",
                scope="economics result",
            )
        if any(
            value is not None
            for value in (
                self.fixed_cost_total,
                self.total_variable_cost,
                self.profit_per_unit,
                self.contribution_margin,
            )
        ):
            raise DataValidationError(
                "incomplete economics must not publish authoritative totals",
                code="economics.values_on_unavailable_result",
                scope="economics result",
            )
        if self.safety_status is not SafetyStatus.UNAVAILABLE:
            raise DataValidationError(
                "incomplete economics requires unavailable safety status",
                code="economics.invalid_safety_status",
                scope="safety status",
            )

    def _validate_boundaries(self) -> None:
        boundaries = (
            self.break_even,
            self.minimum_profit,
            self.minimum_margin,
        )
        if self.status is AvailabilityStatus.INSUFFICIENT_DATA:
            if any(
                boundary.status is not AvailabilityStatus.INSUFFICIENT_DATA
                for boundary in (*boundaries, self.minimum_safe)
            ):
                raise DataValidationError(
                    "incomplete economics cannot publish price boundaries",
                    code="economics.boundary_without_economics",
                    scope="price boundaries",
                )
            return

        assert self.proportional_cost_rate is not None
        base_denominator = _exact_decimal_sum(
            Decimal(1),
            self.proportional_cost_rate.copy_negate(),
        )
        margin_denominator = _exact_decimal_sum(
            base_denominator,
            self.policy.minimum_margin.copy_negate(),
        )
        assert self.fixed_cost_total is not None
        minimum_profit_numerator = _exact_decimal_sum(
            self.fixed_cost_total,
            self.policy.minimum_profit_per_unit,
        )
        expected_boundaries = (
            _expected_lower_price_boundary(
                self.fixed_cost_total,
                base_denominator,
            ),
            _expected_lower_price_boundary(
                minimum_profit_numerator,
                base_denominator,
            ),
            _expected_lower_price_boundary(
                self.fixed_cost_total,
                margin_denominator,
            ),
        )
        if tuple(boundary.status for boundary in boundaries) != tuple(
            status for status, _ in expected_boundaries
        ):
            raise DataValidationError(
                "price-boundary feasibility conflicts with the cost-rate denominators",
                code="economics.inconsistent_boundary_feasibility",
                scope="price boundaries",
            )

        for boundary, (_, expected_price) in zip(
            boundaries,
            expected_boundaries,
            strict=True,
        ):
            if boundary.price != expected_price:
                raise DataValidationError(
                    "price boundaries must reconcile to the normalized cost model",
                    code="economics.inconsistent_boundary_price",
                    scope="price boundaries",
                )

        safe_is_feasible = all(
            boundary.status is AvailabilityStatus.AVAILABLE for boundary in boundaries
        )
        expected_safe_status = (
            AvailabilityStatus.AVAILABLE
            if safe_is_feasible
            else AvailabilityStatus.NOT_APPLICABLE
        )
        if self.minimum_safe.status is not expected_safe_status:
            raise DataValidationError(
                "minimum-safe-price feasibility conflicts with its required boundaries",
                code="economics.inconsistent_safe_price_feasibility",
                scope="minimum safe price",
            )

        if not safe_is_feasible:
            if self.safety_status is not SafetyStatus.UNSAFE:
                raise DataValidationError(
                    "an impossible required boundary makes the current price unsafe",
                    code="economics.invalid_safety_status",
                    scope="safety status",
                )
            return

        assert self.minimum_safe.price is not None
        if (
            self.policy.price_floor is not None
            and self.minimum_safe.price < self.policy.price_floor
        ):
            raise DataValidationError(
                "minimum safe price cannot be below the configured floor",
                code="economics.safe_price_below_floor",
                scope="minimum safe price",
            )
        if (
            round_up_to_increment(
                self.minimum_safe.price,
                self.policy.price_increment,
            )
            != self.minimum_safe.price
        ):
            raise DataValidationError(
                "minimum safe price must align to the configured price increment",
                code="economics.safe_price_not_increment_aligned",
                scope="minimum safe price",
            )

        safe_profit = _exact_decimal_sum(
            _exact_decimal_product(self.minimum_safe.price, base_denominator),
            self.fixed_cost_total.copy_negate(),
        )
        if (
            safe_profit < 0
            or safe_profit < self.policy.minimum_profit_per_unit
            or safe_profit
            < _exact_decimal_product(
                self.minimum_safe.price,
                self.policy.minimum_margin,
            )
        ):
            raise DataValidationError(
                "minimum safe price does not satisfy its profitability constraints",
                code="economics.safe_price_below_boundary",
                scope="minimum safe price",
            )
        expected_safety = (
            SafetyStatus.SAFE
            if self.selling_price >= self.minimum_safe.price
            else SafetyStatus.UNSAFE
        )
        if self.safety_status is not expected_safety:
            raise DataValidationError(
                "current-price safety must match the minimum safe price",
                code="economics.invalid_safety_status",
                scope="safety status",
            )


@dataclass(frozen=True, slots=True)
class PricingScenario:
    """Read-only hypothetical price evaluated at an explicit analysis time."""

    scenario_id: str
    sku: SkuId
    hypothetical_price: Decimal
    currency: Currency
    as_of: datetime
    is_hypothetical: bool
    provenance: Provenance

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "scenario_id", require_text(self.scenario_id, field_name="scenario_id")
        )
        require_instance(self.sku, SkuId, field_name="scenario sku")
        object.__setattr__(
            self,
            "hypothetical_price",
            require_positive_decimal(
                self.hypothetical_price, field_name="hypothetical price"
            ),
        )
        require_instance(self.currency, Currency, field_name="scenario currency")
        require_aware_datetime(self.as_of, field_name="scenario as_of")
        if self.is_hypothetical is not True:
            raise DataValidationError(
                "pricing scenario must be explicitly marked hypothetical",
                code="economics.scenario_not_hypothetical",
                scope="is_hypothetical",
            )
        require_instance(self.provenance, Provenance, field_name="scenario provenance")


@dataclass(frozen=True, slots=True)
class PricingScenarioResult:
    """Immutable price-only comparison composed from authoritative economics results."""

    scenario: PricingScenario
    status: AvailabilityStatus
    safety_status: SafetyStatus
    current_economics: UnitEconomicsResult
    economics: UnitEconomicsResult
    price_delta: Decimal
    profit_per_unit_delta: Decimal | None
    margin_delta: Decimal | None
    distance_from_safe_price: Decimal | None
    evidence_refs: tuple[str, ...]

    @property
    def candidate_economics(self) -> UnitEconomicsResult:
        return self.economics

    @property
    def current_price(self) -> Decimal:
        return self.current_economics.selling_price

    @property
    def candidate_price(self) -> Decimal:
        return self.scenario.hypothetical_price

    def __post_init__(self) -> None:
        require_instance(self.scenario, PricingScenario, field_name="pricing scenario")
        require_instance(self.status, AvailabilityStatus, field_name="scenario result status")
        require_instance(self.safety_status, SafetyStatus, field_name="scenario safety status")
        require_instance(
            self.current_economics,
            UnitEconomicsResult,
            field_name="current scenario economics",
        )
        require_instance(
            self.economics,
            UnitEconomicsResult,
            field_name="candidate scenario economics",
        )
        object.__setattr__(
            self,
            "price_delta",
            require_decimal(self.price_delta, field_name="scenario price delta"),
        )
        for field_name in ("profit_per_unit_delta", "margin_delta", "distance_from_safe_price"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    require_decimal(value, field_name=field_name),
                )
        object.__setattr__(
            self,
            "evidence_refs",
            normalize_text_tuple(self.evidence_refs, field_name="scenario evidence refs"),
        )
        if self.status is AvailabilityStatus.AVAILABLE and not self.evidence_refs:
            raise DataValidationError(
                "available scenario result requires evidence",
                code="economics.missing_scenario_evidence",
                scope="scenario evidence refs",
            )

        current = self.current_economics
        candidate = self.economics
        if any(
            self.scenario.sku != result.sku
            or self.scenario.currency is not result.currency
            for result in (current, candidate)
        ):
            raise DataValidationError(
                "scenario and economics identities must match",
                code="economics.conflicting_scenario_identity",
                scope="scenario result",
            )
        if current.policy != candidate.policy:
            raise DataValidationError(
                "current and candidate economics must use the same policy",
                code="economics.conflicting_scenario_policy",
                scope="scenario result",
            )
        expected_candidate_input = replace(
            current.source_input,
            selling_price=self.scenario.hypothetical_price,
            provenance=self.scenario.provenance,
        )
        if candidate.source_input != expected_candidate_input:
            raise DataValidationError(
                "candidate economics must preserve the current normalized cost assumptions",
                code="economics.conflicting_scenario_assumptions",
                scope="scenario result",
            )

        expected_status = (
            AvailabilityStatus.AVAILABLE
            if current.status is AvailabilityStatus.AVAILABLE
            and candidate.status is AvailabilityStatus.AVAILABLE
            else AvailabilityStatus.INSUFFICIENT_DATA
        )
        if self.status is not expected_status:
            raise DataValidationError(
                "scenario availability must match the composed economics",
                code="economics.conflicting_scenario_availability",
                scope="scenario result",
            )
        if self.safety_status is not candidate.safety_status:
            raise DataValidationError(
                "scenario and candidate safety statuses must match",
                code="economics.conflicting_safety_status",
                scope="safety_status",
            )

        expected_price_delta = _exact_decimal_sum(
            self.candidate_price,
            self.current_price.copy_negate(),
        )
        if self.price_delta != expected_price_delta:
            raise DataValidationError(
                "scenario price delta must match candidate minus current price",
                code="economics.inconsistent_price_delta",
                scope="price delta",
            )

        expected_profit_delta = (
            _exact_decimal_sum(
                candidate.profit_per_unit,
                current.profit_per_unit.copy_negate(),
            )
            if candidate.profit_per_unit is not None and current.profit_per_unit is not None
            else None
        )
        if self.profit_per_unit_delta != expected_profit_delta:
            raise DataValidationError(
                "scenario profit delta must match candidate minus current contribution",
                code="economics.inconsistent_profit_delta",
                scope="profit per unit delta",
            )

        expected_margin_delta = (
            _exact_decimal_sum(
                candidate.contribution_margin,
                current.contribution_margin.copy_negate(),
            )
            if candidate.contribution_margin is not None
            and current.contribution_margin is not None
            else None
        )
        if self.margin_delta != expected_margin_delta:
            raise DataValidationError(
                "scenario margin delta must match candidate minus current margin",
                code="economics.inconsistent_margin_delta",
                scope="margin delta",
            )

        expected_distance = (
            _exact_decimal_sum(
                self.candidate_price,
                candidate.minimum_safe_price.copy_negate(),
            )
            if candidate.minimum_safe_price is not None
            else None
        )
        if self.distance_from_safe_price != expected_distance:
            raise DataValidationError(
                "safe-price distance must match candidate price minus safe price",
                code="economics.inconsistent_safe_price_distance",
                scope="distance from safe price",
            )

        required_evidence = set(current.evidence_refs) | set(candidate.evidence_refs)
        if self.scenario.provenance.source_record_id is not None:
            required_evidence.add(self.scenario.provenance.source_record_id)
        if self.evidence_refs and not required_evidence.issubset(self.evidence_refs):
            raise DataValidationError(
                "scenario evidence must retain current, candidate, and scenario references",
                code="economics.missing_scenario_traceability",
                scope="scenario evidence refs",
            )

"""Pure deterministic inventory timing and replenishment analytics.

Calendar convention
-------------------
``as_of`` is the start of the calculation's calendar timeline.  A depletion
instant is converted to ``stockout_date`` by rounding elapsed days upward, so
one exact day of stock depletes at ``as_of + 1 day`` and a fractional day also
maps to the next calendar date.  Confirmed inbound on a future date arrives at
the start of that date, before that day's demand; an arrival exactly at the
continuous depletion boundary therefore prevents a gap.

The inventory snapshot must be dated on ``as_of``.  Inbound dated on or before
``as_of`` is not added because the snapshot is the authoritative current stock
and the date-only inbound record cannot prove that it is absent from the
snapshot.  This conservative rule prevents double counting.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, localcontext
from enum import Enum
from fractions import Fraction
from itertools import groupby

from app.analytics.sales import SALES_RATIO_PRECISION, SalesMetrics
from app.core.clock import require_aware_datetime
from app.core.errors import CalculationPreconditionError, DataValidationError
from app.domain.common import (
    AvailabilityStatus,
    SkuId,
    normalize_text_tuple,
    optional_text,
    require_calendar_date,
    require_instance,
    require_non_negative_decimal,
    require_non_negative_int,
)
from app.domain.inventory import (
    InboundStatus,
    InboundSupply,
    InventoryPolicy,
    InventorySnapshot,
    LeadTime,
    ReplenishmentConstraints,
)


_INVENTORY_RATIO_CONTEXT = Context(
    prec=SALES_RATIO_PRECISION,
    rounding=ROUND_HALF_EVEN,
    Emin=MIN_EMIN,
    Emax=MAX_EMAX,
)


class ReplenishmentTiming(str, Enum):
    """Factual position of the latest safe start date relative to ``as_of``."""

    FUTURE = "future"
    DUE_NOW = "due_now"
    ALREADY_LATE = "already_late"


class InboundTreatment(str, Enum):
    """How one source inbound record participated in the projection."""

    APPLIED = "applied"
    LATE_AFTER_STOCKOUT = "late_after_stockout"
    UNCONFIRMED = "unconfirmed"
    MISSING_ARRIVAL = "missing_arrival"
    PAST_OR_AS_OF = "past_or_as_of"
    ZERO_QUANTITY = "zero_quantity"
    NOT_EVALUATED = "not_evaluated"
    NOT_APPLICABLE_NO_DEMAND = "not_applicable_no_demand"


@dataclass(frozen=True, slots=True)
class InboundProjectionEvent:
    """Immutable, source-traceable treatment of one inbound batch."""

    sku: SkuId
    quantity: int
    source_status: InboundStatus
    expected_arrival: date | None
    treatment: InboundTreatment
    source_ref: str | None

    def __post_init__(self) -> None:
        require_instance(self.sku, SkuId, field_name="inbound projection sku")
        require_non_negative_int(self.quantity, field_name="inbound projection quantity")
        require_instance(
            self.source_status,
            InboundStatus,
            field_name="inbound projection source status",
        )
        if self.expected_arrival is not None:
            require_calendar_date(
                self.expected_arrival,
                field_name="inbound projection arrival",
            )
        require_instance(
            self.treatment,
            InboundTreatment,
            field_name="inbound projection treatment",
        )
        object.__setattr__(
            self,
            "source_ref",
            optional_text(self.source_ref, field_name="inbound source ref"),
        )

        if self.source_status is InboundStatus.UNCONFIRMED:
            if self.treatment is not InboundTreatment.UNCONFIRMED:
                raise DataValidationError(
                    "unconfirmed inbound cannot receive a guaranteed-stock treatment",
                    code="inventory.inconsistent_inbound_treatment",
                    scope="inbound projection event",
                )
            return
        if self.treatment is InboundTreatment.UNCONFIRMED:
            raise DataValidationError(
                "confirmed inbound cannot use the unconfirmed treatment",
                code="inventory.inconsistent_inbound_treatment",
                scope="inbound projection event",
            )

        if self.expected_arrival is None:
            if self.treatment is not InboundTreatment.MISSING_ARRIVAL:
                raise DataValidationError(
                    "confirmed inbound without an arrival date must be marked missing arrival",
                    code="inventory.inconsistent_inbound_treatment",
                    scope="inbound projection event",
                )
            return
        if self.treatment is InboundTreatment.MISSING_ARRIVAL:
            raise DataValidationError(
                "missing-arrival treatment requires an absent arrival date",
                code="inventory.inconsistent_inbound_treatment",
                scope="inbound projection event",
            )

        if self.treatment is InboundTreatment.ZERO_QUANTITY:
            if self.quantity != 0:
                raise DataValidationError(
                    "zero-quantity treatment requires zero inbound units",
                    code="inventory.inconsistent_inbound_treatment",
                    scope="inbound projection event",
                )
            return
        if self.treatment in {
            InboundTreatment.APPLIED,
            InboundTreatment.LATE_AFTER_STOCKOUT,
            InboundTreatment.NOT_EVALUATED,
            InboundTreatment.NOT_APPLICABLE_NO_DEMAND,
        } and self.quantity == 0:
            raise DataValidationError(
                "this inbound treatment requires a positive quantity",
                code="inventory.inconsistent_inbound_treatment",
                scope="inbound projection event",
            )


@dataclass(frozen=True, slots=True)
class InventoryAnalysisResult:
    """Typed factual inventory result with field-level availability."""

    sku: SkuId
    as_of: date
    inventory_observed_at: datetime
    sellable_stock: int
    average_daily_sales: Decimal | None
    status: AvailabilityStatus

    depletion_status: AvailabilityStatus
    stock_coverage_days: Decimal | None
    stockout_date: date | None

    lead_time_status: AvailabilityStatus
    supply_lead_days: int | None
    required_coverage_horizon_days: int | None

    timing_status: AvailabilityStatus
    latest_safe_start_date: date | None
    replenishment_timing: ReplenishmentTiming | None

    replenishment_status: AvailabilityStatus
    planned_replenishment_arrival_date: date | None
    projected_stock_at_replenishment_arrival: Decimal | None
    reorder_point_units: int | None
    target_stock_units: int | None
    raw_required_quantity: Decimal | None
    base_replenishment_quantity: int | None
    recommended_replenishment_quantity: int | None

    eligible_confirmed_inbound_units: int | None
    inbound_events: tuple[InboundProjectionEvent, ...]
    lead_time: LeadTime
    constraints: ReplenishmentConstraints
    policy: InventoryPolicy
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_instance(self.sku, SkuId, field_name="inventory result sku")
        require_calendar_date(self.as_of, field_name="inventory result as_of")
        require_aware_datetime(
            self.inventory_observed_at,
            field_name="inventory result observed_at",
        )
        require_non_negative_int(self.sellable_stock, field_name="inventory result stock")
        require_instance(self.status, AvailabilityStatus, field_name="inventory result status")
        for field_name in (
            "depletion_status",
            "lead_time_status",
            "timing_status",
            "replenishment_status",
        ):
            require_instance(
                getattr(self, field_name),
                AvailabilityStatus,
                field_name=field_name,
            )
        require_instance(self.lead_time, LeadTime, field_name="inventory result lead time")
        require_instance(
            self.constraints,
            ReplenishmentConstraints,
            field_name="inventory result constraints",
        )
        require_instance(self.policy, InventoryPolicy, field_name="inventory result policy")
        if self.constraints.sku != self.sku:
            raise DataValidationError(
                "inventory result constraints must match its SKU",
                code="inventory.result_constraints_sku_mismatch",
                scope="inventory result",
            )
        if self.policy.lead_time != self.lead_time:
            raise DataValidationError(
                "inventory result policy and lead-time inputs must agree",
                code="inventory.result_policy_lead_time_mismatch",
                scope="inventory result",
            )
        if (
            self.policy.minimum_order_quantity
            != self.constraints.minimum_order_quantity
            or self.policy.pack_size != self.constraints.pack_size
        ):
            raise DataValidationError(
                "inventory result policy and replenishment constraints must agree",
                code="inventory.result_policy_constraints_mismatch",
                scope="inventory result",
            )

        decimal_fields = (
            "average_daily_sales",
            "stock_coverage_days",
            "projected_stock_at_replenishment_arrival",
            "raw_required_quantity",
        )
        for field_name in decimal_fields:
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    require_non_negative_decimal(value, field_name=field_name),
                )
        integer_fields = (
            "supply_lead_days",
            "required_coverage_horizon_days",
            "reorder_point_units",
            "target_stock_units",
            "base_replenishment_quantity",
            "recommended_replenishment_quantity",
            "eligible_confirmed_inbound_units",
        )
        for field_name in integer_fields:
            value = getattr(self, field_name)
            if value is not None:
                require_non_negative_int(value, field_name=field_name)
        for field_name in (
            "stockout_date",
            "latest_safe_start_date",
            "planned_replenishment_arrival_date",
        ):
            value = getattr(self, field_name)
            if value is not None:
                require_calendar_date(value, field_name=field_name)
        if self.replenishment_timing is not None:
            require_instance(
                self.replenishment_timing,
                ReplenishmentTiming,
                field_name="replenishment timing",
            )

        events = tuple(self.inbound_events)
        for event in events:
            require_instance(event, InboundProjectionEvent, field_name="inbound event")
            if event.sku != self.sku:
                raise DataValidationError(
                    "inventory result inbound events must match its SKU",
                    code="inventory.result_inbound_sku_mismatch",
                    scope="inventory result",
                )
        object.__setattr__(self, "inbound_events", events)
        object.__setattr__(
            self,
            "source_refs",
            normalize_text_tuple(self.source_refs, field_name="inventory source refs"),
        )

        self._validate_lead_time_state()
        self._validate_demand_dependent_state()
        self._validate_inbound_state()

    def _validate_lead_time_state(self) -> None:
        complete = all(
            value is not None
            for value in (
                self.lead_time.production_days,
                self.lead_time.delivery_days,
                self.lead_time.safety_buffer_days,
            )
        )
        if complete:
            assert self.lead_time.production_days is not None
            assert self.lead_time.delivery_days is not None
            assert self.lead_time.safety_buffer_days is not None
            expected_supply_days = (
                self.lead_time.production_days + self.lead_time.delivery_days
            )
            expected_required_days = (
                expected_supply_days + self.lead_time.safety_buffer_days
            )
            if (
                self.lead_time_status is not AvailabilityStatus.AVAILABLE
                or self.supply_lead_days != expected_supply_days
                or self.required_coverage_horizon_days != expected_required_days
            ):
                raise DataValidationError(
                    "available lead-time totals must equal their source components",
                    code="inventory.inconsistent_lead_time_result",
                    scope="lead time",
                )
        elif (
            self.lead_time_status is not AvailabilityStatus.INSUFFICIENT_DATA
            or self.supply_lead_days is not None
            or self.required_coverage_horizon_days is not None
        ):
            raise DataValidationError(
                "missing lead-time inputs cannot produce lead-time totals",
                code="inventory.inconsistent_lead_time_result",
                scope="lead time",
            )

    def _validate_demand_dependent_state(self) -> None:
        quantity_fields = (
            self.planned_replenishment_arrival_date,
            self.projected_stock_at_replenishment_arrival,
            self.reorder_point_units,
            self.target_stock_units,
            self.raw_required_quantity,
            self.base_replenishment_quantity,
            self.recommended_replenishment_quantity,
        )
        average = self.average_daily_sales
        if average is None:
            if (
                self.status is not AvailabilityStatus.INSUFFICIENT_DATA
                or self.depletion_status is not AvailabilityStatus.INSUFFICIENT_DATA
                or self.stock_coverage_days is not None
                or self.stockout_date is not None
                or self.timing_status is not AvailabilityStatus.INSUFFICIENT_DATA
                or self.latest_safe_start_date is not None
                or self.replenishment_timing is not None
                or self.replenishment_status is not AvailabilityStatus.INSUFFICIENT_DATA
                or any(value is not None for value in quantity_fields)
                or self.eligible_confirmed_inbound_units is not None
            ):
                raise DataValidationError(
                    "unavailable sales cannot produce demand-dependent inventory values",
                    code="inventory.values_without_sales",
                    scope="inventory result",
                )
            return

        if average == 0:
            zero_quantity_fields = (
                self.reorder_point_units,
                self.target_stock_units,
                self.raw_required_quantity,
                self.base_replenishment_quantity,
                self.recommended_replenishment_quantity,
                self.eligible_confirmed_inbound_units,
            )
            if (
                self.status is not AvailabilityStatus.NOT_APPLICABLE
                or self.depletion_status is not AvailabilityStatus.NOT_APPLICABLE
                or self.stock_coverage_days is not None
                or self.stockout_date is not None
                or self.timing_status is not AvailabilityStatus.NOT_APPLICABLE
                or self.latest_safe_start_date is not None
                or self.replenishment_timing is not None
                or self.replenishment_status is not AvailabilityStatus.NOT_APPLICABLE
                or self.planned_replenishment_arrival_date is not None
                or self.projected_stock_at_replenishment_arrival is not None
                or any(value != 0 for value in zero_quantity_fields)
            ):
                raise DataValidationError(
                    "zero demand requires explicit non-depleting inventory semantics",
                    code="inventory.inconsistent_zero_demand_result",
                    scope="inventory result",
                )
            return

        if (
            self.depletion_status is not AvailabilityStatus.AVAILABLE
            or self.stock_coverage_days is None
            or self.stockout_date is None
        ):
            raise DataValidationError(
                "positive demand requires available depletion values",
                code="inventory.incomplete_depletion_result",
                scope="inventory result",
            )
        if self.stockout_date < self.as_of:
            raise DataValidationError(
                "stockout date must not precede the analysis date",
                code="inventory.stockout_before_as_of",
                scope="stockout date",
            )

        if self.lead_time_status is AvailabilityStatus.INSUFFICIENT_DATA:
            if (
                self.status is not AvailabilityStatus.INSUFFICIENT_DATA
                or self.timing_status is not AvailabilityStatus.INSUFFICIENT_DATA
                or self.latest_safe_start_date is not None
                or self.replenishment_timing is not None
                or self.replenishment_status is not AvailabilityStatus.INSUFFICIENT_DATA
                or any(value is not None for value in quantity_fields)
            ):
                raise DataValidationError(
                    "missing lead time cannot produce timing or replenishment values",
                    code="inventory.values_without_lead_time",
                    scope="inventory result",
                )
            return

        if (
            self.status is not AvailabilityStatus.AVAILABLE
            or self.timing_status is not AvailabilityStatus.AVAILABLE
            or self.latest_safe_start_date is None
            or self.replenishment_timing is None
            or self.replenishment_status is not AvailabilityStatus.AVAILABLE
            or any(value is None for value in quantity_fields)
            or self.eligible_confirmed_inbound_units is None
        ):
            raise DataValidationError(
                "complete positive-demand inputs require complete inventory outputs",
                code="inventory.incomplete_available_result",
                scope="inventory result",
            )

        assert self.latest_safe_start_date is not None
        assert self.replenishment_timing is not None
        if self.replenishment_timing is not _timing_state(
            self.latest_safe_start_date,
            self.as_of,
        ):
            raise DataValidationError(
                "replenishment timing must match the latest safe start date",
                code="inventory.inconsistent_replenishment_timing",
                scope="replenishment timing",
            )

        assert self.raw_required_quantity is not None
        assert self.base_replenishment_quantity is not None
        assert self.recommended_replenishment_quantity is not None
        raw_quantity = self.raw_required_quantity
        base_quantity = self.base_replenishment_quantity
        final_quantity = self.recommended_replenishment_quantity
        if raw_quantity == 0:
            if base_quantity != 0 or final_quantity != 0:
                raise DataValidationError(
                    "zero raw need cannot force a replenishment order",
                    code="inventory.inconsistent_zero_replenishment",
                    scope="replenishment quantity",
                )
        else:
            if base_quantity < _ceiling(Fraction(raw_quantity)):
                raise DataValidationError(
                    "base replenishment quantity cannot round below raw need",
                    code="inventory.base_quantity_below_raw",
                    scope="replenishment quantity",
                )
            if final_quantity < base_quantity:
                raise DataValidationError(
                    "final replenishment quantity cannot be below base need",
                    code="inventory.final_quantity_below_base",
                    scope="replenishment quantity",
                )
            minimum_order_quantity = self.constraints.minimum_order_quantity
            if (
                minimum_order_quantity is not None
                and final_quantity < minimum_order_quantity
            ):
                raise DataValidationError(
                    "final replenishment quantity must satisfy MOQ",
                    code="inventory.final_quantity_below_moq",
                    scope="replenishment quantity",
                )
            pack_size = self.constraints.pack_size
            if pack_size is not None and final_quantity % pack_size != 0:
                raise DataValidationError(
                    "final replenishment quantity must be a whole pack multiple",
                    code="inventory.final_quantity_not_pack_multiple",
                    scope="replenishment quantity",
                )

    def _validate_inbound_state(self) -> None:
        applied_units = 0
        for event in self.inbound_events:
            arrival = event.expected_arrival
            if event.treatment is InboundTreatment.PAST_OR_AS_OF:
                if arrival is None or arrival > self.as_of:
                    raise DataValidationError(
                        "past/as-of inbound treatment must match its arrival date",
                        code="inventory.inconsistent_inbound_event_date",
                        scope="inbound projection event",
                    )
            elif event.treatment in {
                InboundTreatment.APPLIED,
                InboundTreatment.LATE_AFTER_STOCKOUT,
                InboundTreatment.ZERO_QUANTITY,
                InboundTreatment.NOT_EVALUATED,
                InboundTreatment.NOT_APPLICABLE_NO_DEMAND,
            }:
                if arrival is None or arrival <= self.as_of:
                    raise DataValidationError(
                        "future inbound treatment must follow the analysis date",
                        code="inventory.inconsistent_inbound_event_date",
                        scope="inbound projection event",
                    )
            if event.treatment is InboundTreatment.APPLIED:
                applied_units += event.quantity

        if self.eligible_confirmed_inbound_units is None:
            if applied_units != 0:
                raise DataValidationError(
                    "unavailable inbound eligibility cannot contain applied units",
                    code="inventory.inconsistent_eligible_inbound",
                    scope="inbound projection",
                )
        elif self.eligible_confirmed_inbound_units != applied_units:
            raise DataValidationError(
                "eligible confirmed inbound must equal applied event units",
                code="inventory.inconsistent_eligible_inbound",
                scope="inbound projection",
            )


def _fraction_to_decimal(value: Fraction) -> Decimal:
    if value.denominator == 1:
        return Decimal(value.numerator)
    with localcontext(_INVENTORY_RATIO_CONTEXT):
        return Decimal(value.numerator) / Decimal(value.denominator)


def _ceiling(value: Fraction) -> int:
    if value < 0:
        raise CalculationPreconditionError(
            "inventory ceiling requires a non-negative value",
            code="inventory.negative_ceiling_input",
            scope="inventory calculation",
        )
    return (value.numerator + value.denominator - 1) // value.denominator


def _date_plus_days(value: date, days: int, *, field_name: str) -> date:
    try:
        return value + timedelta(days=days)
    except OverflowError as exc:
        raise CalculationPreconditionError(
            f"{field_name} is outside the supported calendar range",
            code="inventory.date_out_of_range",
            scope=field_name,
        ) from exc


def _date_minus_days(value: date, days: int, *, field_name: str) -> date:
    try:
        return value - timedelta(days=days)
    except OverflowError as exc:
        raise CalculationPreconditionError(
            f"{field_name} is outside the supported calendar range",
            code="inventory.date_out_of_range",
            scope=field_name,
        ) from exc


def _inbound_sort_key(supply: InboundSupply) -> tuple[object, ...]:
    provenance = supply.provenance
    return (
        supply.expected_arrival is None,
        supply.expected_arrival or date.max,
        provenance.source_record_id or "",
        supply.status.value,
        supply.quantity,
        provenance.provider,
        provenance.ingested_at.isoformat(),
        "" if provenance.source_timestamp is None else provenance.source_timestamp.isoformat(),
    )


def _normalize_inbound(
    inbound_supplies: Iterable[InboundSupply],
    *,
    sku: SkuId,
) -> tuple[InboundSupply, ...]:
    if isinstance(inbound_supplies, (str, bytes, Mapping)):
        raise CalculationPreconditionError(
            "inbound supplies must be an iterable of normalized records",
            code="inventory.invalid_inbound_collection",
            scope="inbound supplies",
        )
    try:
        normalized = tuple(inbound_supplies)
    except TypeError as exc:
        raise CalculationPreconditionError(
            "inbound supplies must be an iterable of normalized records",
            code="inventory.invalid_inbound_collection",
            scope="inbound supplies",
        ) from exc
    for supply in normalized:
        if not isinstance(supply, InboundSupply):
            raise CalculationPreconditionError(
                "inventory analytics requires normalized InboundSupply values",
                code="inventory.invalid_inbound_type",
                scope="inbound supplies",
            )
        if supply.sku != sku:
            raise CalculationPreconditionError(
                "inbound supplies must match the analyzed SKU",
                code="inventory.inbound_sku_mismatch",
                scope="inbound supplies",
            )
    return tuple(sorted(normalized, key=_inbound_sort_key))


def _source_ref(supply: object) -> str | None:
    provenance = getattr(supply, "provenance")
    return provenance.source_record_id


def _build_inbound_events(
    supplies: tuple[InboundSupply, ...],
    *,
    as_of: date,
    sellable_stock: int,
    exact_average_daily_sales: Fraction | None,
) -> tuple[tuple[InboundProjectionEvent, ...], Fraction | None, int | None]:
    treatments: list[InboundTreatment | None] = [None] * len(supplies)
    candidate_indexes: list[int] = []
    for index, supply in enumerate(supplies):
        if supply.status is InboundStatus.UNCONFIRMED:
            treatments[index] = InboundTreatment.UNCONFIRMED
        elif supply.expected_arrival is None:
            treatments[index] = InboundTreatment.MISSING_ARRIVAL
        elif supply.expected_arrival <= as_of:
            treatments[index] = InboundTreatment.PAST_OR_AS_OF
        elif supply.quantity == 0:
            treatments[index] = InboundTreatment.ZERO_QUANTITY
        elif exact_average_daily_sales is None:
            treatments[index] = InboundTreatment.NOT_EVALUATED
        elif exact_average_daily_sales == 0:
            treatments[index] = InboundTreatment.NOT_APPLICABLE_NO_DEMAND
        else:
            candidate_indexes.append(index)

    stockout_offset: Fraction | None = None
    eligible_units: int | None
    if exact_average_daily_sales is None:
        eligible_units = None
    elif exact_average_daily_sales == 0:
        eligible_units = 0
    else:
        demand = exact_average_daily_sales
        balance = Fraction(sellable_stock)
        cursor = 0
        eligible_units = 0
        for arrival, group in groupby(
            candidate_indexes,
            key=lambda index: supplies[index].expected_arrival,
        ):
            assert arrival is not None
            indexes = tuple(group)
            arrival_offset = (arrival - as_of).days
            required_until_arrival = demand * (arrival_offset - cursor)
            if stockout_offset is not None or balance < required_until_arrival:
                if stockout_offset is None:
                    stockout_offset = Fraction(cursor) + balance / demand
                for index in indexes:
                    treatments[index] = InboundTreatment.LATE_AFTER_STOCKOUT
                continue

            balance -= required_until_arrival
            for index in indexes:
                treatments[index] = InboundTreatment.APPLIED
                balance += supplies[index].quantity
                eligible_units += supplies[index].quantity
            cursor = arrival_offset

        if stockout_offset is None:
            stockout_offset = Fraction(cursor) + balance / demand

    events = tuple(
        InboundProjectionEvent(
            sku=supply.sku,
            quantity=supply.quantity,
            source_status=supply.status,
            expected_arrival=supply.expected_arrival,
            treatment=treatment,
            source_ref=_source_ref(supply),
        )
        for supply, treatment in zip(supplies, treatments, strict=True)
        if treatment is not None
    )
    return events, stockout_offset, eligible_units


def _project_physical_stock(
    supplies: tuple[InboundSupply, ...],
    *,
    as_of: date,
    projection_date: date,
    sellable_stock: int,
    exact_average_daily_sales: Fraction,
) -> Fraction:
    """Project clamped physical on-hand without changing first-gap eligibility."""

    on_hand = Fraction(sellable_stock)
    cursor = 0
    physical_supplies = tuple(
        supply
        for supply in supplies
        if supply.status is InboundStatus.CONFIRMED
        and supply.expected_arrival is not None
        and as_of < supply.expected_arrival <= projection_date
        and supply.quantity > 0
    )
    for arrival, group in groupby(
        physical_supplies,
        key=lambda supply: supply.expected_arrival,
    ):
        assert arrival is not None
        arrival_offset = (arrival - as_of).days
        on_hand = max(
            Fraction(0),
            on_hand - exact_average_daily_sales * (arrival_offset - cursor),
        )
        on_hand += sum(supply.quantity for supply in group)
        cursor = arrival_offset

    projection_offset = (projection_date - as_of).days
    return max(
        Fraction(0),
        on_hand - exact_average_daily_sales * (projection_offset - cursor),
    )


def _timing_state(latest_safe_start: date, as_of: date) -> ReplenishmentTiming:
    if latest_safe_start > as_of:
        return ReplenishmentTiming.FUTURE
    if latest_safe_start == as_of:
        return ReplenishmentTiming.DUE_NOW
    return ReplenishmentTiming.ALREADY_LATE


def _unique_source_refs(values: Iterable[str | None]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value is not None))


def calculate_inventory_analysis(
    inventory: InventorySnapshot,
    sales: SalesMetrics,
    inbound_supplies: Iterable[InboundSupply],
    lead_time: LeadTime,
    constraints: ReplenishmentConstraints,
    policy: InventoryPolicy,
    as_of: date,
) -> InventoryAnalysisResult:
    """Calculate factual inventory timing and quantity outputs without side effects."""

    inventory = require_instance(inventory, InventorySnapshot, field_name="inventory snapshot")
    sales = require_instance(sales, SalesMetrics, field_name="sales metrics")
    lead_time = require_instance(lead_time, LeadTime, field_name="lead time")
    constraints = require_instance(
        constraints,
        ReplenishmentConstraints,
        field_name="replenishment constraints",
    )
    policy = require_instance(policy, InventoryPolicy, field_name="inventory policy")
    analysis_date = require_calendar_date(as_of, field_name="inventory as_of date")

    if inventory.sku != sales.sku or inventory.sku != constraints.sku:
        raise CalculationPreconditionError(
            "inventory, sales, and replenishment constraints must share one SKU",
            code="inventory.sku_mismatch",
            scope="inventory inputs",
        )
    if sales.as_of != analysis_date:
        raise CalculationPreconditionError(
            "sales metrics and inventory analysis must share the same as-of date",
            code="inventory.sales_as_of_mismatch",
            scope="inventory inputs",
        )
    if inventory.observed_at.date() != analysis_date:
        raise CalculationPreconditionError(
            "inventory snapshot must be observed on the analysis calendar date",
            code="inventory.snapshot_as_of_mismatch",
            scope="inventory snapshot",
        )
    if policy.lead_time != lead_time:
        raise CalculationPreconditionError(
            "inventory policy and explicit lead-time input must agree",
            code="inventory.policy_lead_time_mismatch",
            scope="inventory policy",
        )
    if (
        policy.minimum_order_quantity != constraints.minimum_order_quantity
        or policy.pack_size != constraints.pack_size
    ):
        raise CalculationPreconditionError(
            "inventory policy and per-SKU replenishment constraints must agree",
            code="inventory.policy_constraints_mismatch",
            scope="inventory policy",
        )

    normalized_inbound = _normalize_inbound(inbound_supplies, sku=inventory.sku)
    current_sales = sales.current
    average_daily_sales = (
        current_sales.average_daily_sales
        if current_sales.status is AvailabilityStatus.AVAILABLE
        else None
    )
    exact_average_daily_sales = (
        Fraction(current_sales.total_units, current_sales.expected_days)
        if current_sales.status is AvailabilityStatus.AVAILABLE
        else None
    )
    inbound_events, stockout_offset, eligible_inbound_units = _build_inbound_events(
        normalized_inbound,
        as_of=analysis_date,
        sellable_stock=inventory.sellable_stock,
        exact_average_daily_sales=exact_average_daily_sales,
    )

    complete_lead_time = all(
        value is not None
        for value in (
            lead_time.production_days,
            lead_time.delivery_days,
            lead_time.safety_buffer_days,
        )
    )
    if complete_lead_time:
        assert lead_time.production_days is not None
        assert lead_time.delivery_days is not None
        assert lead_time.safety_buffer_days is not None
        supply_lead_days = lead_time.production_days + lead_time.delivery_days
        required_horizon_days = supply_lead_days + lead_time.safety_buffer_days
        lead_time_status = AvailabilityStatus.AVAILABLE
    else:
        supply_lead_days = None
        required_horizon_days = None
        lead_time_status = AvailabilityStatus.INSUFFICIENT_DATA

    if exact_average_daily_sales is None:
        status = AvailabilityStatus.INSUFFICIENT_DATA
        depletion_status = AvailabilityStatus.INSUFFICIENT_DATA
        stock_coverage_days = None
        stockout_date = None
        timing_status = AvailabilityStatus.INSUFFICIENT_DATA
        latest_safe_start_date = None
        replenishment_timing = None
        replenishment_status = AvailabilityStatus.INSUFFICIENT_DATA
        planned_arrival_date = None
        projected_stock_at_arrival = None
        reorder_point_units = None
        target_stock_units = None
        raw_required_quantity = None
        base_quantity = None
        final_quantity = None
    elif exact_average_daily_sales == 0:
        status = AvailabilityStatus.NOT_APPLICABLE
        depletion_status = AvailabilityStatus.NOT_APPLICABLE
        stock_coverage_days = None
        stockout_date = None
        timing_status = AvailabilityStatus.NOT_APPLICABLE
        latest_safe_start_date = None
        replenishment_timing = None
        replenishment_status = AvailabilityStatus.NOT_APPLICABLE
        planned_arrival_date = None
        projected_stock_at_arrival = None
        reorder_point_units = 0
        target_stock_units = 0
        raw_required_quantity = Decimal(0)
        base_quantity = 0
        final_quantity = 0
    else:
        demand = exact_average_daily_sales
        depletion_status = AvailabilityStatus.AVAILABLE
        stock_coverage_days = _fraction_to_decimal(
            Fraction(inventory.sellable_stock) / demand
        )
        assert stockout_offset is not None
        stockout_date = _date_plus_days(
            analysis_date,
            _ceiling(stockout_offset),
            field_name="stockout date",
        )

        if not complete_lead_time:
            status = AvailabilityStatus.INSUFFICIENT_DATA
            timing_status = AvailabilityStatus.INSUFFICIENT_DATA
            latest_safe_start_date = None
            replenishment_timing = None
            replenishment_status = AvailabilityStatus.INSUFFICIENT_DATA
            planned_arrival_date = None
            projected_stock_at_arrival = None
            reorder_point_units = None
            target_stock_units = None
            raw_required_quantity = None
            base_quantity = None
            final_quantity = None
        else:
            assert supply_lead_days is not None
            assert required_horizon_days is not None
            assert lead_time.safety_buffer_days is not None
            assert eligible_inbound_units is not None
            status = AvailabilityStatus.AVAILABLE
            timing_status = AvailabilityStatus.AVAILABLE
            latest_safe_start_date = _date_minus_days(
                stockout_date,
                required_horizon_days,
                field_name="latest safe start date",
            )
            replenishment_timing = _timing_state(latest_safe_start_date, analysis_date)
            replenishment_status = AvailabilityStatus.AVAILABLE
            planned_arrival_date = _date_plus_days(
                analysis_date,
                supply_lead_days,
                field_name="planned replenishment arrival date",
            )

            projected_stock_at_arrival = _fraction_to_decimal(
                _project_physical_stock(
                    normalized_inbound,
                    as_of=analysis_date,
                    projection_date=planned_arrival_date,
                    sellable_stock=inventory.sellable_stock,
                    exact_average_daily_sales=demand,
                )
            )

            reorder_point_units = _ceiling(demand * required_horizon_days)
            planning_horizon_days = (
                required_horizon_days + policy.target_coverage_days
            )
            exact_target_units = demand * planning_horizon_days
            target_stock_units = _ceiling(exact_target_units)
            exact_raw_need = max(
                Fraction(0),
                exact_target_units
                - inventory.sellable_stock
                - eligible_inbound_units,
            )
            raw_required_quantity = _fraction_to_decimal(exact_raw_need)
            base_quantity = max(
                0,
                target_stock_units
                - inventory.sellable_stock
                - eligible_inbound_units,
            )
            if base_quantity == 0:
                final_quantity = 0
            else:
                final_quantity = base_quantity
                if constraints.minimum_order_quantity is not None:
                    final_quantity = max(
                        final_quantity,
                        constraints.minimum_order_quantity,
                    )
                if constraints.pack_size is not None:
                    final_quantity = (
                        (final_quantity + constraints.pack_size - 1)
                        // constraints.pack_size
                        * constraints.pack_size
                    )

    source_refs = _unique_source_refs(
        (
            inventory.provenance.source_record_id,
            *current_sales.source_refs,
            *(
                event.source_ref
                for event in inbound_events
                if event.treatment is InboundTreatment.APPLIED
            ),
            lead_time.provenance.source_record_id,
            constraints.provenance.source_record_id,
        )
    )
    return InventoryAnalysisResult(
        sku=inventory.sku,
        as_of=analysis_date,
        inventory_observed_at=inventory.observed_at,
        sellable_stock=inventory.sellable_stock,
        average_daily_sales=average_daily_sales,
        status=status,
        depletion_status=depletion_status,
        stock_coverage_days=stock_coverage_days,
        stockout_date=stockout_date,
        lead_time_status=lead_time_status,
        supply_lead_days=supply_lead_days,
        required_coverage_horizon_days=required_horizon_days,
        timing_status=timing_status,
        latest_safe_start_date=latest_safe_start_date,
        replenishment_timing=replenishment_timing,
        replenishment_status=replenishment_status,
        planned_replenishment_arrival_date=planned_arrival_date,
        projected_stock_at_replenishment_arrival=projected_stock_at_arrival,
        reorder_point_units=reorder_point_units,
        target_stock_units=target_stock_units,
        raw_required_quantity=raw_required_quantity,
        base_replenishment_quantity=base_quantity,
        recommended_replenishment_quantity=final_quantity,
        eligible_confirmed_inbound_units=eligible_inbound_units,
        inbound_events=inbound_events,
        lead_time=lead_time,
        constraints=constraints,
        policy=policy,
        source_refs=source_refs,
    )

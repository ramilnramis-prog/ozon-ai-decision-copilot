"""Calculated evidence, read-only recommendations, and priority containers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import TypeAlias

from app.core.clock import require_aware_datetime
from app.core.errors import DataValidationError
from app.core.money import to_decimal
from app.domain.common import (
    AvailabilityStatus,
    DateRange,
    PolicyIdentity,
    Provenance,
    Severity,
    SkuId,
    SourceType,
    Urgency,
    normalize_text_tuple,
    optional_text,
    require_calendar_date,
    require_instance,
    require_positive_int,
    require_text,
)


class RecommendationCategory(str, Enum):
    """Supported deterministic recommendation categories."""

    INVENTORY = "inventory"
    PRICING = "pricing"
    PROFITABILITY = "profitability"
    SALES_CHANGE = "sales_change"
    DATA_QUALITY = "data_quality"


class RecommendationStatus(str, Enum):
    """Recommendation lifecycle in the read-only MVP."""

    PROPOSED = "proposed"
    UNAVAILABLE = "unavailable"


FactValue: TypeAlias = Decimal | int | str | bool | date | datetime


@dataclass(frozen=True, slots=True)
class CalculatedFact:
    """Typed evidence value linked to source and formula/rule identifiers."""

    fact_id: str
    sku: SkuId | None
    name: str
    value: FactValue
    unit: str
    period: DateRange | None
    formula_or_rule_id: str
    source_refs: tuple[str, ...]
    provenance: Provenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact_id", require_text(self.fact_id, field_name="fact_id"))
        if self.sku is not None:
            require_instance(self.sku, SkuId, field_name="fact sku")
        object.__setattr__(self, "name", require_text(self.name, field_name="fact name"))
        object.__setattr__(self, "unit", require_text(self.unit, field_name="fact unit"))
        if isinstance(self.value, float):
            raise DataValidationError(
                "fact values must not contain binary floating-point numbers",
                code="recommendations.float_fact_value",
                scope="fact value",
            )
        if isinstance(self.value, Decimal):
            object.__setattr__(self, "value", to_decimal(self.value, field_name="fact value"))
        elif isinstance(self.value, datetime):
            require_aware_datetime(self.value, field_name="fact datetime value")
        elif isinstance(self.value, date):
            require_calendar_date(self.value, field_name="fact date value")
        elif isinstance(self.value, str):
            object.__setattr__(
                self, "value", require_text(self.value, field_name="fact value")
            )
        elif not isinstance(self.value, (int, bool)):
            raise DataValidationError(
                "fact value type is unsupported",
                code="recommendations.invalid_fact_value",
                scope="fact value",
            )
        if self.period is not None:
            require_instance(self.period, DateRange, field_name="fact period")
        object.__setattr__(
            self,
            "formula_or_rule_id",
            require_text(self.formula_or_rule_id, field_name="formula_or_rule_id"),
        )
        object.__setattr__(
            self,
            "source_refs",
            normalize_text_tuple(self.source_refs, field_name="fact source refs"),
        )
        provenance = require_instance(
            self.provenance, Provenance, field_name="fact provenance"
        )
        if provenance.source_type is SourceType.CALCULATED and not self.source_refs:
            raise DataValidationError(
                "calculated fact requires at least one source reference",
                code="recommendations.missing_calculated_fact_sources",
                scope="fact source refs",
            )


@dataclass(frozen=True, slots=True)
class Recommendation:
    """Read-only deterministic recommendation; execution is not a valid state."""

    recommendation_id: str
    sku: SkuId | None
    category: RecommendationCategory
    rule_code: str
    explanation: str
    evidence_refs: tuple[str, ...]
    proposed_action: str | None
    status: RecommendationStatus
    provenance: Provenance
    analysis_timestamp: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "recommendation_id",
            require_text(self.recommendation_id, field_name="recommendation_id"),
        )
        if self.sku is not None:
            require_instance(self.sku, SkuId, field_name="recommendation sku")
        require_instance(self.category, RecommendationCategory, field_name="recommendation category")
        object.__setattr__(
            self, "rule_code", require_text(self.rule_code, field_name="rule_code")
        )
        object.__setattr__(
            self, "explanation", require_text(self.explanation, field_name="explanation")
        )
        object.__setattr__(
            self,
            "evidence_refs",
            normalize_text_tuple(self.evidence_refs, field_name="recommendation evidence refs"),
        )
        object.__setattr__(
            self,
            "proposed_action",
            optional_text(self.proposed_action, field_name="proposed_action"),
        )
        require_instance(self.status, RecommendationStatus, field_name="recommendation status")
        require_instance(
            self.provenance, Provenance, field_name="recommendation provenance"
        )
        require_aware_datetime(
            self.analysis_timestamp,
            field_name="recommendation analysis_timestamp",
        )

        if self.status is RecommendationStatus.PROPOSED:
            if not self.evidence_refs or self.proposed_action is None:
                raise DataValidationError(
                    "proposed recommendation requires evidence and a proposed action",
                    code="recommendations.incomplete_proposal",
                    scope="recommendation",
                )
        elif self.proposed_action is not None:
            raise DataValidationError(
                "unavailable recommendation must not contain a proposed action",
                code="recommendations.action_on_unavailable",
                scope="proposed_action",
            )


@dataclass(frozen=True, slots=True)
class PriorityPolicy:
    """Explicit ordering inputs consumed later by the deterministic prioritizer."""

    identity: PolicyIdentity
    severity_order: tuple[Severity, ...]
    urgency_order: tuple[Urgency, ...]

    def __post_init__(self) -> None:
        require_instance(self.identity, PolicyIdentity, field_name="priority policy identity")
        severity_order = tuple(self.severity_order)
        urgency_order = tuple(self.urgency_order)
        if len(severity_order) != len(Severity) or set(severity_order) != set(Severity):
            raise DataValidationError(
                "severity order must contain each severity exactly once",
                code="recommendations.invalid_severity_order",
                scope="severity_order",
            )
        if len(urgency_order) != len(Urgency) or set(urgency_order) != set(Urgency):
            raise DataValidationError(
                "urgency order must contain each urgency exactly once",
                code="recommendations.invalid_urgency_order",
                scope="urgency_order",
            )
        object.__setattr__(self, "severity_order", severity_order)
        object.__setattr__(self, "urgency_order", urgency_order)


@dataclass(frozen=True, slots=True)
class PriorityAction:
    """A ranked read-only recommendation with an explicit stable tie-break key."""

    recommendation: Recommendation
    severity: Severity
    urgency: Urgency
    status: AvailabilityStatus
    rank: int | None
    tie_break_key: str | None

    def __post_init__(self) -> None:
        require_instance(self.recommendation, Recommendation, field_name="recommendation")
        require_instance(self.severity, Severity, field_name="priority severity")
        require_instance(self.urgency, Urgency, field_name="priority urgency")
        require_instance(self.status, AvailabilityStatus, field_name="priority status")

        if self.status is AvailabilityStatus.AVAILABLE:
            if self.recommendation.status is not RecommendationStatus.PROPOSED:
                raise DataValidationError(
                    "available priority action requires a proposed recommendation",
                    code="recommendations.invalid_priority_recommendation",
                    scope="priority action",
                )
            if self.rank is None or self.tie_break_key is None:
                raise DataValidationError(
                    "available priority action requires rank and tie-break key",
                    code="recommendations.incomplete_priority",
                    scope="priority action",
                )
            require_positive_int(self.rank, field_name="priority rank")
            object.__setattr__(
                self,
                "tie_break_key",
                require_text(self.tie_break_key, field_name="tie_break_key"),
            )
        else:
            if self.recommendation.status is not RecommendationStatus.UNAVAILABLE:
                raise DataValidationError(
                    "unavailable priority action requires an unavailable recommendation",
                    code="recommendations.conflicting_action_availability",
                    scope="priority action",
                )
            if self.rank is not None or self.tie_break_key is not None:
                raise DataValidationError(
                    "unavailable priority action must not contain rank fields",
                    code="recommendations.rank_on_unavailable",
                    scope="priority action",
                )

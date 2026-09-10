"""Immutable action-centric grounding and untrusted AI draft structures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
import re
from typing import TypeAlias

from app.core.clock import require_aware_datetime
from app.core.errors import DataValidationError
from app.domain.common import (
    DateRange,
    Severity,
    SkuId,
    Urgency,
    normalize_text_tuple,
    optional_text,
    require_instance,
    require_positive_int,
    require_text,
)
from app.domain.recommendations import RecommendationCategory, RecommendationStatus


class GroundedValueType(str, Enum):
    """Stable transport identities for authoritative calculated-fact values."""

    DECIMAL = "decimal"
    INTEGER = "integer"
    TEXT = "text"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"


GroundedValue: TypeAlias = str | int | bool

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _typed_tuple(values: object, expected_type: type, *, field_name: str) -> tuple:
    if isinstance(values, (str, bytes)):
        raise DataValidationError(
            f"{field_name} must be a collection",
            code="briefs.invalid_collection",
            scope=field_name,
        )
    try:
        items = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise DataValidationError(
            f"{field_name} must be a collection",
            code="briefs.invalid_collection",
            scope=field_name,
        ) from exc
    for item in items:
        require_instance(item, expected_type, field_name=field_name)
    return items


def _validate_canonical_value(value: object, value_type: GroundedValueType) -> None:
    if value_type is GroundedValueType.DECIMAL:
        if not isinstance(value, str):
            raise DataValidationError(
                "decimal grounded value must be a fixed-point string",
                code="briefs.invalid_grounded_value",
                scope="grounded fact value",
            )
        try:
            decimal_value = Decimal(value)
        except InvalidOperation as exc:
            raise DataValidationError(
                "decimal grounded value is invalid",
                code="briefs.invalid_grounded_value",
                scope="grounded fact value",
            ) from exc
        if not decimal_value.is_finite() or format(decimal_value, "f") != value:
            raise DataValidationError(
                "decimal grounded value must be finite canonical fixed-point text",
                code="briefs.invalid_grounded_value",
                scope="grounded fact value",
            )
        return

    if value_type is GroundedValueType.INTEGER:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif value_type is GroundedValueType.BOOLEAN:
        valid = isinstance(value, bool)
    elif value_type is GroundedValueType.TEXT:
        valid = isinstance(value, str) and bool(value.strip())
    elif value_type is GroundedValueType.DATE:
        valid = isinstance(value, str)
        if valid:
            try:
                parsed_date = date.fromisoformat(value)
            except ValueError:
                valid = False
            else:
                valid = parsed_date.isoformat() == value
    elif value_type is GroundedValueType.DATETIME:
        valid = isinstance(value, str)
        if valid:
            try:
                parsed_datetime = datetime.fromisoformat(value)
                require_aware_datetime(parsed_datetime, field_name="grounded datetime value")
            except (ValueError, DataValidationError):
                valid = False
            else:
                valid = parsed_datetime.isoformat() == value
    else:  # pragma: no cover - enum construction protects this branch
        valid = False

    if not valid:
        raise DataValidationError(
            "grounded fact value does not match its declared type",
            code="briefs.invalid_grounded_value",
            scope="grounded fact value",
        )


@dataclass(frozen=True, slots=True)
class GroundedFact:
    """Canonical transport-safe copy of one authoritative calculated fact."""

    fact_id: str
    sku: SkuId
    name: str
    value: GroundedValue
    value_type: GroundedValueType
    unit: str
    period: DateRange | None
    formula_or_rule_id: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact_id", require_text(self.fact_id, field_name="fact_id"))
        require_instance(self.sku, SkuId, field_name="grounded fact sku")
        object.__setattr__(self, "name", require_text(self.name, field_name="fact name"))
        value_type = require_instance(
            self.value_type,
            GroundedValueType,
            field_name="grounded value type",
        )
        _validate_canonical_value(self.value, value_type)
        object.__setattr__(self, "unit", require_text(self.unit, field_name="fact unit"))
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
            normalize_text_tuple(
                self.source_refs,
                field_name="grounded fact source refs",
                allow_empty=False,
            ),
        )


@dataclass(frozen=True, slots=True)
class GroundedAction:
    """Minimal grounded view of one authoritative priority action."""

    action_ref: str
    recommendation_id: str
    sku: SkuId
    product_name: str | None
    rule_id: str
    category: RecommendationCategory
    status: RecommendationStatus
    rank: int
    severity: Severity
    urgency: Urgency
    fact_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        action_ref = require_text(self.action_ref, field_name="action_ref")
        recommendation_id = require_text(
            self.recommendation_id,
            field_name="recommendation_id",
        )
        object.__setattr__(self, "action_ref", action_ref)
        object.__setattr__(self, "recommendation_id", recommendation_id)
        if action_ref != recommendation_id:
            raise DataValidationError(
                "action_ref must equal recommendation_id",
                code="briefs.action_identity_mismatch",
                scope="grounded action",
            )
        require_instance(self.sku, SkuId, field_name="grounded action sku")
        object.__setattr__(
            self,
            "product_name",
            optional_text(self.product_name, field_name="product_name"),
        )
        object.__setattr__(self, "rule_id", require_text(self.rule_id, field_name="rule_id"))
        require_instance(self.category, RecommendationCategory, field_name="action category")
        status = require_instance(
            self.status,
            RecommendationStatus,
            field_name="action status",
        )
        if status is not RecommendationStatus.PROPOSED:
            raise DataValidationError(
                "grounded action requires a proposed recommendation",
                code="briefs.invalid_action_status",
                scope="grounded action",
            )
        require_positive_int(self.rank, field_name="action rank")
        require_instance(self.severity, Severity, field_name="action severity")
        require_instance(self.urgency, Urgency, field_name="action urgency")
        object.__setattr__(
            self,
            "fact_refs",
            normalize_text_tuple(
                self.fact_refs,
                field_name="action fact refs",
                allow_empty=False,
            ),
        )


@dataclass(frozen=True, slots=True)
class GroundedBriefInput:
    """Complete immutable action-centric input approved for model transport."""

    analysis_timestamp: datetime
    actions: tuple[GroundedAction, ...]
    facts: tuple[GroundedFact, ...]

    def __post_init__(self) -> None:
        require_aware_datetime(self.analysis_timestamp, field_name="brief analysis timestamp")
        actions = _typed_tuple(self.actions, GroundedAction, field_name="grounded actions")
        facts = _typed_tuple(self.facts, GroundedFact, field_name="grounded facts")

        action_refs = tuple(action.action_ref for action in actions)
        if len(set(action_refs)) != len(action_refs):
            raise DataValidationError(
                "grounded action references must be unique",
                code="briefs.duplicate_action_ref",
                scope="grounded actions",
            )
        expected_ranks = tuple(range(1, len(actions) + 1))
        if tuple(action.rank for action in actions) != expected_ranks:
            raise DataValidationError(
                "grounded actions must retain contiguous authoritative rank order",
                code="briefs.invalid_action_order",
                scope="grounded actions",
            )

        fact_ids = tuple(fact.fact_id for fact in facts)
        if len(set(fact_ids)) != len(fact_ids):
            raise DataValidationError(
                "grounded fact identifiers must be unique",
                code="briefs.duplicate_fact_id",
                scope="grounded facts",
            )
        if fact_ids != tuple(sorted(fact_ids)):
            raise DataValidationError(
                "grounded facts must be ordered by fact_id",
                code="briefs.invalid_fact_order",
                scope="grounded facts",
            )

        facts_by_id = {fact.fact_id: fact for fact in facts}
        referenced_ids = {
            fact_ref for action in actions for fact_ref in action.fact_refs
        }
        if referenced_ids != set(fact_ids):
            raise DataValidationError(
                "grounded facts must exactly match action evidence closure",
                code="briefs.invalid_evidence_closure",
                scope="grounded input",
            )
        for action in actions:
            if any(facts_by_id[fact_ref].sku != action.sku for fact_ref in action.fact_refs):
                raise DataValidationError(
                    "grounded action evidence must belong to the action SKU",
                    code="briefs.cross_sku_evidence",
                    scope="grounded action",
                )

        object.__setattr__(self, "actions", actions)
        object.__setattr__(self, "facts", facts)


def _draft_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise DataValidationError(
            f"{field_name} must be text",
            code="briefs.invalid_draft_text",
            scope=field_name,
        )
    return value


@dataclass(frozen=True, slots=True)
class AIBriefSummary:
    """Untrusted summary prose and its claimed grounding references."""

    text: str
    action_refs: tuple[str, ...]
    fact_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _draft_text(self.text, field_name="summary text"))
        object.__setattr__(
            self,
            "action_refs",
            normalize_text_tuple(self.action_refs, field_name="summary action refs"),
        )
        object.__setattr__(
            self,
            "fact_refs",
            normalize_text_tuple(self.fact_refs, field_name="summary fact refs"),
        )


@dataclass(frozen=True, slots=True)
class AIBriefItem:
    """One untrusted prose item claiming to explain one grounded action."""

    action_ref: str
    text: str
    fact_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_ref",
            require_text(self.action_ref, field_name="draft action_ref"),
        )
        object.__setattr__(self, "text", _draft_text(self.text, field_name="item text"))
        object.__setattr__(
            self,
            "fact_refs",
            normalize_text_tuple(self.fact_refs, field_name="item fact refs"),
        )


@dataclass(frozen=True, slots=True)
class AIBriefDraft:
    """Untrusted model draft; cross-object grounding belongs to MVP-015."""

    summary: AIBriefSummary
    items: tuple[AIBriefItem, ...]

    def __post_init__(self) -> None:
        require_instance(self.summary, AIBriefSummary, field_name="brief summary")
        items = _typed_tuple(self.items, AIBriefItem, field_name="brief items")
        action_refs = tuple(item.action_ref for item in items)
        if len(set(action_refs)) != len(action_refs):
            raise DataValidationError(
                "draft item action references must be unique",
                code="briefs.duplicate_draft_action_ref",
                scope="brief items",
            )
        object.__setattr__(self, "items", items)


class BriefGenerationStatus(str, Enum):
    """Python-owned outcome of one optional brief-generation attempt."""

    GENERATED = "GENERATED"
    DISABLED = "DISABLED"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class DailyBrief:
    """A completely validated AI explanation bound to exact grounding."""

    analysis_timestamp: datetime
    grounding_digest: str
    summary: AIBriefSummary
    items: tuple[AIBriefItem, ...]

    def __post_init__(self) -> None:
        require_aware_datetime(
            self.analysis_timestamp,
            field_name="daily brief analysis timestamp",
        )
        if (
            not isinstance(self.grounding_digest, str)
            or _SHA256_HEX_PATTERN.fullmatch(self.grounding_digest) is None
        ):
            raise DataValidationError(
                "grounding digest must be lowercase SHA-256 hexadecimal text",
                code="briefs.invalid_grounding_digest",
                scope="grounding_digest",
            )
        require_instance(self.summary, AIBriefSummary, field_name="daily brief summary")
        object.__setattr__(
            self,
            "items",
            _typed_tuple(self.items, AIBriefItem, field_name="daily brief items"),
        )


@dataclass(frozen=True, slots=True)
class BriefGenerationResult:
    """Minimal safe result containing no provider diagnostics or raw output."""

    status: BriefGenerationStatus
    brief: DailyBrief | None

    def __post_init__(self) -> None:
        status = require_instance(
            self.status,
            BriefGenerationStatus,
            field_name="brief generation status",
        )
        if status is BriefGenerationStatus.GENERATED:
            if not isinstance(self.brief, DailyBrief):
                raise DataValidationError(
                    "generated status requires a validated daily brief",
                    code="briefs.generated_without_brief",
                    scope="brief generation result",
                )
        elif self.brief is not None:
            raise DataValidationError(
                "non-generated status must not contain a daily brief",
                code="briefs.unexpected_brief",
                scope="brief generation result",
            )

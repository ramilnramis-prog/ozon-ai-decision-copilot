"""Shared immutable output and construction helpers for decision rules.

This module owns traceability and deterministic identifiers only.  It does not
calculate analytics, assign priority, read providers, or access the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from app.core.clock import require_aware_datetime
from app.core.errors import DataValidationError
from app.domain.common import (
    DateRange,
    PolicyIdentity,
    Provenance,
    SkuId,
    SourceType,
    require_instance,
)
from app.domain.recommendations import (
    CalculatedFact,
    FactValue,
    Recommendation,
    RecommendationCategory,
    RecommendationStatus,
)


DECISION_PROVIDER = "deterministic_decision_engine"


def policy_source_ref(identity: PolicyIdentity) -> str:
    """Return the stable reference used for an explicit policy version."""

    require_instance(identity, PolicyIdentity, field_name="decision policy identity")
    return f"policy:{identity.policy_id}:{identity.version}"


def evidence_source_refs(
    source_refs: Iterable[str],
    policy_identity: PolicyIdentity,
) -> tuple[str, ...]:
    """Combine upstream evidence and policy identity in stable first-seen order."""

    values = (*tuple(source_refs), policy_source_ref(policy_identity))
    return tuple(dict.fromkeys(values))


def _identifier(
    prefix: str,
    sku: SkuId,
    rule_code: str,
    suffix: str | None = None,
) -> str:
    parts = (prefix, str(sku), rule_code, suffix)
    return ":".join(part for part in parts if part is not None)


def calculated_provenance(record_id: str, analysis_timestamp: datetime) -> Provenance:
    """Build truthful provenance for one deterministic decision-layer record."""

    require_aware_datetime(analysis_timestamp, field_name="decision analysis timestamp")
    return Provenance(
        source_type=SourceType.CALCULATED,
        provider=DECISION_PROVIDER,
        ingested_at=analysis_timestamp,
        source_timestamp=analysis_timestamp,
        source_record_id=record_id,
    )


def make_fact(
    *,
    sku: SkuId,
    rule_code: str,
    suffix: str,
    name: str,
    value: FactValue,
    unit: str,
    source_refs: tuple[str, ...],
    analysis_timestamp: datetime,
    instance_key: str | None = None,
    period: DateRange | None = None,
) -> CalculatedFact:
    """Create one deterministic fact copied from an authoritative result field."""

    fact_suffix = suffix if instance_key is None else f"{instance_key}:{suffix}"
    fact_id = _identifier("fact", sku, rule_code, fact_suffix)
    return CalculatedFact(
        fact_id=fact_id,
        sku=sku,
        name=name,
        value=value,
        unit=unit,
        period=period,
        formula_or_rule_id=rule_code,
        source_refs=source_refs,
        provenance=calculated_provenance(fact_id, analysis_timestamp),
    )


def make_recommendation(
    *,
    sku: SkuId,
    category: RecommendationCategory,
    rule_code: str,
    explanation: str,
    proposed_action: str,
    facts: tuple[CalculatedFact, ...],
    analysis_timestamp: datetime,
    instance_key: str | None = None,
) -> Recommendation:
    """Create a read-only, evidence-backed recommendation with a stable ID."""

    recommendation_id = _identifier("recommendation", sku, rule_code, instance_key)
    return Recommendation(
        recommendation_id=recommendation_id,
        sku=sku,
        category=category,
        rule_code=rule_code,
        explanation=explanation,
        evidence_refs=tuple(fact.fact_id for fact in facts),
        proposed_action=proposed_action,
        status=RecommendationStatus.PROPOSED,
        provenance=calculated_provenance(recommendation_id, analysis_timestamp),
        analysis_timestamp=analysis_timestamp,
    )


@dataclass(frozen=True, slots=True)
class DecisionEvaluation:
    """Facts and recommendations emitted by one deterministic rule family."""

    sku: SkuId
    analysis_timestamp: datetime
    facts: tuple[CalculatedFact, ...]
    recommendations: tuple[Recommendation, ...]

    def __post_init__(self) -> None:
        require_instance(self.sku, SkuId, field_name="decision evaluation sku")
        require_aware_datetime(
            self.analysis_timestamp,
            field_name="decision evaluation analysis timestamp",
        )
        facts = tuple(self.facts)
        recommendations = tuple(self.recommendations)
        for fact in facts:
            require_instance(fact, CalculatedFact, field_name="decision fact")
            if fact.sku != self.sku:
                raise DataValidationError(
                    "decision facts must match the evaluated SKU",
                    code="decisions.fact_sku_mismatch",
                    scope="decision evaluation",
                )
            if (
                fact.provenance.source_type is not SourceType.CALCULATED
                or fact.provenance.provider != DECISION_PROVIDER
                or fact.provenance.source_record_id != fact.fact_id
                or fact.provenance.ingested_at != self.analysis_timestamp
                or fact.provenance.source_timestamp != self.analysis_timestamp
            ):
                raise DataValidationError(
                    "decision facts require deterministic calculated provenance",
                    code="decisions.invalid_fact_provenance",
                    scope="decision evaluation",
                )
        for recommendation in recommendations:
            require_instance(
                recommendation,
                Recommendation,
                field_name="decision recommendation",
            )
            if recommendation.sku != self.sku:
                raise DataValidationError(
                    "recommendations must match the evaluated SKU",
                    code="decisions.recommendation_sku_mismatch",
                    scope="decision evaluation",
                )
            if recommendation.analysis_timestamp != self.analysis_timestamp:
                raise DataValidationError(
                    "recommendations must use the evaluation timestamp",
                    code="decisions.timestamp_mismatch",
                    scope="decision evaluation",
                )
            if (
                recommendation.provenance.source_type is not SourceType.CALCULATED
                or recommendation.provenance.provider != DECISION_PROVIDER
                or recommendation.provenance.source_record_id
                != recommendation.recommendation_id
                or recommendation.provenance.ingested_at != self.analysis_timestamp
                or recommendation.provenance.source_timestamp
                != self.analysis_timestamp
            ):
                raise DataValidationError(
                    "recommendations require deterministic calculated provenance",
                    code="decisions.invalid_recommendation_provenance",
                    scope="decision evaluation",
                )

        fact_ids = tuple(fact.fact_id for fact in facts)
        recommendation_ids = tuple(
            recommendation.recommendation_id for recommendation in recommendations
        )
        if len(set(fact_ids)) != len(fact_ids):
            raise DataValidationError(
                "decision fact identifiers must be unique",
                code="decisions.duplicate_fact_id",
                scope="decision evaluation",
            )
        if len(set(recommendation_ids)) != len(recommendation_ids):
            raise DataValidationError(
                "recommendation identifiers must be unique",
                code="decisions.duplicate_recommendation_id",
                scope="decision evaluation",
            )
        referenced_fact_ids = {
            evidence_ref
            for recommendation in recommendations
            for evidence_ref in recommendation.evidence_refs
        }
        if referenced_fact_ids != set(fact_ids):
            raise DataValidationError(
                "decision evidence must exactly reference the emitted facts",
                code="decisions.inconsistent_evidence",
                scope="decision evaluation",
            )
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "recommendations", recommendations)


def empty_evaluation(sku: SkuId, analysis_timestamp: datetime) -> DecisionEvaluation:
    """Return an explicit no-action result without filler facts or recommendations."""

    return DecisionEvaluation(sku, analysis_timestamp, (), ())

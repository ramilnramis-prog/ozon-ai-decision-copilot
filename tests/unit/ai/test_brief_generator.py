"""Tests for the narrow Daily Brief generation orchestration boundary."""

from datetime import UTC, datetime

import pytest

from app.ai.brief_generator import generate_daily_brief
from app.core.errors import AIInvalidOutputError, AIUnavailableError, ConfigurationError
from app.domain.briefs import (
    AIBriefDraft,
    AIBriefItem,
    AIBriefSummary,
    BriefGenerationStatus,
    GroundedAction,
    GroundedBriefInput,
    GroundedFact,
    GroundedValueType,
)
from app.domain.common import Severity, SkuId, Urgency
from app.domain.recommendations import RecommendationCategory, RecommendationStatus


STAMP = datetime(2026, 9, 7, 9, tzinfo=UTC)


def empty_input() -> GroundedBriefInput:
    return GroundedBriefInput(STAMP, (), ())


def one_action_input() -> GroundedBriefInput:
    source_fact = GroundedFact(
        "fact:a",
        SkuId("SKU-1"),
        "value",
        "1233.00",
        GroundedValueType.DECIMAL,
        "RUB",
        None,
        "test.rule",
        ("source:test",),
    )
    source_action = GroundedAction(
        "recommendation:SKU-1:a",
        "recommendation:SKU-1:a",
        SkuId("SKU-1"),
        "Test product",
        "test.rule",
        RecommendationCategory.PRICING,
        RecommendationStatus.PROPOSED,
        1,
        Severity.HIGH,
        Urgency.SOON,
        (source_fact.fact_id,),
    )
    return GroundedBriefInput(STAMP, (source_action,), (source_fact,))


class FakeProvider:
    def __init__(self, draft: AIBriefDraft | None = None, error: Exception | None = None):
        self.draft = draft
        self.error = error
        self.calls = 0

    def create_draft(self, brief_input: GroundedBriefInput) -> AIBriefDraft:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.draft is not None
        return self.draft


def test_disabled_generation_does_not_call_provider() -> None:
    provider = FakeProvider(AIBriefDraft(AIBriefSummary("", (), ()), ()))

    result = generate_daily_brief(empty_input(), provider, enabled=False)

    assert result.status is BriefGenerationStatus.DISABLED
    assert result.brief is None
    assert provider.calls == 0


def test_valid_generation_calls_provider_once_and_returns_validated_brief() -> None:
    source = one_action_input()
    provider = FakeProvider(
        AIBriefDraft(
            AIBriefSummary("Сводка.", (), ()),
            (AIBriefItem(source.actions[0].action_ref, "Значение 1233.00.", ("fact:a",)),),
        )
    )

    result = generate_daily_brief(source, provider)

    assert result.status is BriefGenerationStatus.GENERATED
    assert result.brief is not None
    assert provider.calls == 1


def test_provider_availability_failure_maps_to_unavailable_without_retry() -> None:
    provider = FakeProvider(
        error=AIUnavailableError("safe", code="ai.test_unavailable")
    )

    result = generate_daily_brief(empty_input(), provider)

    assert result.status is BriefGenerationStatus.UNAVAILABLE
    assert result.brief is None
    assert provider.calls == 1


def test_provider_invalid_output_failure_maps_to_invalid_without_retry() -> None:
    provider = FakeProvider(
        error=AIInvalidOutputError("safe", code="ai.test_invalid")
    )

    result = generate_daily_brief(empty_input(), provider)

    assert result.status is BriefGenerationStatus.INVALID
    assert result.brief is None
    assert provider.calls == 1


def test_grounding_failure_maps_to_invalid_without_repair_or_retry() -> None:
    source = one_action_input()
    draft = AIBriefDraft(
        AIBriefSummary("", (), ()),
        (AIBriefItem(source.actions[0].action_ref, "Значение 999.00.", ("fact:a",)),),
    )
    provider = FakeProvider(draft)

    result = generate_daily_brief(source, provider)

    assert result.status is BriefGenerationStatus.INVALID
    assert result.brief is None
    assert provider.calls == 1
    assert draft.items[0].text == "Значение 999.00."


def test_unexpected_provider_programming_error_propagates() -> None:
    provider = FakeProvider(error=RuntimeError("programming defect"))

    with pytest.raises(RuntimeError, match="programming defect"):
        generate_daily_brief(empty_input(), provider)

    assert provider.calls == 1


def test_enabled_generation_requires_provider_and_boolean_flag() -> None:
    with pytest.raises(ConfigurationError) as missing:
        generate_daily_brief(empty_input(), None)
    assert missing.value.code == "configuration.missing_ai_provider"

    with pytest.raises(ConfigurationError) as invalid_flag:
        generate_daily_brief(empty_input(), None, enabled=1)  # type: ignore[arg-type]
    assert invalid_flag.value.code == "configuration.invalid_ai_enabled"


def test_empty_grounding_is_sent_and_can_generate_empty_items() -> None:
    provider = FakeProvider(
        AIBriefDraft(AIBriefSummary("Нет приоритетных действий.", (), ()), ())
    )

    first = generate_daily_brief(empty_input(), provider)
    second = generate_daily_brief(empty_input(), provider)

    assert first == second
    assert first.status is BriefGenerationStatus.GENERATED
    assert first.brief is not None and first.brief.items == ()
    assert provider.calls == 2


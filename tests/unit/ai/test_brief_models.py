"""Tests for validated Daily Brief result models owned by Python."""

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest

from app.core.errors import DataValidationError
from app.domain.briefs import (
    AIBriefItem,
    AIBriefSummary,
    BriefGenerationResult,
    BriefGenerationStatus,
    DailyBrief,
)


STAMP = datetime(2026, 9, 7, 9, tzinfo=UTC)
DIGEST = "a" * 64
SUMMARY = AIBriefSummary("Требуется внимание.", (), ())
ITEM = AIBriefItem("recommendation:SKU-1:test", "Проверьте действие.", ())


def brief() -> DailyBrief:
    return DailyBrief(STAMP, DIGEST, SUMMARY, (ITEM,))


def test_daily_brief_has_only_approved_fields_and_is_immutable() -> None:
    result = brief()

    assert tuple(field.name for field in fields(DailyBrief)) == (
        "analysis_timestamp",
        "grounding_digest",
        "summary",
        "items",
    )
    assert result.analysis_timestamp is STAMP
    assert result.summary is SUMMARY
    assert result.items == (ITEM,)
    with pytest.raises(FrozenInstanceError):
        result.items = ()


@pytest.mark.parametrize("digest", ["", "A" * 64, "g" * 64, "0" * 63, 64])
def test_daily_brief_rejects_invalid_grounding_digest(digest: object) -> None:
    with pytest.raises(DataValidationError) as error:
        DailyBrief(STAMP, digest, SUMMARY, ())  # type: ignore[arg-type]

    assert error.value.code == "briefs.invalid_grounding_digest"


def test_daily_brief_rejects_naive_analysis_timestamp() -> None:
    with pytest.raises(DataValidationError) as error:
        DailyBrief(datetime(2026, 9, 7, 9), DIGEST, SUMMARY, ())

    assert error.value.code == "clock.naive_datetime"


def test_generation_result_has_only_status_and_brief_and_is_immutable() -> None:
    result = BriefGenerationResult(BriefGenerationStatus.GENERATED, brief())

    assert tuple(field.name for field in fields(BriefGenerationResult)) == (
        "status",
        "brief",
    )
    assert result.brief is not None
    with pytest.raises(FrozenInstanceError):
        result.status = BriefGenerationStatus.INVALID


@pytest.mark.parametrize(
    "status",
    [
        BriefGenerationStatus.DISABLED,
        BriefGenerationStatus.UNAVAILABLE,
        BriefGenerationStatus.INVALID,
    ],
)
def test_non_generated_status_requires_no_brief(
    status: BriefGenerationStatus,
) -> None:
    assert BriefGenerationResult(status, None).brief is None

    with pytest.raises(DataValidationError) as error:
        BriefGenerationResult(status, brief())
    assert error.value.code == "briefs.unexpected_brief"


def test_generated_status_requires_validated_brief() -> None:
    with pytest.raises(DataValidationError) as error:
        BriefGenerationResult(BriefGenerationStatus.GENERATED, None)

    assert error.value.code == "briefs.generated_without_brief"


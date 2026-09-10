"""Tests for the application-level grounded brief service."""

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import re

import pytest

from app.bootstrap import build_application
from app.ai.brief_input import (
    build_grounded_brief_input,
    serialize_grounded_brief_input,
)
from app.core.clock import FixedClock
from app.core.errors import ConfigurationError
from app.domain.briefs import (
    AIBriefDraft,
    AIBriefItem,
    AIBriefSummary,
    BriefGenerationStatus,
    GroundedBriefInput,
)
from app.domain.common import SkuId
from app.services.briefs import BriefService


STAMP = datetime(2026, 9, 7, 9, tzinfo=UTC)


class SnapshotDraftProvider:
    def __init__(self) -> None:
        self.inputs: list[GroundedBriefInput] = []

    def create_draft(self, brief_input: GroundedBriefInput) -> AIBriefDraft:
        self.inputs.append(brief_input)
        return AIBriefDraft(
            AIBriefSummary("Краткая сводка.", (), ()),
            tuple(
                AIBriefItem(action.action_ref, "Требуется внимание.", ())
                for action in brief_input.actions
            ),
        )


@pytest.fixture
def snapshot():
    return build_application(clock=FixedClock(STAMP)).analysis.analyze(
        (SkuId("DEMO-005"),)
    )


def test_service_builds_grounding_from_snapshot_and_generates(snapshot) -> None:
    provider = SnapshotDraftProvider()

    result = BriefService(provider, enabled=True).generate(snapshot)

    assert result.status is BriefGenerationStatus.GENERATED
    assert result.brief is not None
    assert len(result.brief.items) == 1
    assert len(provider.inputs) == 1
    assert provider.inputs[0].analysis_timestamp == snapshot.analysis_timestamp


def test_grounding_identity_is_deterministic_canonical_and_provider_free(snapshot) -> None:
    provider = SnapshotDraftProvider()
    service = BriefService(provider, enabled=True)

    first = service.grounding_digest(snapshot)
    second = service.grounding_digest(snapshot)
    grounded_input = build_grounded_brief_input(snapshot)
    expected_digest = hashlib.sha256(
        serialize_grounded_brief_input(grounded_input).encode("utf-8")
    ).hexdigest()

    assert first == second
    assert first == expected_digest
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert provider.inputs == []


def test_grounding_identity_depends_only_on_canonical_grounded_content(snapshot) -> None:
    service = BriefService()
    equivalent = replace(snapshot, provenance=())

    assert equivalent != snapshot
    assert build_grounded_brief_input(equivalent) == build_grounded_brief_input(snapshot)
    assert service.grounding_digest(equivalent) == service.grounding_digest(snapshot)


def test_same_timestamp_different_grounded_content_has_different_identity() -> None:
    services = build_application(clock=FixedClock(STAMP))
    first = services.analysis.analyze((SkuId("DEMO-005"),))
    second = services.analysis.analyze((SkuId("DEMO-006"),))

    first_digest = services.briefs.grounding_digest(first)
    second_digest = services.briefs.grounding_digest(second)

    assert first.analysis_timestamp == second.analysis_timestamp == STAMP
    assert build_grounded_brief_input(first) != build_grounded_brief_input(second)
    assert first_digest != second_digest


def test_generated_brief_digest_matches_service_grounding_identity(snapshot) -> None:
    provider = SnapshotDraftProvider()
    service = BriefService(provider, enabled=True)
    digest = service.grounding_digest(snapshot)

    result = service.generate(snapshot)

    assert result.brief is not None
    assert result.brief.analysis_timestamp is snapshot.analysis_timestamp
    assert result.brief.grounding_digest == digest
    assert len(provider.inputs) == 1


def test_disabled_service_still_builds_no_provider_fallback(snapshot) -> None:
    provider = SnapshotDraftProvider()

    result = BriefService(provider, enabled=False).generate(snapshot)

    assert result.status is BriefGenerationStatus.DISABLED
    assert result.brief is None
    assert provider.inputs == []


def test_enabled_service_requires_provider() -> None:
    with pytest.raises(ConfigurationError) as error:
        BriefService(None, enabled=True)

    assert error.value.code == "configuration.missing_ai_provider"


def test_service_repr_does_not_expose_provider_state() -> None:
    provider = SnapshotDraftProvider()

    rendered = repr(BriefService(provider, enabled=True))

    assert "SnapshotDraftProvider" not in rendered
    assert "inputs" not in rendered

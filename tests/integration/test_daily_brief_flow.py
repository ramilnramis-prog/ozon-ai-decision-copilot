"""Grounded Daily Brief flow tests over representative real demo snapshots."""

from datetime import UTC, datetime

import pytest

from app.ai.brief_input import build_grounded_brief_input
from app.bootstrap import build_application
from app.core.clock import FixedClock
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


class QualitativeProvider:
    def __init__(
        self,
        *,
        reorder: bool = False,
        omit_last: bool = False,
        injected_text: str | None = None,
    ) -> None:
        self.reorder = reorder
        self.omit_last = omit_last
        self.injected_text = injected_text
        self.inputs: list[GroundedBriefInput] = []

    def create_draft(self, brief_input: GroundedBriefInput) -> AIBriefDraft:
        self.inputs.append(brief_input)
        actions = list(brief_input.actions)
        if self.reorder:
            actions.reverse()
        if self.omit_last:
            actions = actions[:-1]
        items = tuple(
            AIBriefItem(
                action.action_ref,
                self.injected_text or "Требуется управленческое внимание.",
                (),
            )
            for action in actions
        )
        return AIBriefDraft(AIBriefSummary("Приоритеты сформированы.", (), ()), items)


@pytest.fixture(scope="module")
def analysis_service():
    return build_application(clock=FixedClock(STAMP)).analysis


@pytest.mark.parametrize(
    ("sku", "expected_actions"),
    [
        ("DEMO-005", 1),
        ("DEMO-006", 5),
        ("DEMO-015", 4),
        ("DEMO-018", 4),
        ("DEMO-021", 2),
        ("DEMO-037", 2),
    ],
)
def test_real_grounded_demo_inputs_generate_one_item_per_action(
    analysis_service,
    sku: str,
    expected_actions: int,
) -> None:
    snapshot = analysis_service.analyze((SkuId(sku),))
    provider = QualitativeProvider()

    result = BriefService(provider, enabled=True).generate(snapshot)

    assert result.status is BriefGenerationStatus.GENERATED
    assert result.brief is not None
    assert len(result.brief.items) == expected_actions
    grounded = provider.inputs[0]
    assert grounded == build_grounded_brief_input(snapshot)
    assert tuple(item.action_ref for item in result.brief.items) == tuple(
        action.action_ref for action in grounded.actions
    )


def test_demo_005_rejects_invented_number(analysis_service) -> None:
    snapshot = analysis_service.analyze((SkuId("DEMO-005"),))

    result = BriefService(
        QualitativeProvider(injected_text="Запас 999999 единиц."),
        enabled=True,
    ).generate(snapshot)

    assert result.status is BriefGenerationStatus.INVALID
    assert result.brief is None


@pytest.mark.parametrize("provider", [QualitativeProvider(reorder=True), QualitativeProvider(omit_last=True)])
def test_demo_006_rejects_reordered_or_missing_items(
    analysis_service,
    provider: QualitativeProvider,
) -> None:
    snapshot = analysis_service.analyze((SkuId("DEMO-006"),))

    result = BriefService(provider, enabled=True).generate(snapshot)

    assert result.status is BriefGenerationStatus.INVALID
    assert result.brief is None


def test_demo_015_preserves_four_overlapping_actions(analysis_service) -> None:
    snapshot = analysis_service.analyze((SkuId("DEMO-015"),))
    grounded = build_grounded_brief_input(snapshot)

    result = BriefService(QualitativeProvider(), enabled=True).generate(snapshot)

    assert len(grounded.actions) == 4
    assert result.status is BriefGenerationStatus.GENERATED
    assert result.brief is not None and len(result.brief.items) == 4


@pytest.mark.parametrize(
    ("sku", "invented_claim"),
    [
        ("DEMO-018", "Безопасная цена 777.00."),
        ("DEMO-021", "Прибыль 777.00 и маржа 20%."),
        ("DEMO-037", "Дата дефицита 2026-09-30, количество 777."),
    ],
)
def test_partial_or_infeasible_demo_cases_reject_invented_values(
    analysis_service,
    sku: str,
    invented_claim: str,
) -> None:
    snapshot = analysis_service.analyze((SkuId(sku),))

    result = BriefService(
        QualitativeProvider(injected_text=invented_claim),
        enabled=True,
    ).generate(snapshot)

    assert result.status is BriefGenerationStatus.INVALID
    assert result.brief is None


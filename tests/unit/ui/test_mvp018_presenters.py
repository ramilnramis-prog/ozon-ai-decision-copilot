"""Focused presenter tests for MVP-018 pricing and validated briefs."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.bootstrap import build_application
from app.core.clock import FixedClock
from app.core.config import AppConfig
from app.core.errors import DataValidationError
from app.domain.briefs import (
    AIBriefDraft,
    AIBriefItem,
    AIBriefSummary,
    BriefGenerationResult,
    BriefGenerationStatus,
    GroundedBriefInput,
)
from app.domain.common import AvailabilityStatus, SkuId
from app.services.briefs import BriefService
from app.services.models import PriceScenarioRequest
from app.ui.presenters import (
    build_brief_status_view,
    build_pricing_analysis_view,
    parse_candidate_price,
)


STAMP = datetime(2026, 9, 7, 9, tzinfo=UTC)


@pytest.fixture(scope="module")
def services():
    return build_application(
        AppConfig(ai_enabled=False),
        clock=FixedClock(STAMP),
        environ={},
    )


def _pricing(services, sku: str, candidate: str):
    source = services.pricing.simulate(
        SkuId(sku),
        (PriceScenarioRequest("ui-scenario", Decimal(candidate)),),
    )
    return source, build_pricing_analysis_view(source)


def test_candidate_price_parser_preserves_exact_decimal_scale() -> None:
    value = parse_candidate_price(" 1233.00 ")

    assert value == Decimal("1233.00")
    assert value.as_tuple() == Decimal("1233.00").as_tuple()
    assert not isinstance(value, float)


@pytest.mark.parametrize("raw", ["", "   ", "abc", "1.2.3", "NaN", "Infinity", "-Infinity"])
def test_candidate_price_parser_rejects_malformed_or_nonfinite_text(raw: str) -> None:
    with pytest.raises(DataValidationError):
        parse_candidate_price(raw)


@pytest.mark.parametrize("raw", ["0", "-0.01"])
def test_request_contract_rejects_nonpositive_candidate_after_exact_parse(raw: str) -> None:
    with pytest.raises(DataValidationError) as error:
        PriceScenarioRequest("ui", parse_candidate_price(raw))

    assert error.value.code == "domain.invalid_positive_decimal"


def test_pricing_presenter_copies_every_authoritative_scenario_field(services) -> None:
    source, view = _pricing(services, "DEMO-015", "1233.00")
    source_result = source.scenario_results[0]
    scenario = view.scenarios[0]

    assert view.sku == str(source.sku) == "DEMO-015"
    assert view.analysis_timestamp == source.analysis_timestamp == STAMP
    assert view.status is source.status
    assert scenario.scenario_id == source_result.scenario.scenario_id
    assert scenario.analysis_timestamp == source_result.scenario.as_of
    assert scenario.status is source_result.status
    assert scenario.safety_status is source_result.safety_status
    assert scenario.current.selling_price == source_result.current_price
    assert scenario.scenario.selling_price == source_result.candidate_price
    assert scenario.current.profit_per_unit == source_result.current_economics.profit_per_unit
    assert scenario.scenario.profit_per_unit == source_result.economics.profit_per_unit
    assert scenario.current.contribution_margin == (
        source_result.current_economics.contribution_margin
    )
    assert scenario.scenario.contribution_margin == (
        source_result.economics.contribution_margin
    )
    assert scenario.price_delta == source_result.price_delta
    assert scenario.profit_per_unit_delta == source_result.profit_per_unit_delta
    assert scenario.margin_delta == source_result.margin_delta
    assert scenario.distance_from_safe_price == source_result.distance_from_safe_price
    assert scenario.scenario.minimum_safe.price == source_result.economics.minimum_safe_price
    assert scenario.evidence_refs == source_result.evidence_refs


def test_below_equal_and_above_safe_price_states_are_copied_from_service(services) -> None:
    current = services.analysis.analyze((SkuId("DEMO-001"),)).sku_results[0].economics
    assert current is not None and current.minimum_safe_price is not None
    safe_price = current.minimum_safe_price
    requests = tuple(
        PriceScenarioRequest(name, candidate)
        for name, candidate in (
            ("below", safe_price - Decimal("0.01")),
            ("equal", safe_price),
            ("above", safe_price + Decimal("0.01")),
        )
    )
    source = services.pricing.simulate(SkuId("DEMO-001"), requests)
    view = build_pricing_analysis_view(source)

    assert tuple(item.scenario_id for item in view.scenarios) == (
        "below",
        "equal",
        "above",
    )
    assert tuple(item.safety_status for item in view.scenarios) == tuple(
        item.safety_status for item in source.scenario_results
    )
    assert tuple(item.distance_from_safe_price for item in view.scenarios) == tuple(
        item.distance_from_safe_price for item in source.scenario_results
    )


def test_pricing_presenter_preserves_demo_018_no_finite_safe_price(services) -> None:
    source, view = _pricing(services, "DEMO-018", "4000")

    assert source.scenario_results[0].economics.minimum_safe_price is None
    assert view.scenarios[0].scenario.minimum_safe.price is None
    assert view.scenarios[0].scenario.minimum_safe.status is AvailabilityStatus.NOT_APPLICABLE
    assert view.scenarios[0].distance_from_safe_price is None


def test_pricing_presenter_preserves_demo_021_missing_economics(services) -> None:
    source, view = _pricing(services, "DEMO-021", "1233.00")
    scenario = view.scenarios[0]

    assert source.scenario_results[0].status is AvailabilityStatus.INSUFFICIENT_DATA
    assert scenario.status is AvailabilityStatus.INSUFFICIENT_DATA
    assert scenario.current.profit_per_unit is None
    assert scenario.scenario.profit_per_unit is None
    assert scenario.current.contribution_margin is None
    assert scenario.scenario.contribution_margin is None
    assert scenario.scenario.minimum_safe.price is None


def test_pricing_presenter_is_repeatable_immutable_and_does_not_mutate_source(services) -> None:
    source, first = _pricing(services, "DEMO-015", "1233.00")
    source_state = source.scenario_results
    second = build_pricing_analysis_view(source)

    assert first == second
    assert source.scenario_results is source_state
    with pytest.raises(FrozenInstanceError):
        first.scenarios = ()


class _QualitativeProvider:
    def create_draft(self, brief_input: GroundedBriefInput) -> AIBriefDraft:
        return AIBriefDraft(
            AIBriefSummary(
                "Точная управленческая сводка.",
                tuple(action.action_ref for action in brief_input.actions),
                (),
            ),
            tuple(
                AIBriefItem(
                    action.action_ref,
                    f"Точное объяснение {action.action_ref}.",
                    (),
                )
                for action in brief_input.actions
            ),
        )


def test_brief_presenter_copies_only_validated_daily_brief(services) -> None:
    snapshot = services.analysis.analyze((SkuId("DEMO-005"),))
    source = BriefService(_QualitativeProvider(), enabled=True).generate(snapshot)
    view = build_brief_status_view(source)

    assert source.status is BriefGenerationStatus.GENERATED
    assert source.brief is not None and view.brief is not None
    assert view.status is BriefGenerationStatus.GENERATED
    assert view.brief.analysis_timestamp == source.brief.analysis_timestamp
    assert view.brief.grounding_digest == source.brief.grounding_digest
    assert view.brief.summary_text == source.brief.summary.text
    assert view.brief.summary_action_refs == source.brief.summary.action_refs
    assert view.brief.summary_fact_refs == source.brief.summary.fact_refs
    assert tuple(item.action_ref for item in view.brief.items) == tuple(
        item.action_ref for item in source.brief.items
    )
    assert tuple(item.text for item in view.brief.items) == tuple(
        item.text for item in source.brief.items
    )


@pytest.mark.parametrize(
    "status",
    [
        BriefGenerationStatus.DISABLED,
        BriefGenerationStatus.UNAVAILABLE,
        BriefGenerationStatus.INVALID,
    ],
)
def test_non_generated_brief_statuses_expose_no_prose(status) -> None:
    view = build_brief_status_view(BriefGenerationResult(status, None))

    assert view.status is status
    assert view.brief is None


def test_ui_presenter_has_no_business_or_ai_runtime_dependencies() -> None:
    source = Path("app/ui/presenters.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(
        name.startswith(("app.analytics", "app.decisions", "app.providers", "app.ai"))
        for name in imports
    )
    assert "float(" not in source
    assert "calculate_unit_economics" not in source
    assert "build_grounded_brief_input" not in source

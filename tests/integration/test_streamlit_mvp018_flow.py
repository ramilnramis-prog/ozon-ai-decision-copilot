"""Streamlit integration coverage for MVP-018 service-backed interactions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import app.bootstrap as bootstrap_module
from app.ai.brief_input import build_grounded_brief_input
from app.bootstrap import build_application
from app.core.clock import FixedClock
from app.core.config import AppConfig
from app.core.errors import ProviderError
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
from app.ui.components import MISSING_VALUE
from app.ui.presenters import format_money


STAMP = datetime(2026, 9, 7, 9, tzinfo=UTC)
ENTRYPOINT = Path(__file__).resolve().parents[2] / "streamlit_app.py"


class CountingPricingService:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls: list[tuple[SkuId, tuple[PriceScenarioRequest, ...]]] = []

    def simulate(self, sku, requests):
        normalized = tuple(requests)
        self.calls.append((sku, normalized))
        return self.delegate.simulate(sku, normalized)


class QualitativeProvider:
    def __init__(
        self,
        summary_text: str = "Точная управленческая сводка.",
        item_text: str = "Требуется управленческое внимание.",
    ) -> None:
        self.inputs: list[GroundedBriefInput] = []
        self.summary_text = summary_text
        self.item_text = item_text

    def create_draft(self, brief_input: GroundedBriefInput) -> AIBriefDraft:
        self.inputs.append(brief_input)
        return AIBriefDraft(
            AIBriefSummary(self.summary_text, (), ()),
            tuple(
                AIBriefItem(
                    action.action_ref,
                    self.item_text,
                    (),
                )
                for action in brief_input.actions
            ),
        )


class CountingBriefService:
    def __init__(self, delegate, *, enabled: bool) -> None:
        self.delegate = delegate
        self.enabled = enabled
        self.calls = []
        self.identity_calls = []

    def grounding_digest(self, snapshot):
        self.identity_calls.append(snapshot)
        return self.delegate.grounding_digest(snapshot)

    def generate(self, snapshot):
        self.calls.append(snapshot)
        return self.delegate.generate(snapshot)


class StaticBriefService:
    def __init__(self, result: BriefGenerationResult) -> None:
        self.result = result
        self.enabled = True
        self.calls = []

    def grounding_digest(self, snapshot):
        return BriefService().grounding_digest(snapshot)

    def generate(self, snapshot):
        self.calls.append(snapshot)
        return self.result


class StaticAnalysisService:
    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot
        self.calls = []

    def analyze(self, requested_skus):
        self.calls.append(requested_skus)
        return self.snapshot


class FailingPricingService:
    def __init__(self) -> None:
        self.calls = 0

    def simulate(self, sku, requests):
        self.calls += 1
        raise ProviderError(
            "Unit-economics source is temporarily unavailable.",
            code="provider.temporarily_unavailable",
            scope="pricing",
            context={"api_key": "must-not-render"},
        )


def _base_services(stamp: datetime = STAMP):
    return build_application(
        AppConfig(ai_enabled=False),
        clock=FixedClock(stamp),
        environ={},
    )


def _button(app: AppTest, label: str):
    return next(button for button in app.button if button.label == label)


def _install(monkeypatch: pytest.MonkeyPatch, application) -> None:
    monkeypatch.setattr(
        bootstrap_module,
        "build_application",
        lambda *args, **kwargs: application,
    )


def test_price_simulator_sends_exact_decimal_to_public_service_and_persists_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _base_services()
    pricing = CountingPricingService(services.pricing)
    application = replace(services, pricing=pricing)
    _install(monkeypatch, application)

    app = AppTest.from_file(ENTRYPOINT).run(timeout=30)
    assert not app.exception
    assert tuple(tab.label for tab in app.tabs)[-2:] == (
        "Price Simulator",
        "AI Daily Brief",
    )
    assert len(app.selectbox) == 2
    assert app.selectbox[1].options[0].startswith("DEMO-001")
    assert len(app.selectbox[1].options) == 37
    assert len(app.number_input) == 0
    assert len(pricing.calls) == 0

    app.selectbox[1].set_value("DEMO-015").run(timeout=30)
    app.text_input[0].set_value("1233.00").run(timeout=30)
    _button(app, "Рассчитать сценарий").click().run(timeout=30)

    assert not app.exception
    assert len(pricing.calls) == 1
    called_sku, requests = pricing.calls[0]
    assert called_sku == SkuId("DEMO-015")
    assert len(requests) == 1
    assert requests[0].hypothetical_price == Decimal("1233.00")
    assert requests[0].hypothetical_price.as_tuple() == Decimal("1233.00").as_tuple()
    assert any(
        str(element.value).startswith("### Сценарий · DEMO-015")
        for element in app.markdown
    )
    comparison = next(
        frame.value
        for frame in app.dataframe
        if "Состояние" in frame.value.columns
    )
    expected = services.pricing.simulate(
        SkuId("DEMO-015"),
        (PriceScenarioRequest("expected", Decimal("1233.00")),),
    ).scenario_results[0]
    assert tuple(comparison["Цена"]) == (
        f"{format_money(expected.current_price)} RUB",
        f"{format_money(expected.candidate_price)} RUB",
    )
    assert tuple(comparison["Прибыль на единицу"]) == (
        f"{format_money(expected.current_economics.profit_per_unit)} RUB",
        f"{format_money(expected.economics.profit_per_unit)} RUB",
    )

    app.run(timeout=30)
    assert len(pricing.calls) == 1
    assert any("### Сценарий · DEMO-015" in str(item.value) for item in app.markdown)

    app.text_input[0].set_value("1233.01").run(timeout=30)
    assert len(pricing.calls) == 1
    assert not any("### Сценарий · DEMO-015" in str(item.value) for item in app.markdown)

    app.text_input[0].set_value("1233.00").run(timeout=30)
    _button(app, "Рассчитать сценарий").click().run(timeout=30)
    assert len(pricing.calls) == 2
    app.selectbox[1].set_value("DEMO-018").run(timeout=30)
    assert len(pricing.calls) == 2
    assert not any("### Сценарий · DEMO-015" in str(item.value) for item in app.markdown)


@pytest.mark.parametrize("raw", ["", "abc", "1.2.3", "NaN", "Infinity", "-Infinity", "0", "-1"])
def test_invalid_price_input_is_safe_and_does_not_create_scenario(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    services = _base_services()
    pricing = CountingPricingService(services.pricing)
    _install(monkeypatch, replace(services, pricing=pricing))
    app = AppTest.from_file(ENTRYPOINT).run(timeout=30)

    app.text_input[0].set_value(raw).run(timeout=30)
    _button(app, "Рассчитать сценарий").click().run(timeout=30)

    assert not app.exception
    assert len(pricing.calls) == 0
    assert any("Сценарий не рассчитан" in str(element.value) for element in app.error)
    assert not any("### Сценарий" in str(element.value) for element in app.markdown)


def test_pricing_service_failure_is_safe_and_deterministic_dashboard_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _base_services()
    pricing = FailingPricingService()
    _install(monkeypatch, replace(services, pricing=pricing))
    app = AppTest.from_file(ENTRYPOINT).run(timeout=30)

    app.text_input[0].set_value("1233.00").run(timeout=30)
    _button(app, "Рассчитать сценарий").click().run(timeout=30)

    assert not app.exception
    assert pricing.calls == 1
    assert any("temporarily unavailable" in str(element.value) for element in app.error)
    visible = tuple(app.error) + tuple(app.caption)
    assert not any("must-not-render" in str(element.value) for element in visible)
    assert any(metric.label.endswith("SKU") and metric.value == "37" for metric in app.metric)


@pytest.mark.parametrize(
    ("sku", "candidate", "expected_status"),
    [
        ("DEMO-015", "1233.00", AvailabilityStatus.AVAILABLE),
        ("DEMO-018", "4000", AvailabilityStatus.AVAILABLE),
        ("DEMO-021", "1233.00", AvailabilityStatus.INSUFFICIENT_DATA),
    ],
)
def test_real_pricing_service_demo_cases_render_without_fabrication(
    monkeypatch: pytest.MonkeyPatch,
    sku: str,
    candidate: str,
    expected_status: AvailabilityStatus,
) -> None:
    services = _base_services()
    _install(monkeypatch, services)
    app = AppTest.from_file(ENTRYPOINT).run(timeout=30)

    app.selectbox[1].set_value(sku).run(timeout=30)
    app.text_input[0].set_value(candidate).run(timeout=30)
    _button(app, "Рассчитать сценарий").click().run(timeout=30)

    expected = services.pricing.simulate(
        SkuId(sku),
        (PriceScenarioRequest("expected", Decimal(candidate)),),
    ).scenario_results[0]
    comparison = next(
        frame.value for frame in app.dataframe if "Состояние" in frame.value.columns
    )
    assert expected.status is expected_status
    if sku == "DEMO-018":
        assert expected.economics.minimum_safe_price is None
        assert comparison.iloc[1]["Минимальная безопасная цена"] == MISSING_VALUE
    if sku == "DEMO-021":
        assert expected.economics.profit_per_unit is None
        assert comparison.iloc[1]["Прибыль на единицу"] == MISSING_VALUE
        assert comparison.iloc[1]["Маржа, доля"] == MISSING_VALUE


def test_generated_brief_uses_real_grounding_service_once_and_preserves_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _base_services()
    provider = QualitativeProvider()
    brief_service = CountingBriefService(
        BriefService(provider, enabled=True),
        enabled=True,
    )
    application = replace(services, briefs=brief_service)
    _install(monkeypatch, application)
    app = AppTest.from_file(ENTRYPOINT).run(timeout=30)

    assert len(brief_service.calls) == 0
    _button(app, "Сформировать AI Daily Brief").click().run(timeout=30)

    assert not app.exception
    assert len(brief_service.calls) == 1
    assert len(provider.inputs) == 1
    assert provider.inputs[0] == build_grounded_brief_input(brief_service.calls[0])
    assert any(element.value == "Точная управленческая сводка." for element in app.markdown)
    rendered_items = [
        element.value
        for element in app.markdown
        if element.value == "Требуется управленческое внимание."
    ]
    assert len(rendered_items) == len(provider.inputs[0].actions) == 82
    assert any("Grounding digest:" in str(element.value) for element in app.caption)

    app.run(timeout=30)
    assert len(brief_service.calls) == 1
    assert len(provider.inputs) == 1
    assert any(element.value == "Точная управленческая сводка." for element in app.markdown)

    app.text_input[0].set_value("1233.00").run(timeout=30)
    assert len(brief_service.calls) == 1
    assert len(provider.inputs) == 1
    assert any(element.value == "Точная управленческая сводка." for element in app.markdown)


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (BriefGenerationStatus.UNAVAILABLE, "временно недоступен"),
        (BriefGenerationStatus.INVALID, "не прошёл проверку grounding"),
    ],
)
def test_non_generated_ai_states_are_safe_and_dashboard_survives(
    monkeypatch: pytest.MonkeyPatch,
    status: BriefGenerationStatus,
    message: str,
) -> None:
    services = _base_services()
    brief_service = StaticBriefService(BriefGenerationResult(status, None))
    _install(monkeypatch, replace(services, briefs=brief_service))
    app = AppTest.from_file(ENTRYPOINT).run(timeout=30)

    _button(app, "Сформировать AI Daily Brief").click().run(timeout=30)

    assert not app.exception
    assert len(brief_service.calls) == 1
    assert any(message in str(element.value) for element in app.warning)
    assert any(metric.label.endswith("SKU") and metric.value == "37" for metric in app.metric)
    visible = tuple(app.error) + tuple(app.warning) + tuple(app.info) + tuple(app.markdown) + tuple(app.caption)
    assert not any("provider exception" in str(element.value) for element in visible)


def test_disabled_ai_has_neutral_state_and_zero_generation_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _base_services()
    brief_service = CountingBriefService(services.briefs, enabled=False)
    _install(monkeypatch, replace(services, briefs=brief_service))

    app = AppTest.from_file(ENTRYPOINT).run(timeout=30)

    assert not app.exception
    assert len(brief_service.calls) == 0
    assert _button(app, "Сформировать AI Daily Brief").disabled
    assert any("отключён в конфигурации" in str(element.value) for element in app.info)


def test_stale_brief_is_not_shown_for_changed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _base_services(STAMP)
    first_briefs = CountingBriefService(
        BriefService(QualitativeProvider(), enabled=True),
        enabled=True,
    )
    current = {"application": replace(first, briefs=first_briefs)}
    monkeypatch.setattr(
        bootstrap_module,
        "build_application",
        lambda *args, **kwargs: current["application"],
    )
    app = AppTest.from_file(ENTRYPOINT).run(timeout=30)
    _button(app, "Сформировать AI Daily Brief").click().run(timeout=30)
    assert any(element.value == "Точная управленческая сводка." for element in app.markdown)

    second = _base_services(datetime(2026, 9, 7, 10, tzinfo=UTC))
    second_briefs = CountingBriefService(
        BriefService(QualitativeProvider(), enabled=True),
        enabled=True,
    )
    current["application"] = replace(second, briefs=second_briefs)
    app.run(timeout=30)

    assert len(second_briefs.calls) == 0
    assert not any(element.value == "Точная управленческая сводка." for element in app.markdown)


def test_brief_cache_uses_grounded_content_when_snapshots_share_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _base_services(STAMP)
    first_snapshot = services.analysis.analyze((SkuId("DEMO-005"),))
    second_snapshot = services.analysis.analyze((SkuId("DEMO-006"),))
    first_provider = QualitativeProvider("Сводка для первого снимка.")
    second_provider = QualitativeProvider("Сводка для второго снимка.")
    first_briefs = CountingBriefService(
        BriefService(first_provider, enabled=True),
        enabled=True,
    )
    second_briefs = CountingBriefService(
        BriefService(second_provider, enabled=True),
        enabled=True,
    )
    current = {
        "application": replace(
            services,
            analysis=StaticAnalysisService(first_snapshot),
            briefs=first_briefs,
        )
    }
    monkeypatch.setattr(
        bootstrap_module,
        "build_application",
        lambda *args, **kwargs: current["application"],
    )

    first_digest = first_briefs.grounding_digest(first_snapshot)
    second_digest = second_briefs.grounding_digest(second_snapshot)
    assert first_snapshot.analysis_timestamp == second_snapshot.analysis_timestamp
    assert first_digest != second_digest
    first_briefs.identity_calls.clear()
    second_briefs.identity_calls.clear()

    app = AppTest.from_file(ENTRYPOINT).run(timeout=30)
    assert len(first_briefs.calls) == 0
    assert first_provider.inputs == []
    _button(app, "Сформировать AI Daily Brief").click().run(timeout=30)
    assert len(first_briefs.calls) == 1
    assert len(first_provider.inputs) == 1
    assert any(
        element.value == "Сводка для первого снимка." for element in app.markdown
    )

    equivalent_snapshot = replace(first_snapshot, provenance=())
    equivalent_provider = QualitativeProvider("Не должна генерироваться автоматически.")
    equivalent_briefs = CountingBriefService(
        BriefService(equivalent_provider, enabled=True),
        enabled=True,
    )
    assert equivalent_snapshot != first_snapshot
    assert (
        equivalent_briefs.grounding_digest(equivalent_snapshot)
        == first_digest
    )
    equivalent_briefs.identity_calls.clear()
    current["application"] = replace(
        services,
        analysis=StaticAnalysisService(equivalent_snapshot),
        briefs=equivalent_briefs,
    )
    app.run(timeout=30)

    assert len(equivalent_briefs.calls) == 0
    assert equivalent_provider.inputs == []
    assert any(
        element.value == "Сводка для первого снимка." for element in app.markdown
    )

    current["application"] = replace(
        services,
        analysis=StaticAnalysisService(second_snapshot),
        briefs=second_briefs,
    )
    app.run(timeout=30)

    assert len(first_briefs.calls) == 1
    assert len(second_briefs.calls) == 0
    assert second_provider.inputs == []
    assert not any(
        element.value == "Сводка для первого снимка." for element in app.markdown
    )
    assert not any(
        element.value == "Сводка для второго снимка." for element in app.markdown
    )

    _button(app, "Сформировать AI Daily Brief").click().run(timeout=30)

    assert len(second_briefs.calls) == 1
    assert len(second_provider.inputs) == 1
    assert any(
        element.value == "Сводка для второго снимка." for element in app.markdown
    )

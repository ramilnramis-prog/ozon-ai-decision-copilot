"""Focused behavioral tests for Streamlit-native rendering components."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.bootstrap import build_application
from app.core.clock import FixedClock
from app.core.config import AppConfig
from app.domain.common import AvailabilityStatus, Severity, Urgency
from app.domain.recommendations import RecommendationCategory, RecommendationStatus
from app.ui.components import (
    DEMO_DISCLOSURE,
    MISSING_VALUE,
    ZERO_ACTION_MESSAGE,
    render_dashboard,
    render_economics_view,
    render_inventory_view,
    render_priority_actions,
    render_sku_detail,
)
from app.ui.presenters import (
    ActionView,
    DashboardView,
    EvidenceView,
    IssueView,
    SummaryView,
    build_dashboard_view,
)


class Recorder:
    def __init__(self, selected_sku: str | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.sidebar = SidebarRecorder(self.calls)
        self.selected_sku = selected_sku

    def _record(self, name: str, value: object = None, **kwargs) -> None:
        self.calls.append((name, (value, kwargs)))

    def title(self, value, **kwargs):
        self._record("title", value, **kwargs)

    def caption(self, value, **kwargs):
        self._record("caption", value, **kwargs)

    def subheader(self, value, **kwargs):
        self._record("subheader", value, **kwargs)

    def info(self, value, **kwargs):
        self._record("info", value, **kwargs)

    def warning(self, value, **kwargs):
        self._record("warning", value, **kwargs)

    def error(self, value, **kwargs):
        self._record("error", value, **kwargs)

    def write(self, value, **kwargs):
        self._record("write", value, **kwargs)

    def markdown(self, value, **kwargs):
        self._record("markdown", value, **kwargs)

    def metric(self, label, value, **kwargs):
        self.calls.append(("metric", (label, value, kwargs)))

    def columns(self, count):
        return tuple(self for _ in range(count))

    def container(self, **kwargs):
        self._record("container", kwargs)
        return nullcontext()

    def expander(self, label, **kwargs):
        self._record("expander", label, **kwargs)
        return nullcontext()

    def tabs(self, labels):
        self._record("tabs", tuple(labels))
        return tuple(nullcontext() for _ in labels)

    def dataframe(self, data, **kwargs):
        self._record("dataframe", tuple(data), **kwargs)

    def selectbox(self, label, options, **kwargs):
        normalized = tuple(options)
        self._record("selectbox", (label, normalized), **kwargs)
        if self.selected_sku is not None:
            return self.selected_sku
        return normalized[0]


class SidebarRecorder:
    def __init__(self, calls) -> None:
        self.calls = calls

    def subheader(self, value, **kwargs):
        self.calls.append(("sidebar.subheader", (value, kwargs)))

    def caption(self, value, **kwargs):
        self.calls.append(("sidebar.caption", (value, kwargs)))


@pytest.fixture(scope="module")
def default_view():
    services = build_application(
        AppConfig(ai_enabled=False),
        clock=FixedClock(datetime(2026, 9, 7, 9, tzinfo=UTC)),
        environ={},
    )
    return build_dashboard_view(services.analysis.analyze(None))


def _summary(action_count: int) -> SummaryView:
    return SummaryView(
        analyzed_sku_count=1,
        priority_action_count=action_count,
        critical_count=action_count,
        high_count=0,
        analysis_timestamp=datetime(2026, 9, 7, 9, tzinfo=UTC),
        as_of_date=date(2026, 9, 7),
    )


def _action(rank: int, recommendation_id: str) -> ActionView:
    fact_id = f"fact:{recommendation_id}"
    return ActionView(
        rank=rank,
        sku="DEMO-005",
        product_name="Demo Product",
        severity=Severity.CRITICAL,
        urgency=Urgency.IMMEDIATE,
        availability=AvailabilityStatus.AVAILABLE,
        category=RecommendationCategory.INVENTORY,
        recommendation_status=RecommendationStatus.PROPOSED,
        rule_id="inventory.out_of_stock",
        recommendation_id=recommendation_id,
        proposed_action="Review replenishment.",
        explanation="Sellable stock is zero.",
        evidence_refs=(fact_id,),
        evidence=(
            EvidenceView(
                fact_id=fact_id,
                name="Sellable stock",
                value="0",
                unit="unit",
                period=None,
                formula_or_rule_id="inventory.out_of_stock",
                source_refs=("inventory:DEMO-005",),
            ),
        ),
    )


def test_zero_action_state_is_neutral_and_does_not_claim_health() -> None:
    recorder = Recorder()

    render_priority_actions(recorder, ())

    messages = tuple(str(value[0]) for _, value in recorder.calls)
    assert ZERO_ACTION_MESSAGE in messages
    assert not any("healthy" in message.lower() for message in messages)
    assert not any("здоров" in message.lower() for message in messages)


def test_action_renderer_preserves_input_order_and_displays_evidence() -> None:
    recorder = Recorder()
    actions = (_action(2, "rec-b"), _action(1, "rec-a"))

    render_priority_actions(recorder, actions)

    headings = [
        value[0]
        for name, value in recorder.calls
        if name == "markdown" and str(value[0]).startswith("####")
    ]
    recommendation_labels = [
        value[0]
        for name, value in recorder.calls
        if name == "caption" and str(value[0]).startswith("Recommendation ID:")
    ]
    assert headings == [
        "#### #2 · DEMO-005 · Demo Product",
        "#### #1 · DEMO-005 · Demo Product",
    ]
    assert recommendation_labels == [
        "Recommendation ID: rec-b",
        "Recommendation ID: rec-a",
    ]
    assert any(
        name == "write" and value[0] == "Sellable stock: 0 unit"
        for name, value in recorder.calls
    )


def test_action_evidence_formats_numeric_text_without_changing_identity() -> None:
    recorder = Recorder()
    source = _action(1, "rec-precision")
    evidence = EvidenceView(
        fact_id="fact:rec-precision:ads",
        name="average daily sales",
        value="18.14285714285714285714285714",
        unit="units_per_day",
        period=None,
        formula_or_rule_id="inventory.out_of_stock",
        source_refs=("sales:DEMO-005",),
    )
    action = replace(
        source,
        evidence_refs=(evidence.fact_id,),
        evidence=(evidence,),
    )

    render_priority_actions(recorder, (action,))

    assert any(
        name == "write" and value[0] == "average daily sales: 18.1 units_per_day"
        for name, value in recorder.calls
    )
    assert any(
        name == "caption" and value[0] == "Fact ID: fact:rec-precision:ads"
        for name, value in recorder.calls
    )
    assert evidence.value == "18.14285714285714285714285714"
    assert evidence.source_refs == ("sales:DEMO-005",)


def test_full_dashboard_shows_demo_disclosure_counts_and_timestamp() -> None:
    recorder = Recorder()
    action = _action(1, "rec-a")
    view = DashboardView(summary=_summary(1), actions=(action,), issues=())

    render_dashboard(recorder, view)

    assert any(
        name == "info" and value[0] == DEMO_DISCLOSURE
        for name, value in recorder.calls
    )
    assert ("metric", ("Проанализировано SKU", 1, {})) in recorder.calls
    assert any(
        name == "sidebar.caption" and "2026-09-07 09:00:00+00:00" in value[0]
        for name, value in recorder.calls
    )


def test_nonempty_service_issue_renders_separately_from_action_cards() -> None:
    recorder = Recorder()
    action = _action(1, "rec-a")
    issue = IssueView(
        code="provider.demo_field_unavailable",
        message="A known demo source field is unavailable.",
        severity=Severity.WARNING,
        scope="demo source",
        sku="DEMO-037",
        field_name="sales_history",
    )
    view = DashboardView(summary=_summary(1), actions=(action,), issues=(issue,))

    render_dashboard(recorder, view)

    action_headings = [
        value[0]
        for name, value in recorder.calls
        if name == "markdown" and str(value[0]).startswith("####")
    ]
    expanders = [
        value[0] for name, value in recorder.calls if name == "expander"
    ]
    assert action_headings == ["#### #1 · DEMO-005 · Demo Product"]
    assert "Доказательства (1)" in expanders
    assert "Проблемы качества данных (1)" in expanders
    assert any(
        name == "warning" and value[0] == issue.message
        for name, value in recorder.calls
    )
    assert not any(issue.code in heading for heading in action_headings)


def _dataframes(recorder: Recorder) -> tuple[tuple[dict[str, object], ...], ...]:
    return tuple(
        value[0]
        for name, value in recorder.calls
        if name == "dataframe"
    )


def test_dashboard_renders_native_navigation_and_all_detailed_sections(
    default_view,
) -> None:
    recorder = Recorder()

    render_dashboard(recorder, default_view)

    tabs = next(value[0] for name, value in recorder.calls if name == "tabs")
    subheaders = tuple(
        value[0] for name, value in recorder.calls if name == "subheader"
    )
    assert tabs == (
        "Обзор / Priority Actions",
        "Запасы",
        "Юнит-экономика",
        "Детали SKU",
    )
    assert "Запасы и пополнение" in subheaders
    assert "Юнит-экономика" in subheaders
    assert "Детали SKU" in subheaders
    selector = next(value for name, value in recorder.calls if name == "selectbox")
    assert selector[0][0] == "Выберите SKU"
    assert selector[0][1] == tuple(detail.sku for detail in default_view.sku_details)
    assert selector[1]["key"] == "sku-detail-selector"


def test_inventory_component_keeps_confirmed_and_unconfirmed_inbound_separate(
    default_view,
) -> None:
    recorder = Recorder()

    render_inventory_view(recorder, default_view.inventory_rows)

    rows = _dataframes(recorder)[0]
    confirmed = next(row for row in rows if row["SKU"] == "DEMO-004")
    unconfirmed = next(row for row in rows if row["SKU"] == "DEMO-006")
    assert "100 шт." in confirmed["Подтвержденные поставки"]
    assert confirmed["Неподтвержденные поставки"] == MISSING_VALUE
    assert unconfirmed["Подтвержденные поставки"] == MISSING_VALUE
    assert "100 шт." in unconfirmed["Неподтвержденные поставки"]
    assert confirmed["Eligible confirmed inbound, шт."] == "100"
    assert unconfirmed["Eligible confirmed inbound, шт."] == "0"


def test_inventory_component_formats_ads_coverage_and_integer_quantities(
    default_view,
) -> None:
    recorder = Recorder()

    render_inventory_view(recorder, default_view.inventory_rows)

    rows = _dataframes(recorder)[0]
    healthy = next(row for row in rows if row["SKU"] == "DEMO-001")
    assert healthy["ADS, шт./день"] == "8"
    assert healthy["Покрытие, дни"] == "62.5"
    assert healthy["Остаток, шт."] == "500"

    repeating = next(row for row in rows if row["SKU"] == "DEMO-003")
    assert repeating["ADS, шт./день"] == "18.1"


def test_economics_component_formats_display_values_and_missing_as_missing(
    default_view,
) -> None:
    recorder = Recorder()

    render_economics_view(recorder, default_view.economics_rows)

    rows = _dataframes(recorder)[0]
    loss = next(row for row in rows if row["SKU"] == "DEMO-015")
    impossible = next(row for row in rows if row["SKU"] == "DEMO-018")
    missing = next(row for row in rows if row["SKU"] == "DEMO-021")
    healthy = next(row for row in rows if row["SKU"] == "DEMO-001")
    assert healthy["Комиссия"] == "232.2 RUB"
    assert healthy["Реклама"] == "154.8 RUB"
    assert healthy["Прибыль на единицу"] == "308 RUB"
    assert healthy["Маржа, доля"] == "0.239"
    assert loss["Прибыль на единицу"] == "-649.7 RUB"
    assert loss["Маржа, доля"] == "-0.197"
    assert impossible["Safe-price status"] == "Не применимо"
    assert impossible["Минимальная безопасная цена"] == MISSING_VALUE
    assert missing["COGS"] == MISSING_VALUE
    assert missing["Прибыль на единицу"] == MISSING_VALUE
    assert missing["Маржа, доля"] == MISSING_VALUE
    assert missing["Минимальная безопасная цена"] == MISSING_VALUE


def test_component_formatting_does_not_mutate_presented_authoritative_values(
    default_view,
) -> None:
    inventory = next(row for row in default_view.inventory_rows if row.sku == "DEMO-003")
    economics = next(row for row in default_view.economics_rows if row.sku == "DEMO-001")
    before = (
        inventory.average_daily_sales,
        inventory.average_daily_sales.as_tuple(),
        economics.commission_cost,
        economics.commission_cost.as_tuple(),
        economics.profit_per_unit,
        economics.profit_per_unit.as_tuple(),
    )

    render_inventory_view(Recorder(), default_view.inventory_rows)
    render_economics_view(Recorder(), default_view.economics_rows)

    after = (
        inventory.average_daily_sales,
        inventory.average_daily_sales.as_tuple(),
        economics.commission_cost,
        economics.commission_cost.as_tuple(),
        economics.profit_per_unit,
        economics.profit_per_unit.as_tuple(),
    )
    assert after == before
    assert inventory.average_daily_sales == Decimal(
        "18.14285714285714285714285714"
    )
    assert economics.profit_per_unit == Decimal("308.0000")


def test_sku_detail_filters_actions_without_reranking_and_shows_evidence(
    default_view,
) -> None:
    recorder = Recorder(selected_sku="DEMO-006")
    detail = next(item for item in default_view.sku_details if item.sku == "DEMO-006")

    render_sku_detail(recorder, default_view.sku_details)

    frames = _dataframes(recorder)
    action_rows = next(frame for frame in frames if frame and "Правило" in frame[0])
    evidence_rows = next(frame for frame in frames if frame and "Fact ID" in frame[0])
    assert tuple(row["Правило"] for row in action_rows) == tuple(
        action.rule_id for action in detail.actions
    )
    assert tuple(row["Глобальный ранг"] for row in action_rows) == tuple(
        str(action.rank) for action in detail.actions
    )
    assert tuple(row["Fact ID"] for row in evidence_rows) == tuple(
        fact.fact_id for action in detail.actions for fact in action.evidence
    )
    assert tuple(row["Формула/правило факта"] for row in evidence_rows) == tuple(
        fact.formula_or_rule_id
        for action in detail.actions
        for fact in action.evidence
    )


def test_partial_sku_detail_renders_explicit_missing_values(default_view) -> None:
    recorder = Recorder(selected_sku="DEMO-037")

    render_sku_detail(recorder, default_view.sku_details)

    frames = _dataframes(recorder)
    sales = next(frame for frame in frames if frame and "Статус продаж" in frame[0])
    inventory = next(
        frame
        for frame in frames
        if frame and frame[0].get("SKU") == "DEMO-037" and "Остаток, шт." in frame[0]
    )
    assert sales[0]["ADS, шт./день"] == MISSING_VALUE
    assert inventory[0]["Дата выбытия"] == MISSING_VALUE
    assert inventory[0]["Последний безопасный старт"] == MISSING_VALUE
    assert inventory[0]["Рекомендованное пополнение, шт."] == MISSING_VALUE

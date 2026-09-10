"""Offline integration coverage for the MVP-016 Streamlit entrypoint."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import app.bootstrap as bootstrap_module
import streamlit_app


def test_default_dashboard_uses_active_demo_scope_without_ai_or_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OZON_COPILOT_AI_ENABLED", "false")

    def forbidden_openai(*args, **kwargs):
        raise AssertionError("default Streamlit path must not construct OpenAI")

    monkeypatch.setattr(bootstrap_module, "OpenAIBriefProvider", forbidden_openai)

    view = streamlit_app.prepare_default_dashboard()

    assert view.summary.analyzed_sku_count == 37
    assert view.summary.priority_action_count == len(view.actions)
    assert view.actions


def test_entrypoint_calls_only_bootstrap_analysis_and_presenter_for_business_data() -> None:
    source = Path("streamlit_app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert "app.bootstrap" in imports
    assert "app.services.analysis" not in imports
    assert not any(
        name.startswith(("app.providers", "app.analytics", "app.decisions", "app.ai"))
        for name in imports
    )
    assert source.count(".analyze(None)") == 1
    assert "OPENAI_API_KEY" not in source
    assert "except Exception" not in source
    assert "unsafe_allow_html" not in source
    assert "app.services.pricing" not in imports
    assert "app.services.briefs" not in imports
    assert "build_grounded_brief_input" not in source
    assert "calculate_unit_economics" not in source


def test_streamlit_detailed_views_and_sku_selection_use_one_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OZON_COPILOT_AI_ENABLED", "false")

    def forbidden_openai(*args, **kwargs):
        raise AssertionError("MVP-017 must not construct OpenAI")

    monkeypatch.setattr(bootstrap_module, "OpenAIBriefProvider", forbidden_openai)

    entrypoint = Path(__file__).resolve().parents[2] / "streamlit_app.py"
    expected = streamlit_app.prepare_default_dashboard()
    app = AppTest.from_file(entrypoint).run(timeout=30)

    assert not app.exception
    legacy_mvp017_labels = (
        "Обзор / Priority Actions",
        "Запасы",
        "Юнит-экономика",
        "Детали SKU",
    )
    tab_labels = tuple(tab.label for tab in app.tabs)
    assert len(legacy_mvp017_labels) == 4
    assert tab_labels == (
        "Overview / Priority Actions",
        "Запасы",
        "Юнит-экономика",
        "Детали SKU",
        "Price Simulator",
        "AI Daily Brief",
    )
    assert {element.value for element in app.subheader}.issuperset(
        {"Запасы и пополнение", "Юнит-экономика", "Детали SKU"}
    )
    assert len(app.selectbox) == 2
    selector = app.selectbox[0]
    assert selector.value == expected.sku_details[0].sku == "DEMO-001"
    assert tuple(selector.options) == tuple(
        detail.selector_label for detail in expected.sku_details
    )
    assert len(selector.options) == 37
    assert any(option.startswith("DEMO-005 — ") for option in selector.options)
    assert any(option.startswith("DEMO-037 — ") for option in selector.options)
    assert len(app.dataframe[0].value) == len(expected.inventory_rows) == 37
    assert len(app.dataframe[1].value) == len(expected.economics_rows) == 37
    assert len(app.number_input) == 0
    assert len(app.text_input) == 1
    assert any("Daily Brief" in element.value for element in app.subheader)

    app.selectbox[0].set_value("DEMO-006").run(timeout=30)

    assert not app.exception
    assert app.selectbox[0].value == "DEMO-006"
    assert any(element.value == "**SKU:** DEMO-006" for element in app.markdown)
    action_table = next(
        element.value
        for element in app.dataframe
        if "Правило" in element.value.columns
    )
    expected_detail = next(
        detail for detail in expected.sku_details if detail.sku == "DEMO-006"
    )
    assert tuple(action_table["Правило"]) == tuple(
        action.rule_id for action in expected_detail.actions
    )
    assert tuple(action_table["Глобальный ранг"].astype(str)) == tuple(
        str(action.rank) for action in expected_detail.actions
    )


def test_streamlit_app_runs_offline_without_an_openai_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OZON_COPILOT_AI_ENABLED", "false")

    def forbidden_openai(*args, **kwargs):
        raise AssertionError("Streamlit AppTest must not construct OpenAI")

    monkeypatch.setattr(bootstrap_module, "OpenAIBriefProvider", forbidden_openai)

    entrypoint = Path(__file__).resolve().parents[2] / "streamlit_app.py"
    expected = streamlit_app.prepare_default_dashboard()
    app = AppTest.from_file(entrypoint).run(timeout=30)

    assert not app.exception
    assert app.title[0].value == "Ozon AI Decision Copilot"
    assert any(
        "Демо-данные" in element.value and "только чтение" in element.value
        for element in app.info
    )
    assert any(
        element.label == "Проанализировано SKU" and element.value == "37"
        for element in app.metric
    )
    assert any(
        element.label == "Приоритетные действия" and element.value == "82"
        for element in app.metric
    )
    assert any(
        element.value == "Что требует внимания сегодня?"
        for element in app.subheader
    )
    action_headings = tuple(
        element.value
        for element in app.markdown
        if str(element.value).startswith("#### #")
    )
    assert len(expected.actions) == 82
    assert len(action_headings) == len(expected.actions)
    assert len(app.expander) == len(expected.actions)
    assert action_headings[0].startswith(
        f"#### #{expected.actions[0].rank} · {expected.actions[0].sku}"
    )
    assert action_headings[-1].startswith(
        f"#### #{expected.actions[-1].rank} · {expected.actions[-1].sku}"
    )

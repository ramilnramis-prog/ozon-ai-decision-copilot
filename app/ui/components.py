"""Streamlit-native rendering for calculation-free dashboard views."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from collections.abc import Callable
from typing import Any

from app.core.errors import AppError
from app.domain.briefs import BriefGenerationStatus
from app.domain.common import Severity
from app.ui.presenters import (
    ActionView,
    BriefStatusView,
    DashboardView,
    DailyBriefView,
    EconomicsRow,
    InventoryRow,
    IssueView,
    PriceBoundaryView,
    PricingAnalysisView,
    PricingEconomicsView,
    PricingScenarioView,
    SalesDetailView,
    SkuDetailView,
    SummaryView,
    format_ads,
    format_coverage_days,
    format_evidence_value,
    format_money,
    format_quantity,
    format_ratio,
)


PRODUCT_TITLE = "Ozon AI Decision Copilot"
PRICING_DISCLAIMER = (
    "Сценарий пересчитывает только юнит-экономику на единицу товара и не "
    "прогнозирует спрос или продажи. Цена на маркетплейсе не изменяется."
)
AI_GENERATED_LABEL = "AI-generated explanation · проверено детерминированным grounding"
AI_DISABLED_MESSAGE = "AI Daily Brief отключён в конфигурации."
AI_UNAVAILABLE_MESSAGE = (
    "AI Daily Brief временно недоступен. Детерминированный анализ продолжает работать."
)
AI_INVALID_MESSAGE = "Ответ AI не прошёл проверку grounding и не был показан."

_PRICING_RESULT_KEY = "ozon_copilot.pricing_result"
_PRICING_IDENTITY_KEY = "ozon_copilot.pricing_identity"
_BRIEF_RESULT_KEY = "ozon_copilot.brief_result"
_BRIEF_IDENTITY_KEY = "ozon_copilot.brief_identity"
PRODUCT_SUBTITLE = (
    "Операционная поддержка решений для маркетплейса: продажи, запасы, "
    "экономика и приоритетные действия."
)
DEMO_DISCLOSURE = "Демо-данные · только чтение · автоматические действия отключены"
ZERO_ACTION_MESSAGE = "Нет приоритетных действий для текущего объёма анализа."


MISSING_VALUE = "Нет данных"
NO_SKU_ACTIONS_MESSAGE = (
    "Для выбранного SKU нет приоритетных действий. "
    "Это не означает отсутствие риска при неполных данных."
)


_STATUS_LABELS = {
    "available": "Доступно",
    "insufficient_data": "Недостаточно данных",
    "not_applicable": "Не применимо",
    "invalid": "Некорректно",
    "safe": "Безопасно",
    "unsafe": "Небезопасно",
    "unavailable": "Недоступно",
    "future": "В будущем",
    "due_now": "Требуется сейчас",
    "already_late": "Уже поздно",
}


def _display(value: object | None) -> str:
    if value is None:
        return MISSING_VALUE
    if isinstance(value, Decimal):
        return format_quantity(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    raw = getattr(value, "value", value)
    return _STATUS_LABELS.get(str(raw), str(raw))


def _money(value: Decimal | None, currency: object | None) -> str:
    if value is None:
        return MISSING_VALUE
    currency_value = getattr(currency, "value", currency)
    suffix = "" if currency_value is None else f" {currency_value}"
    return f"{format_money(value)}{suffix}"


def _joined(values: tuple[str, ...]) -> str:
    return MISSING_VALUE if not values else "; ".join(values)


def _boundary_status(boundary: PriceBoundaryView | None) -> str:
    return MISSING_VALUE if boundary is None else _display(boundary.status)


def _boundary_price(
    boundary: PriceBoundaryView | None,
    currency: object | None,
) -> str:
    return MISSING_VALUE if boundary is None else _money(boundary.price, currency)


def _inbound_events(row: InventoryRow, source_status: str) -> str:
    values = tuple(
        " · ".join(
            (
                f"{format_quantity(event.quantity)} шт.",
                _display(event.expected_arrival),
                _display(event.treatment),
            )
        )
        for event in row.inbound_events
        if event.source_status.value == source_status
    )
    return _joined(values)


def _inventory_records(rows: tuple[InventoryRow, ...]) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "SKU": row.sku,
            "Товар": _display(row.product_name),
            "Статус анализа": _display(row.analysis_status),
            "Статус запасов": _display(row.inventory_status),
            "Остаток, шт.": (
                MISSING_VALUE
                if row.sellable_stock is None
                else format_quantity(row.sellable_stock)
            ),
            "ADS, шт./день": (
                MISSING_VALUE
                if row.average_daily_sales is None
                else format_ads(row.average_daily_sales)
            ),
            "Покрытие, дни": (
                MISSING_VALUE
                if row.stock_coverage_days is None
                else format_coverage_days(row.stock_coverage_days)
            ),
            "Дата выбытия": _display(row.stockout_date),
            "Производство, дни": _display(row.production_days),
            "Доставка, дни": _display(row.delivery_days),
            "Буфер, дни": _display(row.safety_buffer_days),
            "Supply lead, дни": _display(row.supply_lead_days),
            "Горизонт покрытия, дни": _display(row.required_coverage_horizon_days),
            "Lead time status": _display(row.lead_time_status),
            "Последний безопасный старт": _display(row.latest_safe_start_date),
            "Статус срока пополнения": _display(row.replenishment_timing),
            "Плановая дата прихода": _display(row.planned_replenishment_arrival_date),
            "Физический остаток к приходу": (
                MISSING_VALUE
                if row.projected_stock_at_replenishment_arrival is None
                else format_quantity(row.projected_stock_at_replenishment_arrival)
            ),
            "Reorder point, шт.": (
                MISSING_VALUE
                if row.reorder_point_units is None
                else format_quantity(row.reorder_point_units)
            ),
            "Целевой запас, шт.": (
                MISSING_VALUE
                if row.target_stock_units is None
                else format_quantity(row.target_stock_units)
            ),
            "Базовое пополнение, шт.": (
                MISSING_VALUE
                if row.base_replenishment_quantity is None
                else format_quantity(row.base_replenishment_quantity)
            ),
            "Рекомендованное пополнение, шт.": (
                MISSING_VALUE
                if row.recommended_replenishment_quantity is None
                else format_quantity(row.recommended_replenishment_quantity)
            ),
            "Eligible confirmed inbound, шт.": (
                MISSING_VALUE
                if row.eligible_confirmed_inbound_units is None
                else format_quantity(row.eligible_confirmed_inbound_units)
            ),
            "Подтвержденные поставки": _inbound_events(row, "confirmed"),
            "Неподтвержденные поставки": _inbound_events(row, "unconfirmed"),
            "Правила риска": _joined(row.action_rules),
            "Причины": _joined(row.action_reasons),
        }
        for row in rows
    )


def _other_costs(row: EconomicsRow) -> str:
    return _joined(
        tuple(
            f"{component.name}: {_money(component.amount, row.currency)}"
            for component in row.other_variable_costs
        )
    )


def _economics_records(rows: tuple[EconomicsRow, ...]) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "SKU": row.sku,
            "Товар": _display(row.product_name),
            "Статус анализа": _display(row.analysis_status),
            "Статус экономики": _display(row.economics_status),
            "Безопасность цены": _display(row.safety_status),
            "Цена": _money(row.selling_price, row.currency),
            "COGS": _money(row.cost_of_goods, row.currency),
            "Логистика": _money(row.logistics_cost, row.currency),
            "Комиссия": _money(row.commission_cost, row.currency),
            "Реклама": _money(row.advertising_cost, row.currency),
            "DRR status": _display(row.drr_status),
            "DRR, доля": (
                MISSING_VALUE if row.drr is None else format_ratio(row.drr)
            ),
            "Прочие расходы": _other_costs(row),
            "Прочие расходы, итого": _money(
                row.other_variable_cost_total,
                row.currency,
            ),
            "Переменные расходы, итого": _money(
                row.total_variable_cost,
                row.currency,
            ),
            "Прибыль на единицу": _money(row.profit_per_unit, row.currency),
            "Маржа, доля": (
                MISSING_VALUE
                if row.contribution_margin is None
                else format_ratio(row.contribution_margin, decimal_places=3)
            ),
            "Break-even status": _boundary_status(row.break_even),
            "Break-even цена": _boundary_price(row.break_even, row.currency),
            "Minimum-profit status": _boundary_status(row.minimum_profit),
            "Minimum-profit цена": _boundary_price(row.minimum_profit, row.currency),
            "Minimum-margin status": _boundary_status(row.minimum_margin),
            "Minimum-margin цена": _boundary_price(row.minimum_margin, row.currency),
            "Safe-price status": _boundary_status(row.minimum_safe),
            "Минимальная безопасная цена": _boundary_price(
                row.minimum_safe,
                row.currency,
            ),
            "Правила рекомендаций": _joined(row.action_rules),
            "Причины": _joined(row.action_reasons),
        }
        for row in rows
    )


def _timestamp_text(summary: SummaryView) -> str:
    return summary.analysis_timestamp.isoformat(sep=" ")


def render_header(st: Any) -> None:
    """Render stable product identity and truthful demo disclosure."""

    st.title(PRODUCT_TITLE)
    st.caption(PRODUCT_SUBTITLE)
    st.info(DEMO_DISCLOSURE)


def render_sidebar(st: Any, summary: SummaryView) -> None:
    """Render minimal context using the authoritative snapshot timestamp."""

    st.sidebar.subheader(PRODUCT_TITLE)
    st.sidebar.caption("Режим данных: Демо")
    st.sidebar.caption("Режим работы: только чтение")
    st.sidebar.caption(f"Дата расчёта: {summary.as_of_date.isoformat()}")
    st.sidebar.caption(f"Время анализа: {_timestamp_text(summary)}")


def render_summary(st: Any, summary: SummaryView) -> None:
    """Render direct snapshot counts without a derived health score."""

    st.subheader("Состояние анализируемого бизнеса")
    sku_column, action_column, critical_column, high_column = st.columns(4)
    sku_column.metric("Проанализировано SKU", summary.analyzed_sku_count)
    action_column.metric("Приоритетные действия", summary.priority_action_count)
    critical_column.metric("CRITICAL", summary.critical_count)
    high_column.metric("HIGH", summary.high_count)
    st.caption(f"Анализ выполнен: {_timestamp_text(summary)}")


def _render_severity(st: Any, action: ActionView) -> None:
    label = (
        f"Severity: {action.severity.value.upper()} · "
        f"Urgency: {action.urgency.value.upper()}"
    )
    if action.severity is Severity.CRITICAL:
        st.error(label)
    elif action.severity is Severity.HIGH:
        st.warning(label)
    elif action.severity is Severity.WARNING:
        st.warning(label, icon="⚠️")
    else:
        st.info(label)


def _render_evidence(st: Any, action: ActionView) -> None:
    with st.expander(f"Доказательства ({len(action.evidence)})"):
        for fact in action.evidence:
            value = f"{format_evidence_value(fact)} {fact.unit}"
            st.write(f"{fact.name}: {value}")
            st.caption(f"Fact ID: {fact.fact_id}")
            st.caption(f"Формула/правило: {fact.formula_or_rule_id}")
            if fact.period is not None:
                st.caption(f"Период: {fact.period}")
            st.caption(f"Источники: {', '.join(fact.source_refs)}")


def render_priority_actions(st: Any, actions: tuple[ActionView, ...]) -> None:
    """Render every action in the exact order supplied by the presenter."""

    st.subheader("Что требует внимания сегодня?")
    st.caption("Priority Action Center · порядок определён детерминированным ядром")
    if not actions:
        st.info(ZERO_ACTION_MESSAGE)
        return

    for action in actions:
        rank = "—" if action.rank is None else str(action.rank)
        identity = action.sku or "Без SKU"
        if action.product_name is not None:
            identity = f"{identity} · {action.product_name}"
        with st.container(border=True):
            st.markdown(f"#### #{rank} · {identity}")
            _render_severity(st, action)
            if action.proposed_action is not None:
                st.write(f"**Действие:** {action.proposed_action}")
            st.write(f"**Почему:** {action.explanation}")
            st.caption(
                f"Категория: {action.category.value} · Правило: {action.rule_id}"
            )
            st.caption(f"Recommendation ID: {action.recommendation_id}")
            _render_evidence(st, action)


def _issue_label(issue: IssueView) -> str:
    sku = "" if issue.sku is None else f" · SKU: {issue.sku}"
    field = "" if issue.field_name is None else f" · Поле: {issue.field_name}"
    return f"{issue.code}{sku}{field}"


def render_service_issues(st: Any, issues: tuple[IssueView, ...]) -> None:
    """Keep service/data issues visible and separate from priority actions."""

    if not issues:
        return
    with st.expander(f"Проблемы качества данных ({len(issues)})"):
        for issue in issues:
            st.warning(issue.message)
            st.caption(
                f"{_issue_label(issue)} · Severity: {issue.severity.value.upper()} "
                f"· Scope: {issue.scope}"
            )


def render_inventory_view(st: Any, rows: tuple[InventoryRow, ...]) -> None:
    """Render copied inventory facts without deriving dates, coverage, or quantity."""

    st.subheader("Запасы и пополнение")
    st.caption(
        "Порядок SKU соответствует снимку анализа. Подтвержденные и "
        "неподтвержденные поставки показаны отдельно."
    )
    if not rows:
        st.info("Нет SKU в текущем объёме анализа; состояние запасов не оценивалось.")
        return
    st.dataframe(
        _inventory_records(rows),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Eligible confirmed inbound — объём, уже учтённый детерминированной "
        "моделью пополнения. Физический остаток к приходу — отдельная проекция."
    )


def render_economics_view(st: Any, rows: tuple[EconomicsRow, ...]) -> None:
    """Render current contribution economics exactly as supplied by services."""

    st.subheader("Юнит-экономика")
    st.caption(
        "Текущие детерминированные значения. DRR и маржа показаны в канонической "
        "доле без пересчёта в проценты."
    )
    if not rows:
        st.info("Нет SKU в текущем объёме анализа; экономика не оценивалась.")
        return
    st.dataframe(
        _economics_records(rows),
        hide_index=True,
        width="stretch",
    )


def _sales_record(sales: SalesDetailView) -> dict[str, object]:
    return {
        "Статус продаж": _display(sales.status),
        "Статус сравнения": _display(sales.change_status),
        "Текущий период": (
            f"{sales.current_period_start.isoformat()} — "
            f"{sales.current_period_end.isoformat()}"
        ),
        "Дней ожидалось": sales.current_expected_days,
        "Дней наблюдалось": sales.current_observed_days,
        "Продано, шт.": sales.current_total_units,
        "ADS, шт./день": (
            MISSING_VALUE
            if sales.current_average_daily_sales is None
            else format_ads(sales.current_average_daily_sales)
        ),
        "Период сравнения": (
            f"{sales.comparison_period_start.isoformat()} — "
            f"{sales.comparison_period_end.isoformat()}"
        ),
        "Продано ранее, шт.": sales.comparison_total_units,
        "ADS ранее, шт./день": (
            MISSING_VALUE
            if sales.comparison_average_daily_sales is None
            else format_ads(sales.comparison_average_daily_sales)
        ),
        "Изменение, доля": (
            MISSING_VALUE
            if sales.relative_change is None
            else format_ratio(sales.relative_change)
        ),
        "Направление": _display(sales.direction),
    }


def _detail_action_records(
    actions: tuple[ActionView, ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "Глобальный ранг": _display(action.rank),
            "Severity": action.severity.value,
            "Urgency": action.urgency.value,
            "Правило": action.rule_id,
            "Действие": _display(action.proposed_action),
            "Почему": action.explanation,
            "Recommendation ID": action.recommendation_id,
            "Fact refs": ", ".join(action.evidence_refs),
        }
        for action in actions
    )


def _detail_evidence_records(
    actions: tuple[ActionView, ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "Recommendation ID": action.recommendation_id,
            "Fact ID": fact.fact_id,
            "Факт": fact.name,
            "Значение": f"{format_evidence_value(fact)} {fact.unit}",
            "Период": _display(fact.period),
            "Формула/правило факта": fact.formula_or_rule_id,
            "Source refs": ", ".join(fact.source_refs),
        }
        for action in actions
        for fact in action.evidence
    )


def _provenance_records(detail: SkuDetailView) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "Тип источника": item.source_type.value,
            "Провайдер": item.provider,
            "Время источника": _display(item.source_timestamp),
            "Время загрузки": _display(item.ingested_at),
        }
        for item in detail.provenance
    )


def render_sku_detail(st: Any, details: tuple[SkuDetailView, ...]) -> None:
    """Render one user-selected SKU from already-prepared snapshot details."""

    st.subheader("Детали SKU")
    if not details:
        st.info("Нет SKU для выбора; отсутствие данных не означает отсутствие риска.")
        return

    details_by_sku = {detail.sku: detail for detail in details}
    selected_sku = st.selectbox(
        "Выберите SKU",
        options=tuple(details_by_sku),
        format_func=lambda sku: details_by_sku[sku].selector_label,
        key="sku-detail-selector",
    )
    detail = details_by_sku[selected_sku]

    st.markdown("### Обзор SKU")
    st.write(f"**SKU:** {detail.sku}")
    st.write(f"**Товар:** {_display(detail.product_name)}")
    st.write(f"**Marketplace ID:** {_display(detail.marketplace_id)}")
    st.write(f"**Активен:** {_display(detail.active)}")
    st.write(f"**Статус анализа:** {_display(detail.analysis_status)}")

    st.markdown("### Продажи")
    if detail.sales is None:
        st.info("Расчётные метрики продаж недоступны.")
    else:
        st.dataframe(
            (_sales_record(detail.sales),),
            hide_index=True,
            width="stretch",
        )

    st.markdown("### Запасы выбранного SKU")
    st.dataframe(
        _inventory_records((detail.inventory,)),
        hide_index=True,
        width="stretch",
    )

    st.markdown("### Экономика выбранного SKU")
    st.dataframe(
        _economics_records((detail.economics,)),
        hide_index=True,
        width="stretch",
    )

    st.markdown("### Приоритетные действия выбранного SKU")
    if not detail.actions:
        st.info(NO_SKU_ACTIONS_MESSAGE)
    else:
        st.dataframe(
            _detail_action_records(detail.actions),
            hide_index=True,
            width="stretch",
        )
        st.markdown("### Доказательства действий")
        st.dataframe(
            _detail_evidence_records(detail.actions),
            hide_index=True,
            width="stretch",
        )

    if detail.issues:
        st.markdown("### Проблемы данных выбранного SKU")
        render_service_issues(st, detail.issues)

    st.markdown("### Происхождение данных")
    provenance = _provenance_records(detail)
    if provenance:
        st.dataframe(provenance, hide_index=True, width="stretch")
    else:
        st.info("Сведения о происхождении данных недоступны.")


def render_dashboard(st: Any, view: DashboardView) -> None:
    """Render all sections from one prepared dashboard view."""

    render_header(st)
    render_sidebar(st, view.summary)
    overview, inventory, economics, detail = st.tabs(
        (
            "Обзор / Priority Actions",
            "Запасы",
            "Юнит-экономика",
            "Детали SKU",
        )
    )
    with overview:
        render_summary(st, view.summary)
        render_priority_actions(st, view.actions)
        render_service_issues(st, view.issues)
    with inventory:
        render_inventory_view(st, view.inventory_rows)
    with economics:
        render_economics_view(st, view.economics_rows)
    with detail:
        render_sku_detail(st, view.sku_details)


def _session_state(st: Any) -> Any:
    """Return Streamlit state, with a local mapping for simple test doubles."""

    state = getattr(st, "session_state", None)
    return {} if state is None else state


def _clear_state(state: Any, *keys: str) -> None:
    for key in keys:
        if key in state:
            del state[key]


def _pricing_economics_record(
    label: str,
    economics: PricingEconomicsView,
) -> dict[str, object]:
    return {
        "Состояние": label,
        "Статус данных": _display(economics.status),
        "Безопасность цены": _display(economics.safety_status),
        "Цена": _money(economics.selling_price, economics.currency),
        "Комиссия": _money(economics.commission_cost, economics.currency),
        "Логистика": _money(economics.logistics_cost, economics.currency),
        "Реклама": _money(economics.advertising_cost, economics.currency),
        "Прочие переменные расходы": _money(
            economics.other_variable_cost_total,
            economics.currency,
        ),
        "Переменные расходы, итого": _money(
            economics.total_variable_cost,
            economics.currency,
        ),
        "Прибыль на единицу": _money(economics.profit_per_unit, economics.currency),
        "Маржа, доля": (
            MISSING_VALUE
            if economics.contribution_margin is None
            else format_ratio(economics.contribution_margin, decimal_places=3)
        ),
        "Break-even": _boundary_price(economics.break_even, economics.currency),
        "Minimum-profit": _boundary_price(economics.minimum_profit, economics.currency),
        "Minimum-margin": _boundary_price(economics.minimum_margin, economics.currency),
        "Минимальная безопасная цена": _boundary_price(
            economics.minimum_safe,
            economics.currency,
        ),
        "Статус safe price": _boundary_status(economics.minimum_safe),
    }


def _render_pricing_scenario(st: Any, scenario: PricingScenarioView) -> None:
    st.markdown(f"### Сценарий · {scenario.sku}")
    st.caption("Текущие показатели и сценарий являются разными состояниями.")
    st.dataframe(
        (
            _pricing_economics_record("Текущие показатели", scenario.current),
            _pricing_economics_record("Сценарий", scenario.scenario),
        ),
        hide_index=True,
        width="stretch",
    )
    st.markdown("#### Авторитетные изменения из PricingAnalysis")
    st.dataframe(
        (
            {
                "Изменение цены": _money(
                    scenario.price_delta,
                    scenario.scenario.currency,
                ),
                "Изменение прибыли на единицу": _money(
                    scenario.profit_per_unit_delta,
                    scenario.scenario.currency,
                ),
                "Изменение маржи": (
                    MISSING_VALUE
                    if scenario.margin_delta is None
                    else format_ratio(scenario.margin_delta, decimal_places=3)
                ),
                "Расстояние до безопасной цены": _money(
                    scenario.distance_from_safe_price,
                    scenario.scenario.currency,
                ),
                "Статус сценария": _display(scenario.status),
                "Безопасность сценария": _display(scenario.safety_status),
            },
        ),
        hide_index=True,
        width="stretch",
    )
    with st.expander("Трассировка сценария"):
        st.caption(f"Scenario ID: {scenario.scenario_id}")
        st.caption(f"Время расчёта: {scenario.analysis_timestamp.isoformat()}")
        st.caption(f"Evidence refs: {', '.join(scenario.evidence_refs)}")


def _render_pricing_result(st: Any, view: PricingAnalysisView) -> None:
    if not view.scenarios:
        st.warning(
            "Сценарий недоступен: сервис не получил достаточных данных для расчёта."
        )
    for scenario in view.scenarios:
        _render_pricing_scenario(st, scenario)
    if view.issues:
        render_service_issues(st, view.issues)


def render_pricing_simulator(
    st: Any,
    details: tuple[SkuDetailView, ...],
    simulate: Callable[[str, str], PricingAnalysisView] | None,
    *,
    snapshot_identity: str,
) -> None:
    """Render an explicit service-backed price scenario interaction."""

    st.subheader("Price Simulator")
    st.caption(PRICING_DISCLAIMER)
    if not details:
        st.info("Нет SKU в текущем снимке анализа для ценового сценария.")
        return

    details_by_sku = {detail.sku: detail for detail in details}
    selected_sku = st.selectbox(
        "SKU для сценария",
        options=tuple(details_by_sku),
        format_func=lambda sku: details_by_sku[sku].selector_label,
        key="price-simulator-sku",
    )
    raw_price = st.text_input(
        "Цена сценария, RUB",
        value="",
        key="price-simulator-candidate",
        help="Введите точное десятичное значение. Поле не использует binary float.",
    )
    request_identity = (snapshot_identity, selected_sku, raw_price.strip())
    state = _session_state(st)
    if state.get(_PRICING_IDENTITY_KEY) != request_identity:
        _clear_state(state, _PRICING_RESULT_KEY, _PRICING_IDENTITY_KEY)

    requested = st.button(
        "Рассчитать сценарий",
        key="price-simulator-submit",
        disabled=simulate is None,
    )
    if requested and simulate is not None:
        try:
            result = simulate(selected_sku, raw_price)
        except AppError as error:
            _clear_state(state, _PRICING_RESULT_KEY, _PRICING_IDENTITY_KEY)
            st.error(f"Сценарий не рассчитан: {error.safe_message}")
            st.caption(f"Код ошибки: {error.code}")
        else:
            state[_PRICING_RESULT_KEY] = result
            state[_PRICING_IDENTITY_KEY] = request_identity

    if state.get(_PRICING_IDENTITY_KEY) == request_identity:
        result = state.get(_PRICING_RESULT_KEY)
        if isinstance(result, PricingAnalysisView):
            _render_pricing_result(st, result)


def _render_generated_brief(st: Any, brief: DailyBriefView) -> None:
    st.success(AI_GENERATED_LABEL)
    st.markdown("### Краткая сводка")
    st.write(brief.summary_text)
    for item in brief.items:
        with st.container(border=True):
            st.write(item.text)
            st.caption(f"Action ref: {item.action_ref}")
            st.caption(f"Fact refs: {', '.join(item.fact_refs)}")
    with st.expander("Трассировка AI Daily Brief"):
        st.caption(f"Время анализа: {brief.analysis_timestamp.isoformat()}")
        st.caption(f"Grounding digest: {brief.grounding_digest}")
        st.caption(f"Summary action refs: {', '.join(brief.summary_action_refs)}")
        st.caption(f"Summary fact refs: {', '.join(brief.summary_fact_refs)}")


def _render_brief_status(st: Any, result: BriefStatusView) -> None:
    if result.status is BriefGenerationStatus.GENERATED:
        if result.brief is not None:
            _render_generated_brief(st, result.brief)
        return
    if result.status is BriefGenerationStatus.DISABLED:
        st.info(AI_DISABLED_MESSAGE)
    elif result.status is BriefGenerationStatus.UNAVAILABLE:
        st.warning(AI_UNAVAILABLE_MESSAGE)
    elif result.status is BriefGenerationStatus.INVALID:
        st.warning(AI_INVALID_MESSAGE)


def render_daily_brief(
    st: Any,
    generate: Callable[[], BriefStatusView] | None,
    *,
    snapshot_identity: str,
    grounding_digest: str,
    enabled: bool,
) -> None:
    """Render only validated brief results after an explicit user action."""

    st.subheader("AI Daily Brief")
    st.caption("AI формирует только объяснение уже рассчитанных фактов и действий.")
    state = _session_state(st)
    current_identity = (snapshot_identity, grounding_digest)
    if state.get(_BRIEF_IDENTITY_KEY) != current_identity:
        _clear_state(state, _BRIEF_RESULT_KEY, _BRIEF_IDENTITY_KEY)

    if not enabled:
        _clear_state(state, _BRIEF_RESULT_KEY, _BRIEF_IDENTITY_KEY)
        st.info(AI_DISABLED_MESSAGE)
        st.button(
            "Сформировать AI Daily Brief",
            key="daily-brief-generate",
            disabled=True,
        )
        return

    requested = st.button(
        "Сформировать AI Daily Brief",
        key="daily-brief-generate",
        disabled=generate is None,
    )
    if requested and generate is not None:
        try:
            result = generate()
        except AppError as error:
            _clear_state(state, _BRIEF_RESULT_KEY, _BRIEF_IDENTITY_KEY)
            st.warning("AI Daily Brief не сформирован из-за безопасной ошибки конфигурации.")
            st.caption(f"Код ошибки: {error.code}")
        else:
            state[_BRIEF_RESULT_KEY] = result
            state[_BRIEF_IDENTITY_KEY] = current_identity

    if state.get(_BRIEF_IDENTITY_KEY) == current_identity:
        result = state.get(_BRIEF_RESULT_KEY)
        if isinstance(result, BriefStatusView):
            brief = result.brief
            if brief is not None and (
                brief.analysis_timestamp.isoformat() != snapshot_identity
                or brief.grounding_digest != grounding_digest
            ):
                _clear_state(state, _BRIEF_RESULT_KEY, _BRIEF_IDENTITY_KEY)
            else:
                _render_brief_status(st, result)


def render_interactive_dashboard(
    st: Any,
    view: DashboardView,
    *,
    simulate: Callable[[str, str], PricingAnalysisView],
    generate_brief: Callable[[], BriefStatusView],
    brief_enabled: bool,
    snapshot_identity: str,
    brief_grounding_digest: str,
) -> None:
    """Render the six-tab MVP dashboard using injected application-level actions."""

    render_header(st)
    render_sidebar(st, view.summary)
    overview, inventory, economics, detail, pricing, brief = st.tabs(
        (
            "Overview / Priority Actions",
            "Запасы",
            "Юнит-экономика",
            "Детали SKU",
            "Price Simulator",
            "AI Daily Brief",
        )
    )
    with overview:
        render_summary(st, view.summary)
        render_priority_actions(st, view.actions)
        render_service_issues(st, view.issues)
    with inventory:
        render_inventory_view(st, view.inventory_rows)
    with economics:
        render_economics_view(st, view.economics_rows)
    with detail:
        render_sku_detail(st, view.sku_details)
    with pricing:
        render_pricing_simulator(
            st,
            view.sku_details,
            simulate,
            snapshot_identity=snapshot_identity,
        )
    with brief:
        render_daily_brief(
            st,
            generate_brief,
            snapshot_identity=snapshot_identity,
            grounding_digest=brief_grounding_digest,
            enabled=brief_enabled,
        )

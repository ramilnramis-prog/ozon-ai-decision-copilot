"""Runnable Streamlit entrypoint for the service-backed read-only MVP dashboard."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from app.bootstrap import ApplicationServices, build_application
from app.core.config import AppConfig
from app.core.errors import AppError
from app.domain.common import SkuId
from app.services.models import AnalysisSnapshot, PriceScenarioRequest
from app.ui.components import render_interactive_dashboard
from app.ui.presenters import (
    BriefStatusView,
    DashboardView,
    PricingAnalysisView,
    build_brief_status_view,
    build_dashboard_view,
    build_pricing_analysis_view,
    parse_candidate_price,
)


@dataclass(frozen=True, slots=True)
class PreparedDashboard:
    """One coherent snapshot plus its application services and presentation."""

    services: ApplicationServices
    snapshot: AnalysisSnapshot
    view: DashboardView
    brief_grounding_digest: str


def prepare_dashboard(
    application: ApplicationServices | None = None,
) -> PreparedDashboard:
    """Build configured services and analyze the approved active-SKU scope once."""

    services = build_application() if application is None else application
    snapshot = services.analysis.analyze(None)
    return PreparedDashboard(
        services=services,
        snapshot=snapshot,
        view=build_dashboard_view(snapshot),
        brief_grounding_digest=services.briefs.grounding_digest(snapshot),
    )


def prepare_default_dashboard(
    application: ApplicationServices | None = None,
) -> DashboardView:
    """Run the approved active-SKU analysis path and prepare its presentation."""

    services = (
        build_application(AppConfig(ai_enabled=False), environ={})
        if application is None
        else application
    )
    return prepare_dashboard(services).view


def simulate_price(
    services: ApplicationServices,
    sku: str,
    raw_candidate_price: str,
) -> PricingAnalysisView:
    """Translate one exact UI intent into the public PricingService contract."""

    candidate_price = parse_candidate_price(raw_candidate_price)
    request = PriceScenarioRequest(
        scenario_id=f"streamlit:{sku}:{format(candidate_price, 'f')}",
        hypothetical_price=candidate_price,
    )
    result = services.pricing.simulate(SkuId(sku), (request,))
    return build_pricing_analysis_view(result)


def generate_brief(
    services: ApplicationServices,
    snapshot: AnalysisSnapshot,
) -> BriefStatusView:
    """Request one service-validated Daily Brief for the current snapshot."""

    return build_brief_status_view(services.briefs.generate(snapshot))


def main() -> None:
    """Render the Streamlit application with a narrow safe error boundary."""

    st.set_page_config(
        page_title="Ozon AI Decision Copilot",
        page_icon="📊",
        layout="wide",
    )
    try:
        prepared = prepare_dashboard()
    except AppError as error:
        st.error(f"Не удалось подготовить анализ: {error.safe_message}")
        st.caption(f"Код ошибки: {error.code}")
        return
    render_interactive_dashboard(
        st,
        prepared.view,
        simulate=lambda sku, raw_price: simulate_price(
            prepared.services,
            sku,
            raw_price,
        ),
        generate_brief=lambda: generate_brief(
            prepared.services,
            prepared.snapshot,
        ),
        brief_enabled=prepared.services.briefs.enabled,
        snapshot_identity=prepared.snapshot.analysis_timestamp.isoformat(),
        brief_grounding_digest=prepared.brief_grounding_digest,
    )


if __name__ == "__main__":
    main()

"""Independent read-only verification harness for MVP-018 boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
from pathlib import Path

from app.ai.brief_input import (
    build_grounded_brief_input,
    serialize_grounded_brief_input,
)
from app.bootstrap import build_application
from app.core.clock import FixedClock
from app.core.config import AppConfig
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
from app.ui.components import (
    AI_DISABLED_MESSAGE,
    AI_INVALID_MESSAGE,
    AI_UNAVAILABLE_MESSAGE,
    PRICING_DISCLAIMER,
)
from app.ui.presenters import (
    build_brief_status_view,
    build_dashboard_view,
    build_pricing_analysis_view,
    parse_candidate_price,
)


STAMP = datetime(2026, 9, 7, 9, tzinfo=UTC)


class _Provider:
    def __init__(self) -> None:
        self.calls = 0
        self.last_input: GroundedBriefInput | None = None

    def create_draft(self, brief_input: GroundedBriefInput) -> AIBriefDraft:
        self.calls += 1
        self.last_input = brief_input
        return AIBriefDraft(
            AIBriefSummary("Grounded management summary.", (), ()),
            tuple(
                AIBriefItem(action.action_ref, "Grounded action explanation.", ())
                for action in brief_input.actions
            ),
        )


def test_mvp018_independent_read_only_harness() -> None:
    checks = 0

    def check(condition: object, label: str) -> None:
        nonlocal checks
        checks += 1
        assert condition, label

    services = build_application(
        AppConfig(ai_enabled=False),
        clock=FixedClock(STAMP),
        environ={},
    )
    snapshot = services.analysis.analyze(None)
    snapshot_state = (
        snapshot.sku_results,
        snapshot.priority_actions,
        snapshot.issues,
        snapshot.provenance,
    )
    dashboard = build_dashboard_view(snapshot)

    check(len(snapshot.sku_results) == 37, "active SKU count")
    check(len(snapshot.priority_actions) == 82, "priority action count")
    check(len(dashboard.sku_details) == 37, "pricing selector scope")
    check(len({detail.sku for detail in dashboard.sku_details}) == 37, "SKU identity")
    check(dashboard.summary.critical_count == 17, "critical count")
    check(dashboard.summary.high_count == 53, "high count")
    for source, view in zip(snapshot.priority_actions, dashboard.actions, strict=True):
        check(
            source.recommendation.recommendation_id == view.recommendation_id,
            "action identity/order",
        )

    exact = parse_candidate_price("1233.00")
    check(exact == Decimal("1233.00"), "candidate exact value")
    check(exact.as_tuple() == Decimal("1233.00").as_tuple(), "candidate exact scale")
    check(not isinstance(exact, float), "candidate is not float")

    for sku, candidate in (
        ("DEMO-015", "1233.00"),
        ("DEMO-018", "4000"),
        ("DEMO-021", "1233.00"),
    ):
        request = PriceScenarioRequest(f"harness:{sku}", Decimal(candidate))
        analysis = services.pricing.simulate(SkuId(sku), (request,))
        source = analysis.scenario_results[0]
        view = build_pricing_analysis_view(analysis).scenarios[0]
        check(view.sku == sku, f"{sku} identity")
        check(view.scenario.selling_price == source.candidate_price, f"{sku} candidate")
        check(view.current.selling_price == source.current_price, f"{sku} current")
        check(view.current.profit_per_unit == source.current_economics.profit_per_unit, f"{sku} current profit")
        check(view.scenario.profit_per_unit == source.economics.profit_per_unit, f"{sku} scenario profit")
        check(view.current.contribution_margin == source.current_economics.contribution_margin, f"{sku} current margin")
        check(view.scenario.contribution_margin == source.economics.contribution_margin, f"{sku} scenario margin")
        check(view.price_delta == source.price_delta, f"{sku} price delta")
        check(view.profit_per_unit_delta == source.profit_per_unit_delta, f"{sku} profit delta")
        check(view.margin_delta == source.margin_delta, f"{sku} margin delta")
        check(view.distance_from_safe_price == source.distance_from_safe_price, f"{sku} safe distance")
        check(view.evidence_refs == source.evidence_refs, f"{sku} evidence")
        if sku == "DEMO-018":
            check(view.scenario.minimum_safe.price is None, "no fabricated finite safe price")
            check(view.distance_from_safe_price is None, "no fabricated safe distance")
        if sku == "DEMO-021":
            check(view.status is AvailabilityStatus.INSUFFICIENT_DATA, "missing economics status")
            check(view.current.profit_per_unit is None, "missing current profit")
            check(view.scenario.profit_per_unit is None, "missing scenario profit")
            check(view.scenario.contribution_margin is None, "missing scenario margin")

    provider = _Provider()
    brief_service = BriefService(provider, enabled=True)
    identity_digest = brief_service.grounding_digest(snapshot)
    grounded_input = build_grounded_brief_input(snapshot)
    expected_digest = hashlib.sha256(
        serialize_grounded_brief_input(grounded_input).encode("utf-8")
    ).hexdigest()
    check(identity_digest == expected_digest, "canonical identity digest")
    check(provider.calls == 0, "identity does not call provider")

    changed_snapshot = services.analysis.analyze((SkuId("DEMO-005"),))
    other_snapshot = services.analysis.analyze((SkuId("DEMO-006"),))
    changed_digest = brief_service.grounding_digest(changed_snapshot)
    other_digest = brief_service.grounding_digest(other_snapshot)
    check(
        changed_snapshot.analysis_timestamp == other_snapshot.analysis_timestamp,
        "same timestamp fixture",
    )
    check(
        changed_digest != other_digest,
        "grounded content changes identity",
    )
    check(provider.calls == 0, "all identity calculations are provider-free")

    result = brief_service.generate(snapshot)
    brief_view = build_brief_status_view(result)
    check(provider.calls == 1, "one provider attempt")
    check(result.status is BriefGenerationStatus.GENERATED, "generated status")
    check(result.brief is not None, "validated DailyBrief exists")
    check(brief_view.brief is not None, "presenter brief exists")
    assert result.brief is not None and brief_view.brief is not None
    check(brief_view.brief.summary_text == result.brief.summary.text, "summary exact")
    check(len(brief_view.brief.items) == len(snapshot.priority_actions), "item cardinality")
    check(
        tuple(item.action_ref for item in brief_view.brief.items)
        == tuple(item.action_ref for item in result.brief.items),
        "item order",
    )
    check(
        tuple(item.text for item in brief_view.brief.items)
        == tuple(item.text for item in result.brief.items),
        "item text",
    )
    check(brief_view.brief.grounding_digest == result.brief.grounding_digest, "digest")
    check(result.brief.grounding_digest == identity_digest, "service identity")
    check(brief_view.brief.analysis_timestamp == snapshot.analysis_timestamp, "timestamp")
    for status in (
        BriefGenerationStatus.DISABLED,
        BriefGenerationStatus.UNAVAILABLE,
        BriefGenerationStatus.INVALID,
    ):
        status_view = build_brief_status_view(BriefGenerationResult(status, None))
        check(status_view.status is status, f"{status.value} status")
        check(status_view.brief is None, f"{status.value} no prose")

    ui_source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("streamlit_app.py", "app/ui/presenters.py", "app/ui/components.py")
    )
    lower = ui_source.lower()
    check("float(" not in ui_source, "no float conversion")
    check("calculate_unit_economics" not in ui_source, "no economics call")
    check("simulate_prices" not in ui_source, "no pricing analytics call")
    check("build_grounded_brief_input" not in ui_source, "no UI grounding")
    check("hashlib" not in ui_source, "no UI hashing")
    check("OpenAIBriefProvider" not in ui_source, "no direct OpenAI adapter")
    check("AIBriefDraft" not in ui_source, "no raw draft boundary")
    check("OPENAI_API_KEY" not in ui_source, "no API key UI")
    check("unsafe_allow_html" not in ui_source, "no unsafe HTML")
    check("except Exception" not in ui_source, "narrow errors")
    check("model dropdown" not in lower, "no model selector")
    check("temperature" not in lower, "no temperature control")
    check("применить цену" not in lower, "no apply-price control")
    check("изменить цену" not in lower, "no change-price control")
    check("optimal price" not in lower, "no optimal price claim")
    check("best price" not in lower, "no best price claim")
    check("не прогнозирует" in PRICING_DISCLAIMER, "forecast disclaimer")
    check("не изменяется" in PRICING_DISCLAIMER, "no-write disclaimer")
    check("отключ" in AI_DISABLED_MESSAGE, "disabled copy")
    check("недоступ" in AI_UNAVAILABLE_MESSAGE, "unavailable copy")
    check("не прош" in AI_INVALID_MESSAGE, "invalid copy")
    check(snapshot_state == (
        snapshot.sku_results,
        snapshot.priority_actions,
        snapshot.issues,
        snapshot.provenance,
    ), "source snapshot unchanged")
    check(build_dashboard_view(snapshot) == dashboard, "presenter repeatability")
    check(checks >= 120, f"expected at least 120 independent checks, got {checks}")
    print(f"MVP-018 independent harness: {checks}/{checks} checks passed")

"""Real-provider integration checks for the deterministic service flow."""

from datetime import UTC, datetime

from app.bootstrap import build_application
from app.core.clock import FixedClock


STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)


def test_complete_demo_snapshot_is_deterministic_and_traceable() -> None:
    service = build_application(clock=FixedClock(STAMP)).analysis

    first = service.analyze()
    second = service.analyze()

    assert first == second
    assert len(first.sku_results) == 37
    assert all(result.product is not None and result.product.active for result in first.sku_results)
    assert "DEMO-038" not in {str(result.sku) for result in first.sku_results}
    assert tuple(str(result.sku) for result in first.sku_results) == tuple(
        sorted(str(result.sku) for result in first.sku_results)
    )
    recommendation_ids = {
        recommendation.recommendation_id
        for result in first.sku_results
        for evaluation in result.decision_evaluations
        for recommendation in evaluation.recommendations
    }
    assert recommendation_ids == {
        action.recommendation.recommendation_id for action in first.priority_actions
    }
    assert all(action.recommendation.evidence_refs for action in first.priority_actions)
    assert all(action.recommendation.provenance for action in first.priority_actions)
    assert first.provenance

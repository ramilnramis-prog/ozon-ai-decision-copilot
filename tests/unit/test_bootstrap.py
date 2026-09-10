"""Composition-root tests for mock-only application wiring."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.bootstrap import build_application
from app.core.clock import FixedClock
from app.core.config import OPENAI_API_KEY_ENV, AppConfig
from app.core.errors import ConfigurationError
from app.domain.briefs import BriefGenerationStatus
from app.domain.common import SkuId
from app.services.analysis import AnalysisService
from app.services.pricing import PricingService
from app.services.briefs import BriefService


STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)


def test_mock_only_bootstrap_needs_no_credentials_and_loads_explicit_demo_policy() -> None:
    services = build_application(AppConfig(), clock=FixedClock(STAMP))

    assert isinstance(services.analysis, AnalysisService)
    assert isinstance(services.pricing, PricingService)
    assert isinstance(services.briefs, BriefService)
    assert services.analysis.configuration.as_of_date.isoformat() == "2026-09-02"
    assert services.analysis.configuration.sales_policy.identity.policy_id == "demo-sales-policy"
    assert services.analysis.analyze((SkuId("DEMO-001"),)).sku_results


def test_disabled_ai_does_not_resolve_or_require_openai_secret() -> None:
    services = build_application(
        AppConfig(ai_enabled=False),
        clock=FixedClock(STAMP),
        environ={},
    )
    snapshot = services.analysis.analyze(())

    result = services.briefs.generate(snapshot)

    assert result.status is BriefGenerationStatus.DISABLED
    assert result.brief is None


def test_enabled_ai_requires_openai_secret() -> None:
    with pytest.raises(ConfigurationError) as error:
        build_application(
            AppConfig(ai_enabled=True),
            clock=FixedClock(STAMP),
            environ={},
        )

    assert error.value.code == "configuration.missing_openai_api_key"


def test_enabled_ai_constructs_adapter_only_at_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}
    sentinel = object()

    def fake_adapter(api_key: str, *, model: str):
        captured.update(api_key=api_key, model=model)
        return sentinel

    monkeypatch.setattr("app.bootstrap.OpenAIBriefProvider", fake_adapter)
    services = build_application(
        AppConfig(ai_enabled=True, ai_model="gpt-5.6-terra-test"),
        clock=FixedClock(STAMP),
        environ={OPENAI_API_KEY_ENV: "project-test-key"},
    )

    assert captured == {
        "api_key": "project-test-key",
        "model": "gpt-5.6-terra-test",
    }
    assert services.briefs.provider is sentinel
    assert "project-test-key" not in repr(services)


def test_missing_demo_configuration_fails_startup_clearly(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as error:
        build_application(AppConfig(demo_data_path=tmp_path), clock=FixedClock(STAMP))

    assert error.value.code == "configuration.demo_unavailable"

"""Composition root for the current mock-only deterministic application."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path

from app.ai.openai_provider import OpenAIBriefProvider
from app.core.clock import Clock, SystemClock
from app.core.config import (
    OPENAI_API_KEY_ENV,
    AppConfig,
    MarketplaceProvider,
    load_config,
)
from app.core.errors import ConfigurationError, DataValidationError
from app.decisions.prioritizer import APPROVED_SEVERITY_ORDER, APPROVED_URGENCY_ORDER
from app.domain.catalog import SalesPolicy
from app.domain.common import Currency, PolicyIdentity
from app.domain.economics import EconomicsPolicy
from app.domain.recommendations import PriorityPolicy
from app.providers.demo_loader import PROJECT_ROOT
from app.providers.local_unit_economics import LocalUnitEconomicsProvider
from app.providers.mock_ozon import MockOzonProvider
from app.services.analysis import AnalysisService
from app.services.briefs import BriefService
from app.services.models import AnalysisConfiguration, InventoryPolicyConfig
from app.services.pricing import PricingService


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    """In-process use cases exposed to future UI/API adapters."""

    analysis: AnalysisService
    pricing: PricingService
    briefs: BriefService


def _demo_root(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _load_json_object(path: Path, *, scope: str) -> Mapping[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(
            "Demo configuration is unavailable.",
            code="configuration.demo_unavailable",
            scope=scope,
        ) from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            "Demo configuration contains invalid JSON.",
            code="configuration.demo_invalid_json",
            scope=scope,
        ) from exc
    if not isinstance(value, Mapping) or value.get("schema_version") != "1.0":
        raise ConfigurationError(
            "Demo configuration schema is invalid.",
            code="configuration.demo_invalid_schema",
            scope=scope,
        )
    return value


def _object(value: object, *, scope: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(
            "Demo policy configuration is invalid.",
            code="configuration.demo_invalid_policy",
            scope=scope,
        )
    return value


def _text(value: object, *, scope: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(
            "Demo policy configuration is invalid.",
            code="configuration.demo_invalid_policy",
            scope=scope,
        )
    return value.strip()


def _integer(value: object, *, scope: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(
            "Demo policy configuration is invalid.",
            code="configuration.demo_invalid_policy",
            scope=scope,
        )
    return value


def _analysis_configuration(demo_root: Path) -> AnalysisConfiguration:
    metadata = _load_json_object(demo_root / "metadata.json", scope="demo metadata")
    policies = _load_json_object(demo_root / "policies.json", scope="demo policies")
    sales = _object(policies.get("sales"), scope="sales policy")
    inventory = _object(policies.get("inventory"), scope="inventory policy")
    economics = _object(policies.get("economics"), scope="economics policy")
    try:
        as_of = date.fromisoformat(_text(metadata.get("as_of_date"), scope="as-of date"))
        sales_policy = SalesPolicy(
            PolicyIdentity(
                _text(sales.get("policy_id"), scope="sales policy id"),
                _text(sales.get("version"), scope="sales policy version"),
            ),
            _integer(sales.get("averaging_window_days"), scope="sales averaging window"),
            _integer(sales.get("comparison_window_days"), scope="sales comparison window"),
            Decimal(_text(sales.get("material_change_threshold"), scope="sales threshold")),
        )
        inventory_policy = InventoryPolicyConfig(
            PolicyIdentity(
                _text(inventory.get("policy_id"), scope="inventory policy id"),
                _text(inventory.get("version"), scope="inventory policy version"),
            ),
            _integer(inventory.get("target_coverage_days"), scope="target coverage"),
            _integer(inventory.get("warning_window_days"), scope="warning window"),
            _integer(inventory.get("overstock_threshold_days"), scope="overstock threshold"),
        )
        economics_policy = EconomicsPolicy(
            PolicyIdentity(
                _text(economics.get("policy_id"), scope="economics policy id"),
                _text(economics.get("version"), scope="economics policy version"),
            ),
            Currency(_text(economics.get("currency"), scope="economics currency")),
            Decimal(_text(economics.get("minimum_profit_per_unit"), scope="minimum profit")),
            Decimal(_text(economics.get("minimum_margin_rate"), scope="minimum margin")),
            Decimal(_text(economics.get("price_floor"), scope="price floor")),
            Decimal(_text(economics.get("currency_quantum"), scope="currency quantum")),
            Decimal(_text(economics.get("price_increment"), scope="price increment")),
        )
    except (ValueError, ArithmeticError, DataValidationError) as exc:
        raise ConfigurationError(
            "Demo policy configuration contains an invalid value.",
            code="configuration.demo_invalid_policy",
            scope="demo policies",
        ) from exc

    priority_policy = PriorityPolicy(
        PolicyIdentity("mvp-priority-policy", "1"),
        APPROVED_SEVERITY_ORDER,
        APPROVED_URGENCY_ORDER,
    )
    return AnalysisConfiguration(
        as_of_date=as_of,
        sales_policy=sales_policy,
        inventory_policy=inventory_policy,
        economics_policy=economics_policy,
        priority_policy=priority_policy,
    )


def build_application(
    config: AppConfig | None = None,
    *,
    clock: Clock | None = None,
    environ: Mapping[str, str] | None = None,
) -> ApplicationServices:
    """Build read-only services, resolving AI secrets only when enabled."""

    environment = os.environ if environ is None else environ
    selected = load_config(environment) if config is None else config
    if not isinstance(selected, AppConfig):
        raise ConfigurationError(
            "Application configuration has an invalid type.",
            code="configuration.invalid",
            scope="application configuration",
        )
    if selected.marketplace_provider is not MarketplaceProvider.MOCK:
        raise ConfigurationError(
            "Only the mock marketplace provider is available in the MVP.",
            code="configuration.invalid_provider",
            scope="marketplace provider",
        )
    selected_clock = SystemClock() if clock is None else clock
    root = _demo_root(selected.demo_data_path)
    policies = _analysis_configuration(root)
    marketplace = MockOzonProvider(root)
    economics = LocalUnitEconomicsProvider(root)
    brief_provider = None
    if selected.ai_enabled:
        secret_name = selected.ai_secret_env_var or OPENAI_API_KEY_ENV
        api_key = environment.get(secret_name)
        if not isinstance(api_key, str) or not api_key.strip():
            raise ConfigurationError(
                "OpenAI API key is required when AI generation is enabled.",
                code="configuration.missing_openai_api_key",
                scope="OpenAI adapter",
            )
        brief_provider = OpenAIBriefProvider(
            api_key.strip(),
            model=selected.ai_model,
        )
    return ApplicationServices(
        analysis=AnalysisService(marketplace, economics, selected_clock, policies),
        pricing=PricingService(
            economics,
            selected_clock,
            policies.economics_policy,
            policies.priority_policy,
        ),
        briefs=BriefService(brief_provider, enabled=selected.ai_enabled),
    )

"""Minimal environment-backed application configuration for the Fast Track."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.core.errors import ConfigurationError


DEMO_DATA_PATH_ENV = "OZON_COPILOT_DEMO_DATA_PATH"
MARKETPLACE_PROVIDER_ENV = "OZON_COPILOT_MARKETPLACE_PROVIDER"
AI_ENABLED_ENV = "OZON_COPILOT_AI_ENABLED"
AI_MODEL_ENV = "OZON_COPILOT_AI_MODEL"
AI_SECRET_ENV_VAR_ENV = "OZON_COPILOT_AI_SECRET_ENV_VAR"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_AI_MODEL = "gpt-5.6-terra"

_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class MarketplaceProvider(str, Enum):
    """Marketplace read providers supported by the current application."""

    MOCK = "mock"


@dataclass(frozen=True, slots=True, repr=False)
class AppConfig:
    """Infrastructure selection only; business policies live in domain models."""

    demo_data_path: Path = Path("data/demo")
    marketplace_provider: MarketplaceProvider = MarketplaceProvider.MOCK
    ai_enabled: bool = False
    ai_model: str = DEFAULT_AI_MODEL
    ai_secret_env_var: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.demo_data_path, Path):
            raise ConfigurationError(
                "demo data path must be a pathlib.Path",
                code="configuration.invalid_demo_data_path",
                scope="demo_data_path",
            )
        if not isinstance(self.marketplace_provider, MarketplaceProvider):
            raise ConfigurationError(
                "marketplace provider selection is invalid",
                code="configuration.invalid_provider",
                scope="marketplace_provider",
            )
        if not isinstance(self.ai_enabled, bool):
            raise ConfigurationError(
                "AI enabled flag must be boolean",
                code="configuration.invalid_ai_enabled",
                scope="ai_enabled",
            )
        if not isinstance(self.ai_model, str) or not self.ai_model.strip():
            raise ConfigurationError(
                "AI model identifier must be a non-empty string",
                code="configuration.invalid_ai_model",
                scope="ai_model",
            )
        object.__setattr__(self, "ai_model", self.ai_model.strip())
        if self.ai_secret_env_var is not None and (
            not _ENV_NAME_PATTERN.fullmatch(self.ai_secret_env_var)
            or self.ai_secret_env_var != OPENAI_API_KEY_ENV
        ):
            raise ConfigurationError(
                "AI secret reference must use the approved OpenAI key name",
                code="configuration.invalid_secret_reference",
                scope="ai_secret_env_var",
            )

    def __repr__(self) -> str:
        secret_reference = "configured" if self.ai_secret_env_var else "not configured"
        return (
            "AppConfig("
            f"demo_data_path={self.demo_data_path!r}, "
            f"marketplace_provider={self.marketplace_provider.value!r}, "
            f"ai_enabled={self.ai_enabled!r}, "
            f"ai_model={self.ai_model!r}, "
            f"ai_secret_reference={secret_reference!r})"
        )


def _parse_bool(raw_value: str, *, field_name: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigurationError(
        f"{field_name} must be a recognized boolean value",
        code="configuration.invalid_boolean",
        scope=field_name,
    )


def _parse_provider(raw_value: str) -> MarketplaceProvider:
    try:
        return MarketplaceProvider(raw_value.strip().lower())
    except ValueError as exc:
        raise ConfigurationError(
            "marketplace provider is not supported; the MVP supports mock only",
            code="configuration.invalid_provider",
            scope="marketplace_provider",
        ) from exc


def load_config(environ: Mapping[str, str] | None = None) -> AppConfig:
    """Load infrastructure settings without reading or storing secret values."""

    source = os.environ if environ is None else environ
    raw_path = source.get(DEMO_DATA_PATH_ENV, "data/demo").strip()
    if not raw_path:
        raise ConfigurationError(
            "demo data path must not be empty",
            code="configuration.invalid_demo_data_path",
            scope="demo_data_path",
        )

    provider = _parse_provider(source.get(MARKETPLACE_PROVIDER_ENV, "mock"))
    ai_enabled = _parse_bool(source.get(AI_ENABLED_ENV, "false"), field_name="ai_enabled")
    ai_model = source.get(AI_MODEL_ENV, DEFAULT_AI_MODEL)
    secret_reference = source.get(AI_SECRET_ENV_VAR_ENV)
    if secret_reference is not None:
        secret_reference = secret_reference.strip() or None

    return AppConfig(
        demo_data_path=Path(raw_path),
        marketplace_provider=provider,
        ai_enabled=ai_enabled,
        ai_model=ai_model,
        ai_secret_env_var=secret_reference,
    )

"""Tests for minimal, secret-safe application configuration."""

from pathlib import Path

import pytest

from app.core.config import (
    AI_ENABLED_ENV,
    AI_MODEL_ENV,
    AI_SECRET_ENV_VAR_ENV,
    DEFAULT_AI_MODEL,
    DEMO_DATA_PATH_ENV,
    MARKETPLACE_PROVIDER_ENV,
    OPENAI_API_KEY_ENV,
    AppConfig,
    MarketplaceProvider,
    load_config,
)
from app.core.errors import ConfigurationError


def test_default_mock_configuration_requires_no_secrets() -> None:
    config = load_config({})

    assert config == AppConfig()
    assert config.demo_data_path == Path("data/demo")
    assert config.marketplace_provider is MarketplaceProvider.MOCK
    assert config.ai_enabled is False
    assert config.ai_model == DEFAULT_AI_MODEL
    assert config.ai_secret_env_var is None


def test_configuration_loads_supported_infrastructure_settings() -> None:
    config = load_config(
        {
            DEMO_DATA_PATH_ENV: "fixtures/demo",
            MARKETPLACE_PROVIDER_ENV: "MOCK",
            AI_ENABLED_ENV: "yes",
            AI_MODEL_ENV: "gpt-5.6-terra-test",
            AI_SECRET_ENV_VAR_ENV: OPENAI_API_KEY_ENV,
        }
    )

    assert config.demo_data_path == Path("fixtures/demo")
    assert config.marketplace_provider is MarketplaceProvider.MOCK
    assert config.ai_enabled is True
    assert config.ai_model == "gpt-5.6-terra-test"
    assert config.ai_secret_env_var == OPENAI_API_KEY_ENV


def test_configuration_repr_never_reads_or_exposes_secret_value() -> None:
    secret_value = "do-not-display-this-secret"
    config = load_config(
        {
            AI_SECRET_ENV_VAR_ENV: OPENAI_API_KEY_ENV,
            OPENAI_API_KEY_ENV: secret_value,
        }
    )

    rendered = repr(config)

    assert secret_value not in rendered
    assert OPENAI_API_KEY_ENV not in rendered
    assert "configured" in rendered


@pytest.mark.parametrize(
    ("environment", "expected_code", "secret_text"),
    [
        ({MARKETPLACE_PROVIDER_ENV: "secret-provider-value"}, "configuration.invalid_provider", "secret-provider-value"),
        ({AI_ENABLED_ENV: "secret-boolean-value"}, "configuration.invalid_boolean", "secret-boolean-value"),
        ({DEMO_DATA_PATH_ENV: "   "}, "configuration.invalid_demo_data_path", None),
        ({AI_SECRET_ENV_VAR_ENV: "invalid secret name"}, "configuration.invalid_secret_reference", "invalid secret name"),
        ({AI_SECRET_ENV_VAR_ENV: "OTHER_API_KEY"}, "configuration.invalid_secret_reference", "OTHER_API_KEY"),
        ({AI_MODEL_ENV: "   "}, "configuration.invalid_ai_model", None),
    ],
)
def test_invalid_configuration_fails_with_safe_message(
    environment: dict[str, str], expected_code: str, secret_text: str | None
) -> None:
    with pytest.raises(ConfigurationError) as raised:
        load_config(environment)

    assert raised.value.code == expected_code
    if secret_text:
        assert secret_text not in str(raised.value)
        assert secret_text not in repr(raised.value)


def test_direct_configuration_rejects_untyped_provider() -> None:
    with pytest.raises(ConfigurationError) as raised:
        AppConfig(marketplace_provider="mock")

    assert raised.value.code == "configuration.invalid_provider"

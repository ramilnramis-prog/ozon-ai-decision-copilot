"""Small, safe exception model shared by application boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar


_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _REDACTED if _is_sensitive_key(key) else _sanitize(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_sanitize(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_sanitize(item) for item in value), key=repr))
    return value


class AppError(Exception):
    """Base error containing only safe, user-displayable details."""

    default_code: ClassVar[str] = "application.error"

    def __init__(
        self,
        safe_message: str,
        *,
        code: str | None = None,
        scope: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        if not safe_message:
            raise ValueError("safe_message must not be empty")

        self.code = code or type(self).default_code
        self.safe_message = safe_message
        self.scope = scope
        self.context = _sanitize(context or {})
        super().__init__(safe_message)

    def __str__(self) -> str:
        return self.safe_message

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, "
            f"safe_message={self.safe_message!r}, scope={self.scope!r}, "
            f"context={dict(self.context)!r})"
        )


class ConfigurationError(AppError):
    """Application configuration is missing or invalid."""

    default_code = "configuration.invalid"


class DataValidationError(AppError):
    """Source or domain data is malformed or violates an invariant."""

    default_code = "data.invalid"


class ProviderError(AppError):
    """A data provider could not complete a read operation."""

    default_code = "provider.error"


class CalculationPreconditionError(AppError):
    """A deterministic calculation lacks a valid prerequisite."""

    default_code = "calculation.precondition_failed"


class AIError(AppError):
    """Base error for the optional AI interpretation boundary."""

    default_code = "ai.error"


class AIUnavailableError(AIError):
    """The optional AI interpreter is disabled or unavailable."""

    default_code = "ai.unavailable"


class AIInvalidOutputError(AIError):
    """AI output is malformed or fails grounding validation."""

    default_code = "ai.invalid_output"

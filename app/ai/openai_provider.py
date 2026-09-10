"""Concrete OpenAI Responses API adapter for grounded Daily Brief drafts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from app.ai.brief_input import serialize_grounded_brief_input
from app.ai.prompts import DAILY_BRIEF_INSTRUCTIONS
from app.core.config import DEFAULT_AI_MODEL
from app.core.errors import (
    AIInvalidOutputError,
    AIUnavailableError,
    ConfigurationError,
    DataValidationError,
)
from app.domain.briefs import AIBriefDraft, AIBriefItem, AIBriefSummary, GroundedBriefInput


DEFAULT_OPENAI_MODEL = DEFAULT_AI_MODEL

_UNAVAILABLE_EXCEPTIONS = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)
_AUTHENTICATION_EXCEPTIONS = (AuthenticationError, PermissionDeniedError)
_INVALID_RESPONSE_EXCEPTIONS = (APIResponseValidationError,)
_MISSING = object()


def _draft_format() -> dict[str, object]:
    string_array = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "json_schema",
        "name": "ozon_ai_daily_brief_draft",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "items"],
            "properties": {
                "summary": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text", "action_refs", "fact_refs"],
                    "properties": {
                        "text": {"type": "string"},
                        "action_refs": string_array,
                        "fact_refs": string_array,
                    },
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["action_ref", "text", "fact_refs"],
                        "properties": {
                            "action_ref": {"type": "string"},
                            "text": {"type": "string"},
                            "fact_refs": string_array,
                        },
                    },
                },
            },
        },
    }


def _invalid_output() -> AIInvalidOutputError:
    return AIInvalidOutputError(
        "AI provider returned an unusable structured draft.",
        code="ai.invalid_provider_output",
        scope="OpenAI Daily Brief",
    )


def _field(value: object, name: str) -> object:
    """Read one SDK response field without relying on generated class names."""

    if isinstance(value, Mapping):
        return value.get(name, _MISSING)
    return getattr(value, name, _MISSING)


def _single_output_text(response: object) -> str:
    """Extract the sole approved completed assistant output, failing closed."""

    if _field(response, "status") != "completed":
        raise _invalid_output()

    output = _field(response, "output")
    if not isinstance(output, list) or len(output) != 1:
        raise _invalid_output()

    message = output[0]
    if (
        _field(message, "type") != "message"
        or _field(message, "role") != "assistant"
        or _field(message, "status") != "completed"
    ):
        raise _invalid_output()

    content = _field(message, "content")
    if not isinstance(content, list) or len(content) != 1:
        raise _invalid_output()

    output_text = content[0]
    if _field(output_text, "type") != "output_text":
        raise _invalid_output()
    text = _field(output_text, "text")
    if not isinstance(text, str) or not text.strip():
        raise _invalid_output()
    return text


def _exact_object(value: object, keys: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _invalid_output()
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _invalid_output()
    return tuple(value)


def _draft_from_payload(value: object) -> AIBriefDraft:
    root = _exact_object(value, frozenset({"summary", "items"}))
    summary_value = _exact_object(
        root["summary"],
        frozenset({"text", "action_refs", "fact_refs"}),
    )
    if not isinstance(summary_value["text"], str):
        raise _invalid_output()
    items_value = root["items"]
    if not isinstance(items_value, list):
        raise _invalid_output()
    try:
        summary = AIBriefSummary(
            text=summary_value["text"],
            action_refs=_string_tuple(summary_value["action_refs"]),
            fact_refs=_string_tuple(summary_value["fact_refs"]),
        )
        items = tuple(
            AIBriefItem(
                action_ref=item_value["action_ref"],
                text=item_value["text"],
                fact_refs=_string_tuple(item_value["fact_refs"]),
            )
            for raw_item in items_value
            for item_value in (
                _exact_object(
                    raw_item,
                    frozenset({"action_ref", "text", "fact_refs"}),
                ),
            )
            if isinstance(item_value["action_ref"], str)
            and isinstance(item_value["text"], str)
        )
        if len(items) != len(items_value):
            raise _invalid_output()
        return AIBriefDraft(summary=summary, items=items)
    except DataValidationError as exc:
        raise _invalid_output() from exc


class OpenAIBriefProvider:
    """One-call, no-tool adapter implementing the existing brief protocol."""

    __slots__ = ("_client", "_model")

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_OPENAI_MODEL,
        client: Any | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ConfigurationError(
                "OpenAI API key is required when AI generation is enabled.",
                code="configuration.missing_openai_api_key",
                scope="OpenAI adapter",
            )
        if not isinstance(model, str) or not model.strip():
            raise ConfigurationError(
                "OpenAI model identifier must be configured.",
                code="configuration.invalid_ai_model",
                scope="OpenAI adapter",
            )
        self._model = model.strip()
        self._client = OpenAI(api_key=api_key, max_retries=0) if client is None else client

    @property
    def model(self) -> str:
        return self._model

    def __repr__(self) -> str:
        return f"OpenAIBriefProvider(model={self._model!r})"

    def create_draft(self, brief_input: GroundedBriefInput) -> AIBriefDraft:
        payload = serialize_grounded_brief_input(brief_input)
        try:
            response = self._client.responses.create(
                model=self._model,
                reasoning={"effort": "low"},
                instructions=DAILY_BRIEF_INSTRUCTIONS,
                input=[
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": payload}],
                    }
                ],
                text={"format": _draft_format()},
                tools=[],
                store=False,
                stream=False,
                background=False,
            )
        except _UNAVAILABLE_EXCEPTIONS as exc:
            raise AIUnavailableError(
                "OpenAI Daily Brief generation is temporarily unavailable.",
                code="ai.openai_unavailable",
                scope="OpenAI Daily Brief",
            ) from exc
        except _AUTHENTICATION_EXCEPTIONS as exc:
            raise ConfigurationError(
                "OpenAI authentication configuration is invalid.",
                code="configuration.invalid_openai_authentication",
                scope="OpenAI adapter",
            ) from exc
        except _INVALID_RESPONSE_EXCEPTIONS as exc:
            raise _invalid_output() from exc

        output_text = _single_output_text(response)
        try:
            payload_value = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise _invalid_output() from exc
        return _draft_from_payload(payload_value)


__all__ = ("DEFAULT_OPENAI_MODEL", "OpenAIBriefProvider")

"""Offline contract tests for the concrete OpenAI Responses adapter."""

from datetime import UTC, datetime
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from app.ai.brief_input import serialize_grounded_brief_input
from app.ai.brief_generator import generate_daily_brief
from app.ai.contracts import BriefModelProvider
from app.ai.openai_provider import DEFAULT_OPENAI_MODEL, OpenAIBriefProvider
from app.ai.prompts import DAILY_BRIEF_INSTRUCTIONS
from app.core.errors import AIInvalidOutputError, AIUnavailableError, ConfigurationError
from app.domain.briefs import (
    AIBriefDraft,
    BriefGenerationStatus,
    GroundedBriefInput,
)


STAMP = datetime(2026, 9, 7, 9, tzinfo=UTC)
EMPTY_INPUT = GroundedBriefInput(STAMP, (), ())
VALID_PAYLOAD = {
    "summary": {"text": "Нет приоритетных действий.", "action_refs": [], "fact_refs": []},
    "items": [],
}


class FakeResponses:
    def __init__(self, response: object | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response: object | None = None, error: Exception | None = None):
        self.responses = FakeResponses(response, error)


def output_text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="output_text", text=text, annotations=[])


def refusal(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="refusal", refusal=text)


def assistant_message(*content: object) -> SimpleNamespace:
    return SimpleNamespace(
        type="message",
        role="assistant",
        status="completed",
        content=list(content),
    )


def response_with_text(text: str, *, status: str = "completed") -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        output=[assistant_message(output_text(text))],
    )


def completed(payload: object = VALID_PAYLOAD) -> SimpleNamespace:
    return response_with_text(json.dumps(payload))


def test_adapter_implements_existing_protocol_and_maps_strict_draft() -> None:
    client = FakeClient(completed())
    provider = OpenAIBriefProvider("test-secret", client=client)

    draft = provider.create_draft(EMPTY_INPUT)

    assert isinstance(provider, BriefModelProvider)
    assert isinstance(draft, AIBriefDraft)
    assert draft.summary.text == "Нет приоритетных действий."
    assert draft.items == ()


def test_default_sdk_client_is_configured_with_zero_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr("app.ai.openai_provider.OpenAI", fake_openai)

    provider = OpenAIBriefProvider("test-secret")

    assert captured == {"api_key": "test-secret", "max_retries": 0}
    assert provider.model == DEFAULT_OPENAI_MODEL


def test_responses_request_uses_only_approved_runtime_shape() -> None:
    client = FakeClient(completed())
    provider = OpenAIBriefProvider("do-not-leak", client=client)

    provider.create_draft(EMPTY_INPUT)

    assert len(client.responses.calls) == 1
    request = client.responses.calls[0]
    assert request["model"] == DEFAULT_OPENAI_MODEL == "gpt-5.6-terra"
    assert request["reasoning"] == {"effort": "low"}
    assert request["instructions"] == DAILY_BRIEF_INSTRUCTIONS
    assert request["input"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": serialize_grounded_brief_input(EMPTY_INPUT),
                }
            ],
        }
    ]
    assert request["tools"] == []
    assert request["store"] is False
    assert request["stream"] is False
    assert request["background"] is False
    assert "do-not-leak" not in repr(provider)
    assert "do-not-leak" not in repr(request)


def test_structured_output_schema_is_strict_and_has_no_status() -> None:
    client = FakeClient(completed())
    OpenAIBriefProvider("test-secret", client=client).create_draft(EMPTY_INPUT)

    output_format = client.responses.calls[0]["text"]["format"]  # type: ignore[index]
    schema = output_format["schema"]
    assert output_format["type"] == "json_schema"
    assert output_format["strict"] is True
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["summary", "items"]
    assert set(schema["properties"]) == {"summary", "items"}
    assert "status" not in json.dumps(schema)
    assert schema["properties"]["summary"]["additionalProperties"] is False
    assert schema["properties"]["items"]["items"]["additionalProperties"] is False


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/responses")


def _status_error(error_type: type[Exception], status: int) -> Exception:
    response = httpx.Response(status, request=_request())
    return error_type("provider details must remain private", response=response, body=None)


@pytest.mark.parametrize(
    "provider_error",
    [
        APIConnectionError(message="private connection detail", request=_request()),
        APITimeoutError(_request()),
        _status_error(RateLimitError, 429),
        _status_error(InternalServerError, 503),
    ],
)
def test_approved_availability_errors_map_to_safe_typed_error(
    provider_error: Exception,
) -> None:
    provider = OpenAIBriefProvider(
        "test-secret",
        client=FakeClient(error=provider_error),
    )

    with pytest.raises(AIUnavailableError) as error:
        provider.create_draft(EMPTY_INPUT)

    assert error.value.code == "ai.openai_unavailable"
    assert str(provider_error) not in str(error.value)
    assert str(provider_error) not in repr(error.value)


@pytest.mark.parametrize(
    "provider_error",
    [
        _status_error(AuthenticationError, 401),
        _status_error(PermissionDeniedError, 403),
    ],
)
def test_authentication_failures_map_to_configuration_error(
    provider_error: Exception,
) -> None:
    provider = OpenAIBriefProvider(
        "test-secret",
        client=FakeClient(error=provider_error),
    )

    with pytest.raises(ConfigurationError) as error:
        provider.create_draft(EMPTY_INPUT)

    assert error.value.code == "configuration.invalid_openai_authentication"
    assert str(provider_error) not in str(error.value)


def test_sdk_response_validation_failure_maps_to_invalid_output() -> None:
    response = httpx.Response(200, request=_request())
    provider = OpenAIBriefProvider(
        "test-secret",
        client=FakeClient(error=APIResponseValidationError(response, None)),
    )

    with pytest.raises(AIInvalidOutputError) as error:
        provider.create_draft(EMPTY_INPUT)
    assert error.value.code == "ai.invalid_provider_output"


@pytest.mark.parametrize(
    "status",
    ["failed", "incomplete", "cancelled", "queued", "in_progress"],
)
def test_noncompleted_responses_are_invalid(status: str) -> None:
    provider = OpenAIBriefProvider(
        "test-secret",
        client=FakeClient(response_with_text(json.dumps(VALID_PAYLOAD), status=status)),
    )

    with pytest.raises(AIInvalidOutputError) as error:
        provider.create_draft(EMPTY_INPUT)
    assert error.value.code == "ai.invalid_provider_output"


@pytest.mark.parametrize(
    "response",
    [
        response_with_text("not-json"),
        completed({"summary": {}, "items": []}),
        completed({**VALID_PAYLOAD, "status": "GENERATED"}),
        completed({"summary": VALID_PAYLOAD["summary"], "items": "bad"}),
        response_with_text("42"),
        response_with_text("[]"),
    ],
)
def test_unusable_completed_responses_are_invalid(
    response: object,
) -> None:
    provider = OpenAIBriefProvider("test-secret", client=FakeClient(response))

    with pytest.raises(AIInvalidOutputError) as error:
        provider.create_draft(EMPTY_INPUT)
    assert error.value.code == "ai.invalid_provider_output"


def test_refusal_is_invalid_not_unavailable() -> None:
    refused_response = SimpleNamespace(
        status="completed",
        output=[assistant_message(refusal("private refusal text"))],
    )
    provider = OpenAIBriefProvider("test-secret", client=FakeClient(refused_response))

    with pytest.raises(AIInvalidOutputError) as error:
        provider.create_draft(EMPTY_INPUT)
    assert error.value.code == "ai.invalid_provider_output"
    assert "private refusal text" not in repr(error.value)


def test_valid_text_plus_refusal_invalidates_the_entire_response() -> None:
    response = SimpleNamespace(
        status="completed",
        output=[
            assistant_message(
                output_text(json.dumps(VALID_PAYLOAD)),
                refusal("private refusal text"),
            )
        ],
    )
    provider = OpenAIBriefProvider("test-secret", client=FakeClient(response))

    with pytest.raises(AIInvalidOutputError) as error:
        provider.create_draft(EMPTY_INPUT)
    assert error.value.code == "ai.invalid_provider_output"
    assert "private refusal text" not in repr(error.value)

    result = generate_daily_brief(EMPTY_INPUT, provider)
    assert result.status is BriefGenerationStatus.INVALID
    assert result.brief is None


@pytest.mark.parametrize(
    "unexpected_item",
    [
        SimpleNamespace(type="function_call", name="unexpected", arguments="{}"),
        SimpleNamespace(type="future_output_type"),
    ],
)
def test_unexpected_output_item_invalidates_valid_text(
    unexpected_item: object,
) -> None:
    response = SimpleNamespace(
        status="completed",
        output=[
            assistant_message(output_text(json.dumps(VALID_PAYLOAD))),
            unexpected_item,
        ],
    )
    provider = OpenAIBriefProvider("test-secret", client=FakeClient(response))

    with pytest.raises(AIInvalidOutputError):
        provider.create_draft(EMPTY_INPUT)


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(output=completed().output),
        SimpleNamespace(status=None, output=completed().output),
        SimpleNamespace(status="completed"),
        SimpleNamespace(status="completed", output=None),
        SimpleNamespace(status="completed", output=[]),
        SimpleNamespace(status="completed", output=[assistant_message()]),
        SimpleNamespace(
            status="completed",
            output=[assistant_message(output_text(""))],
        ),
        SimpleNamespace(
            status="completed",
            output=[assistant_message(output_text("   "))],
        ),
    ],
)
def test_missing_or_empty_response_envelope_is_invalid(response: object) -> None:
    provider = OpenAIBriefProvider("test-secret", client=FakeClient(response))

    with pytest.raises(AIInvalidOutputError):
        provider.create_draft(EMPTY_INPUT)


def test_multiple_output_text_blocks_are_invalid() -> None:
    response = SimpleNamespace(
        status="completed",
        output=[
            assistant_message(
                output_text(json.dumps(VALID_PAYLOAD)),
                output_text(json.dumps(VALID_PAYLOAD)),
            )
        ],
    )
    provider = OpenAIBriefProvider("test-secret", client=FakeClient(response))

    with pytest.raises(AIInvalidOutputError):
        provider.create_draft(EMPTY_INPUT)


@pytest.mark.parametrize(
    "message",
    [
        SimpleNamespace(
            type="message",
            role="user",
            status="completed",
            content=[output_text(json.dumps(VALID_PAYLOAD))],
        ),
        SimpleNamespace(
            type="message",
            role="assistant",
            status="incomplete",
            content=[output_text(json.dumps(VALID_PAYLOAD))],
        ),
    ],
)
def test_nonapproved_message_shape_is_invalid(message: object) -> None:
    provider = OpenAIBriefProvider(
        "test-secret",
        client=FakeClient(SimpleNamespace(status="completed", output=[message])),
    )

    with pytest.raises(AIInvalidOutputError):
        provider.create_draft(EMPTY_INPUT)


@pytest.mark.parametrize(
    ("error_type", "status"),
    [
        (BadRequestError, 400),
        (NotFoundError, 404),
        (UnprocessableEntityError, 422),
    ],
)
def test_nonavailability_4xx_errors_propagate(
    error_type: type[Exception],
    status: int,
) -> None:
    provider_error = _status_error(error_type, status)
    provider = OpenAIBriefProvider(
        "test-secret",
        client=FakeClient(error=provider_error),
    )

    with pytest.raises(error_type) as error:
        provider.create_draft(EMPTY_INPUT)
    assert error.value is provider_error


def test_unexpected_provider_error_propagates_and_is_not_retried() -> None:
    client = FakeClient(error=RuntimeError("programming defect"))
    provider = OpenAIBriefProvider("test-secret", client=client)

    with pytest.raises(RuntimeError, match="programming defect"):
        provider.create_draft(EMPTY_INPUT)
    assert len(client.responses.calls) == 1


def test_adapter_rejects_missing_key_or_model_without_exposing_values() -> None:
    with pytest.raises(ConfigurationError) as missing:
        OpenAIBriefProvider(" ", client=FakeClient(completed()))
    assert missing.value.code == "configuration.missing_openai_api_key"

    with pytest.raises(ConfigurationError) as model:
        OpenAIBriefProvider("test-secret", model=" ", client=FakeClient(completed()))
    assert model.value.code == "configuration.invalid_ai_model"

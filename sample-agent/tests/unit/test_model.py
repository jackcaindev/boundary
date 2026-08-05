from __future__ import annotations

from dataclasses import dataclass
import json
from types import SimpleNamespace
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
import pytest

from sample_agent.model import (
    EXPECTED_TOOL,
    ModelConfigurationError,
    ModelFailureDiagnostic,
    ModelSelectionError,
    ModelSettings,
    OPENAI_TOOL_NAME,
    OpenAIModelAdapter,
)


@dataclass
class FakeResponses:
    output: list[object]
    request: dict[str, Any] | None = None
    error: Exception | None = None

    async def create(self, **kwargs: Any) -> object:
        self.request = kwargs
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output=self.output)


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/responses")


def _response(status: int, request_id: str) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"x-request-id": request_id},
        request=_request(),
    )


def _connection_error(cause: BaseException) -> APIConnectionError:
    error = APIConnectionError(request=_request(), message="provider detail")
    error.__cause__ = cause
    return error


@pytest.mark.asyncio
async def test_openai_adapter_accepts_one_strict_reviewed_call() -> None:
    responses = FakeResponses(
        [
            SimpleNamespace(
                type="function_call",
                name=OPENAI_TOOL_NAME,
                arguments=json.dumps({"query": "customer 42"}),
            )
        ]
    )
    adapter = OpenAIModelAdapter(
        client=SimpleNamespace(responses=responses),
        model="gpt-test",
        request_timeout_ms=2500,
    )

    selection = await adapter.select_tool("customer 42")

    assert selection.tool == EXPECTED_TOOL
    assert selection.arguments == {"query": "customer 42"}
    assert adapter.model_identity == "openai/gpt-test"
    assert responses.request is not None
    assert responses.request["tool_choice"] == {
        "type": "function",
        "name": OPENAI_TOOL_NAME,
    }
    assert responses.request["parallel_tool_calls"] is False
    assert responses.request["store"] is False
    assert responses.request["timeout"] == 2.5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output", "expected_category"),
    [
        ([], "provider_output_malformed"),
        ([SimpleNamespace(type="message")], "provider_output_malformed"),
        (
            [
                SimpleNamespace(
                    type="function_call",
                    name="unexpected",
                    arguments='{"query":"x"}',
                )
            ],
            "provider_output_malformed",
        ),
        (
            [
                SimpleNamespace(
                    type="function_call",
                    name=OPENAI_TOOL_NAME,
                    arguments='{"query":"x","retry_limit":99}',
                )
            ],
            "provider_arguments_invalid",
        ),
        (
            [
                SimpleNamespace(
                    type="function_call",
                    name=OPENAI_TOOL_NAME,
                    arguments="not-json",
                )
            ],
            "provider_arguments_invalid",
        ),
    ],
)
async def test_openai_adapter_rejects_untrusted_output(
    output: list[object],
    expected_category: str,
) -> None:
    adapter = OpenAIModelAdapter(
        client=SimpleNamespace(responses=FakeResponses(output)),
        model="gpt-test",
        request_timeout_ms=1000,
    )

    with pytest.raises(ModelSelectionError) as captured:
        await adapter.select_tool("x")

    assert captured.value.diagnostic.category == expected_category
    assert captured.value.diagnostic.exception_class is None
    assert captured.value.diagnostic.transport_exception_class is None
    assert captured.value.diagnostic.http_status is None
    assert captured.value.diagnostic.provider_request_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "error",
        "category",
        "exception_class",
        "transport_exception_class",
        "status",
        "request_id",
    ),
    [
        (
            APITimeoutError(_request()),
            "provider_timeout",
            "APITimeoutError",
            None,
            None,
            None,
        ),
        (
            APIConnectionError(request=_request(), message="transport failed"),
            "provider_transport",
            "APIConnectionError",
            None,
            None,
            None,
        ),
        (
            AuthenticationError(
                "unauthorized",
                response=_response(401, "req_auth-safe"),
                body=None,
            ),
            "provider_model_access",
            "AuthenticationError",
            None,
            401,
            "req_auth-safe",
        ),
        (
            PermissionDeniedError(
                "forbidden",
                response=_response(403, "req_permission-safe"),
                body=None,
            ),
            "provider_model_access",
            "PermissionDeniedError",
            None,
            403,
            "req_permission-safe",
        ),
        (
            NotFoundError(
                "not found",
                response=_response(404, "req_model-not-found"),
                body=None,
            ),
            "provider_model_access",
            "NotFoundError",
            None,
            404,
            "req_model-not-found",
        ),
        (
            BadRequestError(
                "bad request",
                response=_response(400, "req_bad-request"),
                body=None,
            ),
            "provider_http",
            "BadRequestError",
            None,
            400,
            "req_bad-request",
        ),
        (
            RateLimitError(
                "rate limited",
                response=_response(429, "unsafe request id"),
                body=None,
            ),
            "provider_http",
            "RateLimitError",
            None,
            429,
            None,
        ),
        (
            RuntimeError("raw provider detail must not escape"),
            "provider_unknown",
            None,
            None,
            None,
            None,
        ),
    ],
)
async def test_openai_adapter_records_only_allowlisted_provider_metadata(
    error: Exception,
    category: str,
    exception_class: str | None,
    transport_exception_class: str | None,
    status: int | None,
    request_id: str | None,
) -> None:
    adapter = OpenAIModelAdapter(
        client=SimpleNamespace(responses=FakeResponses([], error=error)),
        model="gpt-test",
        request_timeout_ms=1000,
    )

    with pytest.raises(ModelSelectionError) as captured:
        await adapter.select_tool("x")

    diagnostic = captured.value.diagnostic
    assert diagnostic.category == category
    assert diagnostic.exception_class == exception_class
    assert diagnostic.transport_exception_class == transport_exception_class
    assert diagnostic.http_status == status
    assert diagnostic.provider_request_id == request_id
    assert str(captured.value) == "model selection failed"
    assert "raw provider detail" not in diagnostic.operational_summary()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cause_type",
    [
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.ProxyError,
        httpx.RemoteProtocolError,
        httpx.LocalProtocolError,
    ],
)
async def test_openai_connection_error_records_allowlisted_direct_cause(
    cause_type: type[httpx.TransportError],
) -> None:
    cause = cause_type("raw transport detail", request=_request())
    adapter = OpenAIModelAdapter(
        client=SimpleNamespace(
            responses=FakeResponses([], error=_connection_error(cause))
        ),
        model="gpt-test",
        request_timeout_ms=1000,
    )

    with pytest.raises(ModelSelectionError) as captured:
        await adapter.select_tool("x")

    diagnostic = captured.value.diagnostic
    assert diagnostic.category == "provider_transport"
    assert diagnostic.transport_exception_class == cause_type.__name__
    assert "raw transport detail" not in diagnostic.operational_summary()


@pytest.mark.asyncio
async def test_openai_connection_error_discards_untrusted_direct_cause() -> None:
    adapter = OpenAIModelAdapter(
        client=SimpleNamespace(
            responses=FakeResponses(
                [],
                error=_connection_error(
                    RuntimeError("untrusted direct-cause detail")
                ),
            )
        ),
        model="gpt-test",
        request_timeout_ms=1000,
    )

    with pytest.raises(ModelSelectionError) as captured:
        await adapter.select_tool("x")

    diagnostic = captured.value.diagnostic
    assert diagnostic.category == "provider_transport"
    assert diagnostic.transport_exception_class is None
    assert "untrusted direct-cause detail" not in diagnostic.operational_summary()


@pytest.mark.asyncio
async def test_openai_timeout_does_not_become_transport_failure() -> None:
    error = APITimeoutError(_request())
    error.__cause__ = httpx.ReadError("raw timeout detail", request=_request())
    adapter = OpenAIModelAdapter(
        client=SimpleNamespace(responses=FakeResponses([], error=error)),
        model="gpt-test",
        request_timeout_ms=1000,
    )

    with pytest.raises(ModelSelectionError) as captured:
        await adapter.select_tool("x")

    diagnostic = captured.value.diagnostic
    assert diagnostic.category == "provider_timeout"
    assert diagnostic.transport_exception_class is None


@pytest.mark.parametrize(
    "diagnostic",
    [
        {
            "category": "provider_http",
            "exception_class": "UntrustedProviderError",
        },
        {
            "category": "provider_transport",
            "transport_exception_class": "UntrustedTransportError",
        },
        {
            "category": "provider_http",
            "provider_request_id": "sk-not-a-request-id",
        },
        {"category": "provider_http", "http_status": 999},
    ],
)
def test_model_failure_diagnostic_rejects_unbounded_metadata(
    diagnostic: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ModelFailureDiagnostic(**diagnostic)  # type: ignore[arg-type]


def test_model_settings_default_to_fake_without_a_secret() -> None:
    settings = ModelSettings.from_environment({})

    assert settings.mode == "fake"
    assert settings.api_key is None
    assert settings.openai_model == "gpt-5.6-luna"


def test_openai_mode_requires_secret_configuration() -> None:
    with pytest.raises(ModelConfigurationError):
        ModelSettings.from_environment({"MODEL_MODE": "openai"})


def test_model_settings_reject_unbounded_timeout() -> None:
    with pytest.raises(ModelConfigurationError):
        ModelSettings.from_environment(
            {
                "MODEL_MODE": "openai",
                "OPENAI_API_KEY": "not-a-real-secret",
                "MODEL_REQUEST_TIMEOUT_MS": "10001",
            }
        )

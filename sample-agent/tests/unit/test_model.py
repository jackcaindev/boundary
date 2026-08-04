from __future__ import annotations

from dataclasses import dataclass
import json
from types import SimpleNamespace
from typing import Any

import pytest

from sample_agent.model import (
    EXPECTED_TOOL,
    ModelConfigurationError,
    ModelSelectionError,
    ModelSettings,
    OPENAI_TOOL_NAME,
    OpenAIModelAdapter,
)


@dataclass
class FakeResponses:
    output: list[object]
    request: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> object:
        self.request = kwargs
        return SimpleNamespace(output=self.output)


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
    "output",
    [
        [],
        [SimpleNamespace(type="message")],
        [
            SimpleNamespace(
                type="function_call",
                name="unexpected",
                arguments='{"query":"x"}',
            )
        ],
        [
            SimpleNamespace(
                type="function_call",
                name=OPENAI_TOOL_NAME,
                arguments='{"query":"x","retry_limit":99}',
            )
        ],
        [
            SimpleNamespace(
                type="function_call",
                name=OPENAI_TOOL_NAME,
                arguments="not-json",
            )
        ],
    ],
)
async def test_openai_adapter_rejects_untrusted_output(
    output: list[object],
) -> None:
    adapter = OpenAIModelAdapter(
        client=SimpleNamespace(responses=FakeResponses(output)),
        model="gpt-test",
        request_timeout_ms=1000,
    )

    with pytest.raises(ModelSelectionError):
        await adapter.select_tool("x")


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

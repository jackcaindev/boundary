from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from sample_agent.tool_client import (
    InvalidToolResponse,
    Phase1ToolClient,
    ToolClientTransportError,
)
from sample_agent.tool_contract_v1 import (
    LookupResponse,
    LookupResult,
)


@pytest.mark.asyncio
async def test_lookup_sends_bearer_and_validates_deterministic_result() -> None:
    run_id = uuid4()
    trace_id = uuid4()
    tool_call_id = uuid4()
    secret = "private-" + ("x" * 48)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {secret}"
        return httpx.Response(
            200,
            json=LookupResponse(
                contract_version="1",
                run_id=run_id,
                trace_id=trace_id,
                tool_identity="boundary.phase1.lookup",
                tool_call_id=tool_call_id,
                retry_ordinal=0,
                result=LookupResult(
                    status="found",
                    value="control-ok",
                ),
            ).model_dump(mode="json"),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = Phase1ToolClient(client=http_client)
        response = await client.lookup(
            endpoint="http://boundary/internal/tool",
            capability=secret,
            run_id=run_id,
            trace_id=trace_id,
            fault_id=None,
            arguments={"query": "control"},
            tool_call_id=tool_call_id,
        )

    assert response.retry_ordinal == 0
    assert response.result.value == "control-ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["remote", "invalid"])
async def test_tool_failures_do_not_expose_capability(kind: str) -> None:
    run_id = uuid4()
    trace_id = uuid4()
    secret = "private-" + ("s" * 48)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        if kind == "remote":
            return httpx.Response(401, text=secret)
        return httpx.Response(200, text=secret)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = Phase1ToolClient(client=http_client)
        expected = (
            ToolClientTransportError
            if kind == "remote"
            else InvalidToolResponse
        )
        with pytest.raises(expected) as raised:
            await client.lookup(
                endpoint="http://boundary/internal/tool",
                capability=secret,
                run_id=run_id,
                trace_id=trace_id,
                fault_id=None,
                arguments={"query": "control"},
            )

    assert secret not in str(raised.value)
    assert secret not in repr(raised.value)

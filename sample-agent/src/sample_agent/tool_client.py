"""Narrow HTTP client for `boundary.phase1.lookup`."""

from __future__ import annotations

from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError

from sample_agent.tool_contract_v1 import (
    CONTRACT_VERSION,
    TOOL_IDENTITY,
    LookupArguments,
    LookupRequest,
    LookupResponse,
)


TOOL_CLIENT_TIMEOUT_SECONDS = 0.5
MAX_TOOL_RESPONSE_BYTES = 16 * 1024


class ToolClientError(Exception):
    """Safe tool-client failure that never contains credential material."""


class ToolClientTimeout(ToolClientError):
    pass


class ToolClientTransportError(ToolClientError):
    pass


class InvalidToolResponse(ToolClientError):
    pass


class Phase1ToolClient:
    """Call only the one Phase 1 lookup endpoint."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = TOOL_CLIENT_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive")
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def lookup(
        self,
        *,
        endpoint: str,
        capability: str,
        run_id: UUID,
        trace_id: UUID,
        fault_id: UUID | None,
        arguments: dict[str, str],
        tool_call_id: UUID | None = None,
    ) -> LookupResponse:
        call_id = tool_call_id or uuid4()
        request = LookupRequest(
            contract_version=CONTRACT_VERSION,
            run_id=run_id,
            trace_id=trace_id,
            tool_identity=TOOL_IDENTITY,
            tool_call_id=call_id,
            fault_id=fault_id,
            arguments=LookupArguments.model_validate(arguments),
        )
        headers = {"Authorization": f"Bearer {capability}"}
        try:
            if self._client is None:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                ) as client:
                    response = await client.post(
                        endpoint,
                        headers=headers,
                        content=request.model_dump_json(),
                    )
            else:
                response = await self._client.post(
                    endpoint,
                    headers=headers,
                    content=request.model_dump_json(),
                    timeout=self._timeout_seconds,
                )
        except httpx.TimeoutException:
            raise ToolClientTimeout("Boundary tool request timed out") from None
        except httpx.TransportError:
            raise ToolClientTransportError(
                "Boundary tool transport failed"
            ) from None

        if response.status_code != 200:
            raise ToolClientTransportError(
                "Boundary tool rejected the request"
            )
        if len(response.content) > MAX_TOOL_RESPONSE_BYTES:
            raise InvalidToolResponse(
                "Boundary tool response exceeded the size limit"
            )
        try:
            parsed = LookupResponse.model_validate_json(response.content)
        except ValidationError:
            raise InvalidToolResponse(
                "Boundary tool response failed validation"
            ) from None
        if (
            parsed.run_id != run_id
            or parsed.trace_id != trace_id
            or parsed.tool_identity != TOOL_IDENTITY
            or parsed.tool_call_id != call_id
        ):
            raise InvalidToolResponse(
                "Boundary tool response identity mismatch"
            )
        return parsed

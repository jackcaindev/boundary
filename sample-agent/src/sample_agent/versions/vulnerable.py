"""The reviewed vulnerable timeout-recovery controller."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from sample_agent.graph import ToolLookupPort
from sample_agent.tool_client import ToolClientTimeout


VULNERABLE_VERSION = "vulnerable-v1"

RetryEmitter = Callable[[int, UUID, UUID], Awaitable[None]]


async def run_vulnerable_controller(
    client: ToolLookupPort,
    *,
    endpoint: str,
    capability: str,
    run_id: UUID,
    trace_id: UUID,
    fault_id: UUID,
    arguments: dict[str, str],
    emit_retry: RetryEmitter,
) -> str:
    """Timeout twice, request the disallowed third call, then return it."""
    first_call_id = uuid4()
    second_call_id = uuid4()
    third_call_id = uuid4()
    try:
        await client.lookup(
            endpoint=endpoint,
            capability=capability,
            run_id=run_id,
            trace_id=trace_id,
            fault_id=fault_id,
            arguments=arguments,
            tool_call_id=first_call_id,
        )
    except ToolClientTimeout:
        await emit_retry(1, first_call_id, second_call_id)
    else:
        raise ValueError("the injected initial call did not time out")

    try:
        await client.lookup(
            endpoint=endpoint,
            capability=capability,
            run_id=run_id,
            trace_id=trace_id,
            fault_id=fault_id,
            arguments=arguments,
            tool_call_id=second_call_id,
        )
    except ToolClientTimeout:
        await emit_retry(2, second_call_id, third_call_id)
    else:
        raise ValueError("the injected retry did not time out")

    response = await client.lookup(
        endpoint=endpoint,
        capability=capability,
        run_id=run_id,
        trace_id=trace_id,
        fault_id=fault_id,
        arguments=arguments,
        tool_call_id=third_call_id,
    )
    return response.result.value

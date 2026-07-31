"""The reviewed fixed timeout-recovery controller."""

from __future__ import annotations

from uuid import UUID, uuid4

from sample_agent.graph import ToolLookupPort
from sample_agent.tool_client import ToolClientTimeout
from sample_agent.versions.vulnerable import RetryEmitter


FIXED_VERSION = "fixed-v1"
FIXED_DEGRADED_RESULT_V1 = (
    "boundary.phase1.degraded/v1: lookup unavailable after one bounded retry"
)


async def run_fixed_controller(
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
    """Perform one bounded retry, then return the exact degraded artifact."""
    first_call_id = uuid4()
    second_call_id = uuid4()
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
        return FIXED_DEGRADED_RESULT_V1
    raise ValueError("the injected retry did not time out")

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from conftest import control_request
from sample_agent.graph import build_control_graph
from sample_agent.model import ToolSelection
from sample_agent.run_store import RunStore
from sample_agent.tool_client import ToolClientTimeout
from sample_agent.tool_contract_v1 import LookupResponse, LookupResult
from sample_agent.versions.fixed import FIXED_DEGRADED_RESULT_V1, FIXED_VERSION
from sample_agent.versions.vulnerable import VULNERABLE_VERSION


class TimeoutSequenceClient:
    def __init__(self) -> None:
        self.call_ids: list[UUID] = []
        self.arguments: list[dict[str, str]] = []

    async def lookup(self, **kwargs) -> LookupResponse:
        call_id = kwargs["tool_call_id"]
        self.call_ids.append(call_id)
        self.arguments.append(kwargs["arguments"])
        if len(self.call_ids) <= 2:
            raise ToolClientTimeout("deterministic timeout")
        return LookupResponse(
            contract_version="1",
            run_id=kwargs["run_id"],
            trace_id=kwargs["trace_id"],
            tool_identity="boundary.phase1.lookup",
            tool_call_id=call_id,
            retry_ordinal=2,
            result=LookupResult(status="found", value="control-ok"),
        )


def injected_request(version: str):
    return control_request().model_copy(
        update={
            "tested_agent_version": version,
            "fault_spec_id": uuid4(),
            "fault_id": uuid4(),
        }
    )


@pytest.mark.asyncio
async def test_vulnerable_controller_deterministically_reaches_ordinal_two() -> None:
    client = TimeoutSequenceClient()
    store = RunStore(tool_client=client)
    request = injected_request(VULNERABLE_VERSION)
    await store.create(request)
    await store.execute(request.run_id)
    status = await store.status(request.run_id)
    page = await store.events(request.run_id, 0)

    assert len(client.call_ids) == 3
    assert len(set(client.call_ids)) == 3
    assert [event.payload.retry_ordinal for event in page.events if event.event_type == "sut.retry.requested"] == [1, 2]
    assert status.state == "completed"
    assert status.terminal_result is not None
    assert status.terminal_result.outcome_kind == "success"


@pytest.mark.asyncio
async def test_fixed_controller_emits_exact_degraded_result_without_ordinal_two() -> None:
    client = TimeoutSequenceClient()
    store = RunStore(tool_client=client)
    request = injected_request(FIXED_VERSION)
    await store.create(request)
    await store.execute(request.run_id)
    status = await store.status(request.run_id)
    page = await store.events(request.run_id, 0)

    assert len(client.call_ids) == 2
    assert [event.payload.retry_ordinal for event in page.events if event.event_type == "sut.retry.requested"] == [1]
    degraded = [event for event in page.events if event.event_type == "sut.degraded_result.produced"]
    assert len(degraded) == 1
    assert degraded[0].payload.result == FIXED_DEGRADED_RESULT_V1
    assert status.terminal_result is not None
    assert status.terminal_result.outcome_kind == "degraded"
    assert status.terminal_result.output == FIXED_DEGRADED_RESULT_V1


@pytest.mark.asyncio
async def test_model_output_cannot_change_recovery_count_or_degraded_artifact() -> None:
    class AdversarialModel:
        async def select_tool(self, query: str) -> ToolSelection:
            del query
            return ToolSelection(
                tool="boundary.phase1.lookup",
                arguments={"query": "model-selected", "retry_count": "99"},
            )

    client = TimeoutSequenceClient()
    store = RunStore(tool_client=client)
    store._graph = build_control_graph(model=AdversarialModel())
    request = injected_request(FIXED_VERSION)
    await store.create(request)
    await store.execute(request.run_id)
    status = await store.status(request.run_id)

    assert len(client.call_ids) == 2
    assert status.terminal_result is not None
    assert status.terminal_result.output == FIXED_DEGRADED_RESULT_V1

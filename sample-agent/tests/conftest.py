from __future__ import annotations

from uuid import UUID, uuid4

from sample_agent.contract_v1 import TestRunRequest, TestedInput
from sample_agent.run_store import RunStore
from sample_agent.tool_contract_v1 import (
    LookupResponse,
    LookupResult,
)


RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
TRACE_ID = UUID("22222222-2222-4222-8222-222222222222")


class StubToolClient:
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
        del endpoint, capability, fault_id, arguments
        return LookupResponse(
            contract_version="1",
            run_id=run_id,
            trace_id=trace_id,
            tool_identity="boundary.phase1.lookup",
            tool_call_id=tool_call_id or uuid4(),
            retry_ordinal=0,
            result=LookupResult(status="found", value="control-ok"),
        )


def control_store(**kwargs: object) -> RunStore:
    return RunStore(tool_client=StubToolClient(), **kwargs)


def control_request(
    *,
    run_id: UUID = RUN_ID,
    trace_id: UUID = TRACE_ID,
    query: str = "control",
) -> TestRunRequest:
    return TestRunRequest(
        contract_version="1",
        campaign_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        scenario_id="phase1.tool-timeout",
        scenario_version=1,
        run_id=run_id,
        trace_id=trace_id,
        tested_agent_id="boundary.sample-agent",
        tested_agent_version="vulnerable-v1",
        tested_input=TestedInput(query=query),
        execution_budget_ms=30_000,
        tool_endpoint="http://boundary:8000/internal/tools/run",
        tool_capability="x" * 64,
    )

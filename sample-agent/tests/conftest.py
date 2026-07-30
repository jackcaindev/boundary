from __future__ import annotations

from uuid import UUID

from sample_agent.contract_v1 import TestRunRequest, TestedInput


RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
TRACE_ID = UUID("22222222-2222-4222-8222-222222222222")


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

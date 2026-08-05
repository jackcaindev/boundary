from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
from openai import APIConnectionError
import pytest

from conftest import RUN_ID, control_request, control_store
from sample_agent.contract_v1 import (
    DegradedResultEvent,
    DegradedResultPayload,
    RunStartedEvent,
    StartedPayload,
)
from sample_agent.run_store import (
    MAX_RETAINED_RUNS,
    InvalidTargetEvent,
    PayloadLimitExceeded,
    StoreCapacityExceeded,
)
from sample_agent.model import (
    ModelFailureDiagnostic,
    ModelSelectionError,
    OpenAIModelAdapter,
)


def _started(sequence: int) -> RunStartedEvent:
    request = control_request()
    return RunStartedEvent(
        contract_version="1",
        run_id=request.run_id,
        trace_id=request.trace_id,
        event_id=uuid4(),
        source="sut",
        event_type="sut.run.started",
        boundary="run",
        producer_seq=sequence,
        payload=StartedPayload(schema_version=1),
    )


@pytest.mark.asyncio
async def test_contiguous_sequences_start_at_one_and_seal() -> None:
    store = control_store()
    await store.create(control_request())
    await store.execute_control(RUN_ID)

    page = await store.events(RUN_ID, 0)
    status = await store.status(RUN_ID)
    assert [event.producer_seq for event in page.events] == [1, 2]
    assert status.final_producer_seq == 2
    with pytest.raises(InvalidTargetEvent):
        await store.append_event(RUN_ID, _started(3))


@pytest.mark.asyncio
async def test_per_event_limit_is_enforced() -> None:
    store = control_store()
    request = control_request()
    await store.create(request)
    event = DegradedResultEvent(
        contract_version="1",
        run_id=request.run_id,
        trace_id=request.trace_id,
        event_id=uuid4(),
        source="sut",
        event_type="sut.degraded_result.produced",
        boundary="agent",
        producer_seq=1,
        payload=DegradedResultPayload(
            schema_version=1,
            result="x" * 65_536,
        ),
    )
    with pytest.raises(PayloadLimitExceeded):
        await store.append_event(RUN_ID, event)


@pytest.mark.asyncio
async def test_event_count_limit_is_enforced() -> None:
    store = control_store()
    await store.create(control_request())
    for sequence in range(1, 257):
        await store.append_event(RUN_ID, _started(sequence))
    with pytest.raises(PayloadLimitExceeded):
        await store.append_event(RUN_ID, _started(257))


@pytest.mark.asyncio
async def test_total_event_bytes_limit_is_enforced() -> None:
    store = control_store()
    request = control_request()
    await store.create(request)
    rejected = False
    for sequence in range(1, 30):
        event = DegradedResultEvent(
            contract_version="1",
            run_id=request.run_id,
            trace_id=request.trace_id,
            event_id=uuid4(),
            source="sut",
            event_type="sut.degraded_result.produced",
            boundary="agent",
            producer_seq=sequence,
            payload=DegradedResultPayload(
                schema_version=1,
                result="x" * 60_000,
            ),
        )
        try:
            await store.append_event(RUN_ID, event)
        except PayloadLimitExceeded:
            rejected = True
            break
    assert rejected


@pytest.mark.asyncio
async def test_terminal_output_limit_is_enforced() -> None:
    store = control_store()
    await store.create(control_request())

    class OversizedGraph:
        async def ainvoke(self, state):
            del state
            return {"output": "x" * 70_000}

    store._graph = OversizedGraph()
    with pytest.raises(PayloadLimitExceeded):
        await store.execute_control(RUN_ID)


@pytest.mark.asyncio
async def test_process_local_run_retention_is_bounded() -> None:
    store = control_store(max_runs=1)
    await store.create(control_request())
    with pytest.raises(StoreCapacityExceeded):
        await store.create(
            control_request(run_id=uuid4(), trace_id=uuid4())
        )

    assert MAX_RETAINED_RUNS == 128


@pytest.mark.asyncio
async def test_provider_failure_is_a_safe_bounded_terminal_state(caplog) -> None:
    class FailingModel:
        model_identity = "openai/gpt-test"

        async def select_tool(self, query: str):
            del query
            raise ModelSelectionError(
                ModelFailureDiagnostic(
                    category="provider_http",
                    exception_class="BadRequestError",
                    transport_exception_class=None,
                    http_status=400,
                    provider_request_id="req_safe-123",
                )
            )

    store = control_store(model=FailingModel())
    await store.create(control_request())

    await store.execute(RUN_ID)

    status = await store.status(RUN_ID)
    assert status.state == "failed"
    assert status.error_summary == (
        "Model selection failed; category=provider_http; "
        "exception_class=BadRequestError; http_status=400; "
        "provider_request_id=req_safe-123"
    )
    assert len(status.error_summary) <= 512
    assert status.terminal_result is not None
    assert status.terminal_result.output is None
    page = await store.events(RUN_ID, 0)
    assert page.events[-1].payload.error_code == "MODEL_SELECTION_FAILED"
    assert "category=provider_http" in caplog.text
    assert "exception_class=BadRequestError" in caplog.text
    assert "http_status=400" in caplog.text
    assert "provider_request_id=req_safe-123" in caplog.text


@pytest.mark.asyncio
async def test_provider_raw_details_never_reach_status_events_or_logs(caplog) -> None:
    class FailingResponses:
        async def create(self, **kwargs):
            del kwargs
            nested = RuntimeError("sensitive-nested-message")
            transport = httpx.ConnectError(
                "sensitive-transport-message",
                request=httpx.Request(
                    "POST", "https://api.openai.com/v1/responses"
                ),
            )
            transport.__cause__ = nested
            provider = APIConnectionError(
                request=transport.request,
                message="sensitive-provider-message",
            )
            provider.__cause__ = transport
            raise provider

    model = OpenAIModelAdapter(
        client=SimpleNamespace(responses=FailingResponses()),
        model="gpt-test",
        request_timeout_ms=1000,
    )
    store = control_store(model=model)
    await store.create(control_request(query="sensitive-tested-input"))

    await store.execute(RUN_ID)

    status = await store.status(RUN_ID)
    page = await store.events(RUN_ID, 0)
    assert status.error_summary == (
        "Model selection failed; category=provider_transport; "
        "exception_class=APIConnectionError; "
        "transport_exception_class=ConnectError"
    )
    assert page.events[-1].payload.error_code == "MODEL_SELECTION_FAILED"
    retained_text = f"{status.model_dump_json()} {page.model_dump_json()} {caplog.text}"
    assert "sensitive-provider-message" not in retained_text
    assert "sensitive-transport-message" not in retained_text
    assert "sensitive-nested-message" not in retained_text
    assert "sensitive-tested-input" not in retained_text

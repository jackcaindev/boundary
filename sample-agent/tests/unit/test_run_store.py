from __future__ import annotations

from uuid import uuid4

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

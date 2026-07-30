from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from conftest import RUN_ID, TRACE_ID, control_request
from sample_agent.contract_v1 import (
    CONTRACT_VERSION_HEADER,
    CONTRACT_VERSIONS_HEADER,
    CancellationAcknowledgement,
    CancellationRequest,
    ProblemResponse,
    RunStatus,
)
from sample_agent.main import create_app
from sample_agent.run_store import RunStore


@pytest_asyncio.fixture
async def manual_client():
    app = create_app(store=RunStore(), auto_execute=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://sample-agent",
    ) as client:
        yield app, client


async def _create(client: httpx.AsyncClient, request=None):
    selected = request or control_request()
    return await client.post(
        "/test-runs",
        headers={CONTRACT_VERSIONS_HEADER: "1"},
        content=selected.model_dump_json(exclude_none=True),
    )


@pytest.mark.asyncio
async def test_version_selection_and_unsupported_version(
    manual_client,
) -> None:
    _, client = manual_client
    unsupported = await client.post(
        "/test-runs",
        headers={CONTRACT_VERSIONS_HEADER: "2, 1"},
        content=control_request().model_dump_json(exclude_none=True),
    )
    problem = ProblemResponse.model_validate(unsupported.json())

    assert unsupported.status_code == 406
    assert problem.error.code == "UNSUPPORTED_CONTRACT_VERSION"
    assert problem.error.supported_versions == ["1"]

    accepted = await _create(client)
    assert accepted.status_code == 202
    assert accepted.headers[CONTRACT_VERSION_HEADER] == "1"


@pytest.mark.asyncio
async def test_creation_is_idempotent_and_conflicting_reuse_fails(
    manual_client,
) -> None:
    _, client = manual_client
    first = await _create(client)
    second = await _create(client)
    conflict = await _create(
        client,
        control_request(query="different"),
    )

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert conflict.status_code == 409
    assert ProblemResponse.model_validate_json(
        conflict.content
    ).error.code == "RUN_CONFLICT"


@pytest.mark.asyncio
async def test_unknown_request_field_is_bounded_invalid_request(
    manual_client,
) -> None:
    _, client = manual_client
    document = control_request().model_dump(mode="json")
    document["unknown"] = "rejected"
    response = await client.post(
        "/test-runs",
        headers={CONTRACT_VERSIONS_HEADER: "1"},
        json=document,
    )

    assert response.status_code == 422
    problem = ProblemResponse.model_validate(response.json())
    assert problem.error.code == "INVALID_REQUEST"
    assert "unknown" not in problem.error.message

    invalid_path = await client.get("/test-runs/not-a-uuid")
    invalid_query = await client.get(
        f"/test-runs/{RUN_ID}/events?after_producer_seq=-1"
    )
    assert ProblemResponse.model_validate_json(
        invalid_path.content
    ).error.code == "INVALID_REQUEST"
    assert ProblemResponse.model_validate_json(
        invalid_query.content
    ).error.code == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_cancellation_before_start_and_duplicate_is_idempotent(
    manual_client,
) -> None:
    _, client = manual_client
    await _create(client)
    cancellation_id = uuid4()
    request = CancellationRequest(
        contract_version="1",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        cancellation_id=cancellation_id,
    )

    first = await client.post(
        f"/test-runs/{RUN_ID}/cancel",
        content=request.model_dump_json(),
    )
    duplicate = await client.post(
        f"/test-runs/{RUN_ID}/cancel",
        content=request.model_dump_json(),
    )
    acknowledgement = CancellationAcknowledgement.model_validate_json(
        first.content
    )

    assert first.json() == duplicate.json()
    assert acknowledgement.cancellation_applied is True
    assert acknowledgement.status.state == "cancelled"
    assert acknowledgement.status.final_producer_seq == 1


@pytest.mark.asyncio
async def test_cancellation_while_running_stays_cancelled(
    manual_client,
) -> None:
    app, client = manual_client
    await _create(client)

    class BlockingGraph:
        def __init__(self) -> None:
            self.release = asyncio.Event()

        async def ainvoke(self, state):
            await self.release.wait()
            return {"output": "control-ok"}

    graph = BlockingGraph()
    app.state.run_store._graph = graph
    task = asyncio.create_task(
        app.state.run_store.execute_control(RUN_ID)
    )
    while (
        await app.state.run_store.status(RUN_ID)
    ).state != "running":
        await asyncio.sleep(0)
    request = CancellationRequest(
        contract_version="1",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        cancellation_id=uuid4(),
    )
    response = await client.post(
        f"/test-runs/{RUN_ID}/cancel",
        content=request.model_dump_json(),
    )
    graph.release.set()
    await task

    acknowledgement = CancellationAcknowledgement.model_validate_json(
        response.content
    )
    status = RunStatus.model_validate_json(
        (await client.get(f"/test-runs/{RUN_ID}")).content
    )
    assert acknowledgement.cancellation_applied is True
    assert status.state == "cancelled"
    assert status.final_producer_seq == 2


@pytest.mark.asyncio
async def test_cancellation_after_completion_does_not_rewrite_terminal(
    manual_client,
) -> None:
    app, client = manual_client
    await _create(client)
    await app.state.run_store.execute_control(RUN_ID)
    before = RunStatus.model_validate_json(
        (await client.get(f"/test-runs/{RUN_ID}")).content
    )
    request = CancellationRequest(
        contract_version="1",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        cancellation_id=uuid4(),
    )
    response = await client.post(
        f"/test-runs/{RUN_ID}/cancel",
        content=request.model_dump_json(),
    )
    acknowledgement = CancellationAcknowledgement.model_validate_json(
        response.content
    )

    assert acknowledgement.cancellation_applied is False
    assert acknowledgement.status == before

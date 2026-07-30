from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from boundary.sut.client import InvalidWireResponse, SutClient
from boundary.sut.contract_v1 import (
    CONTRACT_VERSION_HEADER,
    CancellationAcknowledgement,
    EventPage,
    RunStatus,
    TestRunRequest as WireTestRunRequest,
    TestedInput as WireTestedInput,
)


pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).parents[3] / "contract-fixtures" / "v1"
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
TRACE_ID = UUID("22222222-2222-4222-8222-222222222222")


def _request() -> WireTestRunRequest:
    return WireTestRunRequest(
        contract_version="1",
        campaign_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        scenario_id="phase1.tool-timeout",
        scenario_version=1,
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        tested_agent_id="boundary.sample-agent",
        tested_agent_version="vulnerable-v1",
        tested_input=WireTestedInput(query="control"),
        execution_budget_ms=30_000,
        tool_endpoint="http://boundary:8000/internal/tools/run",
        tool_capability="x" * 64,
    )


def test_shared_contract_fixtures_validate_strictly() -> None:
    page = EventPage.model_validate_json(
        (FIXTURES / "control-terminal-events.json").read_bytes()
    )
    status = RunStatus.model_validate_json(
        (FIXTURES / "control-terminal-status.json").read_bytes()
    )

    assert [event.producer_seq for event in page.events] == [1, 2]
    assert status.terminal_result is not None
    assert status.terminal_result.event_id == page.events[-1].event_id


@pytest.mark.parametrize(
    ("model", "document"),
    [
        (
            WireTestRunRequest,
            {
                **_request().model_dump(mode="json"),
                "unknown": "rejected",
            },
        ),
        (
            EventPage,
            {
                **json.loads(
                    (
                        FIXTURES / "control-terminal-events.json"
                    ).read_bytes()
                ),
                "unknown": "rejected",
            },
        ),
        (
            RunStatus,
            {
                **json.loads(
                    (
                        FIXTURES / "control-terminal-status.json"
                    ).read_bytes()
                ),
                "unknown": "rejected",
            },
        ),
    ],
)
def test_unknown_fields_are_rejected(model, document) -> None:
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(document))


@pytest.mark.parametrize(
    ("mutation",),
    [
        (lambda page: page["events"][0].update(
            {"event_type": "boundary.run.accepted"}
        ),),
        (lambda page: page["events"][0].update(
            {"source": "boundary"}
        ),),
        (lambda page: page["events"][0]["payload"].update(
            {"unknown": True}
        ),),
        (lambda page: page["events"][0].update(
            {"producer_seq": 1.5}
        ),),
    ],
)
def test_unknown_authority_and_malformed_events_are_rejected(
    mutation,
) -> None:
    document = json.loads(
        (FIXTURES / "control-terminal-events.json").read_bytes()
    )
    mutation(document)
    with pytest.raises(ValidationError):
        EventPage.model_validate_json(json.dumps(document))


def test_cancellation_acknowledgement_rejects_nested_identity_mismatch(
) -> None:
    status = json.loads(
        (FIXTURES / "control-terminal-status.json").read_bytes()
    )
    acknowledgement = {
        "contract_version": "1",
        "run_id": str(RUN_ID),
        "trace_id": str(TRACE_ID),
        "cancellation_id": "33333333-3333-4333-8333-333333333333",
        "cancellation_applied": True,
        "status": {
            **status,
            "run_id": "44444444-4444-4444-8444-444444444444",
            "terminal_result": {
                **status["terminal_result"],
                "run_id": "44444444-4444-4444-8444-444444444444",
            },
        },
    }

    with pytest.raises(ValidationError):
        CancellationAcknowledgement.model_validate(acknowledgement)


@pytest.mark.asyncio
async def test_client_rejects_contract_header_mismatch() -> None:
    accepted = {
        "contract_version": "1",
        "run_id": str(RUN_ID),
        "trace_id": str(TRACE_ID),
        "tested_agent_id": "boundary.sample-agent",
        "tested_agent_version": "vulnerable-v1",
        "state": "accepted",
        "status_url": f"/test-runs/{RUN_ID}",
        "events_url": f"/test-runs/{RUN_ID}/events",
        "cancellation_url": f"/test-runs/{RUN_ID}/cancel",
        "producer_high_watermark": 0,
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            202,
            json=accepted,
            headers={CONTRACT_VERSION_HEADER: "2"},
        )

    http_client = httpx.AsyncClient(
        base_url="http://target",
        transport=httpx.MockTransport(handler),
    )
    client = SutClient(
        "http://target",
        timeout_seconds=1,
        client=http_client,
    )
    with pytest.raises(InvalidWireResponse):
        await client.create_run(_request())
    await http_client.aclose()


@pytest.mark.asyncio
async def test_client_rejects_unknown_response_field() -> None:
    document = json.loads(
        (FIXTURES / "control-terminal-status.json").read_bytes()
    )
    document["verdict"] = "PASS"

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json=document,
            headers={CONTRACT_VERSION_HEADER: "1"},
        )

    http_client = httpx.AsyncClient(
        base_url="http://target",
        transport=httpx.MockTransport(handler),
    )
    client = SutClient(
        "http://target",
        timeout_seconds=1,
        client=http_client,
    )
    with pytest.raises(InvalidWireResponse):
        await client.get_status(RUN_ID)
    await http_client.aclose()


@pytest.mark.asyncio
async def test_client_rejects_terminal_output_above_64_kib() -> None:
    document = json.loads(
        (FIXTURES / "control-terminal-status.json").read_bytes()
    )
    document["terminal_result"]["output"] = "x" * 65_537

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json=document,
            headers={CONTRACT_VERSION_HEADER: "1"},
        )

    http_client = httpx.AsyncClient(
        base_url="http://target",
        transport=httpx.MockTransport(handler),
    )
    client = SutClient(
        "http://target",
        timeout_seconds=1,
        client=http_client,
    )
    with pytest.raises(InvalidWireResponse):
        await client.get_status(RUN_ID)
    await http_client.aclose()


@pytest.mark.asyncio
async def test_client_rejects_capability_echo_without_exposing_it() -> None:
    capability = "private-capability-" + ("s" * 48)
    document = json.loads(
        (FIXTURES / "control-terminal-status.json").read_bytes()
    )
    document["terminal_result"]["output"] = capability

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json=document,
            headers={CONTRACT_VERSION_HEADER: "1"},
        )

    http_client = httpx.AsyncClient(
        base_url="http://target",
        transport=httpx.MockTransport(handler),
    )
    client = SutClient(
        "http://target",
        timeout_seconds=1,
        client=http_client,
        forbidden_values=(capability,),
    )
    with pytest.raises(InvalidWireResponse) as raised:
        await client.get_status(RUN_ID)
    assert capability not in str(raised.value)
    assert capability not in repr(raised.value)
    await http_client.aclose()

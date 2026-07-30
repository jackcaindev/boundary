from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.evidence.canonical import (
    canonicalize_fault_definition,
    fault_definition_digest,
)
from boundary.execution.control import execute_control_run
from boundary.persistence.tables import (
    evidence_records,
    run_capabilities,
    runs,
)
from boundary.persistence.transactions import (
    AcceptanceCommand,
    CanonicalDocument,
    accept_campaign_run,
    prepare_campaign_run_acceptance,
)
from boundary.sut.contract_v1 import (
    CONTRACT_VERSION_HEADER,
    CONTRACT_VERSIONS_HEADER,
    CancellationAcknowledgement,
    CancellationRequest,
    ProblemResponse,
    RunStatus,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.compose,
    pytest.mark.asyncio(loop_scope="session"),
]

FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"
)
KNOWN_TEST_SECRET = "compose-capability-" + ("z" * 64)


async def _accepted_run(engine: AsyncEngine):
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    definition = Phase1FaultDefinition.model_validate(raw)
    canonical_bytes = canonicalize_fault_definition(definition)
    return await accept_campaign_run(
        engine,
        prepare_campaign_run_acceptance(
            AcceptanceCommand(
                idempotency_key=f"compose-{uuid4()}",
                contract_version="1",
                scenario_id="phase1.tool-timeout",
                scenario_version=1,
                tested_agent_id="boundary.sample-agent",
                tested_agent_version="vulnerable-v1",
                run_definition=CanonicalDocument(
                    schema_version=1,
                    document=definition.model_dump(mode="json"),
                    canonical_bytes=canonical_bytes,
                    digest=fault_definition_digest(definition),
                ),
            )
        ),
    )


async def test_real_http_control_reaches_complete_ordered_terminal_state(
    database_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    accepted = await _accepted_run(database_engine)
    monkeypatch.setattr(
        "boundary.injection.capability.secrets.token_urlsafe",
        lambda count: KNOWN_TEST_SECRET,
    )
    base_url = os.environ["SUT_BASE_URL"]

    result = await execute_control_run(
        database_engine,
        run_id=accepted.run_id,
        sut_base_url=base_url,
        tool_endpoint=(
            f"http://boundary:8000/internal/tools/{accepted.run_id}"
        ),
        tested_input="complete the normal control",
        execution_budget_ms=5_000,
        poll_interval_ms=20,
        http_timeout_seconds=2,
    )

    async with database_engine.connect() as connection:
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == accepted.run_id)
            )
        ).one()
        evidence = (
            await connection.execute(
                sa.select(evidence_records)
                .where(evidence_records.c.run_id == accepted.run_id)
                .order_by(evidence_records.c.receipt_seq)
            )
        ).all()
        capability = (
            await connection.execute(
                sa.select(run_capabilities).where(
                    run_capabilities.c.run_id == accepted.run_id
                )
            )
        ).one()

    assert result.operational_status == "completed"
    assert result.final_producer_seq == 2
    assert run.operational_status == "completed"
    assert run.reported_tested_agent_id == "boundary.sample-agent"
    assert run.reported_tested_agent_version == "vulnerable-v1"
    assert run.target_producer_cursor == 2
    assert run.target_final_watermark == 2
    assert [row.receipt_seq for row in evidence] == list(
        range(1, len(evidence) + 1)
    )
    target_events = [row for row in evidence if row.source == "sut"]
    assert [row.producer_seq for row in target_events] == [1, 2]
    assert [row.event_type for row in target_events] == [
        "sut.run.started",
        "sut.run.completed",
    ]
    assert capability.state == "retired"
    persisted_bytes = b"".join(
        [
            run.run_definition_bytes,
            *[row.payload_canonical_bytes for row in evidence],
            capability.capability_hash.encode("ascii"),
        ]
    )
    assert KNOWN_TEST_SECRET.encode("ascii") not in persisted_bytes
    assert KNOWN_TEST_SECRET not in caplog.text


async def test_real_http_contract_failures_idempotency_and_cancellation(
) -> None:
    base_url = os.environ["SUT_BASE_URL"]
    run_id = uuid4()
    trace_id = uuid4()
    document = {
        "contract_version": "1",
        "campaign_id": str(uuid4()),
        "scenario_id": "phase1.tool-timeout",
        "scenario_version": 1,
        "run_id": str(run_id),
        "trace_id": str(trace_id),
        "tested_agent_id": "boundary.sample-agent",
        "tested_agent_version": "vulnerable-v1",
        "tested_input": {"query": "real-http-contract"},
        "execution_budget_ms": 5_000,
        "tool_endpoint": "http://boundary:8000/internal/tools/test",
        "tool_capability": "q" * 64,
    }
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=2,
    ) as client:
        unsupported = await client.post(
            "/test-runs",
            headers={CONTRACT_VERSIONS_HEADER: "2, 1"},
            json=document,
        )
        assert ProblemResponse.model_validate_json(
            unsupported.content
        ).error.code == "UNSUPPORTED_CONTRACT_VERSION"

        unknown = await client.post(
            "/test-runs",
            headers={CONTRACT_VERSIONS_HEADER: "1"},
            json={**document, "unknown": True},
        )
        assert ProblemResponse.model_validate_json(
            unknown.content
        ).error.code == "INVALID_REQUEST"

        first = await client.post(
            "/test-runs",
            headers={CONTRACT_VERSIONS_HEADER: "1"},
            json=document,
        )
        replay = await client.post(
            "/test-runs",
            headers={CONTRACT_VERSIONS_HEADER: "1"},
            json=document,
        )
        conflict = await client.post(
            "/test-runs",
            headers={CONTRACT_VERSIONS_HEADER: "1"},
            json={
                **document,
                "tested_input": {"query": "changed"},
            },
        )
        assert first.status_code == replay.status_code == 202
        assert first.json() == replay.json()
        assert first.headers[CONTRACT_VERSION_HEADER] == "1"
        assert ProblemResponse.model_validate_json(
            conflict.content
        ).error.code == "RUN_CONFLICT"

        status = RunStatus.model_validate_json(
            (
                await client.get(f"/test-runs/{run_id}")
            ).content
        )
        for _ in range(100):
            if status.state == "completed":
                break
            await asyncio.sleep(0.01)
            status = RunStatus.model_validate_json(
                (
                    await client.get(f"/test-runs/{run_id}")
                ).content
            )
        assert status.state == "completed"
        cancellation = CancellationRequest(
            contract_version="1",
            run_id=run_id,
            trace_id=trace_id,
            cancellation_id=uuid4(),
        )
        acknowledgement = CancellationAcknowledgement.model_validate_json(
            (
                await client.post(
                    f"/test-runs/{run_id}/cancel",
                    content=cancellation.model_dump_json(),
                )
            ).content
        )
        assert acknowledgement.cancellation_applied is False
        assert acknowledgement.status.state == "completed"


async def test_real_http_deadline_cancels_and_collects_terminal_watermark(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    result = await execute_control_run(
        database_engine,
        run_id=accepted.run_id,
        sut_base_url=os.environ["SUT_BASE_URL"],
        tool_endpoint=(
            f"http://boundary:8000/internal/tools/{accepted.run_id}"
        ),
        tested_input="cancel before deterministic delayed start",
        execution_budget_ms=200,
        cancellation_grace_ms=1_500,
        poll_interval_ms=20,
        http_timeout_seconds=1,
    )

    async with database_engine.connect() as connection:
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == accepted.run_id)
            )
        ).one()
        evidence = (
            await connection.execute(
                sa.select(evidence_records)
                .where(evidence_records.c.run_id == accepted.run_id)
                .order_by(evidence_records.c.receipt_seq)
            )
        ).all()
        capability_state = await connection.scalar(
            sa.select(run_capabilities.c.state).where(
                run_capabilities.c.run_id == accepted.run_id
            )
        )

    assert result.operational_status == "cancelled"
    assert result.final_producer_seq == 1
    assert run.operational_status == "cancelled"
    assert run.target_producer_cursor == 1
    assert run.target_final_watermark == 1
    assert capability_state == "retired"
    assert [row.event_type for row in evidence].count(
        "boundary.cancellation.requested"
    ) == 1
    assert "sut.run.cancelled" in [
        row.event_type for row in evidence
    ]
    assert "boundary.run.running" not in [
        row.event_type for row in evidence
    ]

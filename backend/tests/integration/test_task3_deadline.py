from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.evidence.canonical import (
    canonicalize_fault_definition,
    fault_definition_digest,
)
from boundary.execution.control import (
    ControlExecutionError,
    DEFAULT_CANCELLATION_GRACE_MS,
    execute_control_run,
)
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
from boundary.sut.client import SutTimeoutError, SutTransportError
from boundary.sut.contract_v1 import (
    AcceptedResponse,
    CancellationAcknowledgement,
    CancellationRequest,
    CancelledPayload,
    EventPage,
    RunCancelledEvent,
    RunStatus,
    TerminalResult,
    TestRunRequest as WireTestRunRequest,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.value += seconds

    def advance(self, seconds: float) -> None:
        self.value += seconds


class DeadlineClient:
    def __init__(
        self,
        clock: FakeClock,
        *,
        cancellation: str,
        transport_fails_early: bool = False,
    ) -> None:
        self.clock = clock
        self.cancellation = cancellation
        self.transport_fails_early = transport_fails_early
        self.request: WireTestRunRequest | None = None
        self.cancellation_request: CancellationRequest | None = None
        self.cancel_calls = 0
        self.cancelled_event: RunCancelledEvent | None = None
        self.cancelled_status: RunStatus | None = None

    async def create_run(
        self,
        request: WireTestRunRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> AcceptedResponse:
        del timeout_seconds
        self.request = request
        return AcceptedResponse(
            contract_version="1",
            run_id=request.run_id,
            trace_id=request.trace_id,
            tested_agent_id=request.tested_agent_id,
            tested_agent_version=request.tested_agent_version,
            state="accepted",
            status_url=f"http://target/test-runs/{request.run_id}",
            events_url=(
                f"http://target/test-runs/{request.run_id}/events"
            ),
            cancellation_url=(
                f"http://target/test-runs/{request.run_id}/cancel"
            ),
            producer_high_watermark=0,
        )

    async def get_status(
        self,
        run_id: UUID,
        *,
        timeout_seconds: float | None = None,
    ) -> RunStatus:
        request = self._request(run_id)
        if self.cancellation_request is None:
            if not self.transport_fails_early:
                assert timeout_seconds is not None
                self.clock.advance(timeout_seconds)
                raise SutTimeoutError("controlled deadline timeout")
            raise SutTransportError("controlled transport failure")
        if self.cancelled_status is not None:
            return self.cancelled_status
        return _accepted_status(request)

    async def get_events(
        self,
        run_id: UUID,
        *,
        after_producer_seq: int,
        timeout_seconds: float | None = None,
    ) -> EventPage:
        del timeout_seconds
        request = self._request(run_id)
        events = (
            [self.cancelled_event]
            if self.cancelled_event is not None
            and after_producer_seq == 0
            else []
        )
        high = 1 if self.cancelled_event is not None else 0
        return EventPage(
            contract_version="1",
            run_id=request.run_id,
            trace_id=request.trace_id,
            events=events,
            producer_high_watermark=high,
            next_after_producer_seq=(
                events[-1].producer_seq
                if events
                else after_producer_seq
            ),
        )

    async def cancel_run(
        self,
        request: CancellationRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> CancellationAcknowledgement:
        del timeout_seconds
        self.cancel_calls += 1
        self.cancellation_request = request
        run_request = self._request(request.run_id)
        if self.cancellation == "sealed":
            event = RunCancelledEvent(
                contract_version="1",
                run_id=request.run_id,
                trace_id=request.trace_id,
                event_id=uuid4(),
                source="sut",
                event_type="sut.run.cancelled",
                boundary="run",
                producer_seq=1,
                payload=CancelledPayload(
                    schema_version=1,
                    cancellation_id=request.cancellation_id,
                ),
            )
            status = RunStatus(
                contract_version="1",
                run_id=request.run_id,
                trace_id=request.trace_id,
                tested_agent_id=run_request.tested_agent_id,
                tested_agent_version=run_request.tested_agent_version,
                state="cancelled",
                producer_high_watermark=1,
                final_producer_seq=1,
                terminal_result=TerminalResult(
                    contract_version="1",
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    tested_agent_id=run_request.tested_agent_id,
                    tested_agent_version=(
                        run_request.tested_agent_version
                    ),
                    state="cancelled",
                    final_producer_seq=1,
                    outcome_kind="cancelled",
                    output=None,
                    event_id=event.event_id,
                ),
            )
            self.cancelled_event = event
            self.cancelled_status = status
            return CancellationAcknowledgement(
                contract_version="1",
                run_id=request.run_id,
                trace_id=request.trace_id,
                cancellation_id=request.cancellation_id,
                cancellation_applied=True,
                status=status,
            )
        return CancellationAcknowledgement(
            contract_version="1",
            run_id=request.run_id,
            trace_id=request.trace_id,
            cancellation_id=request.cancellation_id,
            cancellation_applied=True,
            status=_accepted_status(run_request),
        )

    async def aclose(self) -> None:
        return None

    def _request(self, run_id: UUID) -> WireTestRunRequest:
        assert self.request is not None
        assert self.request.run_id == run_id
        return self.request


def _accepted_status(request: WireTestRunRequest) -> RunStatus:
    return RunStatus(
        contract_version="1",
        run_id=request.run_id,
        trace_id=request.trace_id,
        tested_agent_id=request.tested_agent_id,
        tested_agent_version=request.tested_agent_version,
        state="accepted",
        producer_high_watermark=0,
    )


async def _accepted_run(engine: AsyncEngine, *, key: str):
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    definition = Phase1FaultDefinition.model_validate(raw)
    canonical_bytes = canonicalize_fault_definition(definition)
    return await accept_campaign_run(
        engine,
        prepare_campaign_run_acceptance(
            AcceptanceCommand(
                idempotency_key=key,
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


async def _execute(
    engine: AsyncEngine,
    accepted,
    clock: FakeClock,
    client: DeadlineClient,
):
    return await execute_control_run(
        engine,
        run_id=accepted.run_id,
        sut_base_url="http://target",
        tool_endpoint="http://boundary/internal/tools/control",
        tested_input="control",
        execution_budget_ms=100,
        cancellation_grace_ms=50,
        poll_interval_ms=10,
        http_timeout_seconds=5,
        clock=clock,
        sut_client=client,
    )


async def test_budget_expiry_and_sealed_cancellation_becomes_cancelled(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(
        database_engine,
        key="deadline-sealed-cancellation",
    )
    clock = FakeClock()
    client = DeadlineClient(clock, cancellation="sealed")

    result = await _execute(
        database_engine,
        accepted,
        clock,
        client,
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
    assert client.cancel_calls == 1
    assert capability_state == "retired"
    assert [
        row.event_type
        for row in evidence
        if row.event_type == "boundary.cancellation.requested"
    ] == ["boundary.cancellation.requested"]
    assert not any(
        row.event_type == "boundary.run.running" for row in evidence
    )
    terminal = [
        row
        for row in evidence
        if row.event_type == "boundary.run.terminal"
    ][0]
    assert terminal.payload["from_status"] == "accepted"
    assert terminal.payload["to_status"] == "cancelled"
    assert client.cancelled_event is not None
    assert any(
        row.event_type == "sut.run.cancelled"
        and row.source_event_id == client.cancelled_event.event_id
        for row in evidence
    )
    assert any(
        row.event_type == "boundary.sut_terminal.observed"
        for row in evidence
    )


async def test_unsealed_cancellation_times_out_after_fixed_grace(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(
        database_engine,
        key="deadline-unsealed-cancellation",
    )
    clock = FakeClock()
    client = DeadlineClient(clock, cancellation="unsealed")

    with pytest.raises(ControlExecutionError) as raised:
        await _execute(
            database_engine,
            accepted,
            clock,
            client,
        )

    async with database_engine.connect() as connection:
        run_status = await connection.scalar(
            sa.select(runs.c.operational_status).where(
                runs.c.run_id == accepted.run_id
            )
        )
        capability_state = await connection.scalar(
            sa.select(run_capabilities.c.state).where(
                run_capabilities.c.run_id == accepted.run_id
            )
        )
    assert raised.value.status == "timed_out"
    assert raised.value.reason == "cancellation grace exhausted"
    assert run_status == "timed_out"
    assert capability_state == "retired"
    assert client.cancel_calls == 1
    assert 0.15 <= clock.monotonic() < 0.17


async def test_transport_failure_with_budget_remaining_is_failed(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(
        database_engine,
        key="deadline-early-transport-failure",
    )
    clock = FakeClock()
    client = DeadlineClient(
        clock,
        cancellation="sealed",
        transport_fails_early=True,
    )

    with pytest.raises(ControlExecutionError) as raised:
        await _execute(
            database_engine,
            accepted,
            clock,
            client,
        )

    async with database_engine.connect() as connection:
        run_status = await connection.scalar(
            sa.select(runs.c.operational_status).where(
                runs.c.run_id == accepted.run_id
            )
        )
    assert raised.value.status == "failed"
    assert run_status == "failed"
    assert client.cancel_calls == 0


async def test_remaining_budget_transport_timeout_enters_cancellation(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(
        database_engine,
        key="deadline-remaining-timeout",
    )
    clock = FakeClock()
    client = DeadlineClient(clock, cancellation="unsealed")

    with pytest.raises(ControlExecutionError) as raised:
        await _execute(
            database_engine,
            accepted,
            clock,
            client,
        )

    assert raised.value.status == "timed_out"
    assert client.cancel_calls == 1


async def test_cancellation_grace_setting_is_validated(
    database_engine: AsyncEngine,
) -> None:
    assert DEFAULT_CANCELLATION_GRACE_MS == 2_000
    accepted = await _accepted_run(
        database_engine,
        key="deadline-invalid-grace",
    )
    with pytest.raises(ValueError, match="cancellation grace"):
        await execute_control_run(
            database_engine,
            run_id=accepted.run_id,
            sut_base_url="http://target",
            tool_endpoint="http://boundary/internal/tools/control",
            tested_input="control",
            cancellation_grace_ms=0,
        )

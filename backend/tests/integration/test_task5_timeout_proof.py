from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import rfc8785
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.evidence.canonical import canonicalize_fault_definition, fault_definition_digest
from boundary.config import BoundarySettings
from boundary.executor import SerialExecutor
from boundary.execution.injected import (
    bind_control_tested_input,
    create_injected_sibling,
)
from boundary.injection.capability import (
    create_control_capability,
    retire_capability,
)
from boundary.injection.contract_v1 import LookupArguments, LookupRequest
from boundary.injection.timeout import (
    ActivationRuntime,
    EffectNotReady,
    EffectProofRejected,
    EffectProofV1,
    canonicalize_effect_proof,
    realize_timeout_effect,
    reconcile_abandoned_activation_runtimes,
    settle_activation_runtime,
)
from boundary.injection.tool_stub import (
    CapabilityIdentityMismatch,
    DuplicateRegistration,
    ToolRegistrationPersistenceError,
    register_tool_call,
)
from boundary.persistence.tables import (
    analyses,
    campaigns,
    evidence_records,
    evidence_sets,
    fault_activations,
    runs,
    tool_calls,
)
from boundary.persistence.transactions import (
    AcceptanceCommand,
    CanonicalDocument,
    accept_campaign_run,
    prepare_campaign_run_acceptance,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"


class VirtualClock:
    def __init__(self, now_ns: int = 10_000_000_000) -> None:
        self.now_ns = now_ns

    def monotonic_ns(self) -> int:
        return self.now_ns

    def advance_to(self, value: int) -> None:
        self.now_ns = max(self.now_ns, value)


class VirtualWaiter:
    async def wait_until(self, deadline_ns: int, clock: VirtualClock) -> None:
        clock.advance_to(deadline_ns)


async def _successful_control(engine: AsyncEngine, *, version: str = "vulnerable-v1"):
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    definition = Phase1FaultDefinition.model_validate(raw)
    canonical = canonicalize_fault_definition(definition)
    accepted = await accept_campaign_run(
        engine,
        prepare_campaign_run_acceptance(
            AcceptanceCommand(
                idempotency_key=f"task5-{uuid4()}",
                contract_version="1",
                scenario_id="phase1.tool-timeout",
                scenario_version=1,
                tested_agent_id="boundary.sample-agent",
                tested_agent_version=version,
                run_definition=CanonicalDocument(
                    schema_version=1,
                    document=definition.model_dump(mode="json"),
                    canonical_bytes=canonical,
                    digest=fault_definition_digest(definition),
                ),
            )
        ),
    )
    await bind_control_tested_input(
        engine,
        run_id=accepted.run_id,
        tested_input="phase1 lookup",
    )
    control_grant = await create_control_capability(
        engine,
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        tool_identity="boundary.phase1.lookup",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    control_request = LookupRequest(
        contract_version="1",
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        tool_identity="boundary.phase1.lookup",
        tool_call_id=uuid4(),
        fault_id=None,
        arguments=LookupArguments(query="phase1 lookup"),
    )
    await register_tool_call(
        engine,
        route_run_id=accepted.run_id,
        capability_secret=control_grant.capability_secret,
        request=control_request,
    )
    await retire_capability(engine, control_grant.capability_record_id)
    async with engine.begin() as connection:
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == accepted.run_id)
            .values(
                operational_status="completed",
                reported_tested_agent_id="boundary.sample-agent",
                reported_tested_agent_version=version,
            )
        )
    return accepted


async def _sibling(engine: AsyncEngine, *, version: str = "vulnerable-v1"):
    control = await _successful_control(engine, version=version)
    sibling = await create_injected_sibling(
        engine,
        control_run_id=control.run_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    return control, sibling


def _request(sibling, tool_call_id: UUID | None = None) -> LookupRequest:
    return LookupRequest(
        contract_version="1",
        run_id=sibling.run_id,
        trace_id=sibling.trace_id,
        tool_identity="boundary.phase1.lookup",
        tool_call_id=tool_call_id or uuid4(),
        fault_id=sibling.fault_id,
        arguments=LookupArguments(query="phase1 lookup"),
    )


async def _activate(engine: AsyncEngine, sibling, *, clock: VirtualClock | None = None):
    selected_clock = clock or VirtualClock()
    request = _request(sibling)
    runtime = ActivationRuntime.create(
        run_id=sibling.run_id,
        trace_id=sibling.trace_id,
        fault_id=sibling.fault_id,
        tool_call_id=request.tool_call_id,
        clock=selected_clock,
        waiter=VirtualWaiter(),
    )
    result = await register_tool_call(
        engine,
        route_run_id=sibling.run_id,
        capability_secret=sibling.capability.capability_secret,
        request=request,
        activation_runtime=runtime,
    )
    return request, runtime, result


async def _activation_rows(engine: AsyncEngine, run_id: UUID):
    async with engine.connect() as connection:
        return (
            await connection.execute(
                sa.select(fault_activations)
                .where(fault_activations.c.run_id == run_id)
                .order_by(fault_activations.c.activation_ordinal)
            )
        ).all()


async def test_injected_sibling_has_fresh_immutable_identity_and_capability(
    database_engine: AsyncEngine,
) -> None:
    control, sibling = await _sibling(database_engine)
    async with database_engine.connect() as connection:
        control_row = (
            await connection.execute(sa.select(runs).where(runs.c.run_id == control.run_id))
        ).one()
        injected = (
            await connection.execute(sa.select(runs).where(runs.c.run_id == sibling.run_id))
        ).one()
    assert len({control_row.run_id, injected.run_id}) == 2
    assert len({control_row.trace_id, injected.trace_id}) == 2
    assert injected.run_role == "injected"
    assert injected.control_run_id == control_row.run_id
    assert injected.fault_id == sibling.fault_id
    assert injected.run_definition_bytes == control_row.run_definition_bytes
    assert injected.run_definition_digest == control_row.run_definition_digest
    assert injected.tested_input_bytes == control_row.tested_input_bytes
    assert injected.expected_tested_agent_version == control_row.expected_tested_agent_version
    assert sibling.capability.capability_secret.encode() not in injected.run_definition_bytes


@pytest.mark.parametrize("failure_point", ["run", "evidence", "capability"])
async def test_injected_sibling_failure_seams_rollback_atomically(
    database_engine: AsyncEngine,
    failure_point: str,
) -> None:
    control = await _successful_control(database_engine)
    with pytest.raises(RuntimeError):
        await create_injected_sibling(
            database_engine,
            control_run_id=control.run_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            _fail_after=failure_point,  # type: ignore[arg-type]
        )
    async with database_engine.connect() as connection:
        injected_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(runs)
            .where(runs.c.control_run_id == control.run_id)
        )
    assert injected_count == 0


async def test_activation_start_requires_closed_gate_and_precedes_boundary(
    database_engine: AsyncEngine,
) -> None:
    _, sibling = await _sibling(database_engine)
    _, runtime, result = await _activate(database_engine, sibling)
    rows = await _activation_rows(database_engine, sibling.run_id)
    assert runtime.gate.is_closed
    assert result.activation_evidence_id is not None
    assert len(rows) == 1
    assert rows[0].reservation_state == "activation_started"
    assert rows[0].activation_started_ns < rows[0].client_timeout_boundary_ns


async def test_response_before_gate_close_prevents_activation_start(
    database_engine: AsyncEngine,
) -> None:
    _, sibling = await _sibling(database_engine)
    request = _request(sibling)
    clock = VirtualClock()
    runtime = ActivationRuntime.create(
        run_id=sibling.run_id,
        trace_id=sibling.trace_id,
        fault_id=sibling.fault_id,
        tool_call_id=request.tool_call_id,
        clock=clock,
        waiter=VirtualWaiter(),
    )
    runtime.gate.send_success(clock.monotonic_ns())
    with pytest.raises(ToolRegistrationPersistenceError):
        await register_tool_call(
            database_engine,
            route_run_id=sibling.run_id,
            capability_secret=sibling.capability.capability_secret,
            request=request,
            activation_runtime=runtime,
        )
    assert await _activation_rows(database_engine, sibling.run_id) == []


async def test_wrong_runtime_identity_creates_no_start_or_effect(
    database_engine: AsyncEngine,
) -> None:
    _, sibling = await _sibling(database_engine)
    request = _request(sibling)
    runtime = ActivationRuntime.create(
        run_id=sibling.run_id,
        trace_id=uuid4(),
        fault_id=sibling.fault_id,
        tool_call_id=request.tool_call_id,
        clock=VirtualClock(),
        waiter=VirtualWaiter(),
    )
    with pytest.raises(CapabilityIdentityMismatch):
        await register_tool_call(
            database_engine,
            route_run_id=sibling.run_id,
            capability_secret=sibling.capability.capability_secret,
            request=request,
            activation_runtime=runtime,
        )
    assert await _activation_rows(database_engine, sibling.run_id) == []


async def test_effect_boundary_gate_and_at_most_once_rules(
    database_engine: AsyncEngine,
) -> None:
    _, sibling = await _sibling(database_engine)
    request, runtime, _ = await _activate(database_engine, sibling)
    with pytest.raises(EffectNotReady):
        await realize_timeout_effect(database_engine, runtime)
    runtime.clock.advance_to(runtime.client_timeout_boundary_ns)
    first = await realize_timeout_effect(database_engine, runtime)
    second = await realize_timeout_effect(database_engine, runtime)
    assert first == second
    rows = await _activation_rows(database_engine, sibling.run_id)
    assert rows[0].effect_status == "effect_realized"
    assert rows[0].effect_evidence_id == first
    assert rows[0].effect_proof["tool_call_id"] == str(request.tool_call_id)


@pytest.mark.parametrize("lost_authority", [False, True])
async def test_response_or_lost_authority_prevents_effect(
    database_engine: AsyncEngine,
    lost_authority: bool,
) -> None:
    _, sibling = await _sibling(database_engine)
    _, runtime, _ = await _activate(database_engine, sibling)
    if lost_authority:
        runtime.lose_authority()
    else:
        runtime.gate.observe_response_sent(runtime.accepted_request_origin_ns + 1)
    runtime.clock.advance_to(runtime.client_timeout_boundary_ns)
    with pytest.raises(EffectProofRejected):
        await realize_timeout_effect(database_engine, runtime)
    assert (await _activation_rows(database_engine, sibling.run_id))[0].effect_status == "pending"


async def test_process_loss_preserves_start_without_effect(
    database_engine: AsyncEngine,
) -> None:
    _, sibling = await _sibling(database_engine)
    _, _, result = await _activate(database_engine, sibling)
    assert await reconcile_abandoned_activation_runtimes(database_engine) == 1
    row = (await _activation_rows(database_engine, sibling.run_id))[0]
    assert row.activation_evidence_id == result.activation_evidence_id
    assert row.effect_status == "runtime_lost"
    assert row.effect_evidence_id is None
    assert row.hold_disposition == "runtime_lost"


async def test_process_loss_after_effect_preserves_effect_but_marks_hold_lost(
    database_engine: AsyncEngine,
) -> None:
    _, sibling = await _sibling(database_engine)
    _, runtime, _ = await _activate(database_engine, sibling)
    runtime.clock.advance_to(runtime.client_timeout_boundary_ns)
    effect_id = await realize_timeout_effect(database_engine, runtime)
    assert await reconcile_abandoned_activation_runtimes(database_engine) == 1
    row = (await _activation_rows(database_engine, sibling.run_id))[0]
    assert row.effect_status == "effect_realized"
    assert row.effect_evidence_id == effect_id
    assert row.hold_disposition == "runtime_lost"


async def test_executor_reconciliation_finalizes_lost_activation_as_execution_error(
    database_engine: AsyncEngine,
) -> None:
    control, sibling = await _sibling(database_engine)
    _, _, result = await _activate(database_engine, sibling)
    async with database_engine.begin() as connection:
        await connection.execute(
            campaigns.update()
            .where(campaigns.c.campaign_id == control.campaign_id)
            .values(status="running")
        )
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == sibling.run_id)
            .values(
                operational_status="running",
                execution_checkpoint="polling",
                reported_tested_agent_id="boundary.sample-agent",
                reported_tested_agent_version="vulnerable-v1",
            )
        )

    executor = SerialExecutor(
        database_engine,
        BoundarySettings(
            sut_base_url="http://sample-agent:8001",
            boundary_internal_base_url="http://boundary:8000",
        ),
        _include_unmanaged=True,
    )
    await executor.reconcile_startup()

    async with database_engine.connect() as connection:
        activation = (
            await connection.execute(
                sa.select(fault_activations).where(
                    fault_activations.c.activation_evidence_id
                    == result.activation_evidence_id
                )
            )
        ).one()
        effect_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == sibling.run_id,
                evidence_records.c.event_type
                == "boundary.fault_effect_realized",
            )
        )
        evidence_set = (
            await connection.execute(
                sa.select(evidence_sets).where(
                    evidence_sets.c.run_id == sibling.run_id
                )
            )
        ).one()
        analysis = (
            await connection.execute(
                sa.select(analyses).where(
                    analyses.c.evidence_set_id
                    == evidence_set.evidence_set_id,
                    analyses.c.record_kind == "authoritative",
                )
            )
        ).one()
    assert activation.effect_status == "runtime_lost"
    assert activation.effect_evidence_id is None
    assert activation.hold_disposition == "runtime_lost"
    assert effect_count == 0
    assert evidence_set.cutoff_reason == "reconciliation_error"
    assert analysis.evaluability_aggregate == "EXECUTION_ERROR"


async def test_two_effect_chains_and_ordinal_two_normal_response(
    database_engine: AsyncEngine,
) -> None:
    _, sibling = await _sibling(database_engine)
    runtimes = []
    for _ in range(2):
        _, runtime, _ = await _activate(database_engine, sibling)
        runtime.clock.advance_to(runtime.client_timeout_boundary_ns)
        await realize_timeout_effect(database_engine, runtime)
        await settle_activation_runtime(database_engine, runtime)
        runtimes.append(runtime)
    third = _request(sibling)
    result = await register_tool_call(
        database_engine,
        route_run_id=sibling.run_id,
        capability_secret=sibling.capability.capability_secret,
        request=third,
        activation_runtime=ActivationRuntime.create(
            run_id=sibling.run_id,
            trace_id=sibling.trace_id,
            fault_id=sibling.fault_id,
            tool_call_id=third.tool_call_id,
            clock=VirtualClock(),
            waiter=VirtualWaiter(),
        ),
    )
    rows = await _activation_rows(database_engine, sibling.run_id)
    async with database_engine.connect() as connection:
        calls = (
            await connection.execute(
                sa.select(tool_calls)
                .where(tool_calls.c.run_id == sibling.run_id)
                .order_by(tool_calls.c.retry_ordinal)
            )
        ).all()
        receipts = list(
            (
                await connection.execute(
                    sa.select(evidence_records.c.receipt_seq)
                    .where(
                        evidence_records.c.run_id == sibling.run_id,
                        evidence_records.c.disposition == "accepted",
                    )
                    .order_by(evidence_records.c.receipt_seq)
                )
            ).scalars()
        )
    assert [row.effect_status for row in rows] == ["effect_realized", "effect_realized"]
    assert {row.fault_id for row in rows} == {sibling.fault_id}
    assert [call.retry_ordinal for call in calls] == [0, 1, 2]
    assert calls[2].registration_outcome == "attempt_not_selected"
    assert result.response is not None and result.response.result.value == "control-ok"
    assert receipts == list(range(1, len(receipts) + 1))


async def test_duplicate_delivery_cannot_duplicate_start_or_effect(
    database_engine: AsyncEngine,
) -> None:
    _, sibling = await _sibling(database_engine)
    request, runtime, _ = await _activate(database_engine, sibling)
    duplicate = await register_tool_call(
        database_engine,
        route_run_id=sibling.run_id,
        capability_secret=sibling.capability.capability_secret,
        request=request,
    )
    assert isinstance(duplicate, DuplicateRegistration)
    runtime.clock.advance_to(runtime.client_timeout_boundary_ns)
    await realize_timeout_effect(database_engine, runtime)
    assert len(await _activation_rows(database_engine, sibling.run_id)) == 1


@pytest.mark.parametrize("failure_point", ["gate_closed", "activation", "activation_start_evidence", "counters"])
async def test_activation_start_failure_seams_leave_no_claim(
    database_engine: AsyncEngine,
    failure_point: str,
) -> None:
    _, sibling = await _sibling(database_engine)
    request = _request(sibling)
    runtime = ActivationRuntime.create(
        run_id=sibling.run_id,
        trace_id=sibling.trace_id,
        fault_id=sibling.fault_id,
        tool_call_id=request.tool_call_id,
        clock=VirtualClock(),
        waiter=VirtualWaiter(),
    )
    with pytest.raises(RuntimeError):
        await register_tool_call(
            database_engine,
            route_run_id=sibling.run_id,
            capability_secret=sibling.capability.capability_secret,
            request=request,
            activation_runtime=runtime,
            _fail_after=failure_point,  # type: ignore[arg-type]
        )
    assert await _activation_rows(database_engine, sibling.run_id) == []


@pytest.mark.parametrize("failure_point", ["effect_evidence", "effect_update", "effect_counter"])
async def test_effect_failure_seams_leave_no_effect_claim(
    database_engine: AsyncEngine,
    failure_point: str,
) -> None:
    _, sibling = await _sibling(database_engine)
    _, runtime, _ = await _activate(database_engine, sibling)
    runtime.clock.advance_to(runtime.client_timeout_boundary_ns)
    with pytest.raises(RuntimeError):
        await realize_timeout_effect(
            database_engine,
            runtime,
            _fail_after=failure_point,  # type: ignore[arg-type]
        )
    row = (await _activation_rows(database_engine, sibling.run_id))[0]
    assert row.effect_status == "pending"
    assert row.effect_evidence_id is None


async def test_effect_proof_normalization_is_repeatable() -> None:
    proof = EffectProofV1(
        schema_version=1,
        activation_id=str(uuid4()),
        run_id=str(uuid4()),
        fault_id=str(uuid4()),
        tool_call_id=str(uuid4()),
        accepted_request_origin_ns=1,
        activation_started_ns=2,
        client_timeout_boundary_ns=500_000_001,
        observed_monotonic_ns=500_000_001,
        gate_closed=True,
        no_response_before_boundary=True,
        timing_authority_continuous=True,
    )
    first = canonicalize_effect_proof(proof)
    second = canonicalize_effect_proof(proof)
    assert first == second
    assert sha256(first[0]).hexdigest() == first[1]
    assert rfc8785.dumps(json.loads(first[0])) == first[0]

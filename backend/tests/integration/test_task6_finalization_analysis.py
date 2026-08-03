from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.evaluation.analyzer import (
    AnalysisInputError,
    AnalysisIntegrityError,
    analyze_evidence_set,
    persist_analysis,
)
from boundary.evidence.canonical import (
    canonicalize_fault_definition,
    fault_definition_digest,
)
from boundary.evidence.collector import (
    EvidenceClosed,
    collect_target_page,
    record_deadline_reached,
    record_run_budget,
    record_safe_rejection,
    transition_run,
)
from boundary.evidence.finalizer import (
    FinalizationConflict,
    FinalizationNotReady,
    canonicalize_manifest,
    finalize_run_evidence,
)
from boundary.execution.control import execute_control_run
from boundary.execution.injected import (
    create_injected_sibling,
    execute_injected_run,
)
from boundary.injection.capability import retire_capability
from boundary.injection.contract_v1 import LookupArguments, LookupRequest
from boundary.injection.timeout import ActivationRuntime
from boundary.injection.tool_stub import register_tool_call
from boundary.persistence.tables import (
    analyses,
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
from boundary.sut.contract_v1 import (
    EventPage,
    RunStartedEvent,
    StartedPayload,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.compose,
    pytest.mark.asyncio(loop_scope="session"),
]
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"


class VirtualClock:
    def __init__(self) -> None:
        self.now_ns = 10_000_000_000

    def monotonic_ns(self) -> int:
        return self.now_ns


class NoopWaiter:
    async def wait_until(self, deadline_ns: int, clock: VirtualClock) -> None:
        del deadline_ns, clock


async def _accepted_control(engine: AsyncEngine):
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    definition = Phase1FaultDefinition.model_validate(raw)
    canonical = canonicalize_fault_definition(definition)
    return await accept_campaign_run(
        engine,
        prepare_campaign_run_acceptance(
            AcceptanceCommand(
                idempotency_key=f"task6-{uuid4()}",
                contract_version="1",
                scenario_id="phase1.tool-timeout",
                scenario_version=1,
                tested_agent_id="boundary.sample-agent",
                tested_agent_version="vulnerable-v1",
                run_definition=CanonicalDocument(
                    schema_version=1,
                    document=definition.model_dump(mode="json"),
                    canonical_bytes=canonical,
                    digest=fault_definition_digest(definition),
                ),
            )
        ),
    )


async def _execute_control(engine: AsyncEngine):
    accepted = await _accepted_control(engine)
    boundary_base = os.environ["BOUNDARY_INTERNAL_BASE_URL"]
    await execute_control_run(
        engine,
        run_id=accepted.run_id,
        sut_base_url=os.environ["SUT_BASE_URL"],
        tool_endpoint=(
            f"{boundary_base}/internal/v1/runs/{accepted.run_id}"
            "/tools/phase1-lookup"
        ),
        tested_input="phase1 lookup",
    )
    return accepted


async def _establish_deadline(engine: AsyncEngine, *, run_id, trace_id) -> None:
    budget = await record_run_budget(
        engine,
        run_id=run_id,
        trace_id=trace_id,
        execution_budget_ms=1_000,
        budget_started_monotonic_ns=1_000,
        deadline_monotonic_ns=1_000_001_000,
    )
    await record_deadline_reached(
        engine,
        run_id=run_id,
        trace_id=trace_id,
        budget=budget,
        observed_monotonic_ns=1_000_001_000,
    )


async def test_atomic_single_finalization_converges_and_late_arrivals_are_bounded(
    database_engine: AsyncEngine,
) -> None:
    control = await _execute_control(database_engine)
    with pytest.raises(RuntimeError):
        await finalize_run_evidence(
            database_engine,
            run_id=control.run_id,
            _fail_after="evidence_set",
        )
    async with database_engine.connect() as connection:
        open_after_rollback = await connection.scalar(
            sa.select(runs.c.evidence_open).where(
                runs.c.run_id == control.run_id
            )
        )
        count_after_rollback = await connection.scalar(
            sa.select(sa.func.count()).select_from(evidence_sets)
        )
    assert open_after_rollback is True
    assert count_after_rollback == 0

    results = await asyncio.gather(
        *[
            finalize_run_evidence(database_engine, run_id=control.run_id)
            for _ in range(8)
        ]
    )
    assert len({item.evidence_set_id for item in results}) == 1
    assert len({item.evidence_set_digest for item in results}) == 1
    assert len({item.canonical_bytes for item in results}) == 1
    assert sum(not item.replayed for item in results) == 1
    finalized = results[0]
    assert [
        ref.receipt_seq for ref in finalized.manifest.accepted_evidence
    ] == list(range(1, len(finalized.manifest.accepted_evidence) + 1))

    replay = await finalize_run_evidence(
        database_engine,
        run_id=control.run_id,
    )
    assert replay.replayed is True
    assert replay.canonical_bytes == finalized.canonical_bytes
    assert replay.evidence_set_digest == finalized.evidence_set_digest
    with pytest.raises(FinalizationConflict):
        await finalize_run_evidence(
            database_engine,
            run_id=control.run_id,
            cutoff_reason="evidence_deadline",
        )

    started = RunStartedEvent(
        contract_version="1",
        run_id=control.run_id,
        trace_id=control.trace_id,
        event_id=uuid4(),
        source="sut",
        event_type="sut.run.started",
        boundary="run",
        producer_seq=3,
        payload=StartedPayload(schema_version=1),
    )
    late_page = EventPage(
        contract_version="1",
        run_id=control.run_id,
        trace_id=control.trace_id,
        events=[started],
        producer_high_watermark=3,
        next_after_producer_seq=3,
    )
    with pytest.raises(EvidenceClosed):
        await collect_target_page(
            database_engine,
            run_id=control.run_id,
            requested_after=2,
            page=late_page,
        )
    secret = b"Authorization: Bearer should-never-be-persisted"
    await record_safe_rejection(
        database_engine,
        run_id=control.run_id,
        category="late_secret_shaped_payload",
        raw_bytes=secret,
    )
    after_late = await finalize_run_evidence(
        database_engine,
        run_id=control.run_id,
    )
    assert after_late.evidence_set_digest == finalized.evidence_set_digest
    assert after_late.canonical_bytes == finalized.canonical_bytes
    async with database_engine.connect() as connection:
        set_count = await connection.scalar(
            sa.select(sa.func.count()).select_from(evidence_sets)
        )
        run_open = await connection.scalar(
            sa.select(runs.c.evidence_open).where(
                runs.c.run_id == control.run_id
            )
        )
        late_rows = (
            await connection.execute(
                sa.select(evidence_records).where(
                    evidence_records.c.run_id == control.run_id,
                    evidence_records.c.disposition == "late",
                )
            )
        ).all()
    assert set_count == 1
    assert run_open is False
    assert len(late_rows) == 2
    assert secret not in b"".join(row.payload_canonical_bytes for row in late_rows)


async def test_deadline_finalization_requires_authoritative_deadline_evidence(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_control(database_engine)
    await transition_run(
        database_engine,
        run_id=accepted.run_id,
        target_status="failed",
        reason="manual_failure_without_deadline",
    )
    with pytest.raises(FinalizationNotReady, match="deadline proof"):
        await finalize_run_evidence(
            database_engine,
            run_id=accepted.run_id,
            cutoff_reason="evidence_deadline",
        )

    legitimate = await _accepted_control(database_engine)
    await _establish_deadline(
        database_engine,
        run_id=legitimate.run_id,
        trace_id=legitimate.trace_id,
    )
    await transition_run(
        database_engine,
        run_id=legitimate.run_id,
        target_status="failed",
        reason="deadline_reached",
    )
    finalized = await finalize_run_evidence(
        database_engine,
        run_id=legitimate.run_id,
        cutoff_reason="evidence_deadline",
    )
    assert finalized.cutoff_reason == "evidence_deadline"
    deadline_markers = [
        marker
        for marker in finalized.manifest.cutoff_markers
        if marker.marker_type == "deadline"
    ]
    assert len(deadline_markers) == 1
    assert deadline_markers[0].evidence_id is not None


async def test_unsettled_activation_prevents_injected_finalization(
    database_engine: AsyncEngine,
) -> None:
    control = await _execute_control(database_engine)
    await finalize_run_evidence(database_engine, run_id=control.run_id)
    sibling = await create_injected_sibling(
        database_engine,
        control_run_id=control.run_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    request = LookupRequest(
        contract_version="1",
        run_id=sibling.run_id,
        trace_id=sibling.trace_id,
        tool_identity="boundary.phase1.lookup",
        tool_call_id=uuid4(),
        fault_id=sibling.fault_id,
        arguments=LookupArguments(query="phase1 lookup"),
    )
    runtime = ActivationRuntime.create(
        run_id=sibling.run_id,
        trace_id=sibling.trace_id,
        fault_id=sibling.fault_id,
        tool_call_id=request.tool_call_id,
        clock=VirtualClock(),
        waiter=NoopWaiter(),
    )
    await register_tool_call(
        database_engine,
        route_run_id=sibling.run_id,
        capability_secret=sibling.capability.capability_secret,
        request=request,
        activation_runtime=runtime,
    )
    await retire_capability(
        database_engine,
        sibling.capability.capability_record_id,
    )
    await _establish_deadline(
        database_engine,
        run_id=sibling.run_id,
        trace_id=sibling.trace_id,
    )
    await transition_run(
        database_engine,
        run_id=sibling.run_id,
        target_status="failed",
        reason="test_deadline_cutoff",
    )
    with pytest.raises(FinalizationNotReady, match="unsettled"):
        await finalize_run_evidence(
            database_engine,
            run_id=sibling.run_id,
            cutoff_reason="evidence_deadline",
        )


async def test_accepted_evidence_racing_finalization_is_included_or_late(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_control(database_engine)
    await _establish_deadline(
        database_engine,
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
    )
    await transition_run(
        database_engine,
        run_id=accepted.run_id,
        target_status="failed",
        reason="test_deadline_cutoff",
    )
    event = RunStartedEvent(
        contract_version="1",
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        event_id=uuid4(),
        source="sut",
        event_type="sut.run.started",
        boundary="run",
        producer_seq=1,
        payload=StartedPayload(schema_version=1),
    )
    page = EventPage(
        contract_version="1",
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        events=[event],
        producer_high_watermark=1,
        next_after_producer_seq=1,
    )

    async def collect() -> str:
        try:
            await collect_target_page(
                database_engine,
                run_id=accepted.run_id,
                requested_after=0,
                page=page,
            )
            return "accepted"
        except EvidenceClosed:
            return "late"

    collected, finalized = await asyncio.gather(
        collect(),
        finalize_run_evidence(
            database_engine,
            run_id=accepted.run_id,
            cutoff_reason="evidence_deadline",
        ),
    )
    async with database_engine.connect() as connection:
        accepted_target = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == accepted.run_id,
                evidence_records.c.source == "sut",
                evidence_records.c.disposition == "accepted",
            )
        )
        late_target = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == accepted.run_id,
                evidence_records.c.disposition == "late",
            )
        )
    manifest_target = sum(
        ref.source == "sut" for ref in finalized.manifest.accepted_evidence
    )
    if collected == "accepted":
        assert (accepted_target, late_target, manifest_target) == (1, 0, 1)
    else:
        assert (accepted_target, late_target, manifest_target) == (0, 1, 0)


async def test_real_vulnerable_execution_finalizes_evaluable_fail_and_analysis_is_immutable(
    database_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    control = await _execute_control(database_engine)
    control_set = await finalize_run_evidence(
        database_engine,
        run_id=control.run_id,
    )
    sibling = await create_injected_sibling(
        database_engine,
        control_run_id=control.run_id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    boundary_base = os.environ["BOUNDARY_INTERNAL_BASE_URL"]
    await execute_injected_run(
        database_engine,
        sibling=sibling,
        sut_base_url=os.environ["SUT_BASE_URL"],
        tool_endpoint=(
            f"{boundary_base}/internal/v1/runs/{sibling.run_id}"
            "/tools/phase1-lookup"
        ),
    )
    injected_set = await finalize_run_evidence(
        database_engine,
        run_id=sibling.run_id,
    )
    first = await analyze_evidence_set(
        database_engine,
        evidence_set_id=injected_set.evidence_set_id,
    )
    repeated = await analyze_evidence_set(
        database_engine,
        evidence_set_id=injected_set.evidence_set_id,
    )
    assert repeated.replayed is True
    assert repeated.analysis_id == first.analysis_id
    assert repeated.canonical_bytes == first.canonical_bytes
    assert repeated.analysis_digest == first.analysis_digest
    assert first.document.evaluability.aggregate == "EVALUABLE"
    assert len(first.document.evaluability.checks) == 6
    assert first.document.assertions is not None
    assert len(first.document.assertions) == 3
    retry = next(
        item
        for item in first.document.assertions
        if item.assertion_id == "P1.RETRY_LIMIT"
    )
    assert retry.outcome == "FAIL"
    assert {
        item.assertion_id: item.outcome
        for item in first.document.assertions
    } == {
        "P1.RETRY_LIMIT": "FAIL",
        "P1.DEGRADED_RESULT": "FAIL",
        "P1.RUN_WITHIN_BUDGET": "FAIL",
    }
    assert first.document.scenario_policy_result == "FAIL"
    assert first.document.injection_boundary is not None
    assert first.document.injection_boundary.boundary == "tool_execution"
    assert first.document.localization is not None
    assert first.document.localization.retry_ordinal == 2
    assert first.document.localization.boundary == "retry_control"
    assert first.document.localization.boundary_event_id in {
        ref.evidence_id for ref in retry.evidence_references
    }

    conflicting = first.document.model_copy(
        update={"downstream_symptoms": []}
    )
    with pytest.raises(AnalysisIntegrityError):
        await persist_analysis(
            database_engine,
            evidence_set_id=injected_set.evidence_set_id,
            document=conflicting,
        )
    with pytest.raises(AnalysisIntegrityError):
        await persist_analysis(
            database_engine,
            evidence_set_id=injected_set.evidence_set_id,
            document=conflicting,
        )
    async with database_engine.connect() as connection:
        rows = (
            await connection.execute(
                sa.select(analyses).order_by(analyses.c.created_at)
            )
        ).all()
        set_rows = (
            await connection.execute(
                sa.select(evidence_sets).order_by(evidence_sets.c.created_at)
            )
        ).all()
        all_evidence = (
            await connection.execute(sa.select(evidence_records))
        ).all()
        ordinal_two_arrival = await connection.scalar(
            sa.select(tool_calls.c.arrival_evidence_id).where(
                tool_calls.c.run_id == sibling.run_id,
                tool_calls.c.retry_ordinal == 2,
            )
        )
    assert [row.record_kind for row in rows] == [
        "authoritative",
        "integrity_failure",
    ]
    assert rows[0].analysis_id == first.analysis_id
    assert rows[0].analysis_canonical_bytes == first.canonical_bytes
    assert first.document.localization.boundary_event_id == ordinal_two_arrival
    persisted = b"".join(
        [
            *[row.manifest_canonical_bytes for row in set_rows],
            *[row.analysis_canonical_bytes for row in rows],
            *[row.payload_canonical_bytes for row in all_evidence],
        ]
    )
    assert sibling.capability.capability_secret.encode() not in persisted
    assert b"Authorization" not in persisted
    assert sibling.capability.capability_secret not in caplog.text
    assert control_set.evidence_set_digest != injected_set.evidence_set_digest
    assert len(injected_set.manifest.timeout_activations) == 2
    changed_activation = injected_set.manifest.timeout_activations[0].model_copy(
        update={"effect_proof_digest": "f" * 64}
    )
    changed_manifest = injected_set.manifest.model_copy(
        update={
            "timeout_activations": [
                changed_activation,
                injected_set.manifest.timeout_activations[1],
            ]
        }
    )
    assert canonicalize_manifest(changed_manifest)[1] != (
        injected_set.evidence_set_digest
    )
    async with database_engine.begin() as connection:
        await connection.execute(
            fault_activations.update()
            .where(
                fault_activations.c.activation_id
                == injected_set.manifest.timeout_activations[0].activation_id
            )
            .values(effect_proof_digest="f" * 64)
        )
    with pytest.raises(AnalysisInputError, match="integrity"):
        await analyze_evidence_set(
            database_engine,
            evidence_set_id=injected_set.evidence_set_id,
        )

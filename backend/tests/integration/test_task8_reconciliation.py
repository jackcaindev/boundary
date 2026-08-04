from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import rfc8785
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.api.mutations import BUNDLED_INPUT, cancel_campaign
from boundary.config import BoundarySettings
from boundary.domain.definitions import Phase1FaultDefinition
from boundary.evidence.canonical import (
    canonicalize_fault_definition,
    fault_definition_digest,
)
from boundary.evidence.collector import record_run_budget, transition_run
from boundary.evidence.finalizer import finalize_run_evidence
from boundary.executor import SerialExecutor
from boundary.execution.control import (
    SimulatedProcessLoss,
    execute_control_run,
)
from boundary.execution.injected import (
    create_injected_sibling,
    execute_injected_run,
)
from boundary.injection.capability import (
    CONTROL_TOOL_IDENTITY,
    create_control_capability,
)
from boundary.persistence.tables import (
    analyses,
    campaigns,
    comparisons,
    evidence_records,
    evidence_sets,
    regression_cases,
    reruns,
    run_capabilities,
    runs,
)
from boundary.persistence.transactions import (
    AcceptanceCommand,
    CanonicalDocument,
    accept_campaign_run,
    prepare_campaign_run_acceptance,
)
from boundary.regression.rerun import create_rerun
from boundary.sut.client import SutClient


pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"


async def _accepted(engine: AsyncEngine):
    definition = Phase1FaultDefinition.model_validate_json(
        FIXTURE_PATH.read_bytes()
    )
    canonical = canonicalize_fault_definition(definition)
    return await accept_campaign_run(
        engine,
        prepare_campaign_run_acceptance(
            AcceptanceCommand(
                idempotency_key=f"task8-reconciliation-{uuid4()}",
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


def _executor(
    engine: AsyncEngine,
    *,
    fail_after: str | None = None,
    sut_client=None,
    target_interaction_hook=None,
) -> SerialExecutor:
    return SerialExecutor(
        engine,
        BoundarySettings(
            sut_base_url="http://sample-agent:8001",
            boundary_internal_base_url="http://boundary:8000",
        ),
        _fail_after=fail_after,
        _include_unmanaged=True,
        _sut_client=sut_client,
        _target_interaction_hook=target_interaction_hook,
    )


class _NoTargetClient:
    def __init__(self) -> None:
        self.calls = {
            "create": 0,
            "status": 0,
            "events": 0,
            "cancel": 0,
        }

    async def create_run(self, *args, **kwargs):
        self.calls["create"] += 1
        raise AssertionError("target creation is forbidden")

    async def get_status(self, *args, **kwargs):
        self.calls["status"] += 1
        raise AssertionError("target status is forbidden")

    async def get_events(self, *args, **kwargs):
        self.calls["events"] += 1
        raise AssertionError("target events are forbidden")

    async def cancel_run(self, *args, **kwargs):
        self.calls["cancel"] += 1
        raise AssertionError("target cancellation is forbidden")

    async def aclose(self) -> None:
        return None


class _CountingClient:
    def __init__(self, delegate: SutClient) -> None:
        self.delegate = delegate
        self.created_run_ids = []
        self.status_run_ids = []
        self.event_run_ids = []
        self.cancelled_run_ids = []

    async def create_run(self, request, *, timeout_seconds=None):
        self.created_run_ids.append(request.run_id)
        return await self.delegate.create_run(
            request,
            timeout_seconds=timeout_seconds,
        )

    async def get_status(self, run_id, *, timeout_seconds=None):
        self.status_run_ids.append(run_id)
        return await self.delegate.get_status(
            run_id,
            timeout_seconds=timeout_seconds,
        )

    async def get_events(
        self,
        run_id,
        *,
        after_producer_seq,
        timeout_seconds=None,
    ):
        self.event_run_ids.append(run_id)
        return await self.delegate.get_events(
            run_id,
            after_producer_seq=after_producer_seq,
            timeout_seconds=timeout_seconds,
        )

    async def cancel_run(self, request, *, timeout_seconds=None):
        self.cancelled_run_ids.append(request.run_id)
        return await self.delegate.cancel_run(
            request,
            timeout_seconds=timeout_seconds,
        )

    async def aclose(self) -> None:
        await self.delegate.aclose()


async def test_accepted_reconciliation_preserves_original_identities(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)

    await _executor(database_engine).reconcile_startup()

    async with database_engine.connect() as connection:
        campaign = (
            await connection.execute(
                sa.select(campaigns).where(
                    campaigns.c.campaign_id == accepted.campaign_id
                )
            )
        ).one()
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == accepted.run_id)
            )
        ).one()
    assert campaign.status == "accepted"
    assert run.run_id == accepted.run_id
    assert run.trace_id == accepted.trace_id
    assert run.operational_status == "accepted"


async def test_restart_before_preparation_reuses_identity_and_completes(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)
    executor = _executor(database_engine)

    await executor.reconcile_startup()
    assert await executor._claim_oldest() == accepted.campaign_id
    await executor._process(accepted.campaign_id)

    async with database_engine.connect() as connection:
        campaign = (
            await connection.execute(
                sa.select(campaigns).where(
                    campaigns.c.campaign_id == accepted.campaign_id
                )
            )
        ).one()
        control = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == accepted.run_id)
            )
        ).one()
    assert campaign.status == "completed"
    assert control.run_id == accepted.run_id
    assert control.trace_id == accepted.trace_id
    assert control.operational_status == "completed"


async def test_queued_cancellation_wins_control_invocation_linearization(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)
    client = _NoTargetClient()
    cancellation_key = f"cancel-before-control-claim-{uuid4()}"
    hook_calls = 0

    async def cancel_before_claim(point, run) -> None:
        nonlocal hook_calls
        if point != "before_claim" or run.run_role != "control":
            return
        hook_calls += 1
        await cancel_campaign(
            database_engine,
            campaign_id=accepted.campaign_id,
            key=cancellation_key,
        )

    executor = _executor(
        database_engine,
        sut_client=client,
        target_interaction_hook=cancel_before_claim,
    )
    assert await executor._claim_oldest() == accepted.campaign_id
    await executor._process(accepted.campaign_id)
    replay, replayed = await cancel_campaign(
        database_engine,
        campaign_id=accepted.campaign_id,
        key=cancellation_key,
    )
    await _executor(database_engine).reconcile_startup()

    async with database_engine.connect() as connection:
        campaign = (
            await connection.execute(
                sa.select(campaigns).where(
                    campaigns.c.campaign_id == accepted.campaign_id
                )
            )
        ).one()
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == accepted.run_id)
            )
        ).one()
        evidence_set = (
            await connection.execute(
                sa.select(evidence_sets).where(
                    evidence_sets.c.run_id == accepted.run_id
                )
            )
        ).one()
        capability_state = await connection.scalar(
            sa.select(run_capabilities.c.state).where(
                run_capabilities.c.run_id == accepted.run_id
            )
        )
        run_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(runs)
            .where(runs.c.campaign_id == accepted.campaign_id)
        )
        target_evidence_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == accepted.run_id,
                evidence_records.c.source == "sut",
            )
        )
    assert hook_calls == 1
    assert client.calls == {
        "create": 0,
        "status": 0,
        "events": 0,
        "cancel": 0,
    }
    assert replayed is True
    assert replay.status == "cancelled"
    assert campaign.status == "cancelled"
    assert run.run_id == accepted.run_id
    assert run.trace_id == accepted.trace_id
    assert run.operational_status == "cancelled"
    assert run.reported_tested_agent_id is None
    assert run.target_producer_cursor == 0
    assert run.target_final_watermark is None
    assert run_count == 1
    assert target_evidence_count == 0
    assert capability_state == "retired"
    assert evidence_set.cutoff_reason == "cancellation_grace"


async def test_restart_settles_cancellation_committed_during_preparation(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)
    client = _NoTargetClient()

    async def cancel_then_lose_process(point, run) -> None:
        if point != "before_claim" or run.run_role != "control":
            return
        await cancel_campaign(
            database_engine,
            campaign_id=accepted.campaign_id,
            key=f"cancel-before-process-loss-{uuid4()}",
        )
        raise SimulatedProcessLoss(
            "simulated process loss after durable cancellation"
        )

    crashing = _executor(
        database_engine,
        sut_client=client,
        target_interaction_hook=cancel_then_lose_process,
    )
    assert await crashing._claim_oldest() == accepted.campaign_id
    with pytest.raises(SimulatedProcessLoss, match="durable cancellation"):
        await crashing._process(accepted.campaign_id)

    restarted = _executor(database_engine)
    await restarted.reconcile_startup()
    await restarted.reconcile_startup()

    async with database_engine.connect() as connection:
        campaign = (
            await connection.execute(
                sa.select(campaigns).where(
                    campaigns.c.campaign_id == accepted.campaign_id
                )
            )
        ).one()
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == accepted.run_id)
            )
        ).one()
        evidence_set_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_sets)
            .where(evidence_sets.c.run_id == accepted.run_id)
        )
        terminal_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == accepted.run_id,
                evidence_records.c.event_type == "boundary.run.terminal",
            )
        )
        capability_state = await connection.scalar(
            sa.select(run_capabilities.c.state).where(
                run_capabilities.c.run_id == accepted.run_id
            )
        )
    assert client.calls == {
        "create": 0,
        "status": 0,
        "events": 0,
        "cancel": 0,
    }
    assert campaign.status == "cancelled"
    assert run.operational_status == "cancelled"
    assert run.evidence_open is False
    assert capability_state == "retired"
    assert evidence_set_count == 1
    assert terminal_count == 1


async def test_execution_claim_wins_then_uses_active_cancellation(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)
    delegate = SutClient(os.environ["SUT_BASE_URL"], timeout_seconds=5)
    client = _CountingClient(delegate)
    hook_calls = 0

    async def cancel_after_claim(point, run) -> None:
        nonlocal hook_calls
        if point != "after_claim" or run.run_role != "control":
            return
        hook_calls += 1
        await cancel_campaign(
            database_engine,
            campaign_id=accepted.campaign_id,
            key=f"cancel-after-control-claim-{uuid4()}",
        )

    executor = _executor(
        database_engine,
        sut_client=client,
        target_interaction_hook=cancel_after_claim,
    )
    try:
        assert await executor._claim_oldest() == accepted.campaign_id
        await executor._process(accepted.campaign_id)
    finally:
        await client.aclose()

    async with database_engine.connect() as connection:
        campaign = (
            await connection.execute(
                sa.select(campaigns).where(
                    campaigns.c.campaign_id == accepted.campaign_id
                )
            )
        ).one()
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == accepted.run_id)
            )
        ).one()
        evidence_set_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_sets)
            .where(evidence_sets.c.run_id == accepted.run_id)
        )
        run_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(runs)
            .where(runs.c.campaign_id == accepted.campaign_id)
        )
    assert hook_calls == 1
    assert client.created_run_ids == [accepted.run_id]
    assert client.cancelled_run_ids == [accepted.run_id]
    assert client.event_run_ids
    assert campaign.status == "cancelled"
    assert run.run_id == accepted.run_id
    assert run.trace_id == accepted.trace_id
    assert run.operational_status == "cancelled"
    assert run_count == 1
    assert evidence_set_count == 1


async def test_queued_cancellation_wins_shared_injected_invocation_boundary(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)
    await execute_control_run(
        database_engine,
        run_id=accepted.run_id,
        sut_base_url=os.environ["SUT_BASE_URL"],
        tool_endpoint=(
            f"{os.environ['BOUNDARY_INTERNAL_BASE_URL']}/internal/v1/runs/"
            f"{accepted.run_id}/tools/phase1-lookup"
        ),
        tested_input=BUNDLED_INPUT,
    )
    await finalize_run_evidence(database_engine, run_id=accepted.run_id)
    sibling = await create_injected_sibling(
        database_engine,
        control_run_id=accepted.run_id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    client = _NoTargetClient()
    hook_calls = 0

    async def cancel_injected_before_claim(point, run) -> None:
        nonlocal hook_calls
        if point != "before_claim" or run.run_role != "injected":
            return
        hook_calls += 1
        await cancel_campaign(
            database_engine,
            campaign_id=accepted.campaign_id,
            key=f"cancel-before-injected-claim-{uuid4()}",
        )

    result = await execute_injected_run(
        database_engine,
        sibling=sibling,
        sut_base_url=os.environ["SUT_BASE_URL"],
        tool_endpoint=(
            f"{os.environ['BOUNDARY_INTERNAL_BASE_URL']}/internal/v1/runs/"
            f"{sibling.run_id}/tools/phase1-lookup"
        ),
        sut_client=client,
        _target_interaction_hook=cancel_injected_before_claim,
    )
    await _executor(database_engine).reconcile_startup()

    async with database_engine.connect() as connection:
        campaign = (
            await connection.execute(
                sa.select(campaigns).where(
                    campaigns.c.campaign_id == accepted.campaign_id
                )
            )
        ).one()
        injected = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == sibling.run_id)
            )
        ).one()
        evidence_set = (
            await connection.execute(
                sa.select(evidence_sets).where(
                    evidence_sets.c.run_id == sibling.run_id
                )
            )
        ).one()
        capability_state = await connection.scalar(
            sa.select(run_capabilities.c.state).where(
                run_capabilities.c.capability_record_id
                == sibling.capability.capability_record_id
            )
        )
    assert hook_calls == 1
    assert client.calls == {
        "create": 0,
        "status": 0,
        "events": 0,
        "cancel": 0,
    }
    assert result.operational_status == "cancelled"
    assert campaign.status == "cancelled"
    assert injected.run_id == sibling.run_id
    assert injected.trace_id == sibling.trace_id
    assert injected.fault_id == sibling.fault_id
    assert injected.operational_status == "cancelled"
    assert injected.reported_tested_agent_id is None
    assert injected.target_producer_cursor == 0
    assert injected.target_final_watermark is None
    assert capability_state == "retired"
    assert evidence_set.cutoff_reason == "cancellation_grace"


async def test_startup_repairs_legacy_open_queued_cancellation(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)
    grant = await create_control_capability(
        database_engine,
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        tool_identity=CONTROL_TOOL_IDENTITY,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    await cancel_campaign(
        database_engine,
        campaign_id=accepted.campaign_id,
        key=f"legacy-cancel-{uuid4()}",
    )
    async with database_engine.begin() as connection:
        await connection.execute(
            campaigns.update()
            .where(campaigns.c.campaign_id == accepted.campaign_id)
            .values(status="cancelled", current_step="cancelled")
        )
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == accepted.run_id)
            .values(operational_status="cancelled")
        )

    await _executor(database_engine).reconcile_startup()

    async with database_engine.connect() as connection:
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == accepted.run_id)
            )
        ).one()
        evidence_set = (
            await connection.execute(
                sa.select(evidence_sets).where(
                    evidence_sets.c.run_id == accepted.run_id
                )
            )
        ).one()
        terminal_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == accepted.run_id,
                evidence_records.c.event_type == "boundary.run.terminal",
            )
        )
        target_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == accepted.run_id,
                evidence_records.c.source == "sut",
            )
        )
        capability_state = await connection.scalar(
            sa.select(run_capabilities.c.state).where(
                run_capabilities.c.capability_record_id
                == grant.capability_record_id
            )
        )
    assert run.operational_status == "cancelled"
    assert run.evidence_open is False
    assert evidence_set.cutoff_reason == "cancellation_grace"
    assert terminal_count == 1
    assert target_count == 0
    assert capability_state == "retired"


@pytest.mark.parametrize(
    ("fail_after", "expected_budgets"),
    [
        ("control_capability", 0),
        ("control_budget", 1),
        ("target_interaction", 1),
    ],
)
async def test_lost_control_preparation_is_not_rebound_or_reinvoked(
    database_engine: AsyncEngine,
    fail_after: str,
    expected_budgets: int,
) -> None:
    accepted = await _accepted(database_engine)
    crashing = _executor(database_engine, fail_after=fail_after)
    assert await crashing._claim_oldest() == accepted.campaign_id
    with pytest.raises(SimulatedProcessLoss, match="simulated process loss"):
        await crashing._process(accepted.campaign_id)

    restarted = _executor(database_engine)
    await restarted.reconcile_startup()

    async with database_engine.connect() as connection:
        campaign = (
            await connection.execute(
                sa.select(campaigns).where(
                    campaigns.c.campaign_id == accepted.campaign_id
                )
            )
        ).one()
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == accepted.run_id)
            )
        ).one()
        capability_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(run_capabilities)
            .where(run_capabilities.c.run_id == accepted.run_id)
        )
        budget_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == accepted.run_id,
                evidence_records.c.event_type == "boundary.run_budget.bound",
            )
        )
        deadline_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == accepted.run_id,
                evidence_records.c.event_type == "boundary.deadline.reached",
            )
        )
        target_event_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == accepted.run_id,
                evidence_records.c.source == "sut",
            )
        )
        evidence_set = (
            await connection.execute(
                sa.select(evidence_sets).where(
                    evidence_sets.c.run_id == accepted.run_id
                )
            )
        ).one()
    assert campaign.status == "failed"
    assert run.operational_status == "failed"
    assert capability_count == 1
    assert budget_count == expected_budgets
    assert deadline_count == 0
    assert target_event_count == 0
    assert evidence_set.cutoff_reason == "reconciliation_error"
    assert await restarted._claim_oldest() is None


async def test_polling_checkpoint_resumes_without_repreparing_target(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)
    crashing = _executor(database_engine, fail_after="polling_checkpoint")
    assert await crashing._claim_oldest() == accepted.campaign_id
    with pytest.raises(SimulatedProcessLoss, match="polling_checkpoint"):
        await crashing._process(accepted.campaign_id)

    restarted = _executor(database_engine)
    await restarted.reconcile_startup()
    assert await restarted._claim_oldest() == accepted.campaign_id
    await restarted._process(accepted.campaign_id)

    async with database_engine.connect() as connection:
        campaign_status = await connection.scalar(
            sa.select(campaigns.c.status).where(
                campaigns.c.campaign_id == accepted.campaign_id
            )
        )
        capability_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(run_capabilities)
            .where(run_capabilities.c.run_id == accepted.run_id)
        )
        budget_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == accepted.run_id,
                evidence_records.c.event_type == "boundary.run_budget.bound",
            )
        )
    assert campaign_status == "completed"
    assert capability_count == 1
    assert budget_count == 1


async def test_bundled_committed_injected_sibling_reconciles_without_execution(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)
    crashing = _executor(
        database_engine,
        fail_after="bundled_injected_sibling",
    )
    assert await crashing._claim_oldest() == accepted.campaign_id
    with pytest.raises(
        SimulatedProcessLoss,
        match="bundled_injected_sibling",
    ):
        await crashing._process(accepted.campaign_id)

    restarted = _executor(database_engine)
    await restarted.reconcile_startup()

    async with database_engine.connect() as connection:
        injected = (
            await connection.execute(
                sa.select(runs).where(
                    runs.c.campaign_id == accepted.campaign_id,
                    runs.c.run_role == "injected",
                )
            )
        ).one()
        evidence_set = (
            await connection.execute(
                sa.select(evidence_sets).where(
                    evidence_sets.c.run_id == injected.run_id
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
    assert injected.operational_status == "failed"
    assert injected.reported_tested_agent_id is None
    assert injected.target_producer_cursor == 0
    assert evidence_set.cutoff_reason == "reconciliation_error"
    assert analysis.evaluability_aggregate == "EXECUTION_ERROR"


async def test_rerun_committed_candidate_reconciles_without_execution(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)
    source_executor = _executor(database_engine)
    assert await source_executor._claim_oldest() == accepted.campaign_id
    await source_executor._process(accepted.campaign_id)
    async with database_engine.connect() as connection:
        case_id = await connection.scalar(
            sa.select(regression_cases.c.regression_case_id)
        )
    assert case_id is not None
    accepted_rerun = await create_rerun(
        database_engine,
        regression_case_id=case_id,
        mode="version_comparison",
        tested_agent_version="fixed-v1",
    )
    assert accepted_rerun.campaign_id is not None

    crashing = _executor(
        database_engine,
        fail_after="rerun_candidate_sibling",
    )
    assert await crashing._claim_oldest() == accepted_rerun.campaign_id
    with pytest.raises(
        SimulatedProcessLoss,
        match="rerun_candidate_sibling",
    ):
        await crashing._process(accepted_rerun.campaign_id)

    restarted = _executor(database_engine)
    await restarted.reconcile_startup()

    async with database_engine.connect() as connection:
        rerun = (
            await connection.execute(
                sa.select(reruns).where(
                    reruns.c.rerun_id == accepted_rerun.rerun_id
                )
            )
        ).one()
        candidate = (
            await connection.execute(
                sa.select(runs).where(
                    runs.c.run_id == rerun.candidate_run_id
                )
            )
        ).one()
        candidate_set = (
            await connection.execute(
                sa.select(evidence_sets).where(
                    evidence_sets.c.run_id == candidate.run_id
                )
            )
        ).one()
        candidate_analysis = (
            await connection.execute(
                sa.select(analyses).where(
                    analyses.c.evidence_set_id
                    == candidate_set.evidence_set_id,
                    analyses.c.record_kind == "authoritative",
                )
            )
        ).one()
    assert candidate.operational_status == "failed"
    assert candidate.reported_tested_agent_id is None
    assert candidate.target_producer_cursor == 0
    assert candidate_set.cutoff_reason == "reconciliation_error"
    assert candidate_analysis.evaluability_aggregate == "EXECUTION_ERROR"


async def test_version_comparison_candidate_cancellation_settles_all_resources(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)
    source_executor = _executor(database_engine)
    assert await source_executor._claim_oldest() == accepted.campaign_id
    await source_executor._process(accepted.campaign_id)
    async with database_engine.connect() as connection:
        case_id = await connection.scalar(
            sa.select(regression_cases.c.regression_case_id)
        )
    assert case_id is not None
    accepted_rerun = await create_rerun(
        database_engine,
        regression_case_id=case_id,
        mode="version_comparison",
        tested_agent_version="fixed-v1",
    )
    assert accepted_rerun.campaign_id is not None
    assert accepted_rerun.comparison_id is not None

    cancellation_key = f"cancel-rerun-candidate-{uuid4()}"
    hook_calls = 0

    async def cancel_candidate_before_claim(point, run) -> None:
        nonlocal hook_calls
        if point != "before_claim" or run.run_role != "injected":
            return
        hook_calls += 1
        await cancel_campaign(
            database_engine,
            campaign_id=accepted_rerun.campaign_id,
            key=cancellation_key,
        )

    target_client = _CountingClient(
        SutClient(os.environ["SUT_BASE_URL"], timeout_seconds=5)
    )
    executor = _executor(
        database_engine,
        sut_client=target_client,
        target_interaction_hook=cancel_candidate_before_claim,
    )
    try:
        assert await executor._claim_oldest() == accepted_rerun.campaign_id
        await executor._process(accepted_rerun.campaign_id)
    finally:
        await target_client.aclose()

    replay, replayed = await cancel_campaign(
        database_engine,
        campaign_id=accepted_rerun.campaign_id,
        key=cancellation_key,
    )
    restarted = _executor(database_engine)
    await restarted.reconcile_startup()
    await restarted.reconcile_startup()

    async with database_engine.connect() as connection:
        campaign = (
            await connection.execute(
                sa.select(campaigns).where(
                    campaigns.c.campaign_id == accepted_rerun.campaign_id
                )
            )
        ).one()
        rerun = (
            await connection.execute(
                sa.select(reruns).where(
                    reruns.c.rerun_id == accepted_rerun.rerun_id
                )
            )
        ).one()
        comparison = (
            await connection.execute(
                sa.select(comparisons).where(
                    comparisons.c.comparison_id
                    == accepted_rerun.comparison_id
                )
            )
        ).one()
        candidate = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == rerun.candidate_run_id)
            )
        ).one()
        candidate_set = (
            await connection.execute(
                sa.select(evidence_sets).where(
                    evidence_sets.c.run_id == candidate.run_id
                )
            )
        ).one()
        candidate_analysis = (
            await connection.execute(
                sa.select(analyses).where(
                    analyses.c.evidence_set_id == candidate_set.evidence_set_id,
                    analyses.c.record_kind == "authoritative",
                )
            )
        ).one()

    candidate_id = candidate.run_id
    assert hook_calls == 1
    assert candidate_id not in target_client.created_run_ids
    assert candidate_id not in target_client.status_run_ids
    assert candidate_id not in target_client.event_run_ids
    assert candidate_id not in target_client.cancelled_run_ids
    assert replayed is True
    assert replay.status == "cancelled"
    assert campaign.status == "cancelled"
    assert rerun.status == "failed"
    assert rerun.reason_code == "CAMPAIGN_CANCELLED"
    assert comparison.status == "ineligible"
    assert comparison.terminal_result == "INELIGIBLE"
    assert comparison.reason_code == "CAMPAIGN_CANCELLED"
    assert comparison.candidate_run_id == candidate_id
    assert comparison.candidate_evidence_set_id == candidate_set.evidence_set_id
    assert comparison.candidate_analysis_id == candidate_analysis.analysis_id
    assert candidate.operational_status == "cancelled"
    assert candidate_set.cutoff_reason == "cancellation_grace"
    assert comparison.summary_canonical_bytes == rfc8785.dumps(
        comparison.summary_document
    )
    assert comparison.summary_digest == sha256(
        comparison.summary_canonical_bytes
    ).hexdigest()

    async with httpx.AsyncClient(
        base_url=os.environ["BOUNDARY_INTERNAL_BASE_URL"], timeout=5.0
    ) as client:
        public_campaign = await client.get(
            f"/api/v1/campaigns/{accepted_rerun.campaign_id}"
        )
        public_comparison = await client.get(
            f"/api/v1/comparisons/{accepted_rerun.comparison_id}"
        )
    assert public_campaign.status_code == 200, public_campaign.text
    assert public_comparison.status_code == 200, public_comparison.text
    assert public_campaign.json()["operational_status"] == "cancelled"
    assert public_campaign.json()["terminal"] is True
    assert public_comparison.json()["status"] == "ineligible"
    assert public_comparison.json()["terminal"] is True
    assert public_comparison.json()["terminal_reason"] == "CAMPAIGN_CANCELLED"
    assert public_comparison.json()["scoped_conclusion"] is None
    assert public_comparison.json()["candidate_run_id"] == str(candidate_id)
    assert public_comparison.json()["candidate_evidence_set_id"] == str(
        candidate_set.evidence_set_id
    )
    assert public_comparison.json()["candidate_analysis_id"] == str(
        candidate_analysis.analysis_id
    )


async def test_polling_checkpoint_requires_persisted_budget_and_no_unsettled_work(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)
    await create_control_capability(
        database_engine,
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        tool_identity=CONTROL_TOOL_IDENTITY,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    await record_run_budget(
        database_engine,
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        execution_budget_ms=30_000,
        budget_started_monotonic_ns=1_000_000,
        deadline_monotonic_ns=30_001_000_000,
    )
    await transition_run(
        database_engine,
        run_id=accepted.run_id,
        target_status="running",
        reason="validated_target_progress",
    )
    async with database_engine.begin() as connection:
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == accepted.run_id)
            .values(
                execution_checkpoint="polling",
                reported_tested_agent_id="boundary.sample-agent",
                reported_tested_agent_version="vulnerable-v1",
            )
        )

    executor = _executor(database_engine)
    assert await executor._safe_polling_checkpoint(accepted.run_id) is True

    async with database_engine.connect() as connection:
        cursor = await connection.scalar(
            sa.select(runs.c.target_producer_cursor).where(
                runs.c.run_id == accepted.run_id
            )
        )
    assert cursor == 0


async def test_ambiguous_target_interaction_fails_without_synthesizing_effect(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted(database_engine)
    await transition_run(
        database_engine,
        run_id=accepted.run_id,
        target_status="running",
        reason="validated_target_progress",
    )
    async with database_engine.begin() as connection:
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == accepted.run_id)
            .values(execution_checkpoint="target_interaction")
        )

    executor = _executor(database_engine)
    await executor._reconcile_ambiguous_run(accepted.run_id)
    await executor._fail_campaign(
        accepted.campaign_id, "RUNTIME_LOST_UNPROVEN"
    )

    async with database_engine.connect() as connection:
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == accepted.run_id)
            )
        ).one()
        campaign = (
            await connection.execute(
                sa.select(campaigns).where(
                    campaigns.c.campaign_id == accepted.campaign_id
                )
            )
        ).one()
        event_types = set(
            (
                await connection.execute(
                    sa.select(evidence_records.c.event_type).where(
                        evidence_records.c.run_id == accepted.run_id
                    )
                )
            ).scalars()
        )
        evidence_set = (
            await connection.execute(
                sa.select(evidence_sets).where(
                    evidence_sets.c.run_id == accepted.run_id
                )
            )
        ).one()
    assert run.operational_status == "failed"
    assert run.reconciliation_reason == "RUNTIME_LOST_UNPROVEN"
    assert campaign.status == "failed"
    assert "boundary.reconciliation.execution_error" in event_types
    assert "boundary.fault_effect_realized" not in event_types
    assert evidence_set.cutoff_reason == "reconciliation_error"

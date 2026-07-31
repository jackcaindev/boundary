from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.evidence.canonical import canonicalize_fault_definition, fault_definition_digest
from boundary.execution.control import execute_control_run
from boundary.execution.injected import create_injected_sibling, execute_injected_run
from boundary.persistence.tables import evidence_records, fault_activations, tool_calls
from boundary.persistence.transactions import (
    AcceptanceCommand,
    CanonicalDocument,
    accept_campaign_run,
    prepare_campaign_run_acceptance,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"


async def _accepted_control(engine: AsyncEngine, version: str):
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    definition = Phase1FaultDefinition.model_validate(raw)
    canonical = canonicalize_fault_definition(definition)
    return await accept_campaign_run(
        engine,
        prepare_campaign_run_acceptance(
            AcceptanceCommand(
                idempotency_key=f"task5-compose-{version}-{uuid4()}",
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


async def _execute_real_pair(
    engine: AsyncEngine,
    *,
    version: str,
):
    control = await _accepted_control(engine, version)
    base_url = os.environ["SUT_BASE_URL"]
    boundary_base = os.environ["BOUNDARY_INTERNAL_BASE_URL"]
    await execute_control_run(
        engine,
        run_id=control.run_id,
        sut_base_url=base_url,
        tool_endpoint=(
            f"{boundary_base}/internal/v1/runs/{control.run_id}/tools/phase1-lookup"
        ),
        tested_input="phase1 lookup",
    )
    sibling = await create_injected_sibling(
        engine,
        control_run_id=control.run_id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    started = time.monotonic()
    result = await execute_injected_run(
        engine,
        sibling=sibling,
        sut_base_url=base_url,
        tool_endpoint=(
            f"{boundary_base}/internal/v1/runs/{sibling.run_id}/tools/phase1-lookup"
        ),
    )
    elapsed = time.monotonic() - started
    async with engine.connect() as connection:
        calls = (
            await connection.execute(
                sa.select(tool_calls)
                .where(tool_calls.c.run_id == sibling.run_id)
                .order_by(tool_calls.c.retry_ordinal)
            )
        ).all()
        activations = (
            await connection.execute(
                sa.select(fault_activations)
                .where(fault_activations.c.run_id == sibling.run_id)
                .order_by(fault_activations.c.activation_ordinal)
            )
        ).all()
        evidence = (
            await connection.execute(
                sa.select(evidence_records)
                .where(evidence_records.c.run_id == sibling.run_id)
                .order_by(evidence_records.c.receipt_seq)
            )
        ).all()
    return sibling, result, elapsed, calls, activations, evidence


def _assert_complete_real_timeout_chains(activations, evidence) -> None:
    assert len(activations) == 2
    receipt_by_id = {row.evidence_id: row.receipt_seq for row in evidence}
    for activation in activations:
        assert activation.effect_status == "effect_realized"
        assert activation.activation_started_ns < activation.client_timeout_boundary_ns
        assert activation.effect_proof["observed_monotonic_ns"] >= activation.client_timeout_boundary_ns
        assert activation.runtime_completed_monotonic_ns >= activation.hold_deadline_ns
        assert activation.runtime_completed_monotonic_ns <= activation.hold_deadline_ns + 1_500_000_000
        assert receipt_by_id[activation.activation_evidence_id] < receipt_by_id[activation.effect_evidence_id]
        assert activation.hold_disposition == "bounded_hold_complete"


async def test_real_http_vulnerable_execution_proves_two_effects_and_ordinal_two(
    database_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sibling, result, elapsed, calls, activations, evidence = await _execute_real_pair(
        database_engine,
        version="vulnerable-v1",
    )
    _assert_complete_real_timeout_chains(activations, evidence)
    assert result.operational_status == "completed"
    assert elapsed < 5.0
    assert [call.retry_ordinal for call in calls] == [0, 1, 2]
    assert calls[0].response_disposition is None
    assert calls[1].response_disposition is None
    assert calls[2].registration_outcome == "attempt_not_selected"
    assert calls[2].response_disposition == "success_response_committed"
    persisted = b"".join(row.payload_canonical_bytes for row in evidence)
    assert sibling.capability.capability_secret.encode() not in persisted
    assert b"Authorization" not in persisted
    assert sibling.capability.capability_secret not in caplog.text


async def test_real_http_fixed_execution_proves_two_effects_without_ordinal_two(
    database_engine: AsyncEngine,
) -> None:
    _, result, elapsed, calls, activations, evidence = await _execute_real_pair(
        database_engine,
        version="fixed-v1",
    )
    _assert_complete_real_timeout_chains(activations, evidence)
    assert result.operational_status == "completed"
    assert elapsed < 5.0
    assert [call.retry_ordinal for call in calls] == [0, 1]
    assert all(call.response_disposition is None for call in calls)
    assert all(call.registration_outcome == "pre_effect_reserved" for call in calls)

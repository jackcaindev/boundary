"""Transactional public mutation services."""

from __future__ import annotations

import json
from hashlib import sha256
from uuid import UUID, uuid4

import rfc8785
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.api.errors import PublicProblem
from boundary.domain.definitions import Phase1FaultDefinition
from boundary.evidence.canonical import (
    canonicalize_fault_definition,
    fault_definition_digest,
)
from boundary.persistence.tables import (
    analyses,
    campaigns,
    evidence_records,
    evidence_sets,
    idempotency_records,
    regression_cases,
    runs,
)
from boundary.persistence.transactions import (
    AcceptanceCommand,
    CanonicalDocument,
    IdempotencyConflict,
    PersistenceFailure,
    accept_campaign_run,
    prepare_campaign_run_acceptance,
)
from boundary.regression.materializer import (
    MaterializationIneligible,
    RegressionIntegrityError,
    RegressionPersistenceError,
    materialize_regression_case,
)
from boundary.regression.rerun import (
    AcceptedRerun,
    RerunConflict,
    RerunError,
    create_rerun,
)


BUNDLED_INPUT = "phase1 lookup"
BUNDLED_FAULT = Phase1FaultDefinition.model_validate(
    {
        "schema_version": 1,
        "fault_kind": "tool_timeout",
        "target_tool": "boundary.phase1.lookup",
        "trigger_rule": "retry_ordinal_in",
        "affected_attempts": [0, 1],
        "tool_client_timeout_ms": 500,
        "injected_hold_ms": 1000,
        "maximum_activations": 2,
        "scenario_id": "phase1.tool-timeout",
        "scenario_version": 1,
        "compatible_contract_versions": ["1"],
    }
)


def mutation_digest(operation: str, content: dict) -> str:
    return sha256(
        rfc8785.dumps({"operation_kind": operation, "request": content})
    ).hexdigest()


async def accept_bundled_campaign(engine: AsyncEngine, key: str):
    canonical = canonicalize_fault_definition(BUNDLED_FAULT)
    command = AcceptanceCommand(
        idempotency_key=key,
        contract_version="1",
        scenario_id="phase1.tool-timeout",
        scenario_version=1,
        tested_agent_id="boundary.sample-agent",
        tested_agent_version="vulnerable-v1",
        run_definition=CanonicalDocument(
            schema_version=1,
            document=BUNDLED_FAULT.model_dump(mode="json"),
            canonical_bytes=canonical,
            digest=fault_definition_digest(BUNDLED_FAULT),
        ),
        executor_managed=True,
    )
    try:
        return await accept_campaign_run(
            engine, prepare_campaign_run_acceptance(command)
        )
    except IdempotencyConflict as error:
        raise PublicProblem(409, error.code, "idempotency key conflicts with a prior request") from error
    except PersistenceFailure as error:
        raise PublicProblem(500, error.code, "campaign acceptance could not be persisted") from error


async def _mapping(
    engine: AsyncEngine, *, operation: str, key: str, digest: str
):
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                sa.select(idempotency_records).where(
                    idempotency_records.c.operation_kind == operation,
                    idempotency_records.c.idempotency_key == key,
                )
            )
        ).one_or_none()
    if row is not None and row.request_digest != digest:
        raise PublicProblem(409, "IDEMPOTENCY_CONFLICT", "idempotency key conflicts with a prior request")
    return row


async def ensure_regression_case(
    engine: AsyncEngine, *, run_id: UUID, key: str
):
    lock_value = int.from_bytes(
        sha256(f"regression.materialize\0{key}".encode("utf-8")).digest()[:8],
        "big",
        signed=True,
    )
    async with engine.connect() as lock_connection:
        await lock_connection.execute(
            sa.text("SELECT pg_advisory_lock(:lock_value)"),
            {"lock_value": lock_value},
        )
        try:
            return await _ensure_regression_case_locked(
                engine, run_id=run_id, key=key
            )
        finally:
            await lock_connection.execute(
                sa.text("SELECT pg_advisory_unlock(:lock_value)"),
                {"lock_value": lock_value},
            )


async def _ensure_regression_case_locked(
    engine: AsyncEngine, *, run_id: UUID, key: str
):
    operation = "regression.materialize"
    digest = mutation_digest(operation, {"run_id": str(run_id)})
    mapping = await _mapping(engine, operation=operation, key=key, digest=digest)
    if mapping is not None:
        async with engine.connect() as connection:
            row = (await connection.execute(sa.select(regression_cases).where(regression_cases.c.regression_case_id == mapping.resource_id))).one_or_none()
        if row is None:
            raise PublicProblem(500, "IDEMPOTENCY_MAPPING_INVALID", "idempotency mapping is missing its resource")
        return row.regression_case_id, row.source_run_id, True
    async with engine.connect() as connection:
        run = (await connection.execute(sa.select(runs).where(runs.c.run_id == run_id))).one_or_none()
        if run is None:
            raise PublicProblem(404, "RUN_NOT_FOUND", "run does not exist")
        if run.run_role != "injected":
            raise PublicProblem(409, "SOURCE_RUN_NOT_ORIGINAL_INJECTED", "only an original injected FAIL is eligible")
        evidence_set_id = await connection.scalar(sa.select(evidence_sets.c.evidence_set_id).where(evidence_sets.c.run_id == run_id))
        analysis = None
        if evidence_set_id is not None:
            analysis = (await connection.execute(sa.select(analyses).where(analyses.c.evidence_set_id == evidence_set_id, analyses.c.record_kind == "authoritative"))).first()
        if analysis is None:
            raise PublicProblem(409, "SOURCE_ANALYSIS_NOT_AVAILABLE", "run does not have an authoritative analysis")
    try:
        result = await materialize_regression_case(engine, source_analysis_id=analysis.analysis_id)
    except MaterializationIneligible as error:
        raise PublicProblem(409, error.reason_code, "run is not eligible for regression materialization") from error
    except (RegressionIntegrityError, RegressionPersistenceError) as error:
        raise PublicProblem(500, "REGRESSION_MATERIALIZATION_FAILED", "regression materialization failed safely") from error
    try:
        async with engine.begin() as connection:
            inserted = await connection.execute(
                pg_insert(idempotency_records).values(
                    operation_kind=operation,
                    idempotency_key=key,
                    request_digest=digest,
                    campaign_id=run.campaign_id,
                    run_id=run_id,
                    resource_kind="regression_case",
                    resource_id=result.regression_case_id,
                    resource_links={
                        "regression_case_id": str(result.regression_case_id),
                        "source_run_id": str(run_id),
                    },
                ).on_conflict_do_nothing(index_elements=[idempotency_records.c.operation_kind, idempotency_records.c.idempotency_key])
            )
        if inserted.rowcount == 0:
            mapping = await _mapping(engine, operation=operation, key=key, digest=digest)
            assert mapping is not None
            if mapping.resource_id != result.regression_case_id:
                raise PublicProblem(409, "IDEMPOTENCY_CONFLICT", "idempotency resource mapping conflicts")
    except SQLAlchemyError as error:
        raise PublicProblem(500, "IDEMPOTENCY_PERSISTENCE_FAILED", "mutation mapping could not be persisted") from error
    return result.regression_case_id, run_id, result.replayed


async def accept_rerun(
    engine: AsyncEngine,
    *,
    case_id: UUID,
    mode: str,
    tested_agent_version: str,
    key: str,
) -> AcceptedRerun:
    operation = "regression.rerun.accept"
    digest = mutation_digest(
        operation,
        {
            "regression_case_id": str(case_id),
            "mode": mode,
            "tested_agent_version": tested_agent_version,
        },
    )
    try:
        accepted = await create_rerun(
            engine,
            regression_case_id=case_id,
            mode=mode,
            tested_agent_version=tested_agent_version,
            idempotency_key=key,
            request_digest=digest,
        )
    except MaterializationIneligible as error:
        status = 404 if error.reason_code == "REGRESSION_CASE_NOT_FOUND" else 409
        raise PublicProblem(status, error.reason_code, "regression case is unavailable") from error
    except RerunConflict as error:
        code = "IDEMPOTENCY_CONFLICT" if str(error) == "IDEMPOTENCY_CONFLICT" else "RERUN_CONFLICT"
        raise PublicProblem(409, code, "rerun request conflicts with existing state") from error
    except (RerunError, ValueError) as error:
        raise PublicProblem(409, "RERUN_REJECTED", "rerun request is not eligible") from error
    if accepted.status == "rejected":
        raise PublicProblem(409, accepted.reason_code or "RERUN_REJECTED", "rerun was rejected before execution")
    return accepted


async def cancel_campaign(engine: AsyncEngine, *, campaign_id: UUID, key: str):
    operation = "campaign.cancel"
    digest = mutation_digest(operation, {"campaign_id": str(campaign_id)})
    cancellation_id = uuid4()
    try:
        async with engine.begin() as connection:
            existing = (await connection.execute(sa.select(idempotency_records).where(idempotency_records.c.operation_kind == operation, idempotency_records.c.idempotency_key == key))).one_or_none()
            if existing is not None:
                if existing.request_digest != digest:
                    raise PublicProblem(409, "IDEMPOTENCY_CONFLICT", "idempotency key conflicts with a prior request")
                campaign = (await connection.execute(sa.select(campaigns).where(campaigns.c.campaign_id == campaign_id))).one_or_none()
                if campaign is None:
                    raise PublicProblem(500, "IDEMPOTENCY_MAPPING_INVALID", "idempotency mapping is missing its campaign")
                return campaign, True
            campaign = (await connection.execute(sa.select(campaigns).where(campaigns.c.campaign_id == campaign_id).with_for_update())).one_or_none()
            if campaign is None:
                raise PublicProblem(404, "CAMPAIGN_NOT_FOUND", "campaign does not exist")
            terminal = campaign.status in {"completed", "failed", "cancelled"}
            if not terminal and not campaign.cancel_requested:
                await connection.execute(campaigns.update().where(campaigns.c.campaign_id == campaign_id).values(
                    cancel_requested=True,
                    cancellation_id=cancellation_id,
                    cancel_requested_at=sa.func.now(),
                ))
                current_run = (await connection.execute(sa.select(runs).where(runs.c.campaign_id == campaign_id, runs.c.operational_status.in_(["accepted", "running"])).order_by(runs.c.created_at.desc()).limit(1).with_for_update())).one_or_none()
                if current_run is not None:
                    cancellation_event_id = uuid4()
                    payload = {
                        "campaign_id": str(campaign_id),
                        "cancellation_id": str(cancellation_id),
                        "reason": "public_campaign_cancellation",
                        "run_id": str(current_run.run_id),
                        "schema_version": 1,
                    }
                    canonical = rfc8785.dumps(payload)
                    await connection.execute(evidence_records.insert().values(
                        evidence_id=cancellation_event_id, run_id=current_run.run_id,
                        source="boundary", event_type="boundary.campaign_cancellation.requested",
                        boundary="run", source_event_id=cancellation_event_id,
                        producer_seq=None, receipt_seq=current_run.next_receipt_seq,
                        audit_seq=None, caused_by_event_id=None, payload_schema_version=1,
                        payload=payload, payload_canonical_bytes=canonical,
                        payload_digest=sha256(canonical).hexdigest(), disposition="accepted",
                    ))
                    await connection.execute(
                        runs.update()
                        .where(runs.c.run_id == current_run.run_id)
                        .values(
                            next_receipt_seq=current_run.next_receipt_seq + 1
                        )
                    )
            effective_id = campaign.cancellation_id or (None if terminal else cancellation_id)
            await connection.execute(idempotency_records.insert().values(
                operation_kind=operation, idempotency_key=key, request_digest=digest,
                campaign_id=campaign_id, run_id=None, resource_kind="cancellation",
                resource_id=effective_id or campaign_id,
                resource_links={"campaign_id": str(campaign_id), "cancellation_id": str(effective_id) if effective_id else None},
            ))
        async with engine.connect() as connection:
            refreshed = (await connection.execute(sa.select(campaigns).where(campaigns.c.campaign_id == campaign_id))).one()
        return refreshed, False
    except PublicProblem:
        raise
    except (IntegrityError, SQLAlchemyError) as error:
        raise PublicProblem(500, "CANCELLATION_PERSISTENCE_FAILED", "cancellation could not be persisted") from error

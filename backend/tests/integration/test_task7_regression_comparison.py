from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from uuid import UUID, uuid4

import pytest
import rfc8785
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.domain.evaluation import AnalysisDocumentV1
from boundary.evaluation.analyzer import analyze_evidence_set
from boundary.evidence.canonical import (
    canonicalize_fault_definition,
    fault_definition_digest,
)
from boundary.evidence.finalizer import finalize_run_evidence
from boundary.execution.control import ControlExecutionError, execute_control_run
from boundary.execution.injected import (
    create_injected_sibling,
    execute_injected_run,
)
from boundary.persistence.tables import (
    analyses,
    campaigns,
    comparisons,
    evidence_records,
    evidence_sets,
    fault_activations,
    regression_cases,
    reruns,
    run_capabilities,
    runs,
    tool_calls,
)
from boundary.persistence.transactions import (
    AcceptanceCommand,
    CanonicalDocument,
    accept_campaign_run,
    prepare_campaign_run_acceptance,
)
from boundary.regression.materializer import (
    MaterializationIneligible,
    RegressionConflict,
    RegressionIntegrityError,
    load_regression_case,
    materialize_regression_case,
)
from boundary.regression.rerun import (
    RerunConflict,
    create_rerun,
    definition_from_artifact,
    execute_rerun,
    seal_comparison,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.compose,
    pytest.mark.asyncio(loop_scope="session"),
]
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"
EXACT_DEGRADED_RESULT = (
    "boundary.phase1.degraded/v1: lookup unavailable after one bounded retry"
)


@dataclass(frozen=True, slots=True)
class VulnerableSource:
    campaign_id: UUID
    control_run_id: UUID
    injected_run_id: UUID
    injected_trace_id: UUID
    source_fault_id: UUID
    control_evidence_set_id: UUID
    evidence_set_id: UUID
    analysis_id: UUID
    capability_secret: str


async def _vulnerable_source(engine: AsyncEngine) -> VulnerableSource:
    definition = Phase1FaultDefinition.model_validate_json(
        FIXTURE_PATH.read_bytes()
    )
    canonical = canonicalize_fault_definition(definition)
    control = await accept_campaign_run(
        engine,
        prepare_campaign_run_acceptance(
            AcceptanceCommand(
                idempotency_key=f"task7-source-{uuid4()}",
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
    boundary_base = os.environ["BOUNDARY_INTERNAL_BASE_URL"]
    await execute_control_run(
        engine,
        run_id=control.run_id,
        sut_base_url=os.environ["SUT_BASE_URL"],
        tool_endpoint=(
            f"{boundary_base}/internal/v1/runs/{control.run_id}"
            "/tools/phase1-lookup"
        ),
        tested_input="phase1 lookup",
    )
    control_set = await finalize_run_evidence(engine, run_id=control.run_id)
    sibling = await create_injected_sibling(
        engine,
        control_run_id=control.run_id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    await execute_injected_run(
        engine,
        sibling=sibling,
        sut_base_url=os.environ["SUT_BASE_URL"],
        tool_endpoint=(
            f"{boundary_base}/internal/v1/runs/{sibling.run_id}"
            "/tools/phase1-lookup"
        ),
    )
    evidence_set = await finalize_run_evidence(engine, run_id=sibling.run_id)
    analysis = await analyze_evidence_set(
        engine, evidence_set_id=evidence_set.evidence_set_id
    )
    assert analysis.document.evaluability.aggregate == "EVALUABLE"
    assert analysis.document.scenario_policy_result == "FAIL"
    assert analysis.document.assertions is not None
    assert {
        item.assertion_id: item.outcome
        for item in analysis.document.assertions
    } == {
        "P1.RETRY_LIMIT": "FAIL",
        "P1.DEGRADED_RESULT": "FAIL",
        "P1.RUN_WITHIN_BUDGET": "FAIL",
    }
    assert sibling.fault_id is not None
    return VulnerableSource(
        campaign_id=control.campaign_id,
        control_run_id=control.run_id,
        injected_run_id=sibling.run_id,
        injected_trace_id=sibling.trace_id,
        source_fault_id=sibling.fault_id,
        control_evidence_set_id=control_set.evidence_set_id,
        evidence_set_id=evidence_set.evidence_set_id,
        analysis_id=analysis.analysis_id,
        capability_secret=sibling.capability.capability_secret,
    )


async def _count(engine: AsyncEngine, table) -> int:
    async with engine.connect() as connection:
        return int(await connection.scalar(sa.select(sa.func.count()).select_from(table)))


async def test_materialization_converges_and_mode_rejections_do_not_invoke_target(
    database_engine: AsyncEngine,
) -> None:
    source = await _vulnerable_source(database_engine)
    first = await materialize_regression_case(
        database_engine, source_analysis_id=source.analysis_id
    )
    concurrent = await asyncio.gather(
        *[
            materialize_regression_case(
                database_engine, source_analysis_id=source.analysis_id
            )
            for _ in range(6)
        ]
    )
    assert first.replayed is False
    assert {item.regression_case_id for item in concurrent} == {
        first.regression_case_id
    }
    assert all(item.replayed for item in concurrent)
    assert await _count(database_engine, regression_cases) == 1

    campaign_count = await _count(database_engine, campaigns)
    run_count = await _count(database_engine, runs)
    rejected = await create_rerun(
        database_engine,
        regression_case_id=first.regression_case_id,
        mode="version_comparison",
        tested_agent_version="vulnerable-v1",
    )
    assert rejected.status == "rejected"
    assert rejected.reason_code == "SAME_VERSION_VERSION_COMPARISON"
    assert rejected.campaign_id is None
    assert rejected.control_run_id is None
    assert await _count(database_engine, campaigns) == campaign_count
    assert await _count(database_engine, runs) == run_count

    proposed = definition_from_artifact(
        first.artifact, tested_agent_version="fixed-v1"
    ).model_copy(update={"policy_version": "changed-policy/v2"})
    drifted = await create_rerun(
        database_engine,
        regression_case_id=first.regression_case_id,
        mode="version_comparison",
        tested_agent_version="fixed-v1",
        proposed_definition=proposed,
    )
    assert drifted.status == "rejected"
    assert drifted.reason_code == "TEST_DEFINITION_MISMATCH"
    assert await _count(database_engine, campaigns) == campaign_count
    assert await _count(database_engine, runs) == run_count

    reproduction = await create_rerun(
        database_engine,
        regression_case_id=first.regression_case_id,
        mode="reproduction",
        tested_agent_version="vulnerable-v1",
    )
    assert reproduction.status == "accepted"
    version_row = next(
        row
        for row in reproduction.pre_invariance_report.rows
        if row.field_identifier == "tested_agent_version"
    )
    assert version_row.result == "MATCH"


async def test_rerun_acceptance_failure_is_atomic(
    database_engine: AsyncEngine,
) -> None:
    source = await _vulnerable_source(database_engine)
    case = await materialize_regression_case(
        database_engine, source_analysis_id=source.analysis_id
    )
    for failure_point in [
        "campaign",
        "control_run",
        "control_evidence",
        "rerun",
        "comparison",
    ]:
        before = {
            "campaigns": await _count(database_engine, campaigns),
            "runs": await _count(database_engine, runs),
            "reruns": await _count(database_engine, reruns),
            "comparisons": await _count(database_engine, comparisons),
        }
        with pytest.raises(RuntimeError, match="test failure"):
            await create_rerun(
                database_engine,
                regression_case_id=case.regression_case_id,
                mode="version_comparison",
                tested_agent_version="fixed-v1",
                _fail_after=failure_point,
            )
        assert await _count(database_engine, campaigns) == before["campaigns"]
        assert await _count(database_engine, runs) == before["runs"]
        assert await _count(database_engine, reruns) == before["reruns"]
        assert await _count(database_engine, comparisons) == before["comparisons"]

    accepted = await create_rerun(
        database_engine,
        regression_case_id=case.regression_case_id,
        mode="version_comparison",
        tested_agent_version="fixed-v1",
    )
    assert accepted.control_run_id is not None
    boundary_base = os.environ["BOUNDARY_INTERNAL_BASE_URL"]
    await execute_control_run(
        database_engine,
        run_id=accepted.control_run_id,
        sut_base_url=os.environ["SUT_BASE_URL"],
        tool_endpoint=(
            f"{boundary_base}/internal/v1/runs/{accepted.control_run_id}"
            "/tools/phase1-lookup"
        ),
        tested_input="phase1 lookup",
    )
    await finalize_run_evidence(
        database_engine, run_id=accepted.control_run_id
    )
    before_candidate = {
        "runs": await _count(database_engine, runs),
        "evidence": await _count(database_engine, evidence_records),
        "capabilities": await _count(database_engine, run_capabilities),
    }
    with pytest.raises(RuntimeError, match="test failure"):
        await create_injected_sibling(
            database_engine,
            control_run_id=accepted.control_run_id,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            rerun_id=accepted.rerun_id,
            _fail_after="rerun_link",
        )
    assert await _count(database_engine, runs) == before_candidate["runs"]
    assert (
        await _count(database_engine, evidence_records)
        == before_candidate["evidence"]
    )
    assert (
        await _count(database_engine, run_capabilities)
        == before_candidate["capabilities"]
    )
    async with database_engine.connect() as connection:
        persisted_rerun = (
            await connection.execute(
                sa.select(reruns).where(reruns.c.rerun_id == accepted.rerun_id)
            )
        ).one()
        pending_comparison = (
            await connection.execute(
                sa.select(comparisons).where(
                    comparisons.c.rerun_id == accepted.rerun_id
                )
            )
        ).one()
    assert persisted_rerun.candidate_run_id is None
    assert pending_comparison.status == "pending"

    failed = await create_rerun(
        database_engine,
        regression_case_id=case.regression_case_id,
        mode="version_comparison",
        tested_agent_version="fixed-v1",
    )
    with pytest.raises(ControlExecutionError):
        await execute_rerun(
            database_engine,
            rerun_id=failed.rerun_id,
            sut_base_url="http://127.0.0.1:1",
            boundary_internal_base_url=boundary_base,
        )
    async with database_engine.connect() as connection:
        failed_rerun = (
            await connection.execute(
                sa.select(reruns).where(reruns.c.rerun_id == failed.rerun_id)
            )
        ).one()
        failed_campaign = (
            await connection.execute(
                sa.select(campaigns).where(
                    campaigns.c.campaign_id == failed.campaign_id
                )
            )
        ).one()
        failed_comparison = (
            await connection.execute(
                sa.select(comparisons).where(
                    comparisons.c.rerun_id == failed.rerun_id
                )
            )
        ).one()
    assert failed_rerun.status == "failed"
    assert failed_rerun.reason_code == "RERUN_EXECUTION_ERROR"
    assert failed_campaign.status == "failed"
    assert failed_comparison.status == "execution_error"
    assert failed_comparison.terminal_result == "EXECUTION_ERROR"
    assert failed_comparison.candidate_policy_result is None


async def test_real_http_vulnerable_fail_to_fixed_pass_comparison(
    database_engine: AsyncEngine,
) -> None:
    source = await _vulnerable_source(database_engine)
    case = await materialize_regression_case(
        database_engine, source_analysis_id=source.analysis_id
    )
    accepted = await create_rerun(
        database_engine,
        regression_case_id=case.regression_case_id,
        mode="version_comparison",
        tested_agent_version="fixed-v1",
    )
    assert accepted.status == "accepted"
    assert accepted.campaign_id not in {None, source.campaign_id}
    assert accepted.control_run_id not in {
        None,
        source.control_run_id,
        source.injected_run_id,
    }
    version_row = next(
        row
        for row in accepted.pre_invariance_report.rows
        if row.field_identifier == "tested_agent_version"
    )
    assert version_row.result == "PERMITTED_DIFFERENCE"

    started = time.monotonic()
    completed = await execute_rerun(
        database_engine,
        rerun_id=accepted.rerun_id,
        sut_base_url=os.environ["SUT_BASE_URL"],
        boundary_internal_base_url=os.environ["BOUNDARY_INTERNAL_BASE_URL"],
    )
    assert time.monotonic() - started < 8.0
    assert all(
        row.result in {"MATCH", "PERMITTED_DIFFERENCE"}
        for row in completed.completed_invariance_report.rows
    )

    async with database_engine.connect() as connection:
        candidate_analysis = (
            await connection.execute(
                sa.select(analyses).where(
                    analyses.c.analysis_id == completed.candidate_analysis_id
                )
            )
        ).one()
        candidate_calls = (
            await connection.execute(
                sa.select(tool_calls)
                .where(tool_calls.c.run_id == completed.candidate_run_id)
                .order_by(tool_calls.c.retry_ordinal)
            )
        ).all()
        candidate_activations = (
            await connection.execute(
                sa.select(fault_activations)
                .where(fault_activations.c.run_id == completed.candidate_run_id)
                .order_by(fault_activations.c.activation_ordinal)
            )
        ).all()
        candidate_evidence = (
            await connection.execute(
                sa.select(evidence_records)
                .where(evidence_records.c.run_id == completed.candidate_run_id)
                .order_by(evidence_records.c.receipt_seq)
            )
        ).all()
        candidate_run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == completed.candidate_run_id)
            )
        ).one()
        control_run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == completed.control_run_id)
            )
        ).one()
        comparison = (
            await connection.execute(
                sa.select(comparisons).where(comparisons.c.rerun_id == accepted.rerun_id)
            )
        ).one()
        source_capability_ids = set(
            (
                await connection.execute(
                    sa.select(run_capabilities.c.capability_record_id).where(
                        run_capabilities.c.run_id.in_(
                            [source.control_run_id, source.injected_run_id]
                        )
                    )
                )
            ).scalars()
        )
        candidate_capability_ids = set(
            (
                await connection.execute(
                    sa.select(run_capabilities.c.capability_record_id).where(
                        run_capabilities.c.run_id.in_(
                            [completed.control_run_id, completed.candidate_run_id]
                        )
                    )
                )
            ).scalars()
        )

    document = json.loads(candidate_analysis.analysis_canonical_bytes)
    assert document["evaluability"]["aggregate"] == "EVALUABLE"
    assert {
        item["assertion_id"]: item["outcome"]
        for item in document["assertions"]
    } == {
        "P1.RETRY_LIMIT": "PASS",
        "P1.DEGRADED_RESULT": "PASS",
        "P1.RUN_WITHIN_BUDGET": "PASS",
    }
    assert document["scenario_policy_result"] == "PASS"
    assert [call.retry_ordinal for call in candidate_calls] == [0, 1]
    assert len(candidate_activations) == 2
    assert all(
        activation.effect_status == "effect_realized"
        for activation in candidate_activations
    )
    degraded = [
        row
        for row in candidate_evidence
        if row.event_type == "sut.degraded_result.produced"
    ]
    assert len(degraded) == 1
    assert degraded[0].payload["payload"]["result"] == EXACT_DEGRADED_RESULT
    assert candidate_run.expected_tested_agent_version == "fixed-v1"
    assert candidate_run.reported_tested_agent_version == "fixed-v1"
    assert control_run.expected_tested_agent_version == "fixed-v1"
    assert control_run.reported_tested_agent_version == "fixed-v1"
    assert candidate_run.run_id not in {
        source.control_run_id,
        source.injected_run_id,
        control_run.run_id,
    }
    assert candidate_run.trace_id not in {
        source.injected_trace_id,
        control_run.trace_id,
    }
    assert candidate_run.fault_id != source.source_fault_id
    assert source_capability_ids.isdisjoint(candidate_capability_ids)
    assert comparison.status == "valid"
    assert comparison.terminal_result == "VALID"
    assert comparison.source_policy_result == "FAIL"
    assert comparison.candidate_policy_result == "PASS"
    assert comparison.reason_code == "VULNERABLE_FAIL_FIXED_PASS"

    persisted = b"".join(
        [
            case.canonical_bytes,
            accepted.pre_invariance_digest.encode(),
            rfc8785.dumps(completed.completed_invariance_report.model_dump(mode="json")),
            comparison.summary_canonical_bytes,
            *[row.payload_canonical_bytes for row in candidate_evidence],
        ]
    )
    assert source.capability_secret.encode() not in persisted
    assert b"Authorization" not in persisted

    replay = await seal_comparison(
        database_engine,
        rerun_id=accepted.rerun_id,
        candidate_analysis_id=completed.candidate_analysis_id,
    )
    assert replay.replayed is True
    with pytest.raises(RerunConflict):
        await seal_comparison(
            database_engine,
            rerun_id=accepted.rerun_id,
            candidate_analysis_id=source.analysis_id,
        )


async def test_materialization_failure_and_conflict_do_not_overwrite(
    database_engine: AsyncEngine,
) -> None:
    source = await _vulnerable_source(database_engine)
    with pytest.raises(RuntimeError, match="after regression case"):
        await materialize_regression_case(
            database_engine,
            source_analysis_id=source.analysis_id,
            _fail_after="case",
        )
    assert await _count(database_engine, regression_cases) == 0
    case = await materialize_regression_case(
        database_engine, source_analysis_id=source.analysis_id
    )
    async with database_engine.begin() as connection:
        await connection.execute(
            regression_cases.update()
            .where(
                regression_cases.c.regression_case_id
                == case.regression_case_id
            )
            .values(integrity_digest="f" * 64)
        )
    with pytest.raises(RegressionConflict):
        await materialize_regression_case(
            database_engine, source_analysis_id=source.analysis_id
        )


async def test_ineligible_results_missing_proof_and_tampering_create_no_case(
    database_engine: AsyncEngine,
) -> None:
    source = await _vulnerable_source(database_engine)
    async with database_engine.connect() as connection:
        original_analysis = (
            await connection.execute(
                sa.select(analyses).where(analyses.c.analysis_id == source.analysis_id)
            )
        ).one()
        original_evidence = (
            await connection.execute(
                sa.select(evidence_sets).where(
                    evidence_sets.c.evidence_set_id == source.evidence_set_id
                )
            )
        ).one()
        original_run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == source.injected_run_id)
            )
        ).one()

    async def replace_analysis(document: dict) -> None:
        validated = AnalysisDocumentV1.model_validate_json(rfc8785.dumps(document))
        canonical = rfc8785.dumps(validated.model_dump(mode="json"))
        async with database_engine.begin() as connection:
            await connection.execute(
                analyses.update()
                .where(analyses.c.analysis_id == source.analysis_id)
                .values(
                    analysis_document=json.loads(canonical),
                    analysis_canonical_bytes=canonical,
                    analysis_digest=sha256(canonical).hexdigest(),
                    evaluability_aggregate=validated.evaluability.aggregate,
                    policy_result=validated.scenario_policy_result,
                )
            )

    async def restore_analysis() -> None:
        async with database_engine.begin() as connection:
            await connection.execute(
                analyses.update()
                .where(analyses.c.analysis_id == source.analysis_id)
                .values(
                    analysis_document=original_analysis.analysis_document,
                    analysis_canonical_bytes=original_analysis.analysis_canonical_bytes,
                    analysis_digest=original_analysis.analysis_digest,
                    evaluability_aggregate=original_analysis.evaluability_aggregate,
                    policy_result=original_analysis.policy_result,
                )
            )

    original_document = dict(original_analysis.analysis_document)
    for aggregate, policy, reason in [
        ("EVALUABLE", "PASS", "SOURCE_POLICY_PASS"),
        ("INCOMPLETE", "INCOMPLETE", "SOURCE_INCOMPLETE"),
        ("INVALID", "INVALID", "SOURCE_INVALID"),
        ("EXECUTION_ERROR", "EXECUTION_ERROR", "SOURCE_EXECUTION_ERROR"),
    ]:
        changed = dict(original_document)
        changed["evaluability"] = {
            **original_document["evaluability"],
            "aggregate": aggregate,
        }
        changed["scenario_policy_result"] = policy
        if aggregate != "EVALUABLE":
            changed.update(
                assertions=None,
                injection_boundary=None,
                localization=None,
                downstream_symptoms=[],
            )
        await replace_analysis(changed)
        with pytest.raises(MaterializationIneligible) as rejected:
            await materialize_regression_case(
                database_engine, source_analysis_id=source.analysis_id
            )
        assert rejected.value.reason_code == reason
        assert await _count(database_engine, regression_cases) == 0
        await restore_analysis()

    missing_localization = dict(original_document)
    missing_localization["localization"] = None
    await replace_analysis(missing_localization)
    with pytest.raises(MaterializationIneligible) as rejected:
        await materialize_regression_case(
            database_engine, source_analysis_id=source.analysis_id
        )
    assert rejected.value.reason_code == "SOURCE_ORDINAL_2_LOCALIZATION_MISSING"
    assert await _count(database_engine, regression_cases) == 0
    await restore_analysis()

    no_failures = dict(original_document)
    no_failures["assertions"] = [
        {**assertion, "outcome": "PASS"}
        for assertion in original_document["assertions"]
    ]
    await replace_analysis(no_failures)
    with pytest.raises(MaterializationIneligible) as rejected:
        await materialize_regression_case(
            database_engine, source_analysis_id=source.analysis_id
        )
    assert rejected.value.reason_code == "SOURCE_HAS_NO_FAILED_ASSERTION"
    assert await _count(database_engine, regression_cases) == 0
    await restore_analysis()

    retry_passed = dict(original_document)
    retry_passed["assertions"] = [
        (
            {**assertion, "outcome": "PASS"}
            if assertion["assertion_id"] == "P1.RETRY_LIMIT"
            else assertion
        )
        for assertion in original_document["assertions"]
    ]
    await replace_analysis(retry_passed)
    with pytest.raises(MaterializationIneligible) as rejected:
        await materialize_regression_case(
            database_engine, source_analysis_id=source.analysis_id
        )
    assert rejected.value.reason_code == "SOURCE_RETRY_LIMIT_DID_NOT_FAIL"
    assert await _count(database_engine, regression_cases) == 0
    await restore_analysis()

    async with database_engine.begin() as connection:
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == source.injected_run_id)
            .values(fault_spec_id=uuid4())
        )
    with pytest.raises(MaterializationIneligible) as rejected:
        await materialize_regression_case(
            database_engine, source_analysis_id=source.analysis_id
        )
    assert rejected.value.reason_code == "SOURCE_PROVENANCE_INCOMPLETE"
    assert await _count(database_engine, regression_cases) == 0
    async with database_engine.begin() as connection:
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == source.injected_run_id)
            .values(fault_spec_id=original_run.fault_spec_id)
        )

    async with database_engine.begin() as connection:
        await connection.execute(
            analyses.update()
            .where(analyses.c.analysis_id == source.analysis_id)
            .values(analysis_digest="e" * 64)
        )
    with pytest.raises(RegressionIntegrityError):
        await materialize_regression_case(
            database_engine, source_analysis_id=source.analysis_id
        )
    assert await _count(database_engine, regression_cases) == 0
    await restore_analysis()

    async with database_engine.begin() as connection:
        await connection.execute(
            evidence_sets.update()
            .where(evidence_sets.c.evidence_set_id == source.evidence_set_id)
            .values(evidence_set_digest="d" * 64)
        )
    with pytest.raises(RegressionIntegrityError):
        await materialize_regression_case(
            database_engine, source_analysis_id=source.analysis_id
        )
    assert await _count(database_engine, regression_cases) == 0
    async with database_engine.begin() as connection:
        await connection.execute(
            evidence_sets.update()
            .where(evidence_sets.c.evidence_set_id == source.evidence_set_id)
            .values(evidence_set_digest=original_evidence.evidence_set_digest)
        )

    missing_timeout = dict(original_evidence.manifest)
    missing_timeout["timeout_activations"] = missing_timeout[
        "timeout_activations"
    ][:1]
    async with database_engine.begin() as connection:
        await connection.execute(
            evidence_sets.update()
            .where(evidence_sets.c.evidence_set_id == source.evidence_set_id)
            .values(manifest=missing_timeout)
        )
    with pytest.raises(RegressionIntegrityError):
        await materialize_regression_case(
            database_engine, source_analysis_id=source.analysis_id
        )
    assert await _count(database_engine, regression_cases) == 0
    async with database_engine.begin() as connection:
        await connection.execute(
            evidence_sets.update()
            .where(evidence_sets.c.evidence_set_id == source.evidence_set_id)
            .values(manifest=original_evidence.manifest)
        )

    case = await materialize_regression_case(
        database_engine, source_analysis_id=source.analysis_id
    )
    async with database_engine.begin() as connection:
        await connection.execute(
            regression_cases.update()
            .where(regression_cases.c.regression_case_id == case.regression_case_id)
            .values(artifact={**case.artifact.model_dump(mode="json"), "policy_version": "tampered/v2"})
        )
    async with database_engine.connect() as connection:
        with pytest.raises(RegressionIntegrityError):
            await load_regression_case(
                connection, regression_case_id=case.regression_case_id
            )

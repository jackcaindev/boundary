"""Task 7 rerun acceptance, invariance completion, and comparison sealing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

import rfc8785
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from boundary.domain.evaluation import AnalysisDocumentV1
from boundary.domain.regression import (
    ComparisonFailureSummaryV1,
    ComparisonSummaryV1,
    CompletedInvarianceReportV1,
    InvarianceRowV1,
    PreInvocationInvarianceReportV1,
    RegressionArtifactV1,
    RerunDefinitionV1,
    RerunMode,
)
from boundary.evaluation.analyzer import analyze_evidence_set
from boundary.evaluation.snapshot import (
    FinalizedSnapshotError,
    load_finalized_snapshot,
)
from boundary.evidence.finalizer import finalize_run_evidence
from boundary.execution.control import execute_control_run
from boundary.execution.injected import (
    FIXED_AGENT_VERSION,
    VULNERABLE_AGENT_VERSION,
    create_injected_sibling,
    execute_injected_run,
)
from boundary.injection.fault_spec import (
    FaultDefinitionMismatch,
    validate_phase1_fault_document,
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
    tool_calls,
)
from boundary.regression.materializer import (
    MaterializationIneligible,
    RegressionIntegrityError,
    load_regression_case,
)


class RerunError(Exception):
    """Safe base error for a Task 7 rerun."""


class RerunRejected(RerunError):
    def __init__(self, rerun_id: UUID, reason_code: str) -> None:
        self.rerun_id = rerun_id
        self.reason_code = reason_code
        super().__init__("rerun was rejected before target invocation")


class RerunIntegrityError(RerunError):
    """A rerun or invariance document failed immutable verification."""


class RerunConflict(RerunError):
    """Write-once rerun or comparison content conflicted."""


class RerunPersistenceError(RerunError):
    """PostgreSQL could not commit a Task 7 transaction."""


class ComparisonIneligible(RerunError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__("comparison is not eligible")


@dataclass(frozen=True, slots=True)
class AcceptedRerun:
    rerun_id: UUID
    regression_case_id: UUID
    mode: RerunMode
    campaign_id: UUID | None
    control_run_id: UUID | None
    control_trace_id: UUID | None
    comparison_id: UUID | None
    pre_invariance_report: PreInvocationInvarianceReportV1
    pre_invariance_digest: str
    status: Literal["accepted", "rejected"]
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class CompletedRerun:
    rerun_id: UUID
    control_run_id: UUID
    candidate_run_id: UUID
    candidate_evidence_set_id: UUID
    candidate_analysis_id: UUID
    completed_invariance_report: CompletedInvarianceReportV1
    completed_invariance_digest: str
    comparison_id: UUID | None


@dataclass(frozen=True, slots=True)
class SealedComparison:
    comparison_id: UUID
    status: str
    terminal_result: str
    reason_code: str
    summary_digest: str
    replayed: bool


def canonicalize_document(model) -> tuple[bytes, str]:
    canonical_bytes = rfc8785.dumps(model.model_dump(mode="json"))
    return canonical_bytes, sha256(canonical_bytes).hexdigest()


def definition_from_artifact(
    artifact: RegressionArtifactV1,
    *,
    tested_agent_version: str,
) -> RerunDefinitionV1:
    return RerunDefinitionV1(
        schema_version=1,
        regression_case_id=artifact.regression_case_id,
        contract_version=artifact.contract_version,
        scenario_id=artifact.scenario_id,
        scenario_version=artifact.scenario_version,
        tested_agent_id=artifact.original_tested_agent_id,
        tested_agent_version=tested_agent_version,
        tested_input=artifact.tested_input,
        tested_input_digest=artifact.tested_input_digest,
        fault_spec_id=artifact.fault_spec_id,
        fault_definition=artifact.fault_definition,
        fault_definition_digest=artifact.fault_definition_digest,
        analyzer_version=artifact.analyzer_version,
        assertion_set_version=artifact.assertion_set_version,
        policy_version=artifact.policy_version,
    )


def build_pre_invocation_report(
    *,
    rerun_id: UUID,
    artifact: RegressionArtifactV1,
    mode: RerunMode,
    proposed: RerunDefinitionV1,
) -> PreInvocationInvarianceReportV1:
    source_ref = f"regression_case:{artifact.regression_case_id}"
    rerun_ref = f"rerun:{rerun_id}:proposed_definition"
    rows = [
        _equality_row("regression_case_id", artifact.regression_case_id, proposed.regression_case_id, source_ref, rerun_ref),
        _equality_row("contract_version", artifact.contract_version, proposed.contract_version, source_ref, rerun_ref),
        _equality_row("scenario_id", artifact.scenario_id, proposed.scenario_id, source_ref, rerun_ref),
        _equality_row("scenario_version", artifact.scenario_version, proposed.scenario_version, source_ref, rerun_ref),
        _equality_row("tested_agent_id", artifact.original_tested_agent_id, proposed.tested_agent_id, source_ref, rerun_ref),
        _version_row(artifact, proposed, mode, source_ref, rerun_ref),
        _equality_row("tested_input", artifact.tested_input, proposed.tested_input, source_ref, rerun_ref),
        _equality_row("tested_input_digest", artifact.tested_input_digest, proposed.tested_input_digest, source_ref, rerun_ref),
        _equality_row("fault_spec_id", artifact.fault_spec_id, proposed.fault_spec_id, source_ref, rerun_ref),
        _equality_row("fault_definition", artifact.fault_definition, proposed.fault_definition, source_ref, rerun_ref),
        _equality_row("fault_definition_digest", artifact.fault_definition_digest, proposed.fault_definition_digest, source_ref, rerun_ref),
        _equality_row("analyzer_version", artifact.analyzer_version, proposed.analyzer_version, source_ref, rerun_ref),
        _equality_row("assertion_set_version", artifact.assertion_set_version, proposed.assertion_set_version, source_ref, rerun_ref),
        _equality_row("policy_version", artifact.policy_version, proposed.policy_version, source_ref, rerun_ref),
    ]
    return PreInvocationInvarianceReportV1(
        report_schema_version=1,
        report_phase="pre_invocation",
        rerun_id=rerun_id,
        regression_case_id=artifact.regression_case_id,
        mode=mode,
        rows=rows,
    )


async def create_rerun(
    engine: AsyncEngine,
    *,
    regression_case_id: UUID,
    mode: RerunMode,
    tested_agent_version: str,
    proposed_definition: RerunDefinitionV1 | None = None,
    _fail_after: str | None = None,
) -> AcceptedRerun:
    """Create the pre-report and either reject or atomically accept execution."""
    if mode not in {"reproduction", "version_comparison"}:
        raise ValueError("rerun mode is unsupported")
    rerun_id = uuid4()
    campaign_id = uuid4()
    control_run_id = uuid4()
    control_trace_id = uuid4()
    accepted_evidence_id = uuid4()
    comparison_id = uuid4() if mode == "version_comparison" else None
    try:
        async with engine.begin() as connection:
            _, artifact = await load_regression_case(
                connection,
                regression_case_id=regression_case_id,
                lock=True,
            )
            proposed = proposed_definition or definition_from_artifact(
                artifact,
                tested_agent_version=tested_agent_version,
            )
            try:
                proposed = RerunDefinitionV1.model_validate_json(
                    rfc8785.dumps(proposed.model_dump(mode="json"))
                )
            except ValidationError as error:
                raise RerunIntegrityError(
                    "proposed rerun definition is invalid"
                ) from error
            if proposed.tested_agent_version != tested_agent_version:
                raise RerunIntegrityError(
                    "requested version conflicts with proposed definition"
                )
            report = build_pre_invocation_report(
                rerun_id=rerun_id,
                artifact=artifact,
                mode=mode,
                proposed=proposed,
            )
            report_bytes, report_digest = canonicalize_document(report)
            reason_code = _pre_invocation_rejection_reason(
                artifact=artifact,
                proposed=proposed,
                mode=mode,
                report=report,
            )
            if reason_code is not None:
                await connection.execute(
                    reruns.insert().values(
                        rerun_id=rerun_id,
                        regression_case_id=regression_case_id,
                        mode=mode,
                        requested_tested_agent_version=tested_agent_version,
                        campaign_id=None,
                        control_run_id=None,
                        candidate_run_id=None,
                        status="rejected",
                        reason_code=reason_code,
                        pre_report_schema_version=1,
                        pre_invariance_report=json.loads(report_bytes),
                        pre_invariance_canonical_bytes=report_bytes,
                        pre_invariance_digest=report_digest,
                    )
                )
                if _fail_after == "rejected_rerun":
                    raise RuntimeError("test failure after rejected rerun")
                return AcceptedRerun(
                    rerun_id=rerun_id,
                    regression_case_id=regression_case_id,
                    mode=mode,
                    campaign_id=None,
                    control_run_id=None,
                    control_trace_id=None,
                    comparison_id=None,
                    pre_invariance_report=report,
                    pre_invariance_digest=report_digest,
                    status="rejected",
                    reason_code=reason_code,
                )
            fault_bytes = rfc8785.dumps(
                artifact.fault_definition.model_dump(mode="json")
            )
            input_bytes = rfc8785.dumps(
                artifact.tested_input.model_dump(mode="json")
            )
            event_payload = {
                "campaign_id": str(campaign_id),
                "from_status": None,
                "regression_case_id": str(regression_case_id),
                "rerun_id": str(rerun_id),
                "run_id": str(control_run_id),
                "schema_version": 1,
                "to_status": "accepted",
                "transition": "regression_control_accepted",
            }
            event_bytes = rfc8785.dumps(event_payload)
            await connection.execute(
                campaigns.insert().values(
                    campaign_id=campaign_id,
                    campaign_kind="phase1.tool-timeout.rerun",
                    status="accepted",
                    current_step="control",
                    cancel_requested=False,
                )
            )
            _raise_failure(_fail_after, "campaign")
            await connection.execute(
                runs.insert().values(
                    run_id=control_run_id,
                    trace_id=control_trace_id,
                    campaign_id=campaign_id,
                    control_run_id=None,
                    run_role="control",
                    fault_spec_id=None,
                    fault_id=None,
                    contract_version=artifact.contract_version,
                    scenario_id=artifact.scenario_id,
                    scenario_version=artifact.scenario_version,
                    expected_tested_agent_id=artifact.original_tested_agent_id,
                    expected_tested_agent_version=tested_agent_version,
                    reported_tested_agent_id=None,
                    reported_tested_agent_version=None,
                    operational_status="accepted",
                    definition_schema_version=1,
                    run_definition=json.loads(fault_bytes),
                    run_definition_bytes=fault_bytes,
                    run_definition_digest=artifact.fault_definition_digest,
                    tested_input=json.loads(input_bytes),
                    tested_input_bytes=input_bytes,
                    tested_input_digest=artifact.tested_input_digest,
                    target_producer_cursor=0,
                    target_final_watermark=None,
                    next_receipt_seq=2,
                    next_audit_seq=1,
                    next_tool_ordinal=0,
                    evidence_open=True,
                )
            )
            _raise_failure(_fail_after, "control_run")
            await connection.execute(
                evidence_records.insert().values(
                    evidence_id=accepted_evidence_id,
                    run_id=control_run_id,
                    source="boundary",
                    event_type="boundary.run.regression_control_accepted",
                    boundary="run",
                    source_event_id=accepted_evidence_id,
                    producer_seq=None,
                    receipt_seq=1,
                    audit_seq=None,
                    caused_by_event_id=None,
                    payload_schema_version=1,
                    payload=json.loads(event_bytes),
                    payload_canonical_bytes=event_bytes,
                    payload_digest=sha256(event_bytes).hexdigest(),
                    disposition="accepted",
                )
            )
            _raise_failure(_fail_after, "control_evidence")
            await connection.execute(
                reruns.insert().values(
                    rerun_id=rerun_id,
                    regression_case_id=regression_case_id,
                    mode=mode,
                    requested_tested_agent_version=tested_agent_version,
                    campaign_id=campaign_id,
                    control_run_id=control_run_id,
                    candidate_run_id=None,
                    status="accepted",
                    reason_code=None,
                    pre_report_schema_version=1,
                    pre_invariance_report=json.loads(report_bytes),
                    pre_invariance_canonical_bytes=report_bytes,
                    pre_invariance_digest=report_digest,
                )
            )
            _raise_failure(_fail_after, "rerun")
            if comparison_id is not None:
                await connection.execute(
                    comparisons.insert().values(
                        comparison_id=comparison_id,
                        regression_case_id=regression_case_id,
                        rerun_id=rerun_id,
                        source_run_id=artifact.source_run_id,
                        source_evidence_set_id=artifact.source_evidence_set_id,
                        source_analysis_id=artifact.source_analysis_id,
                        candidate_run_id=None,
                        candidate_evidence_set_id=None,
                        candidate_analysis_id=None,
                        source_tested_agent_version=(
                            artifact.original_tested_agent_version
                        ),
                        candidate_tested_agent_version=tested_agent_version,
                        source_policy_result="FAIL",
                        candidate_policy_result=None,
                        status="pending",
                        terminal_result=None,
                        reason_code=None,
                        summary_schema_version=None,
                        summary_canonical_bytes=None,
                        summary_digest=None,
                    )
                )
                _raise_failure(_fail_after, "comparison")
    except (
        MaterializationIneligible,
        RegressionIntegrityError,
        RerunIntegrityError,
        RuntimeError,
    ):
        raise
    except IntegrityError as error:
        if _constraint_name(error) in {
            "pk_reruns",
            "uq_reruns_campaign_id",
            "uq_reruns_control_run_id",
            "uq_reruns_candidate_run_id",
            "pk_comparisons",
            "uq_comparisons_rerun_id",
        }:
            raise RerunConflict(
                "rerun acceptance identity conflicted"
            ) from None
        raise RerunPersistenceError(
            "rerun acceptance integrity constraint failed"
        ) from None
    except SQLAlchemyError:
        raise RerunPersistenceError("rerun acceptance failed") from None
    return AcceptedRerun(
        rerun_id=rerun_id,
        regression_case_id=regression_case_id,
        mode=mode,
        campaign_id=campaign_id,
        control_run_id=control_run_id,
        control_trace_id=control_trace_id,
        comparison_id=comparison_id,
        pre_invariance_report=report,
        pre_invariance_digest=report_digest,
        status="accepted",
        reason_code=None,
    )


def _equality_row(field_identifier, source, rerun_value, source_ref, rerun_ref):
    source_value = _value(source)
    candidate_value = _value(rerun_value)
    return InvarianceRowV1(
        field_identifier=field_identifier,
        source_value_or_digest=source_value,
        rerun_value_or_digest=candidate_value,
        comparison_rule="exact_equality",
        result="MATCH" if source_value == candidate_value else "MISMATCH",
        authoritative_references=[source_ref, rerun_ref],
    )


def _version_row(artifact, proposed, mode, source_ref, rerun_ref):
    source = artifact.original_tested_agent_version
    candidate = proposed.tested_agent_version
    if mode == "version_comparison":
        matches_rule = candidate == FIXED_AGENT_VERSION and candidate != source
        result = "PERMITTED_DIFFERENCE" if matches_rule else "MISMATCH"
        rule = "different_version_and_phase1_fixed_v1_required"
    elif candidate == source:
        result = "MATCH"
        rule = "same_or_different_version_permitted_for_reproduction"
    elif candidate in {VULNERABLE_AGENT_VERSION, FIXED_AGENT_VERSION}:
        result = "PERMITTED_DIFFERENCE"
        rule = "same_or_different_version_permitted_for_reproduction"
    else:
        result = "MISMATCH"
        rule = "phase1_supported_version_required"
    return InvarianceRowV1(
        field_identifier="tested_agent_version",
        source_value_or_digest=source,
        rerun_value_or_digest=candidate,
        comparison_rule=rule,
        result=result,
        authoritative_references=[source_ref, rerun_ref],
    )


def _value(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return rfc8785.dumps(value).decode("utf-8")
    return str(value)


def _pre_invocation_rejection_reason(*, artifact, proposed, mode, report):
    if (
        mode == "version_comparison"
        and proposed.tested_agent_version
        == artifact.original_tested_agent_version
    ):
        return "SAME_VERSION_VERSION_COMPARISON"
    if (
        mode == "version_comparison"
        and proposed.tested_agent_version != FIXED_AGENT_VERSION
    ):
        return "PHASE1_FIXED_V1_REQUIRED"
    if any(row.result == "MISMATCH" for row in report.rows):
        return "TEST_DEFINITION_MISMATCH"
    return None


def _raise_failure(selected: str | None, current: str) -> None:
    if selected == current:
        raise RuntimeError(f"test failure after rerun {current}")


def _constraint_name(error: IntegrityError) -> str | None:
    original = error.orig
    return getattr(original, "constraint_name", None) or getattr(
        getattr(original, "__cause__", None), "constraint_name", None
    )


async def execute_rerun(
    engine: AsyncEngine,
    *,
    rerun_id: UUID,
    sut_base_url: str,
    boundary_internal_base_url: str,
    execution_budget_ms: int = 30_000,
) -> CompletedRerun:
    """Run fresh control and injected evidence through the existing real path."""
    async with engine.begin() as connection:
        rerun = (
            await connection.execute(
                sa.select(reruns)
                .where(reruns.c.rerun_id == rerun_id)
                .with_for_update()
            )
        ).one_or_none()
        if rerun is None:
            raise RerunError("rerun does not exist")
        if rerun.status == "rejected":
            raise RerunRejected(rerun_id, rerun.reason_code)
        if rerun.status != "accepted":
            raise RerunError("rerun is not accepted for execution")
        await connection.execute(
            reruns.update()
            .where(reruns.c.rerun_id == rerun_id)
            .values(status="running")
        )
        await connection.execute(
            campaigns.update()
            .where(campaigns.c.campaign_id == rerun.campaign_id)
            .values(status="running", current_step="control")
        )
        _, artifact = await load_regression_case(
            connection,
            regression_case_id=rerun.regression_case_id,
        )
    try:
        assert rerun.control_run_id is not None
        await execute_control_run(
            engine,
            run_id=rerun.control_run_id,
            sut_base_url=sut_base_url,
            tool_endpoint=(
                f"{boundary_internal_base_url}/internal/v1/runs/"
                f"{rerun.control_run_id}/tools/phase1-lookup"
            ),
            tested_input=artifact.tested_input.query,
            execution_budget_ms=execution_budget_ms,
        )
        control_set = await finalize_run_evidence(
            engine,
            run_id=rerun.control_run_id,
        )
        async with engine.begin() as connection:
            await connection.execute(
                campaigns.update()
                .where(campaigns.c.campaign_id == rerun.campaign_id)
                .values(current_step="injected")
            )
        sibling = await create_injected_sibling(
            engine,
            control_run_id=rerun.control_run_id,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            rerun_id=rerun_id,
        )
        await execute_injected_run(
            engine,
            sibling=sibling,
            sut_base_url=sut_base_url,
            tool_endpoint=(
                f"{boundary_internal_base_url}/internal/v1/runs/"
                f"{sibling.run_id}/tools/phase1-lookup"
            ),
            execution_budget_ms=execution_budget_ms,
        )
        candidate_set = await finalize_run_evidence(
            engine,
            run_id=sibling.run_id,
        )
        candidate_analysis = await analyze_evidence_set(
            engine,
            evidence_set_id=candidate_set.evidence_set_id,
        )
        report, report_digest, _ = await complete_invariance_report(
            engine,
            rerun_id=rerun_id,
            candidate_analysis_id=candidate_analysis.analysis_id,
        )
        comparison_id = None
        if rerun.mode == "version_comparison":
            comparison = await seal_comparison(
                engine,
                rerun_id=rerun_id,
                candidate_analysis_id=candidate_analysis.analysis_id,
            )
            comparison_id = comparison.comparison_id
            if comparison.status != "valid":
                raise ComparisonIneligible(comparison.reason_code)
        async with engine.begin() as connection:
            await connection.execute(
                campaigns.update()
                .where(campaigns.c.campaign_id == rerun.campaign_id)
                .values(status="completed", current_step="completed")
            )
        return CompletedRerun(
            rerun_id=rerun_id,
            control_run_id=rerun.control_run_id,
            candidate_run_id=sibling.run_id,
            candidate_evidence_set_id=candidate_set.evidence_set_id,
            candidate_analysis_id=candidate_analysis.analysis_id,
            completed_invariance_report=report,
            completed_invariance_digest=report_digest,
            comparison_id=comparison_id,
        )
    except Exception as error:
        await _mark_rerun_failed(engine, rerun_id=rerun_id)
        await _seal_execution_error_if_pending(
            engine,
            rerun_id=rerun_id,
            reason_code="RERUN_EXECUTION_ERROR",
        )
        raise


async def complete_invariance_report(
    engine: AsyncEngine,
    *,
    rerun_id: UUID,
    candidate_analysis_id: UUID,
) -> tuple[CompletedInvarianceReportV1, str, bool]:
    """Verify runtime identities and write the completed report once."""
    try:
        async with engine.begin() as connection:
            rerun = (
                await connection.execute(
                    sa.select(reruns)
                    .where(reruns.c.rerun_id == rerun_id)
                    .with_for_update()
                )
            ).one_or_none()
            if (
                rerun is None
                or rerun.status not in {"running", "completed", "failed"}
                or rerun.control_run_id is None
                or rerun.candidate_run_id is None
            ):
                raise RerunIntegrityError(
                    "rerun runtime identities are incomplete"
                )
            _, artifact = await load_regression_case(
                connection,
                regression_case_id=rerun.regression_case_id,
                lock=True,
            )
            pre_report = _load_pre_report(rerun)
            source = await load_finalized_snapshot(
                connection,
                evidence_set_id=artifact.source_evidence_set_id,
            )
            control = await load_finalized_snapshot(
                connection,
                run_id=rerun.control_run_id,
            )
            candidate = await load_finalized_snapshot(
                connection,
                run_id=rerun.candidate_run_id,
            )
            if source is None or control is None or candidate is None:
                raise RerunIntegrityError(
                    "source, control, or candidate evidence is not finalized"
                )
            source_control = None
            if source.manifest.control_run_id is not None:
                source_control = await load_finalized_snapshot(
                    connection,
                    run_id=source.manifest.control_run_id,
                )
            if source_control is None:
                raise RerunIntegrityError("source control evidence is missing")
            control_run = await _run_row(
                connection, rerun.control_run_id, lock=True
            )
            candidate_run = await _run_row(
                connection, rerun.candidate_run_id, lock=True
            )
            candidate_analysis = await _analysis_row(
                connection, candidate_analysis_id, lock=True
            )
            candidate_document = _verify_analysis_row(candidate_analysis)
            if (
                candidate_analysis.evidence_set_id
                != candidate.manifest.evidence_set_id
                or candidate_document.evidence_set_id
                != candidate.manifest.evidence_set_id
            ):
                raise RerunIntegrityError(
                    "candidate analysis is not bound to candidate evidence"
                )
            actual = _definition_from_run(
                candidate_run,
                artifact=artifact,
                analysis=candidate_document,
            )
            actual_pre = build_pre_invocation_report(
                rerun_id=rerun_id,
                artifact=artifact,
                mode=rerun.mode,
                proposed=actual,
            )
            rows = list(actual_pre.rows)
            rows = _apply_control_definition_verification(
                rows,
                control_run=control_run,
                candidate_run=candidate_run,
                artifact=artifact,
            )
            runtime_rows = await _runtime_identity_rows(
                connection,
                rerun=rerun,
                artifact=artifact,
                source=source,
                source_control=source_control,
                control=control,
                candidate=candidate,
                candidate_analysis_id=candidate_analysis_id,
            )
            rows.extend(runtime_rows)
            completed = CompletedInvarianceReportV1(
                report_schema_version=1,
                report_phase="completed",
                rerun_id=rerun_id,
                regression_case_id=artifact.regression_case_id,
                mode=rerun.mode,
                rows=rows,
            )
            canonical_bytes, digest = canonicalize_document(completed)
            if rerun.completed_invariance_digest is not None:
                try:
                    existing = CompletedInvarianceReportV1.model_validate_json(
                        json.dumps(rerun.completed_invariance_report)
                    )
                except ValidationError as error:
                    raise RerunIntegrityError(
                        "completed invariance report schema is invalid"
                    ) from error
                existing_bytes, existing_digest = canonicalize_document(existing)
                if (
                    existing_bytes != rerun.completed_invariance_canonical_bytes
                    or existing_digest != rerun.completed_invariance_digest
                    or canonical_bytes != existing_bytes
                    or digest != existing_digest
                ):
                    raise RerunConflict(
                        "completed invariance content conflicts"
                    )
                return existing, existing_digest, True
            has_mismatch = any(row.result == "MISMATCH" for row in rows)
            await connection.execute(
                reruns.update()
                .where(
                    reruns.c.rerun_id == rerun_id,
                    reruns.c.completed_invariance_digest.is_(None),
                )
                .values(
                    completed_report_schema_version=1,
                    completed_invariance_report=json.loads(canonical_bytes),
                    completed_invariance_canonical_bytes=canonical_bytes,
                    completed_invariance_digest=digest,
                    status="failed" if has_mismatch else "completed",
                    reason_code=(
                        "RUNTIME_IDENTITY_MISMATCH" if has_mismatch else None
                    ),
                )
            )
            return completed, digest, False
    except (
        RerunIntegrityError,
        RerunConflict,
        RegressionIntegrityError,
        FinalizedSnapshotError,
    ):
        raise
    except (IntegrityError, SQLAlchemyError):
        raise RerunPersistenceError(
            "completed invariance persistence failed"
        ) from None


def _load_pre_report(rerun) -> PreInvocationInvarianceReportV1:
    try:
        report = PreInvocationInvarianceReportV1.model_validate_json(
            json.dumps(rerun.pre_invariance_report)
        )
    except ValidationError as error:
        raise RerunIntegrityError(
            "pre-invocation invariance report schema is invalid"
        ) from error
    canonical_bytes, digest = canonicalize_document(report)
    if (
        canonical_bytes != rerun.pre_invariance_canonical_bytes
        or digest != rerun.pre_invariance_digest
        or report.rerun_id != rerun.rerun_id
        or report.regression_case_id != rerun.regression_case_id
        or report.mode != rerun.mode
        or any(row.result == "MISMATCH" for row in report.rows)
    ):
        raise RerunIntegrityError(
            "pre-invocation invariance report failed verification"
        )
    return report


def _definition_from_run(run, *, artifact, analysis) -> RerunDefinitionV1:
    try:
        fault = validate_phase1_fault_document(
            run.run_definition,
            run.run_definition_bytes,
            run.run_definition_digest,
        )
    except FaultDefinitionMismatch as error:
        raise RerunIntegrityError(
            "candidate fault definition failed verification"
        ) from error
    input_bytes = rfc8785.dumps(run.tested_input)
    if (
        input_bytes != run.tested_input_bytes
        or sha256(input_bytes).hexdigest() != run.tested_input_digest
    ):
        raise RerunIntegrityError("candidate tested input failed verification")
    return RerunDefinitionV1(
        schema_version=1,
        regression_case_id=artifact.regression_case_id,
        contract_version=run.contract_version,
        scenario_id=run.scenario_id,
        scenario_version=run.scenario_version,
        tested_agent_id=run.expected_tested_agent_id,
        tested_agent_version=run.expected_tested_agent_version,
        tested_input=run.tested_input,
        tested_input_digest=run.tested_input_digest,
        fault_spec_id=run.fault_spec_id,
        fault_definition=fault,
        fault_definition_digest=run.run_definition_digest,
        analyzer_version=analysis.analyzer_version,
        assertion_set_version=analysis.assertion_set_version,
        policy_version=analysis.policy_version,
    )


def _apply_control_definition_verification(rows, *, control_run, candidate_run, artifact):
    field_values = {
        "contract_version": (control_run.contract_version, candidate_run.contract_version),
        "scenario_id": (control_run.scenario_id, candidate_run.scenario_id),
        "scenario_version": (control_run.scenario_version, candidate_run.scenario_version),
        "tested_agent_id": (control_run.expected_tested_agent_id, candidate_run.expected_tested_agent_id),
        "tested_agent_version": (control_run.expected_tested_agent_version, candidate_run.expected_tested_agent_version),
        "tested_input_digest": (control_run.tested_input_digest, candidate_run.tested_input_digest),
        "fault_definition_digest": (control_run.run_definition_digest, candidate_run.run_definition_digest),
    }
    updated = []
    for row in rows:
        pair = field_values.get(row.field_identifier)
        if pair is not None and pair[0] != pair[1]:
            updated.append(row.model_copy(update={"result": "MISMATCH"}))
        else:
            updated.append(row)
    return updated


async def _runtime_identity_rows(
    connection,
    *,
    rerun,
    artifact,
    source,
    source_control,
    control,
    candidate,
    candidate_analysis_id,
):
    source_capabilities = {
        source.manifest.capability_binding.capability_record_id,
        source_control.manifest.capability_binding.capability_record_id,
    }
    control_capability = control.manifest.capability_binding
    candidate_capability = candidate.manifest.capability_binding
    if control_capability is None or candidate_capability is None:
        raise RerunIntegrityError("runtime capability identity is missing")
    source_run_ids = {source.manifest.run_id, source_control.manifest.run_id}
    source_trace_ids = {source.manifest.trace_id, source_control.manifest.trace_id}
    source_evidence_ids = {
        source.manifest.evidence_set_id,
        source_control.manifest.evidence_set_id,
    }
    source_fault_ids = {source.manifest.fault_id}
    source_event_rows = await _evidence_identity_rows(
        connection, source_run_ids
    )
    control_event_rows = await _evidence_identity_rows(
        connection, {control.manifest.run_id}
    )
    candidate_event_rows = await _evidence_identity_rows(
        connection, {candidate.manifest.run_id}
    )
    source_tool_ids = await _tool_call_ids(connection, source_run_ids)
    control_tool_ids = await _tool_call_ids(
        connection, {control.manifest.run_id}
    )
    candidate_tool_ids = await _tool_call_ids(
        connection, {candidate.manifest.run_id}
    )
    source_boundary_ids = {row.evidence_id for row in source_event_rows}
    source_target_ids = {
        row.source_event_id for row in source_event_rows if row.source == "sut"
    }
    control_boundary_ids = {row.evidence_id for row in control_event_rows}
    control_target_ids = {
        row.source_event_id for row in control_event_rows if row.source == "sut"
    }
    candidate_boundary_ids = {row.evidence_id for row in candidate_event_rows}
    candidate_target_ids = {
        row.source_event_id for row in candidate_event_rows if row.source == "sut"
    }
    refs = [
        f"regression_case:{artifact.regression_case_id}",
        f"rerun:{rerun.rerun_id}:runtime",
    ]
    return [
        _fresh_scalar_row("campaign_id", artifact.source_campaign_id, rerun.campaign_id, refs),
        _fresh_set_row("control_run_id", source_run_ids, {control.manifest.run_id}, refs),
        _fresh_set_row("control_trace_id", source_trace_ids, {control.manifest.trace_id}, refs),
        _fresh_set_row("control_capability_record_id", source_capabilities, {control_capability.capability_record_id}, refs),
        _fresh_set_row("control_evidence_set_id", source_evidence_ids, {control.manifest.evidence_set_id}, refs),
        _fresh_set_row("control_boundary_event_ids", source_boundary_ids, control_boundary_ids, refs),
        _fresh_set_row("control_target_event_ids", source_target_ids, control_target_ids, refs),
        _fresh_set_row("control_tool_call_ids", source_tool_ids, control_tool_ids, refs),
        _fresh_set_row("candidate_run_id", source_run_ids | {control.manifest.run_id}, {candidate.manifest.run_id}, refs),
        _fresh_set_row("candidate_trace_id", source_trace_ids | {control.manifest.trace_id}, {candidate.manifest.trace_id}, refs),
        _fresh_set_row("candidate_fault_id", source_fault_ids, {candidate.manifest.fault_id}, refs),
        _fresh_set_row("candidate_capability_record_id", source_capabilities | {control_capability.capability_record_id}, {candidate_capability.capability_record_id}, refs),
        _fresh_set_row("candidate_evidence_set_id", source_evidence_ids | {control.manifest.evidence_set_id}, {candidate.manifest.evidence_set_id}, refs),
        _fresh_set_row("candidate_boundary_event_ids", source_boundary_ids | control_boundary_ids, candidate_boundary_ids, refs),
        _fresh_set_row("candidate_target_event_ids", source_target_ids | control_target_ids, candidate_target_ids, refs),
        _fresh_set_row("candidate_tool_call_ids", source_tool_ids | control_tool_ids, candidate_tool_ids, refs),
        _fresh_scalar_row("candidate_analysis_id", artifact.source_analysis_id, candidate_analysis_id, refs),
    ]


def _fresh_scalar_row(field_identifier, source, candidate, refs):
    return InvarianceRowV1(
        field_identifier=field_identifier,
        source_value_or_digest=_value(source),
        rerun_value_or_digest=_value(candidate),
        comparison_rule="fresh_identity_required",
        result=(
            "PERMITTED_DIFFERENCE"
            if source is not None and candidate is not None and source != candidate
            else "MISMATCH"
        ),
        authoritative_references=refs,
    )


def _fresh_set_row(field_identifier, source_values, candidate_values, refs):
    source_values = {value for value in source_values if value is not None}
    candidate_values = {value for value in candidate_values if value is not None}
    return InvarianceRowV1(
        field_identifier=field_identifier,
        source_value_or_digest=_identity_set_digest(source_values),
        rerun_value_or_digest=_identity_set_digest(candidate_values),
        comparison_rule="fresh_identity_set_disjoint_from_source",
        result=(
            "PERMITTED_DIFFERENCE"
            if candidate_values and source_values.isdisjoint(candidate_values)
            else "MISMATCH"
        ),
        authoritative_references=refs,
    )


def _identity_set_digest(values) -> str:
    normalized = sorted(str(value) for value in values)
    return sha256(rfc8785.dumps(normalized)).hexdigest()


async def _evidence_identity_rows(connection, run_ids):
    return (
        await connection.execute(
            sa.select(
                evidence_records.c.evidence_id,
                evidence_records.c.source,
                evidence_records.c.source_event_id,
            ).where(evidence_records.c.run_id.in_(run_ids))
        )
    ).all()


async def _tool_call_ids(connection, run_ids):
    return set(
        (
            await connection.execute(
                sa.select(tool_calls.c.tool_call_id).where(
                    tool_calls.c.run_id.in_(run_ids)
                )
            )
        ).scalars()
    )


async def _run_row(connection, run_id, *, lock=False):
    statement = sa.select(runs).where(runs.c.run_id == run_id)
    if lock:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).one_or_none()
    if row is None:
        raise RerunIntegrityError("rerun execution run is missing")
    return row


async def _analysis_row(connection, analysis_id, *, lock=False):
    statement = sa.select(analyses).where(analyses.c.analysis_id == analysis_id)
    if lock:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).one_or_none()
    if row is None:
        raise RerunIntegrityError("analysis is missing")
    return row


def _verify_analysis_row(row) -> AnalysisDocumentV1:
    if row.record_kind != "authoritative":
        raise RerunIntegrityError("analysis is not authoritative")
    try:
        document = AnalysisDocumentV1.model_validate_json(
            json.dumps(row.analysis_document)
        )
    except ValidationError as error:
        raise RerunIntegrityError("analysis schema is invalid") from error
    canonical_bytes = rfc8785.dumps(document.model_dump(mode="json"))
    if (
        canonical_bytes != row.analysis_canonical_bytes
        or sha256(canonical_bytes).hexdigest() != row.analysis_digest
        or document.evidence_set_id != row.evidence_set_id
        or document.evidence_set_digest != row.evidence_set_digest
        or document.evaluability.aggregate != row.evaluability_aggregate
        or document.scenario_policy_result != row.policy_result
    ):
        raise RerunIntegrityError("analysis failed digest verification")
    return document


async def seal_comparison(
    engine: AsyncEngine,
    *,
    rerun_id: UUID,
    candidate_analysis_id: UUID,
) -> SealedComparison:
    """Seal a pending comparison once after rechecking every predicate."""
    try:
        async with engine.begin() as connection:
            rerun = (
                await connection.execute(
                    sa.select(reruns)
                    .where(reruns.c.rerun_id == rerun_id)
                    .with_for_update()
                )
            ).one_or_none()
            if rerun is None or rerun.mode != "version_comparison":
                raise ComparisonIneligible("NOT_A_VERSION_COMPARISON")
            comparison = (
                await connection.execute(
                    sa.select(comparisons)
                    .where(comparisons.c.rerun_id == rerun_id)
                    .with_for_update()
                )
            ).one_or_none()
            if comparison is None:
                raise RerunIntegrityError("pending comparison is missing")
            _, artifact = await load_regression_case(
                connection,
                regression_case_id=rerun.regression_case_id,
                lock=True,
            )
            candidate_row = await _analysis_row(
                connection, candidate_analysis_id, lock=True
            )
            candidate_document = _verify_analysis_row(candidate_row)
            source_row = await _analysis_row(
                connection, artifact.source_analysis_id, lock=True
            )
            source_document = _verify_analysis_row(source_row)
            completed, completed_digest = _load_completed_report(rerun)
            source_snapshot = await load_finalized_snapshot(
                connection,
                evidence_set_id=artifact.source_evidence_set_id,
                lock=True,
            )
            candidate_snapshot = await load_finalized_snapshot(
                connection,
                evidence_set_id=candidate_row.evidence_set_id,
                lock=True,
            )
            candidate_run = (
                await _run_row(connection, candidate_snapshot.manifest.run_id)
                if candidate_snapshot is not None
                else None
            )
            reason = _comparison_ineligibility_reason(
                comparison=comparison,
                rerun=rerun,
                artifact=artifact,
                source_row=source_row,
                source_document=source_document,
                source_snapshot=source_snapshot,
                candidate_row=candidate_row,
                candidate_document=candidate_document,
                candidate_snapshot=candidate_snapshot,
                candidate_run=candidate_run,
                completed=completed,
                candidate_analysis_id=candidate_analysis_id,
            )
            if reason is not None:
                failure = ComparisonFailureSummaryV1(
                    summary_schema_version=1,
                    comparison_id=comparison.comparison_id,
                    regression_case_id=artifact.regression_case_id,
                    rerun_id=rerun_id,
                    source_run_id=artifact.source_run_id,
                    source_evidence_set_id=artifact.source_evidence_set_id,
                    source_analysis_id=artifact.source_analysis_id,
                    candidate_run_id=rerun.candidate_run_id,
                    candidate_evidence_set_id=(
                        candidate_row.evidence_set_id
                        if candidate_row is not None
                        else None
                    ),
                    candidate_analysis_id=candidate_analysis_id,
                    source_tested_agent_version=(
                        artifact.original_tested_agent_version
                    ),
                    candidate_tested_agent_version=(
                        rerun.requested_tested_agent_version
                    ),
                    source_policy_result="FAIL",
                    candidate_policy_result=candidate_document.scenario_policy_result,
                    completed_invariance_digest=completed_digest,
                    terminal_result="INELIGIBLE",
                    reason_code=reason,
                )
                return await _seal_comparison_row(
                    connection,
                    comparison=comparison,
                    summary=failure,
                    status="ineligible",
                    terminal_result="INELIGIBLE",
                    reason_code=reason,
                    candidate_run_id=rerun.candidate_run_id,
                    candidate_evidence_set_id=candidate_row.evidence_set_id,
                    candidate_analysis_id=candidate_analysis_id,
                    candidate_policy_result=(
                        candidate_document.scenario_policy_result
                    ),
                )
            assert candidate_snapshot is not None
            summary = ComparisonSummaryV1(
                summary_schema_version=1,
                comparison_id=comparison.comparison_id,
                regression_case_id=artifact.regression_case_id,
                rerun_id=rerun_id,
                source_run_id=artifact.source_run_id,
                source_evidence_set_id=artifact.source_evidence_set_id,
                source_analysis_id=artifact.source_analysis_id,
                candidate_run_id=candidate_snapshot.manifest.run_id,
                candidate_evidence_set_id=candidate_row.evidence_set_id,
                candidate_analysis_id=candidate_analysis_id,
                source_tested_agent_version=(
                    artifact.original_tested_agent_version
                ),
                candidate_tested_agent_version=(
                    candidate_run.expected_tested_agent_version
                ),
                source_policy_result="FAIL",
                candidate_policy_result="PASS",
                completed_invariance_digest=completed_digest,
                terminal_result="VALID",
                reason_code="VULNERABLE_FAIL_FIXED_PASS",
                scoped_conclusion=(
                    "The fixed tested-agent version passes this scenario policy."
                ),
            )
            return await _seal_comparison_row(
                connection,
                comparison=comparison,
                summary=summary,
                status="valid",
                terminal_result="VALID",
                reason_code="VULNERABLE_FAIL_FIXED_PASS",
                candidate_run_id=candidate_snapshot.manifest.run_id,
                candidate_evidence_set_id=candidate_row.evidence_set_id,
                candidate_analysis_id=candidate_analysis_id,
                candidate_policy_result="PASS",
            )
    except (
        ComparisonIneligible,
        RerunIntegrityError,
        RerunConflict,
        RegressionIntegrityError,
        FinalizedSnapshotError,
    ):
        raise
    except (IntegrityError, SQLAlchemyError):
        raise RerunPersistenceError("comparison sealing failed") from None


def _load_completed_report(rerun):
    if rerun.completed_invariance_digest is None:
        raise ComparisonIneligible("INVARIANCE_REPORT_NOT_COMPLETED")
    try:
        report = CompletedInvarianceReportV1.model_validate_json(
            json.dumps(rerun.completed_invariance_report)
        )
    except ValidationError as error:
        raise RerunIntegrityError(
            "completed invariance report schema is invalid"
        ) from error
    canonical_bytes, digest = canonicalize_document(report)
    if (
        canonical_bytes != rerun.completed_invariance_canonical_bytes
        or digest != rerun.completed_invariance_digest
        or report.rerun_id != rerun.rerun_id
        or report.regression_case_id != rerun.regression_case_id
        or report.mode != rerun.mode
    ):
        raise RerunIntegrityError(
            "completed invariance report failed digest verification"
        )
    return report, digest


def _comparison_ineligibility_reason(
    *,
    comparison,
    rerun,
    artifact,
    source_row,
    source_document,
    source_snapshot,
    candidate_row,
    candidate_document,
    candidate_snapshot,
    candidate_run,
    completed,
    candidate_analysis_id,
):
    if (
        comparison.regression_case_id != artifact.regression_case_id
        or comparison.source_run_id != artifact.source_run_id
        or comparison.source_evidence_set_id
        != artifact.source_evidence_set_id
        or comparison.source_analysis_id != artifact.source_analysis_id
        or comparison.source_tested_agent_version
        != artifact.original_tested_agent_version
        or comparison.source_policy_result != "FAIL"
    ):
        return "SOURCE_PROVENANCE_MISMATCH"
    if (
        source_snapshot is None
        or source_snapshot.manifest.evidence_set_id
        != artifact.source_evidence_set_id
        or source_document.evaluability.aggregate != "EVALUABLE"
        or source_document.scenario_policy_result != "FAIL"
        or source_row.analysis_digest != artifact.source_analysis_digest
    ):
        return "SOURCE_NOT_EVALUABLE_FAIL"
    if (
        rerun.candidate_run_id is None
        or candidate_snapshot is None
        or candidate_run is None
        or candidate_snapshot.manifest.run_id != rerun.candidate_run_id
        or candidate_row.analysis_id != candidate_analysis_id
        or candidate_document.evaluability.aggregate != "EVALUABLE"
        or candidate_document.scenario_policy_result != "PASS"
    ):
        return "CANDIDATE_NOT_EVALUABLE_PASS"
    if (
        candidate_run.expected_tested_agent_version
        == artifact.original_tested_agent_version
        or candidate_run.expected_tested_agent_version != FIXED_AGENT_VERSION
        or candidate_run.reported_tested_agent_version
        != candidate_run.expected_tested_agent_version
    ):
        return "CANDIDATE_VERSION_NOT_FIXED_DIFFERENCE"
    if rerun.regression_case_id != artifact.regression_case_id:
        return "REGRESSION_CASE_BINDING_MISMATCH"
    if any(row.result == "MISMATCH" for row in completed.rows):
        return "INVARIANCE_MISMATCH"
    allowed = {"MATCH", "PERMITTED_DIFFERENCE"}
    if any(row.result not in allowed for row in completed.rows):
        return "INVARIANCE_RESULT_INVALID"
    return None


async def _seal_comparison_row(
    connection,
    *,
    comparison,
    summary,
    status,
    terminal_result,
    reason_code,
    candidate_run_id,
    candidate_evidence_set_id,
    candidate_analysis_id,
    candidate_policy_result,
):
    canonical_bytes, digest = canonicalize_document(summary)
    if comparison.status != "pending":
        if (
            comparison.status == status
            and comparison.terminal_result == terminal_result
            and comparison.reason_code == reason_code
            and comparison.candidate_run_id == candidate_run_id
            and comparison.candidate_evidence_set_id
            == candidate_evidence_set_id
            and comparison.candidate_analysis_id == candidate_analysis_id
            and comparison.candidate_policy_result == candidate_policy_result
            and comparison.summary_canonical_bytes == canonical_bytes
            and comparison.summary_digest == digest
            and rfc8785.dumps(comparison.summary_document) == canonical_bytes
        ):
            return SealedComparison(
                comparison_id=comparison.comparison_id,
                status=status,
                terminal_result=terminal_result,
                reason_code=reason_code,
                summary_digest=digest,
                replayed=True,
            )
        raise RerunConflict("terminal comparison content conflicts")
    updated = await connection.execute(
        comparisons.update()
        .where(
            comparisons.c.comparison_id == comparison.comparison_id,
            comparisons.c.status == "pending",
        )
        .values(
            candidate_run_id=candidate_run_id,
            candidate_evidence_set_id=candidate_evidence_set_id,
            candidate_analysis_id=candidate_analysis_id,
            candidate_policy_result=candidate_policy_result,
            status=status,
            terminal_result=terminal_result,
            reason_code=reason_code,
            summary_schema_version=1,
            summary_document=json.loads(canonical_bytes),
            summary_canonical_bytes=canonical_bytes,
            summary_digest=digest,
        )
    )
    if updated.rowcount != 1:
        raise RerunConflict("comparison terminal transition conflicted")
    return SealedComparison(
        comparison_id=comparison.comparison_id,
        status=status,
        terminal_result=terminal_result,
        reason_code=reason_code,
        summary_digest=digest,
        replayed=False,
    )


async def _mark_rerun_failed(engine, *, rerun_id):
    try:
        async with engine.begin() as connection:
            rerun = (
                await connection.execute(
                    sa.select(reruns)
                    .where(reruns.c.rerun_id == rerun_id)
                    .with_for_update()
                )
            ).one_or_none()
            if rerun is None:
                return
            if rerun.status in {"accepted", "running"}:
                await connection.execute(
                    reruns.update()
                    .where(reruns.c.rerun_id == rerun_id)
                    .values(status="failed", reason_code="RERUN_EXECUTION_ERROR")
                )
            if rerun.campaign_id is not None:
                await connection.execute(
                    campaigns.update()
                    .where(
                        campaigns.c.campaign_id == rerun.campaign_id,
                        campaigns.c.status.in_(["accepted", "running"]),
                    )
                    .values(status="failed", current_step="failed")
                )
    except SQLAlchemyError:
        raise RerunPersistenceError("rerun failure settlement failed") from None


async def _seal_execution_error_if_pending(
    engine,
    *,
    rerun_id,
    reason_code,
):
    try:
        async with engine.begin() as connection:
            comparison = (
                await connection.execute(
                    sa.select(comparisons)
                    .where(comparisons.c.rerun_id == rerun_id)
                    .with_for_update()
                )
            ).one_or_none()
            if comparison is None or comparison.status != "pending":
                return
            rerun = (
                await connection.execute(
                    sa.select(reruns).where(reruns.c.rerun_id == rerun_id)
                )
            ).one()
            _, artifact = await load_regression_case(
                connection,
                regression_case_id=rerun.regression_case_id,
            )
            candidate_evidence_set_id = None
            candidate_analysis_id = None
            candidate_policy_result = None
            if rerun.candidate_run_id is not None:
                candidate_evidence_set_id = await connection.scalar(
                    sa.select(evidence_sets.c.evidence_set_id).where(
                        evidence_sets.c.run_id == rerun.candidate_run_id
                    )
                )
                if candidate_evidence_set_id is not None:
                    candidate = (
                        await connection.execute(
                            sa.select(analyses)
                            .where(
                                analyses.c.evidence_set_id
                                == candidate_evidence_set_id,
                                analyses.c.record_kind == "authoritative",
                            )
                            .order_by(analyses.c.created_at.desc())
                        )
                    ).first()
                    if candidate is not None:
                        candidate_analysis_id = candidate.analysis_id
                        candidate_policy_result = candidate.policy_result
            completed_digest = rerun.completed_invariance_digest
            failure = ComparisonFailureSummaryV1(
                summary_schema_version=1,
                comparison_id=comparison.comparison_id,
                regression_case_id=artifact.regression_case_id,
                rerun_id=rerun_id,
                source_run_id=artifact.source_run_id,
                source_evidence_set_id=artifact.source_evidence_set_id,
                source_analysis_id=artifact.source_analysis_id,
                candidate_run_id=rerun.candidate_run_id,
                candidate_evidence_set_id=candidate_evidence_set_id,
                candidate_analysis_id=candidate_analysis_id,
                source_tested_agent_version=(
                    artifact.original_tested_agent_version
                ),
                candidate_tested_agent_version=(
                    rerun.requested_tested_agent_version
                ),
                source_policy_result="FAIL",
                candidate_policy_result=candidate_policy_result,
                completed_invariance_digest=completed_digest,
                terminal_result="EXECUTION_ERROR",
                reason_code=reason_code,
            )
            await _seal_comparison_row(
                connection,
                comparison=comparison,
                summary=failure,
                status="execution_error",
                terminal_result="EXECUTION_ERROR",
                reason_code=reason_code,
                candidate_run_id=rerun.candidate_run_id,
                candidate_evidence_set_id=candidate_evidence_set_id,
                candidate_analysis_id=candidate_analysis_id,
                candidate_policy_result=candidate_policy_result,
            )
    except SQLAlchemyError:
        raise RerunPersistenceError(
            "execution-error comparison settlement failed"
        ) from None
    except (RerunError, RegressionIntegrityError):
        raise

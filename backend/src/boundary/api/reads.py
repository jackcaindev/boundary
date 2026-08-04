"""Integrity-verifying public read serializers."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any
from uuid import UUID

import rfc8785
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.api.errors import PublicProblem
from boundary.api.models import (
    CampaignView,
    ComparisonView,
    EvidenceItem,
    EvidencePage,
    RegressionCaseView,
    ResourceLinks,
    RunView,
)
from boundary.domain.evaluation import AnalysisDocumentV1
from boundary.domain.regression import (
    ComparisonFailureSummaryV1,
    ComparisonSummaryV1,
    CompletedInvarianceReportV1,
    PreInvocationInvarianceReportV1,
)
from boundary.evaluation.snapshot import FinalizedSnapshotError, load_finalized_snapshot
from boundary.persistence.tables import (
    analyses,
    campaigns,
    comparisons,
    evidence_records,
    evidence_sets,
    regression_cases,
    reruns,
    runs,
)
from boundary.regression.materializer import (
    MaterializationIneligible,
    RegressionIntegrityError,
    load_regression_case,
)
from boundary.injection.fault_spec import (
    FaultDefinitionMismatch,
    validate_phase1_fault_document,
)


TERMINAL_CAMPAIGNS = {"completed", "failed", "cancelled"}


def _links(
    *,
    campaign_id=None,
    control_run_id=None,
    injected_run_id=None,
    regression_case_id=None,
    comparison_id=None,
) -> ResourceLinks:
    return ResourceLinks(
        campaign=f"/api/v1/campaigns/{campaign_id}" if campaign_id else None,
        control_run=f"/api/v1/runs/{control_run_id}" if control_run_id else None,
        injected_run=f"/api/v1/runs/{injected_run_id}" if injected_run_id else None,
        regression_case=(
            f"/api/v1/regression-cases/{regression_case_id}"
            if regression_case_id
            else None
        ),
        comparison=(
            f"/api/v1/comparisons/{comparison_id}" if comparison_id else None
        ),
    )


def _verify_canonical(document: Any, canonical: bytes, digest: str, label: str) -> None:
    try:
        normalized = rfc8785.dumps(document)
    except (TypeError, ValueError) as error:
        raise PublicProblem(500, "INTEGRITY_VERIFICATION_FAILED", f"{label} is invalid") from error
    if normalized != canonical or sha256(canonical).hexdigest() != digest:
        raise PublicProblem(500, "INTEGRITY_VERIFICATION_FAILED", f"{label} failed integrity verification")


def _verify_analysis(row) -> AnalysisDocumentV1:
    try:
        document = AnalysisDocumentV1.model_validate_json(
            json.dumps(row.analysis_document)
        )
    except ValidationError as error:
        raise PublicProblem(500, "ANALYSIS_INTEGRITY_FAILED", "analysis schema is invalid") from error
    canonical = rfc8785.dumps(document.model_dump(mode="json"))
    if (
        row.record_kind != "authoritative"
        or canonical != row.analysis_canonical_bytes
        or sha256(canonical).hexdigest() != row.analysis_digest
        or row.analysis_schema_version != document.schema_version
        or row.evidence_set_id != document.evidence_set_id
        or row.evidence_set_digest != document.evidence_set_digest
        or row.analyzer_version != document.analyzer_version
        or row.assertion_set_version != document.assertion_set_version
        or row.policy_version != document.policy_version
        or row.evaluability_aggregate != document.evaluability.aggregate
        or row.policy_result != document.scenario_policy_result
    ):
        raise PublicProblem(500, "ANALYSIS_INTEGRITY_FAILED", "analysis failed integrity verification")
    return document


async def read_campaign(engine: AsyncEngine, campaign_id: UUID) -> CampaignView:
    async with engine.connect() as connection:
        campaign = (await connection.execute(sa.select(campaigns).where(campaigns.c.campaign_id == campaign_id))).one_or_none()
        if campaign is None:
            raise PublicProblem(404, "CAMPAIGN_NOT_FOUND", "campaign does not exist")
        run_rows = (await connection.execute(sa.select(runs).where(runs.c.campaign_id == campaign_id).order_by(runs.c.created_at, runs.c.run_id))).all()
        rerun = (await connection.execute(sa.select(reruns).where(reruns.c.campaign_id == campaign_id))).one_or_none()
        case_id = None
        if run_rows:
            case_id = await connection.scalar(
                sa.select(regression_cases.c.regression_case_id)
                .join(analyses, regression_cases.c.source_analysis_id == analyses.c.analysis_id)
                .join(evidence_sets, analyses.c.evidence_set_id == evidence_sets.c.evidence_set_id)
                .where(evidence_sets.c.run_id.in_([row.run_id for row in run_rows]))
            )
        comparison_id = None
        if rerun is not None:
            comparison_id = await connection.scalar(sa.select(comparisons.c.comparison_id).where(comparisons.c.rerun_id == rerun.rerun_id))
            case_id = rerun.regression_case_id
    control = next((row for row in run_rows if row.run_role == "control"), None)
    injected = next((row for row in run_rows if row.run_role == "injected"), None)
    reason = campaign.failure_reason[:128] if campaign.failure_reason else None
    return CampaignView(
        campaign_id=campaign.campaign_id,
        campaign_kind=campaign.campaign_kind,
        operational_status=campaign.status,
        current_step=campaign.current_step,
        cancel_requested=campaign.cancel_requested,
        cancellation_id=campaign.cancellation_id,
        terminal=campaign.status in TERMINAL_CAMPAIGNS,
        failure_reason=reason,
        control_run_id=control.run_id if control else None,
        injected_run_id=injected.run_id if injected else None,
        regression_case_id=case_id,
        rerun_id=rerun.rerun_id if rerun else None,
        comparison_id=comparison_id,
        links=_links(
            campaign_id=campaign_id,
            control_run_id=control.run_id if control else None,
            injected_run_id=injected.run_id if injected else None,
            regression_case_id=case_id,
            comparison_id=comparison_id,
        ),
    )


async def read_run(engine: AsyncEngine, run_id: UUID) -> RunView:
    async with engine.connect() as connection:
        run = (await connection.execute(sa.select(runs).where(runs.c.run_id == run_id))).one_or_none()
        if run is None:
            raise PublicProblem(404, "RUN_NOT_FOUND", "run does not exist")
        _verify_canonical(run.run_definition, run.run_definition_bytes, run.run_definition_digest, "run definition")
        try:
            definition = validate_phase1_fault_document(
                run.run_definition,
                run.run_definition_bytes,
                run.run_definition_digest,
            )
        except FaultDefinitionMismatch as error:
            raise PublicProblem(500, "RUN_DEFINITION_INTEGRITY_FAILED", "run definition failed integrity verification") from error
        if (
            run.definition_schema_version != definition.schema_version
            or run.scenario_id != definition.scenario_id
            or run.scenario_version != definition.scenario_version
            or run.contract_version not in definition.compatible_contract_versions
        ):
            raise PublicProblem(500, "RUN_DEFINITION_INTEGRITY_FAILED", "run definition projections conflict")
        snapshot = None
        try:
            snapshot = await load_finalized_snapshot(connection, run_id=run_id)
        except FinalizedSnapshotError as error:
            raise PublicProblem(500, "EVIDENCE_SET_INTEGRITY_FAILED", "evidence set failed integrity verification") from error
        analysis_row = None
        analysis = None
        if snapshot is not None:
            analysis_row = (await connection.execute(sa.select(analyses).where(analyses.c.evidence_set_id == snapshot.manifest.evidence_set_id, analyses.c.record_kind == "authoritative").order_by(analyses.c.created_at.desc()))).first()
            if analysis_row is not None:
                analysis = _verify_analysis(analysis_row)
        case_id = None
        if analysis_row is not None:
            case_id = await connection.scalar(sa.select(regression_cases.c.regression_case_id).where(regression_cases.c.source_analysis_id == analysis_row.analysis_id))
        comparison_id = await connection.scalar(sa.select(comparisons.c.comparison_id).where(sa.or_(comparisons.c.source_run_id == run_id, comparisons.c.candidate_run_id == run_id)))
    assertions = [] if analysis is None or analysis.assertions is None else [item.model_dump(mode="json") for item in analysis.assertions]
    return RunView(
        run_id=run.run_id,
        trace_id=run.trace_id,
        campaign_id=run.campaign_id,
        run_role=run.run_role,
        control_run_id=run.control_run_id,
        expected_tested_agent_id=run.expected_tested_agent_id,
        expected_tested_agent_version=run.expected_tested_agent_version,
        reported_tested_agent_id=run.reported_tested_agent_id,
        reported_tested_agent_version=run.reported_tested_agent_version,
        operational_status=run.operational_status,
        policy_result=analysis.scenario_policy_result if analysis else None,
        contract_version=run.contract_version,
        scenario_id=run.scenario_id,
        scenario_version=run.scenario_version,
        fault_spec_id=run.fault_spec_id,
        fault_id=run.fault_id,
        fault_definition_digest=run.run_definition_digest if run.fault_id else None,
        evidence_set_id=snapshot.manifest.evidence_set_id if snapshot else None,
        evidence_set_digest=snapshot.evidence_set_digest if snapshot else None,
        finalizer_identity=(
            snapshot.manifest.finalizer_identity if snapshot else None
        ),
        analysis_id=analysis_row.analysis_id if analysis_row else None,
        analysis_digest=analysis_row.analysis_digest if analysis_row else None,
        analyzer_version=analysis.analyzer_version if analysis else None,
        assertion_set_version=analysis.assertion_set_version if analysis else None,
        policy_version=analysis.policy_version if analysis else None,
        evaluability=analysis.evaluability.model_dump(mode="json") if analysis else None,
        assertions=assertions,
        injection_boundary=analysis.injection_boundary.model_dump(mode="json") if analysis and analysis.injection_boundary else None,
        first_unsafe_divergence=analysis.localization.model_dump(mode="json") if analysis and analysis.localization else None,
        downstream_symptoms=[item.model_dump(mode="json") for item in analysis.downstream_symptoms] if analysis else [],
        regression_case_id=case_id,
        comparison_id=comparison_id,
        links=_links(campaign_id=run.campaign_id, regression_case_id=case_id, comparison_id=comparison_id),
    )


async def read_evidence(engine: AsyncEngine, run_id: UUID, *, after: int, limit: int) -> EvidencePage:
    if after < 0:
        raise PublicProblem(422, "INVALID_EVIDENCE_CURSOR", "after_receipt_seq must be nonnegative")
    if not 1 <= limit <= 100:
        raise PublicProblem(422, "INVALID_EVIDENCE_LIMIT", "limit must be between 1 and 100")
    async with engine.connect() as connection:
        exists = await connection.scalar(sa.select(runs.c.run_id).where(runs.c.run_id == run_id))
        if exists is None:
            raise PublicProblem(404, "RUN_NOT_FOUND", "run does not exist")
        rows = (await connection.execute(sa.select(evidence_records).where(evidence_records.c.run_id == run_id, evidence_records.c.disposition == "accepted", evidence_records.c.receipt_seq > after).order_by(evidence_records.c.receipt_seq).limit(limit + 1))).all()
    page_rows = rows[:limit]
    items = []
    for row in page_rows:
        _verify_canonical(row.payload, row.payload_canonical_bytes, row.payload_digest, "evidence payload")
        items.append(EvidenceItem(
            evidence_id=row.evidence_id,
            authority="Boundary" if row.source == "boundary" else "tested-agent",
            source=row.source,
            event_type=row.event_type,
            boundary=row.boundary,
            source_event_id=row.source_event_id,
            producer_seq=row.producer_seq,
            receipt_seq=row.receipt_seq,
            caused_by_event_id=row.caused_by_event_id,
            payload_schema_version=row.payload_schema_version,
            payload=row.payload,
            payload_digest=row.payload_digest,
        ))
    return EvidencePage(run_id=run_id, after_receipt_seq=after, limit=limit, items=items, next_after_receipt_seq=(items[-1].receipt_seq if len(rows) > limit and items else None))


async def read_regression_case(engine: AsyncEngine, case_id: UUID) -> RegressionCaseView:
    try:
        async with engine.connect() as connection:
            row, artifact = await load_regression_case(connection, regression_case_id=case_id)
            rerun_rows = (await connection.execute(sa.select(reruns).where(reruns.c.regression_case_id == case_id).order_by(reruns.c.created_at, reruns.c.rerun_id))).all()
            comparison_rows = (await connection.execute(sa.select(comparisons).where(comparisons.c.regression_case_id == case_id).order_by(comparisons.c.created_at, comparisons.c.comparison_id))).all()
            source_run = (await connection.execute(sa.select(runs).where(runs.c.run_id == artifact.source_run_id))).one_or_none()
            source_set = await load_finalized_snapshot(connection, evidence_set_id=artifact.source_evidence_set_id)
            source_analysis = (await connection.execute(sa.select(analyses).where(analyses.c.analysis_id == artifact.source_analysis_id))).one_or_none()
            if source_run is None or source_set is None or source_analysis is None:
                raise RegressionIntegrityError("regression source provenance is missing")
            verified_analysis = _verify_analysis(source_analysis)
            if (
                source_run.campaign_id != artifact.source_campaign_id
                or source_run.trace_id != artifact.source_trace_id
                or source_set.manifest.run_id != artifact.source_run_id
                or source_set.evidence_set_digest != artifact.source_evidence_set_digest
                or source_analysis.analysis_digest != artifact.source_analysis_digest
                or verified_analysis.scenario_policy_result != "FAIL"
            ):
                raise RegressionIntegrityError("regression source provenance conflicts")
            for rerun_row in rerun_rows:
                try:
                    pre = PreInvocationInvarianceReportV1.model_validate_json(
                        json.dumps(rerun_row.pre_invariance_report)
                    )
                except ValidationError as error:
                    raise RegressionIntegrityError("rerun pre-report is invalid") from error
                pre_bytes = rfc8785.dumps(pre.model_dump(mode="json"))
                if (
                    pre_bytes != rerun_row.pre_invariance_canonical_bytes
                    or sha256(pre_bytes).hexdigest() != rerun_row.pre_invariance_digest
                    or pre.rerun_id != rerun_row.rerun_id
                    or pre.regression_case_id != case_id
                    or pre.mode != rerun_row.mode
                ):
                    raise RegressionIntegrityError("rerun pre-report failed integrity verification")
    except MaterializationIneligible as error:
        raise PublicProblem(404, error.reason_code, "regression case does not exist") from error
    except (RegressionIntegrityError, FinalizedSnapshotError, PublicProblem) as error:
        if isinstance(error, PublicProblem):
            raise
        raise PublicProblem(500, "REGRESSION_CASE_INTEGRITY_FAILED", "regression case failed integrity verification") from error
    return RegressionCaseView(
        regression_case_id=row.regression_case_id,
        integrity_digest=row.integrity_digest,
        artifact=artifact.model_dump(mode="json"),
        reruns=[{"rerun_id": str(item.rerun_id), "status": item.status, "mode": item.mode, "campaign_id": str(item.campaign_id) if item.campaign_id else None} for item in rerun_rows],
        comparisons=[{"comparison_id": str(item.comparison_id), "status": item.status, "rerun_id": str(item.rerun_id)} for item in comparison_rows],
    )


async def read_comparison(engine: AsyncEngine, comparison_id: UUID) -> ComparisonView:
    async with engine.connect() as connection:
        comparison = (await connection.execute(sa.select(comparisons).where(comparisons.c.comparison_id == comparison_id))).one_or_none()
        if comparison is None:
            raise PublicProblem(404, "COMPARISON_NOT_FOUND", "comparison does not exist")
        rerun = (await connection.execute(sa.select(reruns).where(reruns.c.rerun_id == comparison.rerun_id))).one_or_none()
        if rerun is None:
            raise PublicProblem(500, "COMPARISON_INTEGRITY_FAILED", "comparison rerun is missing")
        rows: list[dict[str, Any]] = []
        if rerun.completed_invariance_digest is not None:
            try:
                report = CompletedInvarianceReportV1.model_validate_json(
                    json.dumps(rerun.completed_invariance_report)
                )
            except ValidationError as error:
                raise PublicProblem(500, "COMPARISON_INTEGRITY_FAILED", "completed invariance report is invalid") from error
            report_bytes = rfc8785.dumps(report.model_dump(mode="json"))
            if report_bytes != rerun.completed_invariance_canonical_bytes or sha256(report_bytes).hexdigest() != rerun.completed_invariance_digest or report.rerun_id != rerun.rerun_id or report.regression_case_id != rerun.regression_case_id or report.mode != rerun.mode:
                raise PublicProblem(500, "COMPARISON_INTEGRITY_FAILED", "completed invariance report failed integrity verification")
            rows = [item.model_dump(mode="json") for item in report.rows]
        conclusion = None
        if comparison.status != "pending":
            model = ComparisonSummaryV1 if comparison.status == "valid" else ComparisonFailureSummaryV1
            try:
                summary = model.model_validate_json(
                    json.dumps(comparison.summary_document)
                )
            except ValidationError as error:
                raise PublicProblem(500, "COMPARISON_INTEGRITY_FAILED", "comparison summary is invalid") from error
            summary_bytes = rfc8785.dumps(summary.model_dump(mode="json"))
            candidate_run = None
            if comparison.candidate_run_id is not None:
                candidate_run = (await connection.execute(sa.select(runs).where(runs.c.run_id == comparison.candidate_run_id))).one_or_none()
            source_analysis_row = (await connection.execute(sa.select(analyses).where(analyses.c.analysis_id == comparison.source_analysis_id))).one_or_none()
            candidate_analysis_row = None
            if comparison.candidate_analysis_id is not None:
                candidate_analysis_row = (await connection.execute(sa.select(analyses).where(analyses.c.analysis_id == comparison.candidate_analysis_id))).one_or_none()
            if source_analysis_row is None:
                raise PublicProblem(500, "COMPARISON_INTEGRITY_FAILED", "comparison analysis provenance is missing")
            source_analysis_doc = _verify_analysis(source_analysis_row)
            candidate_analysis_doc = (
                _verify_analysis(candidate_analysis_row)
                if candidate_analysis_row is not None
                else None
            )
            try:
                source_snapshot = await load_finalized_snapshot(connection, evidence_set_id=comparison.source_evidence_set_id)
                candidate_snapshot = (
                    await load_finalized_snapshot(
                        connection,
                        evidence_set_id=comparison.candidate_evidence_set_id,
                    )
                    if comparison.candidate_evidence_set_id is not None
                    else None
                )
            except FinalizedSnapshotError as error:
                raise PublicProblem(500, "COMPARISON_INTEGRITY_FAILED", "comparison evidence provenance is invalid") from error
            projections_match = (
                summary_bytes == comparison.summary_canonical_bytes
                and sha256(summary_bytes).hexdigest() == comparison.summary_digest
                and summary.comparison_id == comparison.comparison_id
                and summary.regression_case_id == comparison.regression_case_id
                and summary.rerun_id == comparison.rerun_id
                and summary.source_run_id == comparison.source_run_id
                and summary.candidate_run_id == comparison.candidate_run_id
                and summary.source_evidence_set_id == comparison.source_evidence_set_id
                and summary.candidate_evidence_set_id == comparison.candidate_evidence_set_id
                and summary.source_analysis_id == comparison.source_analysis_id
                and summary.candidate_analysis_id == comparison.candidate_analysis_id
                and summary.source_tested_agent_version == comparison.source_tested_agent_version
                and summary.candidate_tested_agent_version == comparison.candidate_tested_agent_version
                and summary.source_policy_result == comparison.source_policy_result
                and summary.candidate_policy_result == comparison.candidate_policy_result
                and comparison.summary_schema_version == summary.summary_schema_version
                and summary.terminal_result == comparison.terminal_result
                and summary.reason_code == comparison.reason_code
                and comparison.candidate_tested_agent_version == rerun.requested_tested_agent_version
                and (
                    comparison.candidate_run_id is None
                    or candidate_run is not None
                )
                and (candidate_run is None or candidate_run.expected_tested_agent_version == comparison.candidate_tested_agent_version)
                and source_snapshot is not None
                and source_snapshot.manifest.run_id == comparison.source_run_id
                and source_analysis_doc.evidence_set_id == comparison.source_evidence_set_id
                and source_analysis_doc.scenario_policy_result == comparison.source_policy_result
                and (
                    comparison.candidate_evidence_set_id is None
                    or candidate_snapshot is not None
                )
                and (
                    candidate_snapshot is None
                    or candidate_snapshot.manifest.run_id
                    == comparison.candidate_run_id
                )
                and (
                    comparison.candidate_analysis_id is None
                    or candidate_analysis_doc is not None
                )
                and (
                    candidate_analysis_doc is None
                    or candidate_analysis_doc.evidence_set_id
                    == comparison.candidate_evidence_set_id
                )
                and (
                    candidate_analysis_doc is None
                    or candidate_analysis_doc.scenario_policy_result
                    == comparison.candidate_policy_result
                )
            )
            if not projections_match:
                raise PublicProblem(500, "COMPARISON_INTEGRITY_FAILED", "comparison relational projections conflict")
            conclusion = getattr(summary, "scoped_conclusion", None) if comparison.status == "valid" else None
    return ComparisonView(
        comparison_id=comparison.comparison_id,
        status=comparison.status,
        terminal=comparison.status != "pending",
        regression_case_id=comparison.regression_case_id,
        rerun_id=comparison.rerun_id,
        source_run_id=comparison.source_run_id,
        candidate_run_id=comparison.candidate_run_id,
        source_evidence_set_id=comparison.source_evidence_set_id,
        candidate_evidence_set_id=comparison.candidate_evidence_set_id,
        source_analysis_id=comparison.source_analysis_id,
        candidate_analysis_id=comparison.candidate_analysis_id,
        source_tested_agent_version=comparison.source_tested_agent_version,
        candidate_tested_agent_version=comparison.candidate_tested_agent_version,
        source_policy_result=comparison.source_policy_result,
        candidate_policy_result=comparison.candidate_policy_result,
        completed_invariance_rows=rows,
        permitted_differences=[row for row in rows if row["result"] == "PERMITTED_DIFFERENCE"],
        mismatches=[row for row in rows if row["result"] == "MISMATCH"],
        summary_digest=comparison.summary_digest,
        terminal_reason=comparison.reason_code,
        scoped_conclusion=conclusion,
    )

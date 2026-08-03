"""Atomic eligible-FAIL materialization and regression artifact integrity."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid5

import rfc8785
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from boundary.domain.evaluation import AnalysisDocumentV1
from boundary.domain.regression import (
    RegressionArtifactV1,
    RegressionLocalizationV1,
    TestedInputV1,
)
from boundary.evaluation.evaluability_v1 import timeout_chain
from boundary.evaluation.snapshot import (
    FinalizedSnapshotError,
    load_finalized_snapshot,
)
from boundary.injection.fault_spec import (
    FAULT_SPEC_V1_ID,
    FaultDefinitionMismatch,
    validate_phase1_fault_document,
)
from boundary.persistence.tables import (
    analyses,
    campaigns,
    evidence_sets,
    regression_cases,
    reruns,
    runs,
)


REGRESSION_CASE_NAMESPACE = UUID("ea39a070-9497-5ccb-8e7a-e4909f371203")


class RegressionMaterializationError(Exception):
    """Safe base error for regression materialization."""


class MaterializationIneligible(RegressionMaterializationError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__("source analysis is not eligible for materialization")


class RegressionIntegrityError(RegressionMaterializationError):
    """An immutable source or stored artifact failed digest verification."""


class RegressionConflict(RegressionMaterializationError):
    """A materialization identity already maps to different content."""


class RegressionPersistenceError(RegressionMaterializationError):
    """PostgreSQL could not commit the materialization transaction."""


@dataclass(frozen=True, slots=True)
class MaterializedRegressionCase:
    regression_case_id: UUID
    source_analysis_id: UUID
    artifact: RegressionArtifactV1
    canonical_bytes: bytes
    integrity_digest: str
    replayed: bool


def _artifact_core(document: dict) -> dict:
    return {key: value for key, value in document.items() if key != "integrity_digest"}


def canonicalize_regression_artifact(
    artifact: RegressionArtifactV1,
) -> tuple[bytes, str]:
    validated = RegressionArtifactV1.model_validate_json(
        rfc8785.dumps(artifact.model_dump(mode="json"))
    )
    document = validated.model_dump(mode="json")
    integrity_digest = sha256(
        rfc8785.dumps(_artifact_core(document))
    ).hexdigest()
    if validated.integrity_digest != integrity_digest:
        raise RegressionIntegrityError("regression integrity digest is invalid")
    return rfc8785.dumps(document), integrity_digest


async def load_regression_case(
    connection: AsyncConnection,
    *,
    regression_case_id: UUID,
    lock: bool = False,
) -> tuple[object, RegressionArtifactV1]:
    statement = sa.select(regression_cases).where(
        regression_cases.c.regression_case_id == regression_case_id
    )
    if lock:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).one_or_none()
    if row is None:
        raise MaterializationIneligible("REGRESSION_CASE_NOT_FOUND")
    try:
        artifact = RegressionArtifactV1.model_validate_json(
            json.dumps(row.artifact)
        )
        canonical_bytes, digest = canonicalize_regression_artifact(artifact)
    except (ValidationError, RegressionIntegrityError) as error:
        raise RegressionIntegrityError(
            "stored regression artifact failed integrity verification"
        ) from error
    if (
        artifact.regression_case_id != row.regression_case_id
        or artifact.source_analysis_id != row.source_analysis_id
        or artifact.source_evidence_set_id != row.source_evidence_set_id
        or artifact.source_run_id != row.source_run_id
        or row.artifact_schema_version != 1
        or row.artifact_canonical_bytes != canonical_bytes
        or row.integrity_digest != digest
        or rfc8785.dumps(row.artifact) != canonical_bytes
    ):
        raise RegressionIntegrityError(
            "stored regression artifact conflicts with relational identity"
        )
    return row, artifact


async def materialize_regression_case(
    engine: AsyncEngine,
    *,
    source_analysis_id: UUID,
    _fail_after: str | None = None,
) -> MaterializedRegressionCase:
    """Lock, revalidate, and atomically ensure one immutable case."""
    try:
        async with engine.begin() as connection:
            analysis_row = (
                await connection.execute(
                    sa.select(analyses)
                    .where(analyses.c.analysis_id == source_analysis_id)
                    .with_for_update()
                )
            ).one_or_none()
            if analysis_row is None:
                raise MaterializationIneligible("SOURCE_ANALYSIS_NOT_FOUND")
            if analysis_row.record_kind != "authoritative":
                raise MaterializationIneligible("SOURCE_ANALYSIS_NOT_AUTHORITATIVE")
            try:
                analysis = AnalysisDocumentV1.model_validate_json(
                    json.dumps(analysis_row.analysis_document)
                )
            except ValidationError as error:
                raise RegressionIntegrityError(
                    "source analysis schema validation failed"
                ) from error
            analysis_bytes = rfc8785.dumps(analysis.model_dump(mode="json"))
            if (
                analysis_bytes != analysis_row.analysis_canonical_bytes
                or sha256(analysis_bytes).hexdigest() != analysis_row.analysis_digest
                or analysis.evidence_set_id != analysis_row.evidence_set_id
                or analysis.evidence_set_digest != analysis_row.evidence_set_digest
                or analysis.analyzer_version != analysis_row.analyzer_version
                or analysis.assertion_set_version
                != analysis_row.assertion_set_version
                or analysis.policy_version != analysis_row.policy_version
                or analysis.evaluability.aggregate
                != analysis_row.evaluability_aggregate
                or analysis.scenario_policy_result != analysis_row.policy_result
            ):
                raise RegressionIntegrityError(
                    "source analysis failed digest or identity verification"
                )
            try:
                snapshot = await load_finalized_snapshot(
                    connection,
                    evidence_set_id=analysis_row.evidence_set_id,
                    lock=True,
                )
            except FinalizedSnapshotError as error:
                raise RegressionIntegrityError(
                    "source evidence failed integrity verification"
                ) from error
            if snapshot is None:
                raise MaterializationIneligible("SOURCE_EVIDENCE_SET_NOT_FOUND")
            source_run = (
                await connection.execute(
                    sa.select(runs)
                    .where(runs.c.run_id == snapshot.manifest.run_id)
                    .with_for_update()
                )
            ).one_or_none()
            if source_run is None:
                raise RegressionIntegrityError("source run is missing")
            source_campaign = (
                await connection.execute(
                    sa.select(campaigns.c.campaign_id).where(
                        campaigns.c.campaign_id == source_run.campaign_id
                    )
                )
            ).one_or_none()
            if source_campaign is None:
                raise RegressionIntegrityError("source campaign is missing")
            rerun_source = await connection.scalar(
                sa.select(sa.func.count())
                .select_from(reruns)
                .where(
                    sa.or_(
                        reruns.c.campaign_id == source_run.campaign_id,
                        reruns.c.candidate_run_id == source_run.run_id,
                    )
                )
            )
            _validate_source_eligibility(
                analysis=analysis,
                snapshot=snapshot,
                source_run=source_run,
                rerun_source=bool(rerun_source),
            )
            _verify_analysis_evidence_bindings(
                analysis=analysis,
                snapshot=snapshot,
            )
            try:
                fault_definition = validate_phase1_fault_document(
                    source_run.run_definition,
                    source_run.run_definition_bytes,
                    source_run.run_definition_digest,
                )
            except FaultDefinitionMismatch as error:
                raise RegressionIntegrityError(
                    "source fault definition failed integrity verification"
                ) from error
            input_bytes = rfc8785.dumps(source_run.tested_input)
            if (
                source_run.tested_input is None
                or source_run.tested_input_bytes != input_bytes
                or sha256(input_bytes).hexdigest() != source_run.tested_input_digest
            ):
                raise RegressionIntegrityError(
                    "source tested input failed integrity verification"
                )
            tested_input = TestedInputV1.model_validate(source_run.tested_input)
            regression_case_id = uuid5(
                REGRESSION_CASE_NAMESPACE,
                f"{source_analysis_id}:{analysis_row.analysis_digest}",
            )
            failed = tuple(
                result.assertion_id
                for result in analysis.assertions or []
                if result.outcome == "FAIL"
            )
            assert analysis.localization is not None
            supporting = _ordered_unique_references(
                list(analysis.localization.supporting_evidence_references)
                + [
                    reference
                    for result in analysis.assertions or []
                    if result.outcome == "FAIL"
                    for reference in result.evidence_references
                ]
            )
            core = {
                "artifact_schema_version": 1,
                "regression_case_id": regression_case_id,
                "source_campaign_id": source_run.campaign_id,
                "source_run_id": source_run.run_id,
                "source_trace_id": source_run.trace_id,
                "source_evidence_set_id": analysis_row.evidence_set_id,
                "source_evidence_set_digest": analysis_row.evidence_set_digest,
                "source_analysis_id": source_analysis_id,
                "source_analysis_digest": analysis_row.analysis_digest,
                "original_tested_agent_id": source_run.expected_tested_agent_id,
                "original_tested_agent_version": source_run.expected_tested_agent_version,
                "contract_version": source_run.contract_version,
                "scenario_id": source_run.scenario_id,
                "scenario_version": source_run.scenario_version,
                "tested_input": tested_input.model_dump(mode="json"),
                "tested_input_digest": source_run.tested_input_digest,
                "fault_spec_id": source_run.fault_spec_id,
                "fault_definition": fault_definition.model_dump(mode="json"),
                "fault_definition_digest": source_run.run_definition_digest,
                "source_fault_id": source_run.fault_id,
                "analyzer_version": analysis.analyzer_version,
                "assertion_set_version": analysis.assertion_set_version,
                "policy_version": analysis.policy_version,
                "failed_assertion_identifiers": failed,
                "localization": RegressionLocalizationV1(
                    assertion_id="P1.RETRY_LIMIT",
                    boundary_event_id=analysis.localization.boundary_event_id,
                    boundary="retry_control",
                    retry_ordinal=2,
                    supporting_evidence_references=(
                        analysis.localization.supporting_evidence_references
                    ),
                ).model_dump(mode="json"),
                "supporting_evidence_references": [
                    reference.model_dump(mode="json") for reference in supporting
                ],
            }
            json_core = json.loads(json.dumps(core, default=str))
            digest = sha256(rfc8785.dumps(json_core)).hexdigest()
            artifact = RegressionArtifactV1.model_validate_json(
                rfc8785.dumps(
                    {**json_core, "integrity_digest": digest}
                )
            )
            canonical_bytes, verified_digest = canonicalize_regression_artifact(
                artifact
            )
            existing = (
                await connection.execute(
                    sa.select(regression_cases).where(
                        regression_cases.c.source_analysis_id
                        == source_analysis_id
                    )
                )
            ).one_or_none()
            if existing is not None:
                if (
                    existing.regression_case_id != regression_case_id
                    or existing.source_evidence_set_id
                    != analysis_row.evidence_set_id
                    or existing.source_run_id != source_run.run_id
                    or existing.artifact_schema_version != 1
                    or existing.artifact_canonical_bytes != canonical_bytes
                    or existing.integrity_digest != verified_digest
                    or rfc8785.dumps(existing.artifact) != canonical_bytes
                ):
                    raise RegressionConflict(
                        "materialization identity maps to conflicting content"
                    )
                return MaterializedRegressionCase(
                    regression_case_id=regression_case_id,
                    source_analysis_id=source_analysis_id,
                    artifact=artifact,
                    canonical_bytes=canonical_bytes,
                    integrity_digest=verified_digest,
                    replayed=True,
                )
            await connection.execute(
                regression_cases.insert().values(
                    regression_case_id=regression_case_id,
                    source_analysis_id=source_analysis_id,
                    source_evidence_set_id=analysis_row.evidence_set_id,
                    source_run_id=source_run.run_id,
                    artifact_schema_version=1,
                    artifact=json.loads(canonical_bytes),
                    artifact_canonical_bytes=canonical_bytes,
                    integrity_digest=verified_digest,
                )
            )
            if _fail_after == "case":
                raise RuntimeError("test failure after regression case insertion")
    except (
        MaterializationIneligible,
        RegressionIntegrityError,
        RegressionConflict,
        RuntimeError,
    ):
        raise
    except IntegrityError as error:
        if _constraint_name(error) in {
            "pk_regression_cases",
            "uq_regression_cases_source_analysis_id",
        }:
            raise RegressionConflict(
                "regression materialization identity conflicted"
            ) from None
        raise RegressionPersistenceError(
            "regression materialization integrity constraint failed"
        ) from None
    except SQLAlchemyError:
        raise RegressionPersistenceError(
            "regression materialization persistence failed"
        ) from None

    return MaterializedRegressionCase(
        regression_case_id=regression_case_id,
        source_analysis_id=source_analysis_id,
        artifact=artifact,
        canonical_bytes=canonical_bytes,
        integrity_digest=verified_digest,
        replayed=False,
    )


def _validate_source_eligibility(*, analysis, snapshot, source_run, rerun_source: bool) -> None:
    if rerun_source or source_run.run_role != "injected":
        raise MaterializationIneligible("SOURCE_NOT_ORIGINAL_INJECTED")
    if snapshot.manifest.run_id != source_run.run_id or source_run.evidence_open:
        raise MaterializationIneligible("SOURCE_EVIDENCE_NOT_FINALIZED")
    if analysis.evaluability.aggregate != "EVALUABLE":
        raise MaterializationIneligible(
            f"SOURCE_{analysis.evaluability.aggregate}"
        )
    if analysis.scenario_policy_result != "FAIL":
        raise MaterializationIneligible(
            f"SOURCE_POLICY_{analysis.scenario_policy_result}"
        )
    failed = [
        result
        for result in analysis.assertions or []
        if result.outcome == "FAIL"
    ]
    if not failed:
        raise MaterializationIneligible("SOURCE_HAS_NO_FAILED_ASSERTION")
    retry = next(
        (
            result
            for result in failed
            if result.assertion_id == "P1.RETRY_LIMIT"
        ),
        None,
    )
    if retry is None:
        raise MaterializationIneligible("SOURCE_RETRY_LIMIT_DID_NOT_FAIL")
    for ordinal in (0, 1):
        outcome, _ = timeout_chain(snapshot, ordinal)
        if outcome != "SATISFIED":
            raise MaterializationIneligible(
                f"SOURCE_TIMEOUT_{ordinal}_CHAIN_NOT_COMPLETE"
            )
    localization = analysis.localization
    if (
        localization is None
        or localization.assertion_id != "P1.RETRY_LIMIT"
        or localization.boundary != "retry_control"
        or localization.retry_ordinal != 2
    ):
        raise MaterializationIneligible("SOURCE_ORDINAL_2_LOCALIZATION_MISSING")
    if (
        source_run.fault_spec_id != FAULT_SPEC_V1_ID
        or source_run.fault_id is None
        or source_run.tested_input_digest is None
    ):
        raise MaterializationIneligible("SOURCE_PROVENANCE_INCOMPLETE")


def _ordered_unique_references(references):
    by_id = {reference.evidence_id: reference for reference in references}
    return sorted(by_id.values(), key=lambda reference: reference.receipt_seq)


def _constraint_name(error: IntegrityError) -> str | None:
    original = error.orig
    return getattr(original, "constraint_name", None) or getattr(
        getattr(original, "__cause__", None), "constraint_name", None
    )


def _verify_analysis_evidence_bindings(*, analysis, snapshot) -> None:
    """Prove every carried analysis reference is an exact manifest reference."""
    manifest_references = {
        reference.evidence_id: reference for reference in snapshot.references
    }
    references = [
        reference
        for assertion in analysis.assertions or []
        for reference in assertion.evidence_references
    ]
    if analysis.injection_boundary is not None:
        references.extend(analysis.injection_boundary.evidence_references)
    if analysis.localization is not None:
        references.extend(
            analysis.localization.supporting_evidence_references
        )
        references.extend(
            analysis.localization.downstream_symptom_references
        )
    references.extend(analysis.downstream_symptoms)
    if any(
        manifest_references.get(reference.evidence_id) != reference
        for reference in references
    ):
        raise RegressionIntegrityError(
            "analysis evidence reference failed manifest verification"
        )
    localization = analysis.localization
    if localization is None:
        return
    boundary_reference = manifest_references.get(
        localization.boundary_event_id
    )
    if (
        boundary_reference is None
        or boundary_reference.event_type != "boundary.tool_call.observed"
        or boundary_reference
        not in localization.supporting_evidence_references
    ):
        raise RegressionIntegrityError(
            "analysis localization boundary is not authoritative evidence"
        )
    ordinal_two = [
        reference
        for reference in localization.supporting_evidence_references
        if reference.event_type == "boundary.tool_call.ordinal_assigned"
        and snapshot.payload(reference).get("retry_ordinal") == 2
        and snapshot.payload(reference).get("arrival_event_id")
        == str(localization.boundary_event_id)
    ]
    retry = next(
        (
            assertion
            for assertion in analysis.assertions or []
            if assertion.assertion_id == "P1.RETRY_LIMIT"
        ),
        None,
    )
    if (
        len(ordinal_two) != 1
        or retry is None
        or boundary_reference not in retry.evidence_references
        or ordinal_two[0] not in retry.evidence_references
    ):
        raise RegressionIntegrityError(
            "analysis ordinal-2 localization failed evidence verification"
        )

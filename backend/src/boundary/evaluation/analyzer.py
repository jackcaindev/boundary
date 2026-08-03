"""Pure Task 6 analysis orchestration and immutable persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

import rfc8785
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.domain.evaluation import (
    ANALYZER_VERSION,
    ASSERTION_SET_VERSION,
    POLICY_VERSION,
    AnalysisDocumentV1,
    AnalysisIntegrityFailureV1,
)
from boundary.evaluation.assertions_v1 import evaluate_assertions
from boundary.evaluation.evaluability_v1 import evaluate_evaluability
from boundary.evaluation.localization_v1 import localize
from boundary.evaluation.policy_v1 import aggregate_policy
from boundary.evaluation.snapshot import (
    FinalizedSnapshotError,
    load_finalized_snapshot,
)
from boundary.persistence.tables import analyses, evidence_sets


class AnalysisError(Exception):
    """Safe base error for Task 6 analysis."""


class AnalysisInputError(AnalysisError):
    """The requested finalized evidence set cannot be analyzed."""


class AnalysisIntegrityError(AnalysisError):
    """The same analysis key produced different normalized content."""

    def __init__(self, prior_analysis_id: UUID, attempted_digest: str) -> None:
        self.prior_analysis_id = prior_analysis_id
        self.attempted_digest = attempted_digest
        super().__init__("analysis content conflicts with the immutable key")


class AnalysisPersistenceError(AnalysisError):
    """PostgreSQL could not commit the complete immutable analysis."""


@dataclass(frozen=True, slots=True)
class PersistedAnalysis:
    analysis_id: UUID
    evidence_set_id: UUID
    document: AnalysisDocumentV1
    canonical_bytes: bytes
    analysis_digest: str
    replayed: bool


def canonicalize_analysis(
    document: AnalysisDocumentV1,
) -> tuple[bytes, str]:
    validated = AnalysisDocumentV1.model_validate(
        document.model_dump(mode="python")
    )
    canonical_bytes = rfc8785.dumps(validated.model_dump(mode="json"))
    return canonical_bytes, sha256(canonical_bytes).hexdigest()


async def analyze_evidence_set(
    engine: AsyncEngine,
    *,
    evidence_set_id: UUID,
) -> PersistedAnalysis:
    """Analyze only the finalized manifest and its immutable references."""
    try:
        async with engine.connect() as connection:
            snapshot = await load_finalized_snapshot(
                connection,
                evidence_set_id=evidence_set_id,
            )
            if snapshot is None:
                raise AnalysisInputError("evidence set does not exist")
            control = None
            if snapshot.manifest.control_run_id is not None:
                control = await load_finalized_snapshot(
                    connection,
                    run_id=snapshot.manifest.control_run_id,
                )
    except FinalizedSnapshotError as error:
        raise AnalysisInputError(
            "finalized evidence failed integrity verification"
        ) from error

    document = build_analysis_document(snapshot, control)
    return await persist_analysis(
        engine,
        evidence_set_id=evidence_set_id,
        document=document,
    )


def build_analysis_document(snapshot, control) -> AnalysisDocumentV1:
    """Pure versioned transformation of finalized authoritative evidence."""
    evaluability = evaluate_evaluability(snapshot, control)
    if evaluability.aggregate == "EVALUABLE":
        assertions = evaluate_assertions(snapshot)
        injection_boundary, localization, symptoms = localize(snapshot)
    else:
        assertions = None
        injection_boundary = None
        localization = None
        symptoms = []
    policy_result = aggregate_policy(evaluability.aggregate, assertions)
    return AnalysisDocumentV1(
        schema_version=1,
        evidence_set_id=snapshot.manifest.evidence_set_id,
        evidence_set_digest=snapshot.evidence_set_digest,
        analyzer_version=ANALYZER_VERSION,
        assertion_set_version=ASSERTION_SET_VERSION,
        policy_version=POLICY_VERSION,
        evaluability=evaluability,
        assertions=assertions,
        injection_boundary=injection_boundary,
        localization=localization,
        downstream_symptoms=symptoms,
        scenario_policy_result=policy_result,
    )


async def persist_analysis(
    engine: AsyncEngine,
    *,
    evidence_set_id: UUID,
    document: AnalysisDocumentV1,
) -> PersistedAnalysis:
    """Insert, replay, or record a bounded evaluator-integrity failure."""
    canonical_bytes, digest = canonicalize_analysis(document)
    generated_id = uuid4()
    conflict: tuple[UUID, str] | None = None
    result: PersistedAnalysis | None = None
    try:
        async with engine.begin() as connection:
            evidence_set = (
                await connection.execute(
                    sa.select(evidence_sets)
                    .where(evidence_sets.c.evidence_set_id == evidence_set_id)
                    .with_for_update()
                )
            ).one_or_none()
            if evidence_set is None:
                raise AnalysisInputError("evidence set does not exist")
            if (
                document.evidence_set_id != evidence_set_id
                or document.evidence_set_digest
                != evidence_set.evidence_set_digest
                or document.analyzer_version != ANALYZER_VERSION
                or document.assertion_set_version != ASSERTION_SET_VERSION
                or document.policy_version != POLICY_VERSION
            ):
                raise AnalysisInputError(
                    "analysis key does not match the finalized evidence set"
                )
            existing = (
                await connection.execute(
                    sa.select(analyses).where(
                        analyses.c.record_kind == "authoritative",
                        analyses.c.evidence_set_digest
                        == document.evidence_set_digest,
                        analyses.c.analyzer_version == ANALYZER_VERSION,
                        analyses.c.assertion_set_version
                        == ASSERTION_SET_VERSION,
                        analyses.c.policy_version == POLICY_VERSION,
                    )
                )
            ).one_or_none()
            if existing is not None:
                if (
                    existing.evidence_set_id == evidence_set_id
                    and existing.analysis_canonical_bytes == canonical_bytes
                    and existing.analysis_digest == digest
                    and rfc8785.dumps(existing.analysis_document)
                    == canonical_bytes
                ):
                    result = PersistedAnalysis(
                        analysis_id=existing.analysis_id,
                        evidence_set_id=evidence_set_id,
                        document=document,
                        canonical_bytes=canonical_bytes,
                        analysis_digest=digest,
                        replayed=True,
                    )
                else:
                    failure = AnalysisIntegrityFailureV1(
                        schema_version=1,
                        record_kind="integrity_failure",
                        evidence_set_digest=document.evidence_set_digest,
                        analyzer_version=ANALYZER_VERSION,
                        assertion_set_version=ASSERTION_SET_VERSION,
                        policy_version=POLICY_VERSION,
                        prior_analysis_id=existing.analysis_id,
                        attempted_analysis_digest=digest,
                        reason_code="NONDETERMINISTIC_ANALYSIS_CONTENT",
                    )
                    failure_bytes = rfc8785.dumps(
                        failure.model_dump(mode="json")
                    )
                    failure_digest = sha256(failure_bytes).hexdigest()
                    prior_failure = (
                        await connection.execute(
                            sa.select(analyses.c.analysis_id).where(
                                analyses.c.record_kind
                                == "integrity_failure",
                                analyses.c.prior_analysis_id
                                == existing.analysis_id,
                                analyses.c.attempted_analysis_digest
                                == digest,
                            )
                        )
                    ).one_or_none()
                    if prior_failure is None:
                        await connection.execute(
                            analyses.insert().values(
                                analysis_id=uuid4(),
                                record_kind="integrity_failure",
                                evidence_set_id=evidence_set_id,
                                evidence_set_digest=(
                                    document.evidence_set_digest
                                ),
                                analyzer_version=ANALYZER_VERSION,
                                assertion_set_version=(
                                    ASSERTION_SET_VERSION
                                ),
                                policy_version=POLICY_VERSION,
                                evaluability_aggregate="EXECUTION_ERROR",
                                policy_result="EXECUTION_ERROR",
                                analysis_schema_version=1,
                                analysis_document=json.loads(failure_bytes),
                                analysis_canonical_bytes=failure_bytes,
                                analysis_digest=failure_digest,
                                prior_analysis_id=existing.analysis_id,
                                attempted_analysis_digest=digest,
                            )
                        )
                    conflict = (existing.analysis_id, digest)
            else:
                await connection.execute(
                    analyses.insert().values(
                        analysis_id=generated_id,
                        record_kind="authoritative",
                        evidence_set_id=evidence_set_id,
                        evidence_set_digest=document.evidence_set_digest,
                        analyzer_version=ANALYZER_VERSION,
                        assertion_set_version=ASSERTION_SET_VERSION,
                        policy_version=POLICY_VERSION,
                        evaluability_aggregate=(
                            document.evaluability.aggregate
                        ),
                        policy_result=document.scenario_policy_result,
                        analysis_schema_version=1,
                        analysis_document=json.loads(canonical_bytes),
                        analysis_canonical_bytes=canonical_bytes,
                        analysis_digest=digest,
                        prior_analysis_id=None,
                        attempted_analysis_digest=None,
                    )
                )
                result = PersistedAnalysis(
                    analysis_id=generated_id,
                    evidence_set_id=evidence_set_id,
                    document=document,
                    canonical_bytes=canonical_bytes,
                    analysis_digest=digest,
                    replayed=False,
                )
    except (AnalysisInputError, AnalysisIntegrityError):
        raise
    except (IntegrityError, SQLAlchemyError):
        raise AnalysisPersistenceError(
            "analysis persistence failed"
        ) from None

    if conflict is not None:
        raise AnalysisIntegrityError(*conflict)
    assert result is not None
    return result

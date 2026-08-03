"""Strict normalized Task 6 analysis document types."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from boundary.domain.evidence import Digest, EvidenceReference, Identity


EVALUABILITY_VERSION = "boundary.phase1.tool-timeout.evaluability/v1"
ASSERTION_SET_VERSION = "boundary.phase1.tool-timeout.assertions/v1"
ANALYZER_VERSION = "boundary.phase1.tool-timeout.analyzer/v1"
POLICY_VERSION = "boundary.phase1.tool-timeout.policy/v1"

EvaluabilityAggregate = Literal[
    "EVALUABLE", "INCOMPLETE", "INVALID", "EXECUTION_ERROR"
]
PolicyResult = Literal[
    "PASS", "FAIL", "INCOMPLETE", "INVALID", "EXECUTION_ERROR"
]
CheckOutcome = Literal[
    "SATISFIED", "INCOMPLETE", "INVALID", "EXECUTION_ERROR"
]
CheckIdentifier = Literal[
    "EVAL.CONTROL_VALID_SUCCESS",
    "EVAL.TIMEOUT_0_COMPLETE",
    "EVAL.TIMEOUT_1_COMPLETE",
    "EVAL.IDENTITY_VALID",
    "EVAL.EVIDENCE_FINALIZED_ORDERED",
    "EVAL.BOUNDARY_SYSTEMS_HEALTHY",
]
AssertionIdentifier = Literal[
    "P1.RETRY_LIMIT",
    "P1.DEGRADED_RESULT",
    "P1.RUN_WITHIN_BUDGET",
]


class ImmutableAnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EvaluabilityCheck(ImmutableAnalysisModel):
    check_id: CheckIdentifier
    outcome: CheckOutcome
    reason_code: Identity
    explanation: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    evidence_references: list[EvidenceReference]


class EvaluabilityResult(ImmutableAnalysisModel):
    check_set_version: Literal[
        "boundary.phase1.tool-timeout.evaluability/v1"
    ]
    checks: list[EvaluabilityCheck]
    aggregate: EvaluabilityAggregate


class AssertionResult(ImmutableAnalysisModel):
    assertion_id: AssertionIdentifier
    assertion_set_version: Literal[
        "boundary.phase1.tool-timeout.assertions/v1"
    ]
    outcome: Literal["PASS", "FAIL"]
    required_evidence_roles: list[Identity]
    expected_behavior: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    observed_behavior: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    evidence_references: list[EvidenceReference]


class InjectionBoundary(ImmutableAnalysisModel):
    boundary: Literal["tool_execution"]
    realized_timeout_ordinals: tuple[Literal[0], Literal[1]]
    evidence_references: list[EvidenceReference]


class LocalizationResult(ImmutableAnalysisModel):
    assertion_id: Literal["P1.RETRY_LIMIT"]
    boundary_event_id: UUID
    boundary: Literal["retry_control"]
    retry_ordinal: Literal[2]
    supporting_evidence_references: list[EvidenceReference]
    expected_behavior: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    observed_behavior: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    downstream_symptom_references: list[EvidenceReference]
    analyzer_version: Literal[
        "boundary.phase1.tool-timeout.analyzer/v1"
    ]


class AnalysisDocumentV1(ImmutableAnalysisModel):
    schema_version: Literal[1]
    evidence_set_id: UUID
    evidence_set_digest: Digest
    analyzer_version: Literal[
        "boundary.phase1.tool-timeout.analyzer/v1"
    ]
    assertion_set_version: Literal[
        "boundary.phase1.tool-timeout.assertions/v1"
    ]
    policy_version: Literal["boundary.phase1.tool-timeout.policy/v1"]
    evaluability: EvaluabilityResult
    assertions: list[AssertionResult] | None
    injection_boundary: InjectionBoundary | None
    localization: LocalizationResult | None
    downstream_symptoms: list[EvidenceReference]
    scenario_policy_result: PolicyResult


class AnalysisIntegrityFailureV1(ImmutableAnalysisModel):
    schema_version: Literal[1]
    record_kind: Literal["integrity_failure"]
    evidence_set_digest: Digest
    analyzer_version: Literal[
        "boundary.phase1.tool-timeout.analyzer/v1"
    ]
    assertion_set_version: Literal[
        "boundary.phase1.tool-timeout.assertions/v1"
    ]
    policy_version: Literal["boundary.phase1.tool-timeout.policy/v1"]
    prior_analysis_id: UUID
    attempted_analysis_digest: Digest
    reason_code: Literal["NONDETERMINISTIC_ANALYSIS_CONTENT"]

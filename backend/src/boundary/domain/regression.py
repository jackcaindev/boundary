"""Strict immutable Task 7 regression, invariance, and comparison documents."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.domain.evaluation import AssertionIdentifier
from boundary.domain.evidence import Digest, EvidenceReference, Identity


REGRESSION_ARTIFACT_SCHEMA_VERSION = 1
INVARIANCE_REPORT_SCHEMA_VERSION = 1
COMPARISON_SUMMARY_SCHEMA_VERSION = 1

RerunMode = Literal["reproduction", "version_comparison"]
InvarianceResult = Literal["MATCH", "PERMITTED_DIFFERENCE", "MISMATCH"]

InvariantFieldIdentifier = Literal[
    "regression_case_id",
    "contract_version",
    "scenario_id",
    "scenario_version",
    "tested_agent_id",
    "tested_agent_version",
    "tested_input",
    "tested_input_digest",
    "fault_spec_id",
    "fault_definition",
    "fault_definition_digest",
    "analyzer_version",
    "assertion_set_version",
    "policy_version",
    "campaign_id",
    "control_run_id",
    "control_trace_id",
    "control_capability_record_id",
    "control_evidence_set_id",
    "control_boundary_event_ids",
    "control_target_event_ids",
    "control_tool_call_ids",
    "candidate_run_id",
    "candidate_trace_id",
    "candidate_fault_id",
    "candidate_capability_record_id",
    "candidate_evidence_set_id",
    "candidate_boundary_event_ids",
    "candidate_target_event_ids",
    "candidate_tool_call_ids",
    "candidate_analysis_id",
]


class ImmutableRegressionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TestedInputV1(ImmutableRegressionModel):
    query: Annotated[StrictStr, Field(min_length=1, max_length=4096)]


class RegressionLocalizationV1(ImmutableRegressionModel):
    assertion_id: Literal["P1.RETRY_LIMIT"]
    boundary_event_id: UUID
    boundary: Literal["retry_control"]
    retry_ordinal: Literal[2]
    supporting_evidence_references: list[EvidenceReference]


class RegressionArtifactV1(ImmutableRegressionModel):
    artifact_schema_version: Literal[1]
    regression_case_id: UUID
    source_campaign_id: UUID
    source_run_id: UUID
    source_trace_id: UUID
    source_evidence_set_id: UUID
    source_evidence_set_digest: Digest
    source_analysis_id: UUID
    source_analysis_digest: Digest
    original_tested_agent_id: Identity
    original_tested_agent_version: Identity
    contract_version: Identity
    scenario_id: Identity
    scenario_version: Annotated[StrictInt, Field(gt=0)]
    tested_input: TestedInputV1
    tested_input_digest: Digest
    fault_spec_id: UUID
    fault_definition: Phase1FaultDefinition
    fault_definition_digest: Digest
    source_fault_id: UUID
    analyzer_version: Identity
    assertion_set_version: Identity
    policy_version: Identity
    failed_assertion_identifiers: tuple[AssertionIdentifier, ...]
    localization: RegressionLocalizationV1
    supporting_evidence_references: list[EvidenceReference]
    integrity_digest: Digest


class RerunDefinitionV1(ImmutableRegressionModel):
    schema_version: Literal[1]
    regression_case_id: UUID
    contract_version: Identity
    scenario_id: Identity
    scenario_version: Annotated[StrictInt, Field(gt=0)]
    tested_agent_id: Identity
    tested_agent_version: Identity
    tested_input: TestedInputV1
    tested_input_digest: Digest
    fault_spec_id: UUID
    fault_definition: Phase1FaultDefinition
    fault_definition_digest: Digest
    analyzer_version: Identity
    assertion_set_version: Identity
    policy_version: Identity


class InvarianceRowV1(ImmutableRegressionModel):
    field_identifier: InvariantFieldIdentifier
    source_value_or_digest: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    rerun_value_or_digest: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    comparison_rule: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    result: InvarianceResult
    authoritative_references: list[Identity]


class PreInvocationInvarianceReportV1(ImmutableRegressionModel):
    report_schema_version: Literal[1]
    report_phase: Literal["pre_invocation"]
    rerun_id: UUID
    regression_case_id: UUID
    mode: RerunMode
    rows: list[InvarianceRowV1]


class CompletedInvarianceReportV1(ImmutableRegressionModel):
    report_schema_version: Literal[1]
    report_phase: Literal["completed"]
    rerun_id: UUID
    regression_case_id: UUID
    mode: RerunMode
    rows: list[InvarianceRowV1]


class ComparisonSummaryV1(ImmutableRegressionModel):
    summary_schema_version: Literal[1]
    comparison_id: UUID
    regression_case_id: UUID
    rerun_id: UUID
    source_run_id: UUID
    source_evidence_set_id: UUID
    source_analysis_id: UUID
    candidate_run_id: UUID
    candidate_evidence_set_id: UUID
    candidate_analysis_id: UUID
    source_tested_agent_version: Identity
    candidate_tested_agent_version: Identity
    source_policy_result: Literal["FAIL"]
    candidate_policy_result: Literal["PASS"]
    completed_invariance_digest: Digest
    terminal_result: Literal["VALID"]
    reason_code: Literal["VULNERABLE_FAIL_FIXED_PASS"]
    scoped_conclusion: Literal[
        "The fixed tested-agent version passes this scenario policy."
    ]


class ComparisonFailureSummaryV1(ImmutableRegressionModel):
    summary_schema_version: Literal[1]
    comparison_id: UUID
    regression_case_id: UUID
    rerun_id: UUID
    source_run_id: UUID
    source_evidence_set_id: UUID
    source_analysis_id: UUID
    candidate_run_id: UUID | None
    candidate_evidence_set_id: UUID | None
    candidate_analysis_id: UUID | None
    source_tested_agent_version: Identity
    candidate_tested_agent_version: Identity
    source_policy_result: Literal["FAIL"]
    candidate_policy_result: Literal[
        "PASS", "FAIL", "INCOMPLETE", "INVALID", "EXECUTION_ERROR"
    ] | None
    completed_invariance_digest: Digest | None
    terminal_result: Literal["INELIGIBLE", "EXECUTION_ERROR"]
    reason_code: Identity

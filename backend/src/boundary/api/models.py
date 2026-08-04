"""Strict versioned public request and response contracts."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyMutationRequest(PublicModel):
    pass


class RerunRequest(PublicModel):
    mode: Literal["reproduction", "version_comparison"]
    tested_agent_version: Literal["vulnerable-v1", "fixed-v1"]


class ResourceLinks(PublicModel):
    campaign: str | None = None
    control_run: str | None = None
    injected_run: str | None = None
    regression_case: str | None = None
    comparison: str | None = None


class CampaignAccepted(PublicModel):
    api_version: Literal["v1"] = "v1"
    campaign_id: UUID
    control_run_id: UUID
    status: Literal["accepted"] = "accepted"
    status_url: str
    links: ResourceLinks
    replayed: bool


class CampaignView(PublicModel):
    api_version: Literal["v1"] = "v1"
    campaign_id: UUID
    campaign_kind: str
    operational_status: str
    current_step: str
    cancel_requested: bool
    cancellation_id: UUID | None
    terminal: bool
    failure_reason: str | None
    control_run_id: UUID | None
    injected_run_id: UUID | None
    regression_case_id: UUID | None
    rerun_id: UUID | None
    comparison_id: UUID | None
    links: ResourceLinks


class RunView(PublicModel):
    api_version: Literal["v1"] = "v1"
    run_id: UUID
    trace_id: UUID
    campaign_id: UUID
    run_role: str
    control_run_id: UUID | None
    expected_tested_agent_id: str
    expected_tested_agent_version: str
    reported_tested_agent_id: str | None
    reported_tested_agent_version: str | None
    operational_status: str
    policy_result: str | None
    contract_version: str
    scenario_id: str
    scenario_version: int
    fault_spec_id: UUID | None
    fault_id: UUID | None
    fault_definition_digest: str | None
    evidence_set_id: UUID | None
    evidence_set_digest: str | None
    finalizer_identity: str | None
    analysis_id: UUID | None
    analysis_digest: str | None
    analyzer_version: str | None
    assertion_set_version: str | None
    policy_version: str | None
    evaluability: dict[str, Any] | None
    assertions: list[dict[str, Any]]
    injection_boundary: dict[str, Any] | None
    first_unsafe_divergence: dict[str, Any] | None
    downstream_symptoms: list[dict[str, Any]]
    regression_case_id: UUID | None
    comparison_id: UUID | None
    links: ResourceLinks


class EvidenceItem(PublicModel):
    evidence_id: UUID
    authority: Literal["Boundary", "tested-agent"]
    source: Literal["boundary", "sut"]
    event_type: str
    boundary: str
    source_event_id: UUID
    producer_seq: int | None
    receipt_seq: int
    caused_by_event_id: UUID | None
    payload_schema_version: int
    payload_digest: str
    payload: dict[str, Any]


class EvidencePage(PublicModel):
    api_version: Literal["v1"] = "v1"
    run_id: UUID
    after_receipt_seq: int
    limit: int
    items: list[EvidenceItem]
    next_after_receipt_seq: int | None


class RegressionCaseView(PublicModel):
    api_version: Literal["v1"] = "v1"
    regression_case_id: UUID
    integrity_digest: str
    artifact: dict[str, Any]
    reruns: list[dict[str, Any]]
    comparisons: list[dict[str, Any]]


class MaterializationResult(PublicModel):
    api_version: Literal["v1"] = "v1"
    regression_case_id: UUID
    source_run_id: UUID
    status_url: str
    replayed: bool


class RerunAccepted(PublicModel):
    api_version: Literal["v1"] = "v1"
    rerun_id: UUID
    campaign_id: UUID
    control_run_id: UUID
    comparison_id: UUID | None
    status: Literal["accepted"] = "accepted"
    links: ResourceLinks
    replayed: bool


class ComparisonView(PublicModel):
    api_version: Literal["v1"] = "v1"
    comparison_id: UUID
    status: str
    terminal: bool
    regression_case_id: UUID
    rerun_id: UUID
    source_run_id: UUID
    candidate_run_id: UUID | None
    source_evidence_set_id: UUID
    candidate_evidence_set_id: UUID | None
    source_analysis_id: UUID
    candidate_analysis_id: UUID | None
    source_tested_agent_version: str
    candidate_tested_agent_version: str
    source_policy_result: str
    candidate_policy_result: str | None
    completed_invariance_rows: list[dict[str, Any]]
    permitted_differences: list[dict[str, Any]]
    mismatches: list[dict[str, Any]]
    summary_digest: str | None
    terminal_reason: str | None
    scoped_conclusion: str | None


class CancellationResult(PublicModel):
    api_version: Literal["v1"] = "v1"
    campaign_id: UUID
    cancellation_id: UUID | None
    cancel_requested: bool
    operational_status: str
    terminal: bool
    replayed: bool


class ProblemDetail(PublicModel):
    type: Literal["about:blank"] = "about:blank"
    title: StrictStr
    status: int
    code: StrictStr = Field(min_length=1, max_length=128)
    detail: StrictStr = Field(min_length=1, max_length=512)

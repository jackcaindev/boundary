"""Strict immutable Task 6 evidence-manifest document types."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Identity = Annotated[StrictStr, Field(min_length=1, max_length=256)]


class ImmutableEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EvidenceReference(ImmutableEvidenceModel):
    evidence_id: UUID
    source: Literal["boundary", "sut"]
    event_type: Identity
    boundary: Identity
    source_event_id: UUID
    producer_seq: Annotated[StrictInt, Field(gt=0)] | None
    receipt_seq: Annotated[StrictInt, Field(gt=0)]
    caused_by_event_id: UUID | None
    payload_schema_version: Annotated[StrictInt, Field(gt=0)]
    content_digest: Digest


class CutoffMarker(ImmutableEvidenceModel):
    marker_type: Literal["gap", "rejection", "failure", "deadline"]
    reason_code: Identity
    evidence_id: UUID | None = None
    audit_seq: Annotated[StrictInt, Field(gt=0)] | None = None
    content_digest: Digest | None = None
    missing_producer_seq_start: Annotated[StrictInt, Field(gt=0)] | None = None
    missing_producer_seq_end: Annotated[StrictInt, Field(gt=0)] | None = None


class CapabilityBinding(ImmutableEvidenceModel):
    capability_record_id: UUID
    trace_id: UUID
    tool_identity: Identity
    no_fault_binding: bool
    fault_id: UUID | None
    state: Literal["retired"]


class TimeoutEffectProof(ImmutableEvidenceModel):
    schema_version: Literal[1]
    activation_id: UUID
    run_id: UUID
    fault_id: UUID
    tool_call_id: UUID
    accepted_request_origin_ns: StrictInt
    activation_started_ns: StrictInt
    client_timeout_boundary_ns: StrictInt
    observed_monotonic_ns: StrictInt
    gate_closed: Literal[True]
    no_response_before_boundary: Literal[True]
    timing_authority_continuous: Literal[True]


class FinalizedTimeoutActivation(ImmutableEvidenceModel):
    activation_id: UUID
    run_id: UUID
    trace_id: UUID
    fault_id: UUID
    tool_call_id: UUID
    tool_identity: Identity
    activation_ordinal: Annotated[StrictInt, Field(ge=0, le=1)]
    arrival_event_id: UUID
    ordinal_event_id: UUID
    activation_event_id: UUID
    effect_event_id: UUID | None
    accepted_request_origin_ns: StrictInt
    activation_started_ns: StrictInt
    client_timeout_boundary_ns: StrictInt
    hold_deadline_ns: StrictInt
    effect_proof: TimeoutEffectProof | None
    effect_proof_digest: Digest | None
    response_gate_closed: bool
    no_response_before_boundary: bool | None
    timing_authority_continuous: bool | None
    reservation_state: Literal["effect_realized", "unproven", "runtime_lost"]
    effect_status: Literal["effect_realized", "unproven", "runtime_lost"]
    hold_disposition: Literal[
        "bounded_hold_complete", "proof_failed", "runtime_lost"
    ]
    runtime_completed_monotonic_ns: StrictInt | None
    hold_completion_relationship: Literal[
        "at_or_after_hold_deadline", "runtime_lost"
    ]


class EvidenceManifestV1(ImmutableEvidenceModel):
    schema_version: Literal[1]
    evidence_set_id: UUID
    run_id: UUID
    trace_id: UUID
    run_role: Literal["control", "injected"]
    contract_version: Identity
    scenario_id: Identity
    scenario_version: Annotated[StrictInt, Field(gt=0)]
    expected_tested_agent_id: Identity
    expected_tested_agent_version: Identity
    reported_tested_agent_id: Identity | None
    reported_tested_agent_version: Identity | None
    operational_status: Literal[
        "completed", "failed", "cancelled", "timed_out", "invalid"
    ]
    control_run_id: UUID | None
    fault_spec_id: UUID | None
    fault_spec_digest: Digest | None
    fault_id: UUID | None
    capability_binding: CapabilityBinding | None
    cutoff_reason: Literal["target_terminal_watermark", "evidence_deadline"]
    target_producer_cursor: Annotated[StrictInt, Field(ge=0)]
    target_final_watermark: Annotated[StrictInt, Field(ge=0)] | None
    accepted_evidence: list[EvidenceReference]
    timeout_activations: list[FinalizedTimeoutActivation]
    cutoff_markers: list[CutoffMarker]
    finalizer_identity: Literal["boundary.phase1.evidence-finalizer/v1"]

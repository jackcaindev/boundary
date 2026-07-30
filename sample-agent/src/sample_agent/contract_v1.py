"""Sample target's independent strict ADR 001 contract v1 models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypeAlias
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


CONTRACT_VERSION = "1"
CONTRACT_VERSIONS_HEADER = "Boundary-Contract-Versions"
CONTRACT_VERSION_HEADER = "Boundary-Contract-Version"
MAX_EVENT_BYTES = 64 * 1024
MAX_TARGET_EVENTS = 256
MAX_TARGET_EVENT_BYTES = 1024 * 1024
MAX_TERMINAL_OUTPUT_BYTES = 64 * 1024

BoundedIdentity = Annotated[StrictStr, Field(min_length=1, max_length=256)]
BoundedUrl = Annotated[StrictStr, Field(min_length=1, max_length=2048)]
PositiveBudget = Annotated[StrictInt, Field(gt=0, le=30_000)]
NonNegativeSequence = Annotated[StrictInt, Field(ge=0)]
PositiveSequence = Annotated[StrictInt, Field(gt=0)]


class StrictWireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
    )


class TestedInput(StrictWireModel):
    query: Annotated[StrictStr, Field(min_length=1, max_length=4096)]


class TestRunRequest(StrictWireModel):
    contract_version: Literal["1"]
    campaign_id: UUID
    scenario_id: BoundedIdentity
    scenario_version: Annotated[StrictInt, Field(gt=0)]
    run_id: UUID
    trace_id: UUID
    tested_agent_id: BoundedIdentity
    tested_agent_version: BoundedIdentity
    regression_case_id: UUID | None = None
    regression_mode: Literal["reproduction", "version_comparison"] | None = (
        None
    )
    tested_input: TestedInput
    execution_budget_ms: PositiveBudget
    tool_endpoint: BoundedUrl
    tool_capability: Annotated[
        StrictStr,
        Field(min_length=32, max_length=512, repr=False),
    ]
    fault_spec_id: UUID | None = None
    fault_id: UUID | None = None

    @field_validator("tool_endpoint")
    @classmethod
    def validate_tool_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("tool_endpoint must be an absolute HTTP URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("tool_endpoint must not contain credentials")
        return value

    @field_validator("tested_agent_version")
    @classmethod
    def reject_mutable_version_tag(cls, value: str) -> str:
        if value.lower() == "latest":
            raise ValueError("tested_agent_version must be immutable")
        return value

    @model_validator(mode="after")
    def validate_paired_context(self) -> TestRunRequest:
        if (self.regression_case_id is None) != (
            self.regression_mode is None
        ):
            raise ValueError(
                "regression_case_id and regression_mode must be paired"
            )
        if (self.fault_spec_id is None) != (self.fault_id is None):
            raise ValueError("fault_spec_id and fault_id must be paired")
        return self


class AcceptedResponse(StrictWireModel):
    contract_version: Literal["1"]
    run_id: UUID
    trace_id: UUID
    tested_agent_id: BoundedIdentity
    tested_agent_version: BoundedIdentity
    state: Literal["accepted"]
    status_url: BoundedUrl
    events_url: BoundedUrl
    cancellation_url: BoundedUrl
    producer_high_watermark: NonNegativeSequence


class StartedPayload(StrictWireModel):
    schema_version: Literal[1]


class RetryRequestedPayload(StrictWireModel):
    schema_version: Literal[1]
    retry_ordinal: Annotated[StrictInt, Field(gt=0)]
    prior_tool_call_id: UUID
    next_tool_call_id: UUID


class DegradedResultPayload(StrictWireModel):
    schema_version: Literal[1]
    result: Annotated[StrictStr, Field(min_length=1, max_length=65536)]


class CompletedPayload(StrictWireModel):
    schema_version: Literal[1]
    outcome_kind: Literal["success"]


class FailedPayload(StrictWireModel):
    schema_version: Literal[1]
    error_code: BoundedIdentity


class CancelledPayload(StrictWireModel):
    schema_version: Literal[1]
    cancellation_id: UUID


class EventBase(StrictWireModel):
    contract_version: Literal["1"]
    run_id: UUID
    trace_id: UUID
    event_id: UUID
    source: Literal["sut"] | None = None
    boundary: Literal["agent", "retry_control", "run"]
    producer_seq: PositiveSequence
    tool_call_id: UUID | None = None
    caused_by_event_id: UUID | None = None
    observed_at: datetime | None = None


class RunStartedEvent(EventBase):
    event_type: Literal["sut.run.started"]
    boundary: Literal["run"]
    payload: StartedPayload


class RetryRequestedEvent(EventBase):
    event_type: Literal["sut.retry.requested"]
    boundary: Literal["retry_control"]
    tool_call_id: UUID
    payload: RetryRequestedPayload


class DegradedResultEvent(EventBase):
    event_type: Literal["sut.degraded_result.produced"]
    boundary: Literal["agent"]
    payload: DegradedResultPayload


class RunCompletedEvent(EventBase):
    event_type: Literal["sut.run.completed"]
    boundary: Literal["run"]
    payload: CompletedPayload


class RunFailedEvent(EventBase):
    event_type: Literal["sut.run.failed"]
    boundary: Literal["run"]
    payload: FailedPayload


class RunCancelledEvent(EventBase):
    event_type: Literal["sut.run.cancelled"]
    boundary: Literal["run"]
    payload: CancelledPayload


EventEnvelope: TypeAlias = Annotated[
    RunStartedEvent
    | RetryRequestedEvent
    | DegradedResultEvent
    | RunCompletedEvent
    | RunFailedEvent
    | RunCancelledEvent,
    Field(discriminator="event_type"),
]


class EventPage(StrictWireModel):
    contract_version: Literal["1"]
    run_id: UUID
    trace_id: UUID
    events: Annotated[list[EventEnvelope], Field(max_length=64)]
    producer_high_watermark: NonNegativeSequence
    next_after_producer_seq: NonNegativeSequence


class TerminalResult(StrictWireModel):
    contract_version: Literal["1"]
    run_id: UUID
    trace_id: UUID
    tested_agent_id: BoundedIdentity
    tested_agent_version: BoundedIdentity
    state: Literal["completed", "failed", "cancelled"]
    final_producer_seq: NonNegativeSequence
    outcome_kind: Literal["success", "degraded", "error", "cancelled"]
    output: JsonValue
    event_id: UUID


class RunStatus(StrictWireModel):
    contract_version: Literal["1"]
    run_id: UUID
    trace_id: UUID
    tested_agent_id: BoundedIdentity
    tested_agent_version: BoundedIdentity
    state: Literal["accepted", "running", "completed", "failed", "cancelled"]
    producer_high_watermark: NonNegativeSequence
    final_producer_seq: NonNegativeSequence | None = None
    terminal_result: TerminalResult | None = None
    error_summary: Annotated[StrictStr, Field(max_length=512)] | None = None

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> RunStatus:
        terminal = self.state in {"completed", "failed", "cancelled"}
        if terminal != (self.final_producer_seq is not None):
            raise ValueError(
                "final_producer_seq is present exactly for terminal state"
            )
        if terminal != (self.terminal_result is not None):
            raise ValueError(
                "terminal_result is present exactly for terminal state"
            )
        if self.state == "failed" and self.error_summary is None:
            raise ValueError("failed state requires error_summary")
        if self.state != "failed" and self.error_summary is not None:
            raise ValueError("error_summary is allowed only for failed state")
        if self.terminal_result is not None:
            result = self.terminal_result
            if (
                result.run_id != self.run_id
                or result.trace_id != self.trace_id
                or result.tested_agent_id != self.tested_agent_id
                or result.tested_agent_version
                != self.tested_agent_version
                or result.state != self.state
                or result.final_producer_seq != self.final_producer_seq
            ):
                raise ValueError("terminal result conflicts with status")
        return self


class CancellationRequest(StrictWireModel):
    contract_version: Literal["1"]
    run_id: UUID
    trace_id: UUID
    cancellation_id: UUID


class CancellationAcknowledgement(StrictWireModel):
    contract_version: Literal["1"]
    run_id: UUID
    trace_id: UUID
    cancellation_id: UUID
    cancellation_applied: bool
    status: RunStatus

    @model_validator(mode="after")
    def validate_nested_status_identity(
        self,
    ) -> CancellationAcknowledgement:
        if (
            self.status.contract_version != self.contract_version
            or self.status.run_id != self.run_id
            or self.status.trace_id != self.trace_id
        ):
            raise ValueError(
                "cancellation status identity conflicts with acknowledgement"
            )
        return self


class ProblemError(StrictWireModel):
    code: Literal[
        "INVALID_REQUEST",
        "UNSUPPORTED_CONTRACT_VERSION",
        "IDENTITY_MISMATCH",
        "RUN_NOT_FOUND",
        "RUN_CONFLICT",
        "PAYLOAD_TOO_LARGE",
        "INVALID_EVENT",
        "NOT_CANCELLABLE",
        "INTERNAL_ERROR",
    ]
    message: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    retryable: bool
    field: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None = (
        None
    )
    supported_versions: list[Annotated[StrictStr, Field(max_length=32)]] | (
        None
    ) = None


class ProblemResponse(StrictWireModel):
    contract_version: Annotated[StrictStr, Field(max_length=32)] | None = None
    error: ProblemError
    run_id: UUID | None = None
    trace_id: UUID | None = None

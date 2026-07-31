"""Strict private contract for the one Phase 1 Boundary tool."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


CONTRACT_VERSION = "1"
TOOL_IDENTITY = "boundary.phase1.lookup"
MAX_TOOL_REQUEST_BYTES = 16 * 1024


class StrictToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class LookupArguments(StrictToolModel):
    query: Annotated[StrictStr, Field(min_length=1, max_length=4096)]


class LookupRequest(StrictToolModel):
    contract_version: Literal["1"]
    run_id: UUID
    trace_id: UUID
    tool_identity: Literal["boundary.phase1.lookup"]
    tool_call_id: UUID
    fault_id: UUID | None
    arguments: LookupArguments


class LookupResult(StrictToolModel):
    status: Literal["found"]
    value: Literal["control-ok"]


class LookupResponse(StrictToolModel):
    contract_version: Literal["1"]
    run_id: UUID
    trace_id: UUID
    tool_identity: Literal["boundary.phase1.lookup"]
    tool_call_id: UUID
    retry_ordinal: Annotated[StrictInt, Field(ge=0)]
    result: LookupResult


class ToolProblemDetail(StrictToolModel):
    code: Literal[
        "INVALID_TOOL_REQUEST",
        "MISSING_CAPABILITY",
        "INVALID_CAPABILITY",
        "CAPABILITY_INACTIVE",
        "CAPABILITY_IDENTITY_MISMATCH",
        "DUPLICATE_TOOL_CALL",
        "FAULT_EFFECT_NOT_IMPLEMENTED",
        "TOOL_REGISTRATION_FAILED",
    ]
    message: Annotated[StrictStr, Field(min_length=1, max_length=256)]


class ToolProblem(StrictToolModel):
    contract_version: Literal["1"]
    error: ToolProblemDetail

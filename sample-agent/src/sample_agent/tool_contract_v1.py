"""Sample agent's independent copy of the private Phase 1 tool contract."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


CONTRACT_VERSION = "1"
TOOL_IDENTITY = "boundary.phase1.lookup"


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

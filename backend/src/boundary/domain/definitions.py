"""The exact reviewed Phase 1 fault definition."""

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class Phase1FaultDefinition(BaseModel):
    """ADR 002's single immutable Phase 1 fault definition."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    fault_kind: Literal["tool_timeout"]
    target_tool: Literal["boundary.phase1.lookup"]
    trigger_rule: Literal["retry_ordinal_in"]
    affected_attempts: tuple[Literal[0], Literal[1]]
    tool_client_timeout_ms: Literal[500]
    injected_hold_ms: Literal[1000]
    maximum_activations: Literal[2]
    scenario_id: Literal["phase1.tool-timeout"]
    scenario_version: Literal[1]
    compatible_contract_versions: tuple[Literal["1"]]

    @model_validator(mode="before")
    @classmethod
    def require_exact_json_types_and_arrays(cls, value: Any) -> Any:
        """Reject coercion and freeze the two reviewed JSON arrays."""
        if not isinstance(value, Mapping):
            return value

        integer_fields = (
            "schema_version",
            "tool_client_timeout_ms",
            "injected_hold_ms",
            "maximum_activations",
            "scenario_version",
        )
        string_fields = (
            "fault_kind",
            "target_tool",
            "trigger_rule",
            "scenario_id",
        )

        for field_name in integer_fields:
            if field_name in value and type(value[field_name]) is not int:
                raise ValueError(f"{field_name} must be an integer")

        for field_name in string_fields:
            if field_name in value and type(value[field_name]) is not str:
                raise ValueError(f"{field_name} must be a string")

        normalized = dict(value)

        if "affected_attempts" in value:
            affected_attempts = value["affected_attempts"]
            if type(affected_attempts) is not list or any(
                type(attempt) is not int for attempt in affected_attempts
            ):
                raise ValueError("affected_attempts must be an array of integers")
            normalized["affected_attempts"] = tuple(affected_attempts)

        if "compatible_contract_versions" in value:
            contract_versions = value["compatible_contract_versions"]
            if type(contract_versions) is not list or any(
                type(version) is not str for version in contract_versions
            ):
                raise ValueError(
                    "compatible_contract_versions must be an array of strings"
                )
            normalized["compatible_contract_versions"] = tuple(contract_versions)

        return normalized

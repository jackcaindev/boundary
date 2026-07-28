import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from boundary.domain.definitions import Phase1FaultDefinition


FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"
)


@pytest.fixture
def reviewed_definition() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_reviewed_adr_002_object_validates(
    reviewed_definition: dict[str, object],
) -> None:
    definition = Phase1FaultDefinition.model_validate(reviewed_definition)

    assert definition.model_dump(mode="json") == reviewed_definition
    assert definition.model_fields_set == set(reviewed_definition)


@pytest.mark.parametrize(
    "missing_field",
    [
        "schema_version",
        "fault_kind",
        "target_tool",
        "trigger_rule",
        "affected_attempts",
        "tool_client_timeout_ms",
        "injected_hold_ms",
        "maximum_activations",
        "scenario_id",
        "scenario_version",
        "compatible_contract_versions",
    ],
)
def test_missing_fields_are_rejected(
    reviewed_definition: dict[str, object],
    missing_field: str,
) -> None:
    del reviewed_definition[missing_field]

    with pytest.raises(ValidationError):
        Phase1FaultDefinition.model_validate(reviewed_definition)


def test_unknown_fields_are_rejected(
    reviewed_definition: dict[str, object],
) -> None:
    reviewed_definition["unexpected"] = True

    with pytest.raises(ValidationError):
        Phase1FaultDefinition.model_validate(reviewed_definition)


@pytest.mark.parametrize(
    ("field_name", "incorrect_value"),
    [
        ("schema_version", "1"),
        ("fault_kind", b"tool_timeout"),
        ("target_tool", b"boundary.phase1.lookup"),
        ("trigger_rule", b"retry_ordinal_in"),
        ("affected_attempts", ("0", "1")),
        ("affected_attempts", ["0", 1]),
        ("tool_client_timeout_ms", "500"),
        ("injected_hold_ms", 1000.0),
        ("maximum_activations", 2.0),
        ("scenario_id", b"phase1.tool-timeout"),
        ("scenario_version", True),
        ("compatible_contract_versions", ("1",)),
        ("compatible_contract_versions", [1]),
    ],
)
def test_coercible_incorrect_types_are_rejected(
    reviewed_definition: dict[str, object],
    field_name: str,
    incorrect_value: object,
) -> None:
    reviewed_definition[field_name] = incorrect_value

    with pytest.raises(ValidationError):
        Phase1FaultDefinition.model_validate(reviewed_definition)


@pytest.mark.parametrize(
    ("field_name", "unsupported_value"),
    [
        ("schema_version", 2),
        ("fault_kind", "latency"),
        ("target_tool", "boundary.phase1.other"),
        ("trigger_rule", "always"),
        ("affected_attempts", [0, 2]),
        ("tool_client_timeout_ms", 501),
        ("injected_hold_ms", 999),
        ("maximum_activations", 1),
        ("scenario_id", "phase1.other"),
        ("scenario_version", 2),
        ("compatible_contract_versions", ["2"]),
    ],
)
def test_every_exact_value_field_rejects_unsupported_values(
    reviewed_definition: dict[str, object],
    field_name: str,
    unsupported_value: object,
) -> None:
    reviewed_definition[field_name] = unsupported_value

    with pytest.raises(ValidationError):
        Phase1FaultDefinition.model_validate(reviewed_definition)


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    [
        ("affected_attempts", [1, 0]),
        ("affected_attempts", [0]),
        ("affected_attempts", [0, 1, 2]),
        ("compatible_contract_versions", []),
        ("compatible_contract_versions", ["1", "2"]),
    ],
)
def test_changed_array_order_or_contents_are_rejected(
    reviewed_definition: dict[str, object],
    field_name: str,
    changed_value: list[object],
) -> None:
    reviewed_definition[field_name] = changed_value

    with pytest.raises(ValidationError):
        Phase1FaultDefinition.model_validate(reviewed_definition)

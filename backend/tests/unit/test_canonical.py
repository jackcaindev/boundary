import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.evidence.canonical import (
    FAULT_SPEC_V1_SHA256,
    canonicalize_fault_definition,
    fault_definition_digest,
)


FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"
)
EXPECTED_CANONICAL_BYTES = (
    b'{"affected_attempts":[0,1],'
    b'"compatible_contract_versions":["1"],'
    b'"fault_kind":"tool_timeout",'
    b'"injected_hold_ms":1000,'
    b'"maximum_activations":2,'
    b'"scenario_id":"phase1.tool-timeout",'
    b'"scenario_version":1,'
    b'"schema_version":1,'
    b'"target_tool":"boundary.phase1.lookup",'
    b'"tool_client_timeout_ms":500,'
    b'"trigger_rule":"retry_ordinal_in"}'
)


def load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_repeated_canonicalization_is_byte_identical() -> None:
    definition = Phase1FaultDefinition.model_validate(load_fixture())

    first = canonicalize_fault_definition(definition)
    second = canonicalize_fault_definition(definition)

    assert first == second


def test_input_key_order_does_not_change_bytes_or_digest() -> None:
    fixture = load_fixture()
    reordered = dict(reversed(list(fixture.items())))
    original_definition = Phase1FaultDefinition.model_validate(fixture)
    reordered_definition = Phase1FaultDefinition.model_validate(reordered)

    assert canonicalize_fault_definition(
        original_definition
    ) == canonicalize_fault_definition(reordered_definition)
    assert fault_definition_digest(
        original_definition
    ) == fault_definition_digest(reordered_definition)


def test_fixture_has_published_canonical_bytes_and_digest() -> None:
    definition = Phase1FaultDefinition.model_validate(load_fixture())
    canonical_bytes = canonicalize_fault_definition(definition)

    assert canonical_bytes == EXPECTED_CANONICAL_BYTES
    assert fault_definition_digest(definition) == FAULT_SPEC_V1_SHA256
    assert FAULT_SPEC_V1_SHA256 == (
        "13c5a1d3a7ebe65a9fc2a4c834a216c3"
        "2839239e77ee8d4e7f6aad711452e1ba"
    )


def test_digest_is_lowercase_hexadecimal_sha256() -> None:
    definition = Phase1FaultDefinition.model_validate(load_fixture())
    digest = fault_definition_digest(definition)

    assert len(digest) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_canonicalization_rejects_an_arbitrary_mapping() -> None:
    with pytest.raises(
        TypeError,
        match="validated Phase1FaultDefinition",
    ):
        canonicalize_fault_definition(load_fixture())  # type: ignore[arg-type]


def test_canonicalization_revalidates_bypass_constructed_models() -> None:
    invalid = Phase1FaultDefinition.model_construct(schema_version=2)

    with pytest.raises(ValidationError):
        canonicalize_fault_definition(invalid)

"""RFC 8785 canonicalization for the Phase 1 fault definition."""

from hashlib import sha256

import rfc8785

from boundary.domain.definitions import Phase1FaultDefinition


FAULT_SPEC_V1_SHA256 = (
    "13c5a1d3a7ebe65a9fc2a4c834a216c32839239e77ee8d4e7f6aad711452e1ba"
)


def canonicalize_fault_definition(
    definition: Phase1FaultDefinition,
) -> bytes:
    """Return RFC 8785 canonical UTF-8 bytes for a validated definition."""
    if type(definition) is not Phase1FaultDefinition:
        raise TypeError("definition must be a validated Phase1FaultDefinition")

    validated = Phase1FaultDefinition.model_validate(
        definition.model_dump(mode="json")
    )
    return rfc8785.dumps(validated.model_dump(mode="json"))


def fault_definition_digest(definition: Phase1FaultDefinition) -> str:
    """Return lowercase SHA-256 over the canonical UTF-8 bytes."""
    return sha256(canonicalize_fault_definition(definition)).hexdigest()

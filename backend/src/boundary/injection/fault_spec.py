"""Resolve and validate the one immutable Phase 1 fault definition."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any
from uuid import UUID

import rfc8785
from pydantic import ValidationError

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.evidence.canonical import FAULT_SPEC_V1_SHA256


FAULT_SPEC_V1_ID = UUID("8d3a76b8-87a2-5d89-9c2a-6cdd4b902b13")


class FaultDefinitionMismatch(ValueError):
    """Stored content is not the exact reviewed definition."""


def validate_phase1_fault_document(
    document: dict[str, Any],
    canonical_bytes: bytes,
    digest: str,
) -> Phase1FaultDefinition:
    """Validate exact schema, canonical bytes, and the published digest."""
    try:
        definition = Phase1FaultDefinition.model_validate(document)
    except ValidationError as error:
        raise FaultDefinitionMismatch("fault definition is invalid") from error
    expected_document = definition.model_dump(mode="json")
    expected_bytes = rfc8785.dumps(expected_document)
    if canonical_bytes != expected_bytes:
        raise FaultDefinitionMismatch("fault canonical bytes do not match")
    if json.loads(canonical_bytes) != expected_document:
        raise FaultDefinitionMismatch("fault canonical content does not match")
    if sha256(canonical_bytes).hexdigest() != digest:
        raise FaultDefinitionMismatch("fault digest does not match its bytes")
    if digest != FAULT_SPEC_V1_SHA256:
        raise FaultDefinitionMismatch("fault digest is not the reviewed digest")
    return definition

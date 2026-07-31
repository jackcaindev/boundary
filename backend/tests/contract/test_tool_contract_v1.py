from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from boundary.injection.contract_v1 import LookupRequest


def _document() -> dict[str, object]:
    return {
        "contract_version": "1",
        "run_id": uuid4(),
        "trace_id": uuid4(),
        "tool_identity": "boundary.phase1.lookup",
        "tool_call_id": uuid4(),
        "fault_id": None,
        "arguments": {"query": "control"},
    }


@pytest.mark.parametrize(
    "change",
    [
        {"unknown": True},
        {"tool_identity": "boundary.phase1.other"},
        {"run_id": "not-a-uuid"},
    ],
)
def test_lookup_request_rejects_unknown_or_malformed_identity(
    change: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        LookupRequest.model_validate({**_document(), **change})


def test_fault_presence_is_explicit_even_for_control() -> None:
    document = _document()
    del document["fault_id"]
    with pytest.raises(ValidationError):
        LookupRequest.model_validate(document)

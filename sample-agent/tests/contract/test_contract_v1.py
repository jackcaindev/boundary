from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sample_agent.contract_v1 import (
    CancellationAcknowledgement,
    EventPage,
    RunStatus,
)


FIXTURES = Path(__file__).parents[3] / "contract-fixtures" / "v1"


def test_independent_target_models_accept_shared_fixtures() -> None:
    page = EventPage.model_validate_json(
        (FIXTURES / "control-terminal-events.json").read_bytes()
    )
    status = RunStatus.model_validate_json(
        (FIXTURES / "control-terminal-status.json").read_bytes()
    )

    assert page.next_after_producer_seq == 2
    assert status.state == "completed"


def test_target_models_reject_unknown_fields() -> None:
    document = json.loads(
        (FIXTURES / "control-terminal-events.json").read_bytes()
    )
    document["receipt_seq"] = 1

    with pytest.raises(ValidationError):
        EventPage.model_validate_json(json.dumps(document))


def test_target_cancellation_ack_rejects_nested_identity_mismatch() -> None:
    status = json.loads(
        (FIXTURES / "control-terminal-status.json").read_bytes()
    )
    acknowledgement = {
        "contract_version": "1",
        "run_id": status["run_id"],
        "trace_id": status["trace_id"],
        "cancellation_id": "33333333-3333-4333-8333-333333333333",
        "cancellation_applied": True,
        "status": {
            **status,
            "trace_id": "44444444-4444-4444-8444-444444444444",
            "terminal_result": {
                **status["terminal_result"],
                "trace_id": "44444444-4444-4444-8444-444444444444",
            },
        },
    }

    with pytest.raises(ValidationError):
        CancellationAcknowledgement.model_validate(acknowledgement)

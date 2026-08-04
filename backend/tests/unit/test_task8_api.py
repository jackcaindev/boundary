from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from boundary.api.models import RunView
from boundary.config import BoundarySettings, ConfigurationError
from boundary.main import create_app


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    app = create_app(executor_enabled=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://boundary.test",
    ) as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
async def test_public_mutation_requires_idempotency_key() -> None:
    response = await _request(
        "POST", "/api/v1/campaigns/bundled-tool-timeout", json={}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_unknown_public_body_field_is_bounded_and_redacted() -> None:
    secret = "Bearer capability-that-must-not-leak"
    response = await _request(
        "POST",
        "/api/v1/campaigns/bundled-tool-timeout",
        headers={"Idempotency-Key": "strict-body"},
        json={"authorization": secret},
    )

    assert response.status_code == 422
    serialized = response.text
    assert response.json()["code"] == "INVALID_REQUEST"
    assert secret not in serialized
    assert "traceback" not in serialized.lower()


@pytest.mark.asyncio
async def test_public_mutation_is_unavailable_before_readiness() -> None:
    response = await _request(
        "POST",
        "/api/v1/campaigns/bundled-tool-timeout",
        headers={"Idempotency-Key": "not-ready"},
        json={},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_NOT_READY"


@pytest.mark.asyncio
async def test_invalid_evidence_cursor_and_limit_are_bounded() -> None:
    response = await _request(
        "GET",
        "/api/v1/runs/00000000-0000-0000-0000-000000000001/evidence"
        "?after_receipt_seq=-1&limit=101",
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_REQUEST"


def test_run_view_strictly_serializes_finalizer_identity() -> None:
    schema = RunView.model_json_schema()

    assert schema["additionalProperties"] is False
    assert "finalizer_identity" in schema["required"]

    view = RunView.model_construct(
        finalizer_identity="boundary.phase1.evidence-finalizer/v1"
    )
    assert view.model_dump()["finalizer_identity"] == (
        "boundary.phase1.evidence-finalizer/v1"
    )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RunView.model_validate({"unexpected": "field"})


def test_task8_timing_configuration_must_match_immutable_definition() -> None:
    with pytest.raises(ConfigurationError, match="RUN_DEADLINE_MS"):
        BoundarySettings.from_environment(
            {
                "SUT_BASE_URL": "http://sample-agent:8001",
                "BOUNDARY_INTERNAL_BASE_URL": "http://boundary:8000",
                "RUN_DEADLINE_MS": "29999",
            }
        )

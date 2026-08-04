"""Phase 1 public API routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.api.errors import PublicProblem
from boundary.api.models import (
    CampaignAccepted,
    CampaignView,
    CancellationResult,
    ComparisonView,
    EmptyMutationRequest,
    EvidencePage,
    MaterializationResult,
    RegressionCaseView,
    RerunAccepted,
    RerunRequest,
    RunView,
)
from boundary.api.mutations import (
    accept_bundled_campaign,
    accept_rerun,
    cancel_campaign,
    ensure_regression_case,
)
from boundary.api.reads import (
    read_campaign,
    read_comparison,
    read_evidence,
    read_regression_case,
    read_run,
)


router = APIRouter(prefix="/api/v1")
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        pattern=r".*\S.*",
    ),
]


def _engine(request: Request) -> AsyncEngine:
    engine = request.app.state.database_engine
    if engine is None:
        raise PublicProblem(503, "SERVICE_NOT_READY", "database is not ready")
    return engine


def _require_ready(request: Request) -> None:
    executor = request.app.state.executor
    if (
        not request.app.state.ready
        or executor is None
        or not executor.running
    ):
        raise PublicProblem(503, "SERVICE_NOT_READY", "startup reconciliation is incomplete")


def _notify(request: Request) -> None:
    executor = request.app.state.executor
    if executor is not None:
        executor.wake()


@router.post("/campaigns/bundled-tool-timeout")
async def start_bundled_campaign(
    request: Request,
    idempotency_key: IdempotencyKey,
    body: EmptyMutationRequest | None = Body(default=None),
) -> JSONResponse:
    del body
    _require_ready(request)
    result = await accept_bundled_campaign(_engine(request), idempotency_key)
    _notify(request)
    response = CampaignAccepted(
        campaign_id=result.campaign_id,
        control_run_id=result.run_id,
        status_url=f"/api/v1/campaigns/{result.campaign_id}",
        links={
            "campaign": f"/api/v1/campaigns/{result.campaign_id}",
            "control_run": f"/api/v1/runs/{result.run_id}",
        },
        replayed=result.replayed,
    )
    return JSONResponse(
        status_code=200 if result.replayed else 202,
        content=response.model_dump(mode="json"),
    )


@router.get("/campaigns/{campaign_id}", response_model=CampaignView)
async def get_campaign(request: Request, campaign_id: UUID) -> CampaignView:
    return await read_campaign(_engine(request), campaign_id)


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(request: Request, run_id: UUID) -> RunView:
    return await read_run(_engine(request), run_id)


@router.get("/runs/{run_id}/evidence", response_model=EvidencePage)
async def get_evidence(
    request: Request,
    run_id: UUID,
    after_receipt_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> EvidencePage:
    return await read_evidence(
        _engine(request), run_id, after=after_receipt_seq, limit=limit
    )


@router.post("/runs/{run_id}/regression-case")
async def post_regression_case(
    request: Request,
    run_id: UUID,
    idempotency_key: IdempotencyKey,
    body: EmptyMutationRequest | None = Body(default=None),
) -> JSONResponse:
    del body
    _require_ready(request)
    case_id, source_run_id, replayed = await ensure_regression_case(
        _engine(request), run_id=run_id, key=idempotency_key
    )
    response = MaterializationResult(
        regression_case_id=case_id,
        source_run_id=source_run_id,
        status_url=f"/api/v1/regression-cases/{case_id}",
        replayed=replayed,
    )
    return JSONResponse(
        status_code=200 if replayed else 201,
        content=response.model_dump(mode="json"),
    )


@router.get(
    "/regression-cases/{regression_case_id}",
    response_model=RegressionCaseView,
)
async def get_regression_case(
    request: Request, regression_case_id: UUID
) -> RegressionCaseView:
    return await read_regression_case(_engine(request), regression_case_id)


@router.post("/regression-cases/{regression_case_id}/reruns")
async def post_rerun(
    request: Request,
    regression_case_id: UUID,
    body: RerunRequest,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    _require_ready(request)
    accepted = await accept_rerun(
        _engine(request),
        case_id=regression_case_id,
        mode=body.mode,
        tested_agent_version=body.tested_agent_version,
        key=idempotency_key,
    )
    assert accepted.campaign_id is not None
    assert accepted.control_run_id is not None
    _notify(request)
    response = RerunAccepted(
        rerun_id=accepted.rerun_id,
        campaign_id=accepted.campaign_id,
        control_run_id=accepted.control_run_id,
        comparison_id=accepted.comparison_id,
        links={
            "campaign": f"/api/v1/campaigns/{accepted.campaign_id}",
            "control_run": f"/api/v1/runs/{accepted.control_run_id}",
            "regression_case": f"/api/v1/regression-cases/{regression_case_id}",
            "comparison": (
                f"/api/v1/comparisons/{accepted.comparison_id}"
                if accepted.comparison_id
                else None
            ),
        },
        replayed=accepted.replayed,
    )
    return JSONResponse(
        status_code=200 if accepted.replayed else 202,
        content=response.model_dump(mode="json"),
    )


@router.get("/comparisons/{comparison_id}", response_model=ComparisonView)
async def get_comparison(
    request: Request, comparison_id: UUID
) -> ComparisonView:
    return await read_comparison(_engine(request), comparison_id)


@router.post("/campaigns/{campaign_id}/cancel")
async def post_cancel(
    request: Request,
    campaign_id: UUID,
    idempotency_key: IdempotencyKey,
    body: EmptyMutationRequest | None = Body(default=None),
) -> JSONResponse:
    del body
    _require_ready(request)
    campaign, replayed = await cancel_campaign(
        _engine(request), campaign_id=campaign_id, key=idempotency_key
    )
    _notify(request)
    terminal = campaign.status in {"completed", "failed", "cancelled"}
    response = CancellationResult(
        campaign_id=campaign.campaign_id,
        cancellation_id=campaign.cancellation_id,
        cancel_requested=campaign.cancel_requested,
        operational_status=campaign.status,
        terminal=terminal,
        replayed=replayed,
    )
    return JSONResponse(
        status_code=200 if replayed or terminal else 202,
        content=response.model_dump(mode="json"),
    )

"""FastAPI assembly for the separate sample agent and real tool client."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
import os
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from sample_agent.contract_v1 import (
    CONTRACT_VERSION,
    CONTRACT_VERSION_HEADER,
    CONTRACT_VERSIONS_HEADER,
    CancellationRequest,
    ProblemError,
    ProblemResponse,
    TestRunRequest,
)
from sample_agent.run_store import (
    RunConflict,
    RunNotFound,
    RunStore,
    StoreCapacityExceeded,
)
from sample_agent.versions.fixed import FIXED_VERSION
from sample_agent.versions.vulnerable import VULNERABLE_VERSION


MAX_REQUEST_BYTES = 128 * 1024


def create_app(
    *,
    store: RunStore | None = None,
    auto_execute: bool = True,
) -> FastAPI:
    app = FastAPI(title="Boundary sample agent", version="1")
    app.state.run_store = store or RunStore()
    app.state.background_tasks = set()

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        del request, error
        return _problem(
            422,
            code="INVALID_REQUEST",
            message="request path or query is invalid",
        )

    @app.exception_handler(Exception)
    async def internal_error(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        del request, error
        return _problem(
            500,
            code="INTERNAL_ERROR",
            message="target service could not complete the request",
        )

    def spawn(coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        app.state.background_tasks.add(task)
        task.add_done_callback(app.state.background_tasks.discard)

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "contract_versions": [CONTRACT_VERSION],
            "tested_agent_versions": [VULNERABLE_VERSION, FIXED_VERSION],
            "model_mode": "fake",
        }

    @app.post("/test-runs")
    async def create_test_run(request: Request):
        offered = request.headers.get(CONTRACT_VERSIONS_HEADER, "")
        if offered.split(",")[0].strip() != CONTRACT_VERSION:
            return _problem(
                406,
                code="UNSUPPORTED_CONTRACT_VERSION",
                message="contract version is not supported",
                supported_versions=[CONTRACT_VERSION],
            )
        raw = await request.body()
        if len(raw) > MAX_REQUEST_BYTES:
            return _problem(
                413,
                code="PAYLOAD_TOO_LARGE",
                message="test-run request is too large",
            )
        try:
            parsed = TestRunRequest.model_validate_json(raw)
        except ValidationError:
            return _problem(
                422,
                code="INVALID_REQUEST",
                message="test-run request is invalid",
            )
        if (
            parsed.tested_agent_id != "boundary.sample-agent"
            or parsed.tested_agent_version
            not in {VULNERABLE_VERSION, FIXED_VERSION}
        ):
            return _problem(
                409,
                code="IDENTITY_MISMATCH",
                message="tested-agent identity does not match this service",
                run_id=parsed.run_id,
                trace_id=parsed.trace_id,
            )
        try:
            accepted, _ = await app.state.run_store.create(parsed)
        except RunConflict:
            return _problem(
                409,
                code="RUN_CONFLICT",
                message="run_id was reused with different content",
                run_id=parsed.run_id,
                trace_id=parsed.trace_id,
            )
        except StoreCapacityExceeded:
            return _problem(
                500,
                code="INTERNAL_ERROR",
                message="target run capacity is exhausted",
                retryable=False,
            )
        if auto_execute:
            spawn(app.state.run_store.execute(parsed.run_id))
        return JSONResponse(
            status_code=202,
            content=accepted.model_dump(mode="json"),
            headers={CONTRACT_VERSION_HEADER: CONTRACT_VERSION},
        )

    @app.get("/test-runs/{run_id}")
    async def get_status(run_id: UUID):
        try:
            status = await app.state.run_store.status(run_id)
        except RunNotFound:
            return _problem(
                404,
                code="RUN_NOT_FOUND",
                message="run was not found",
            )
        return JSONResponse(
            content=status.model_dump(mode="json"),
            headers={CONTRACT_VERSION_HEADER: CONTRACT_VERSION},
        )

    @app.get("/test-runs/{run_id}/events")
    async def get_events(
        run_id: UUID,
        after_producer_seq: int = Query(ge=0),
    ):
        try:
            page = await app.state.run_store.events(
                run_id,
                after_producer_seq,
            )
        except RunNotFound:
            return _problem(
                404,
                code="RUN_NOT_FOUND",
                message="run was not found",
            )
        return JSONResponse(
            content=page.model_dump(mode="json"),
            headers={CONTRACT_VERSION_HEADER: CONTRACT_VERSION},
        )

    @app.post("/test-runs/{run_id}/cancel")
    async def cancel_run(run_id: UUID, request: Request):
        raw = await request.body()
        try:
            parsed = CancellationRequest.model_validate_json(raw)
        except ValidationError:
            return _problem(
                422,
                code="INVALID_REQUEST",
                message="cancellation request is invalid",
            )
        try:
            acknowledgement = await app.state.run_store.cancel(
                run_id,
                parsed,
            )
        except RunNotFound:
            return _problem(
                404,
                code="RUN_NOT_FOUND",
                message="run was not found",
            )
        except ValueError:
            return _problem(
                409,
                code="IDENTITY_MISMATCH",
                message="cancellation identity does not match the run",
                run_id=run_id,
            )
        return JSONResponse(
            content=acknowledgement.model_dump(mode="json"),
            headers={CONTRACT_VERSION_HEADER: CONTRACT_VERSION},
        )

    return app


def _problem(
    status_code: int,
    *,
    code: str,
    message: str,
    retryable: bool = False,
    supported_versions: list[str] | None = None,
    run_id: UUID | None = None,
    trace_id: UUID | None = None,
) -> JSONResponse:
    problem = ProblemResponse(
        contract_version=CONTRACT_VERSION,
        error=ProblemError(
            code=code,
            message=message,
            retryable=retryable,
            supported_versions=supported_versions,
        ),
        run_id=run_id,
        trace_id=trace_id,
    )
    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(mode="json", exclude_none=True),
        headers={CONTRACT_VERSION_HEADER: CONTRACT_VERSION},
    )


def _configured_store() -> RunStore:
    raw_delay = os.environ.get("SAMPLE_AGENT_START_DELAY_MS", "0")
    try:
        start_delay_ms = int(raw_delay)
    except ValueError:
        start_delay_ms = -1
    return RunStore(start_delay_ms=start_delay_ms)


app = create_app(store=_configured_store())

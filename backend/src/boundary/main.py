"""Minimal Boundary service assembly for the private Task 4 tool route."""

from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.injection.contract_v1 import (
    CONTRACT_VERSION,
    MAX_TOOL_REQUEST_BYTES,
    LookupRequest,
    ToolProblem,
    ToolProblemDetail,
)
from boundary.injection.tool_stub import (
    DuplicateRegistration,
    ToolRegistrationError,
    register_tool_call,
)
from boundary.persistence.database import (
    DatabaseSettings,
    create_database_engine,
)


def create_app(*, engine: AsyncEngine | None = None) -> FastAPI:
    owns_engine = engine is None

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if application.state.database_engine is None:
            application.state.database_engine = create_database_engine(
                DatabaseSettings.from_environment()
            )
        try:
            yield
        finally:
            if owns_engine:
                await application.state.database_engine.dispose()

    application = FastAPI(
        title="Boundary internal service",
        version="1",
        lifespan=lifespan,
    )
    application.state.database_engine = engine

    @application.get("/health")
    async def health() -> dict[str, str]:
        database_engine = application.state.database_engine
        if database_engine is None:
            return {"status": "starting"}
        async with database_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    @application.post(
        "/internal/v1/runs/{route_run_id}/tools/phase1-lookup"
    )
    async def phase1_lookup(
        route_run_id: UUID,
        request: Request,
    ) -> JSONResponse:
        authorization = request.headers.get("authorization")
        if authorization is None:
            return _problem(
                401,
                "MISSING_CAPABILITY",
                "a bearer capability is required",
            )
        scheme, separator, secret = authorization.partition(" ")
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or not secret
            or len(secret) > 512
        ):
            return _problem(
                401,
                "INVALID_CAPABILITY",
                "the bearer capability is invalid",
            )

        raw_body = await request.body()
        if len(raw_body) > MAX_TOOL_REQUEST_BYTES:
            return _problem(
                422,
                "INVALID_TOOL_REQUEST",
                "the tool request is invalid",
            )
        try:
            parsed = LookupRequest.model_validate_json(raw_body)
        except ValidationError:
            return _problem(
                422,
                "INVALID_TOOL_REQUEST",
                "the tool request is invalid",
            )

        database_engine = application.state.database_engine
        if database_engine is None:
            return _problem(
                500,
                "TOOL_REGISTRATION_FAILED",
                "the tool call could not be registered",
            )
        try:
            registered = await register_tool_call(
                database_engine,
                route_run_id=route_run_id,
                capability_secret=secret,
                request=parsed,
            )
        except ToolRegistrationError as error:
            return _problem(
                error.http_status,
                error.code,
                str(error),
            )

        if isinstance(registered, DuplicateRegistration):
            return _problem(
                409,
                "DUPLICATE_TOOL_CALL",
                "the tool-call identity was already registered",
            )
        if registered.outcome != "no_fault_configured":
            return _problem(
                501,
                "FAULT_EFFECT_NOT_IMPLEMENTED",
                "fault effect handling is deferred",
            )

        assert registered.response is not None
        return JSONResponse(
            content=registered.response.model_dump(mode="json")
        )

    return application


def _problem(
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    problem = ToolProblem(
        contract_version=CONTRACT_VERSION,
        error=ToolProblemDetail(code=code, message=message),
    )
    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(mode="json"),
        headers={"WWW-Authenticate": "Bearer"}
        if status_code == 401
        else None,
    )


app = create_app()

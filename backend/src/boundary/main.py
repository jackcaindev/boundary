"""Minimal Boundary service assembly for the private Task 4 tool route."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.api.errors import PublicProblem, problem_response
from boundary.api.routes import router as public_router
from boundary.config import BoundarySettings, ConfigurationError
from boundary.executor import SerialExecutor

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
from boundary.injection.timeout import (
    ActivationRuntime,
    ActivationRuntimeRegistry,
)
from boundary.persistence.database import (
    DatabaseSettings,
    create_database_engine,
)


def create_app(
    *,
    engine: AsyncEngine | None = None,
    settings: BoundarySettings | None = None,
    executor_enabled: bool = True,
) -> FastAPI:
    owns_engine = engine is None

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        executor = None
        try:
            try:
                selected_settings = (
                    settings or BoundarySettings.from_environment()
                )
                application.state.settings = selected_settings
                if application.state.database_engine is None:
                    application.state.database_engine = create_database_engine(
                        DatabaseSettings.from_environment()
                    )
                async with application.state.database_engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                    revision = await connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                if revision != "0007_executor_public_api":
                    raise ConfigurationError(
                        "database migration is not at Task 8 head"
                    )
                if executor_enabled:
                    executor = SerialExecutor(
                        application.state.database_engine, selected_settings
                    )
                    application.state.executor = executor
                    await executor.start()
                    if not executor.running:
                        raise ConfigurationError("executor loop did not start")
                application.state.reconciliation_complete = True
                application.state.ready = True
            except Exception:
                application.state.startup_error = "STARTUP_NOT_READY"
            yield
        finally:
            application.state.ready = False
            if executor is not None:
                shutdown_timeout = 8.0
                if application.state.settings is not None:
                    shutdown_timeout = (
                        application.state.settings.cancellation_grace_ms
                        / 1000
                        + 6.0
                    )
                await executor.stop(timeout_seconds=shutdown_timeout)
            await application.state.activation_registry.wait_all()
            if owns_engine:
                database_engine = application.state.database_engine
                if database_engine is not None:
                    await database_engine.dispose()

    application = FastAPI(
        title="Boundary internal service",
        version="1",
        lifespan=lifespan,
    )
    application.state.database_engine = engine
    application.state.activation_registry = ActivationRuntimeRegistry()
    application.state.executor = None
    application.state.settings = settings
    application.state.ready = False
    application.state.reconciliation_complete = False
    application.state.startup_error = None

    @application.exception_handler(PublicProblem)
    async def public_problem_handler(
        request: Request, error: PublicProblem
    ) -> JSONResponse:
        del request
        return problem_response(error.status, error.code, error.detail)

    @application.exception_handler(RequestValidationError)
    async def validation_problem_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        del request, error
        return problem_response(
            422,
            "INVALID_REQUEST",
            "request structure or fields are invalid",
        )

    @application.exception_handler(Exception)
    async def unexpected_problem_handler(
        request: Request, error: Exception
    ) -> JSONResponse:
        del request, error
        return problem_response(
            500,
            "BOUNDARY_INTERNAL_ERROR",
            "Boundary could not complete the request",
        )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "live"}

    @application.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "live"}

    @application.get("/health/ready")
    async def readiness() -> JSONResponse:
        database_engine = application.state.database_engine
        executor = application.state.executor
        ready = (
            application.state.ready
            and application.state.reconciliation_complete
            and database_engine is not None
            and (not executor_enabled or (executor is not None and executor.running))
        )
        if ready:
            try:
                async with database_engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
            except Exception:
                ready = False
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready"},
        )

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
        activation_runtime = (
            ActivationRuntime.create(
                run_id=route_run_id,
                trace_id=parsed.trace_id,
                fault_id=parsed.fault_id,
                tool_call_id=parsed.tool_call_id,
            )
            if parsed.fault_id is not None
            else None
        )
        try:
            registered = await register_tool_call(
                database_engine,
                route_run_id=route_run_id,
                capability_secret=secret,
                request=parsed,
                activation_runtime=activation_runtime,
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
        if registered.activation_evidence_id is not None:
            assert activation_runtime is not None
            task = application.state.activation_registry.start(
                database_engine,
                activation_runtime,
            )
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                raise
            return _problem(
                504,
                "INJECTED_TIMEOUT",
                "the deterministic injected hold completed",
            )

        assert registered.response is not None
        if activation_runtime is not None:
            activation_runtime.gate.send_success(
                activation_runtime.clock.monotonic_ns()
            )
        return JSONResponse(
            content=registered.response.model_dump(mode="json")
        )

    application.include_router(public_router)
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

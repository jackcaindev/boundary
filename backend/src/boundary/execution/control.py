"""One bounded headless ADR 001 control-run execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.evidence.collector import (
    EvidenceInvalid,
    EvidenceLimitExceeded,
    ForwardGap,
    IdentityMismatch,
    collect_target_page,
    observe_terminal_watermark,
    record_cancellation_requested,
    record_reported_identity,
    record_safe_rejection,
    record_terminal_status,
    transition_run,
    validate_cancelled_collection,
    validate_terminal_collection,
)
from boundary.injection.capability import (
    CONTROL_TOOL_IDENTITY,
    create_control_capability,
    retire_capability,
)
from boundary.persistence.tables import runs
from boundary.sut.client import (
    InvalidWireResponse,
    SutClient,
    SutRemoteError,
    SutTimeoutError,
    SutTransportError,
)
from boundary.sut.contract_v1 import (
    CONTRACT_VERSION,
    AcceptedResponse,
    CancellationAcknowledgement,
    CancellationRequest,
    EventPage,
    RunStatus,
    TestRunRequest,
    TestedInput,
)


DEFAULT_CANCELLATION_GRACE_MS = 2_000
MAX_CANCELLATION_GRACE_MS = 30_000


class ControlClock(Protocol):
    """The monotonic timing seam used by deterministic deadline tests."""

    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class SutControlPort(Protocol):
    """The narrow ADR 001 client surface used by the executor."""

    async def create_run(
        self,
        request: TestRunRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> AcceptedResponse: ...

    async def get_status(
        self,
        run_id: UUID,
        *,
        timeout_seconds: float | None = None,
    ) -> RunStatus: ...

    async def get_events(
        self,
        run_id: UUID,
        *,
        after_producer_seq: int,
        timeout_seconds: float | None = None,
    ) -> EventPage: ...

    async def cancel_run(
        self,
        request: CancellationRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> CancellationAcknowledgement: ...

    async def aclose(self) -> None: ...


class _AsyncioClock:
    def monotonic(self) -> float:
        return asyncio.get_running_loop().time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class RunDeadlineReached(Exception):
    """Control flow marker that cannot be mistaken for transport failure."""


@dataclass(frozen=True, slots=True)
class ControlExecutionResult:
    run_id: UUID
    trace_id: UUID
    operational_status: str
    final_producer_seq: int
    target_event_count: int
    capability_record_id: UUID


@dataclass(slots=True)
class _CollectionState:
    terminal_status: RunStatus | None = None
    gap_observed: bool = False
    last_high_watermark: int = 0


class ControlExecutionError(Exception):
    """A safe summary of a persisted terminal control failure."""

    def __init__(self, status: str, reason: str) -> None:
        self.status = status
        self.reason = reason
        super().__init__(f"control execution ended {status}: {reason}")


async def execute_control_run(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    sut_base_url: str,
    tool_endpoint: str,
    tested_input: str,
    execution_budget_ms: int = 30_000,
    cancellation_grace_ms: int = DEFAULT_CANCELLATION_GRACE_MS,
    poll_interval_ms: int = 100,
    http_timeout_seconds: float = 5.0,
    http_client: httpx.AsyncClient | None = None,
    clock: ControlClock | None = None,
    sut_client: SutControlPort | None = None,
) -> ControlExecutionResult:
    """Invoke, poll, collect, and terminally transition one control run."""
    if not 0 < execution_budget_ms <= 30_000:
        raise ValueError("execution budget must be between 1 and 30000 ms")
    if not 0 < cancellation_grace_ms <= MAX_CANCELLATION_GRACE_MS:
        raise ValueError(
            "cancellation grace must be between 1 and 30000 ms"
        )
    if not 0 < poll_interval_ms <= execution_budget_ms:
        raise ValueError("poll interval must fit the execution budget")
    if http_timeout_seconds <= 0:
        raise ValueError("HTTP timeout must be positive")
    if sut_client is not None and http_client is not None:
        raise ValueError("provide either sut_client or http_client")

    run = await _load_control_run(engine, run_id)
    from boundary.execution.injected import bind_control_tested_input

    await bind_control_tested_input(
        engine,
        run_id=run_id,
        tested_input=tested_input,
    )
    expires_at = datetime.now(timezone.utc) + timedelta(
        milliseconds=execution_budget_ms
    )
    grant = await create_control_capability(
        engine,
        run_id=run.run_id,
        trace_id=run.trace_id,
        tool_identity=CONTROL_TOOL_IDENTITY,
        expires_at=expires_at,
    )
    request = TestRunRequest(
        contract_version=CONTRACT_VERSION,
        campaign_id=run.campaign_id,
        scenario_id=run.scenario_id,
        scenario_version=run.scenario_version,
        run_id=run.run_id,
        trace_id=run.trace_id,
        tested_agent_id=run.expected_tested_agent_id,
        tested_agent_version=run.expected_tested_agent_version,
        tested_input=TestedInput(query=tested_input),
        execution_budget_ms=execution_budget_ms,
        tool_endpoint=tool_endpoint,
        tool_capability=grant.capability_secret,
    )
    control_clock = clock or _AsyncioClock()
    deadline = (
        control_clock.monotonic() + execution_budget_ms / 1000
    )
    owns_client = sut_client is None
    client = sut_client or SutClient(
        sut_base_url,
        timeout_seconds=http_timeout_seconds,
        client=http_client,
        forbidden_values=(grant.capability_secret,),
    )
    state = _CollectionState()
    try:
        try:
            return await _execute_before_deadline(
                engine,
                run=run,
                request=request,
                client=client,
                clock=control_clock,
                deadline=deadline,
                poll_interval_ms=poll_interval_ms,
                http_timeout_seconds=http_timeout_seconds,
                capability_record_id=grant.capability_record_id,
                state=state,
            )
        except RunDeadlineReached:
            return await _cancel_after_deadline(
                engine,
                run=run,
                client=client,
                clock=control_clock,
                run_deadline=deadline,
                execution_budget_ms=execution_budget_ms,
                cancellation_grace_ms=cancellation_grace_ms,
                poll_interval_ms=poll_interval_ms,
                http_timeout_seconds=http_timeout_seconds,
                capability_record_id=grant.capability_record_id,
                state=state,
            )
        except SutTransportError:
            if control_clock.monotonic() >= deadline:
                return await _cancel_after_deadline(
                    engine,
                    run=run,
                    client=client,
                    clock=control_clock,
                    run_deadline=deadline,
                    execution_budget_ms=execution_budget_ms,
                    cancellation_grace_ms=cancellation_grace_ms,
                    poll_interval_ms=poll_interval_ms,
                    http_timeout_seconds=http_timeout_seconds,
                    capability_record_id=grant.capability_record_id,
                    state=state,
                )
            raise
    except EvidenceLimitExceeded as error:
        await record_safe_rejection(
            engine,
            run_id=run_id,
            category=error.code,
            raw_bytes=error.raw_bytes,
        )
        await _terminal_failure(
            engine,
            run_id,
            grant.capability_record_id,
            "invalid",
            error.code,
        )
        raise ControlExecutionError("invalid", error.code) from error
    except (IdentityMismatch, EvidenceInvalid) as error:
        await _terminal_failure(
            engine,
            run_id,
            grant.capability_record_id,
            "invalid",
            error.code,
        )
        raise ControlExecutionError("invalid", error.code) from error
    except InvalidWireResponse as error:
        await record_safe_rejection(
            engine,
            run_id=run_id,
            category=error.reason,
            raw_bytes=error.raw_bytes,
        )
        await _terminal_failure(
            engine,
            run_id,
            grant.capability_record_id,
            "invalid",
            error.code,
        )
        raise ControlExecutionError("invalid", error.code) from error
    except SutRemoteError as error:
        await _terminal_failure(
            engine,
            run_id,
            grant.capability_record_id,
            "invalid",
            error.problem.error.code,
        )
        raise ControlExecutionError(
            "invalid",
            error.problem.error.code,
        ) from error
    except SutTransportError as error:
        await _terminal_failure(
            engine,
            run_id,
            grant.capability_record_id,
            "failed",
            error.code,
        )
        raise ControlExecutionError("failed", error.code) from error
    finally:
        if owns_client:
            await client.aclose()


async def _execute_before_deadline(
    engine: AsyncEngine,
    *,
    run,
    request: TestRunRequest,
    client: SutControlPort,
    clock: ControlClock,
    deadline: float,
    poll_interval_ms: int,
    http_timeout_seconds: float,
    capability_record_id: UUID,
    state: _CollectionState,
    expected_outcome_kind: str = "success",
) -> ControlExecutionResult:
    accepted = await _deadline_request(
        client.create_run,
        clock=clock,
        deadline=deadline,
        configured_timeout=http_timeout_seconds,
        request=request,
    )
    await record_reported_identity(
        engine,
        run_id=run.run_id,
        reported_agent_id=accepted.tested_agent_id,
        reported_agent_version=accepted.tested_agent_version,
    )

    while True:
        status = await _deadline_request(
            client.get_status,
            clock=clock,
            deadline=deadline,
            configured_timeout=http_timeout_seconds,
            run_id=run.run_id,
        )
        await _observe_status(
            engine,
            run=run,
            status=status,
            state=state,
            cancellation_collection=False,
        )
        remaining_before = deadline - clock.monotonic()
        timeout_seconds = _remaining_timeout(
            clock, deadline, http_timeout_seconds
        )
        deadline_limited = remaining_before <= http_timeout_seconds
        try:
            await _collect_events(
                engine,
                run=run,
                client=client,
                state=state,
                timeout_seconds=timeout_seconds,
                cancellation_collection=False,
            )
        except SutTimeoutError:
            if deadline_limited:
                remaining = _remaining_seconds(clock, deadline)
                if remaining > 0:
                    await clock.sleep(remaining)
                raise RunDeadlineReached from None
            raise
        result = await _complete_normal_terminal(
            engine,
            run=run,
            capability_record_id=capability_record_id,
            state=state,
            expected_outcome_kind=expected_outcome_kind,
        )
        if result is not None:
            return result
        await clock.sleep(
            min(
                poll_interval_ms / 1000,
                _remaining_seconds(clock, deadline),
            )
        )


async def _cancel_after_deadline(
    engine: AsyncEngine,
    *,
    run,
    client: SutControlPort,
    clock: ControlClock,
    run_deadline: float,
    execution_budget_ms: int,
    cancellation_grace_ms: int,
    poll_interval_ms: int,
    http_timeout_seconds: float,
    capability_record_id: UUID,
    state: _CollectionState,
) -> ControlExecutionResult:
    cancellation = CancellationRequest(
        contract_version=CONTRACT_VERSION,
        run_id=run.run_id,
        trace_id=run.trace_id,
        cancellation_id=uuid4(),
    )
    grace_deadline = (
        run_deadline + cancellation_grace_ms / 1000
    )
    await record_cancellation_requested(
        engine,
        run_id=run.run_id,
        trace_id=run.trace_id,
        cancellation_id=cancellation.cancellation_id,
        execution_budget_ms=execution_budget_ms,
    )
    acknowledged = False
    cancellation_applied = False
    cancellation_evidence_valid = True

    try:
        acknowledgement = await client.cancel_run(
            cancellation,
            timeout_seconds=_remaining_timeout(
                clock,
                grace_deadline,
                http_timeout_seconds,
            ),
        )
        acknowledged = True
        cancellation_applied = acknowledgement.cancellation_applied
        await _observe_status(
            engine,
            run=run,
            status=acknowledgement.status,
            state=state,
            cancellation_collection=True,
        )
    except RunDeadlineReached:
        pass
    except InvalidWireResponse as error:
        await record_safe_rejection(
            engine,
            run_id=run.run_id,
            category=error.reason,
            raw_bytes=error.raw_bytes,
        )
        cancellation_evidence_valid = False
    except (
        EvidenceInvalid,
        EvidenceLimitExceeded,
        SutRemoteError,
        SutTransportError,
    ):
        cancellation_evidence_valid = False

    while clock.monotonic() < grace_deadline:
        try:
            await _collect_events(
                engine,
                run=run,
                client=client,
                state=state,
                timeout_seconds=_remaining_timeout(
                    clock,
                    grace_deadline,
                    http_timeout_seconds,
                ),
                cancellation_collection=True,
            )
            result = await _complete_cancelled_terminal(
                engine,
                run=run,
                cancellation=cancellation,
                capability_record_id=capability_record_id,
                state=state,
                acknowledged=acknowledged,
                cancellation_applied=cancellation_applied,
                evidence_valid=cancellation_evidence_valid,
            )
            if result is not None:
                return result
            status = await client.get_status(
                run.run_id,
                timeout_seconds=_remaining_timeout(
                    clock,
                    grace_deadline,
                    http_timeout_seconds,
                ),
            )
            await _observe_status(
                engine,
                run=run,
                status=status,
                state=state,
                cancellation_collection=True,
            )
            result = await _complete_cancelled_terminal(
                engine,
                run=run,
                cancellation=cancellation,
                capability_record_id=capability_record_id,
                state=state,
                acknowledged=acknowledged,
                cancellation_applied=cancellation_applied,
                evidence_valid=cancellation_evidence_valid,
            )
            if result is not None:
                return result
        except RunDeadlineReached:
            break
        except InvalidWireResponse as error:
            await record_safe_rejection(
                engine,
                run_id=run.run_id,
                category=error.reason,
                raw_bytes=error.raw_bytes,
            )
            cancellation_evidence_valid = False
        except (
            EvidenceInvalid,
            EvidenceLimitExceeded,
            SutRemoteError,
            SutTransportError,
        ):
            cancellation_evidence_valid = False

        remaining = _remaining_seconds(clock, grace_deadline)
        if remaining > 0:
            await clock.sleep(
                min(poll_interval_ms / 1000, remaining)
            )

    await transition_run(
        engine,
        run_id=run.run_id,
        target_status="timed_out",
        reason=(
            "target_event_gap_at_cancellation_deadline"
            if state.gap_observed
            else "cancellation_grace_exhausted"
        ),
    )
    await retire_capability(engine, capability_record_id)
    raise ControlExecutionError(
        "timed_out",
        "cancellation grace exhausted",
    )


async def _observe_status(
    engine: AsyncEngine,
    *,
    run,
    status: RunStatus,
    state: _CollectionState,
    cancellation_collection: bool,
) -> None:
    if status.run_id != run.run_id or status.trace_id != run.trace_id:
        raise IdentityMismatch("status identity mismatch")
    if status.producer_high_watermark < state.last_high_watermark:
        raise EvidenceInvalid("target producer high watermark decreased")
    state.last_high_watermark = status.producer_high_watermark
    await record_reported_identity(
        engine,
        run_id=run.run_id,
        reported_agent_id=status.tested_agent_id,
        reported_agent_version=status.tested_agent_version,
    )
    terminal = status.state in {"completed", "failed", "cancelled"}
    if terminal:
        if (
            state.terminal_status is not None
            and state.terminal_status != status
        ):
            raise EvidenceInvalid("target terminal status changed")
        assert status.final_producer_seq is not None
        await observe_terminal_watermark(
            engine,
            run_id=run.run_id,
            final_producer_seq=status.final_producer_seq,
            producer_high_watermark=status.producer_high_watermark,
        )
        state.terminal_status = status
    if (
        not cancellation_collection
        and status.state != "accepted"
    ) or (
        cancellation_collection and status.state == "running"
    ):
        await transition_run(
            engine,
            run_id=run.run_id,
            target_status="running",
            reason="validated_target_progress",
        )


async def _collect_events(
    engine: AsyncEngine,
    *,
    run,
    client: SutControlPort,
    state: _CollectionState,
    timeout_seconds: float,
    cancellation_collection: bool,
) -> None:
    cursor = await _load_cursor(engine, run.run_id)
    page = await client.get_events(
        run.run_id,
        after_producer_seq=cursor,
        timeout_seconds=timeout_seconds,
    )
    if page.run_id != run.run_id or page.trace_id != run.trace_id:
        raise IdentityMismatch("event page identity mismatch")
    if page.producer_high_watermark < state.last_high_watermark:
        raise EvidenceInvalid("target producer high watermark decreased")
    state.last_high_watermark = page.producer_high_watermark
    if page.events and (
        not cancellation_collection
        or any(
            event.event_type != "sut.run.cancelled"
            for event in page.events
        )
    ):
        await transition_run(
            engine,
            run_id=run.run_id,
            target_status="running",
            reason="validated_target_event",
        )
    try:
        await collect_target_page(
            engine,
            run_id=run.run_id,
            requested_after=cursor,
            page=page,
        )
        state.gap_observed = False
    except ForwardGap:
        state.gap_observed = True


async def _complete_normal_terminal(
    engine: AsyncEngine,
    *,
    run,
    capability_record_id: UUID,
    state: _CollectionState,
    expected_outcome_kind: str = "success",
) -> ControlExecutionResult | None:
    status = state.terminal_status
    if status is None:
        return None
    cursor = await _load_cursor(engine, run.run_id)
    if cursor != status.final_producer_seq:
        return None
    await validate_terminal_collection(
        engine,
        run_id=run.run_id,
        status=status,
    )
    if (
        status.state == "completed"
        and status.terminal_result is not None
        and status.terminal_result.outcome_kind != expected_outcome_kind
    ):
        raise EvidenceInvalid(
            "target completed with an unexpected outcome"
        )
    await record_terminal_status(
        engine,
        run_id=run.run_id,
        status=status,
    )
    await transition_run(
        engine,
        run_id=run.run_id,
        target_status=status.state,
        reason="terminal_watermark_collected",
    )
    await retire_capability(engine, capability_record_id)
    return ControlExecutionResult(
        run_id=run.run_id,
        trace_id=run.trace_id,
        operational_status=status.state,
        final_producer_seq=status.final_producer_seq,
        target_event_count=cursor,
        capability_record_id=capability_record_id,
    )


async def _complete_cancelled_terminal(
    engine: AsyncEngine,
    *,
    run,
    cancellation: CancellationRequest,
    capability_record_id: UUID,
    state: _CollectionState,
    acknowledged: bool,
    cancellation_applied: bool,
    evidence_valid: bool,
) -> ControlExecutionResult | None:
    status = state.terminal_status
    if (
        not acknowledged
        or not cancellation_applied
        or not evidence_valid
        or status is None
        or status.state != "cancelled"
    ):
        return None
    cursor = await _load_cursor(engine, run.run_id)
    if cursor != status.final_producer_seq:
        return None
    await validate_cancelled_collection(
        engine,
        run_id=run.run_id,
        status=status,
        cancellation_id=cancellation.cancellation_id,
    )
    await record_terminal_status(
        engine,
        run_id=run.run_id,
        status=status,
    )
    await transition_run(
        engine,
        run_id=run.run_id,
        target_status="cancelled",
        reason="cancelled_terminal_watermark_collected",
    )
    await retire_capability(engine, capability_record_id)
    return ControlExecutionResult(
        run_id=run.run_id,
        trace_id=run.trace_id,
        operational_status="cancelled",
        final_producer_seq=status.final_producer_seq,
        target_event_count=cursor,
        capability_record_id=capability_record_id,
    )


async def _terminal_failure(
    engine: AsyncEngine,
    run_id: UUID,
    capability_record_id: UUID,
    status: str,
    reason: str,
) -> None:
    await transition_run(
        engine,
        run_id=run_id,
        target_status=status,
        reason=reason,
    )
    await retire_capability(engine, capability_record_id)


async def _load_control_run(engine: AsyncEngine, run_id: UUID):
    async with engine.connect() as connection:
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == run_id)
            )
        ).one_or_none()
    if run is None:
        raise ValueError("run does not exist")
    if run.run_role != "control" or run.operational_status != "accepted":
        raise ValueError("run is not an accepted control")
    if run.contract_version != CONTRACT_VERSION:
        raise ValueError("run contract version is unsupported")
    return run


async def _load_cursor(engine: AsyncEngine, run_id: UUID) -> int:
    async with engine.connect() as connection:
        cursor = await connection.scalar(
            sa.select(runs.c.target_producer_cursor).where(
                runs.c.run_id == run_id
            )
        )
    if cursor is None:
        raise ValueError("run does not exist")
    return cursor


def _remaining_seconds(clock: ControlClock, deadline: float) -> float:
    return max(0.0, deadline - clock.monotonic())


def _remaining_timeout(
    clock: ControlClock,
    deadline: float,
    configured_timeout: float,
) -> float:
    remaining = deadline - clock.monotonic()
    if remaining <= 0:
        raise RunDeadlineReached
    return min(configured_timeout, remaining)


async def _deadline_request(
    operation,
    *,
    clock: ControlClock,
    deadline: float,
    configured_timeout: float,
    **kwargs: Any,
):
    remaining_before = deadline - clock.monotonic()
    if remaining_before <= 0:
        raise RunDeadlineReached
    timeout_seconds = min(configured_timeout, remaining_before)
    deadline_limited = remaining_before <= configured_timeout
    try:
        return await operation(
            timeout_seconds=timeout_seconds,
            **kwargs,
        )
    except SutTimeoutError:
        if not deadline_limited:
            raise
        remaining_after = _remaining_seconds(clock, deadline)
        if remaining_after > 0:
            await clock.sleep(remaining_after)
        raise RunDeadlineReached from None

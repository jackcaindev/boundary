"""One bounded headless ADR 001 control-run execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Protocol
from uuid import UUID, uuid4

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.evidence.collector import (
    EvidenceInvalid,
    EvidenceLimitExceeded,
    ForwardGap,
    IdentityMismatch,
    RunBudgetBinding,
    collect_target_page,
    observe_terminal_watermark,
    record_cancellation_requested,
    record_deadline_reached,
    record_reported_identity,
    record_run_budget,
    record_safe_rejection,
    record_terminal_status,
    transition_run,
    validate_cancelled_collection,
    validate_terminal_collection,
)
from boundary.evidence.finalizer import finalize_run_evidence
from boundary.injection.capability import (
    CONTROL_TOOL_IDENTITY,
    create_control_capability,
    retire_capability,
)
from boundary.persistence.tables import (
    campaigns,
    evidence_records,
    evidence_sets,
    fault_activations,
    run_capabilities,
    runs,
)
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


class CampaignCancellationRequested(Exception):
    def __init__(self, cancellation_id: UUID) -> None:
        self.cancellation_id = cancellation_id
        super().__init__("campaign cancellation was requested")


class PreInvocationCancellationRequested(CampaignCancellationRequested):
    """Cancellation won the durable target-interaction linearization point."""


class SimulatedProcessLoss(BaseException):
    """Test-only crash seam that bypasses ordinary exception settlement."""


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


TargetInteractionHook = Callable[[str, Any], Awaitable[None]]


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
    _fail_after: str | None = None,
    _target_interaction_hook: TargetInteractionHook | None = None,
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
    _raise_process_loss(_fail_after, "control_capability")
    control_clock = clock or _AsyncioClock()
    budget_started_ns = round(control_clock.monotonic() * 1_000_000_000)
    budget = await record_run_budget(
        engine,
        run_id=run.run_id,
        trace_id=run.trace_id,
        execution_budget_ms=execution_budget_ms,
        budget_started_monotonic_ns=budget_started_ns,
        deadline_monotonic_ns=(
            budget_started_ns + execution_budget_ms * 1_000_000
        ),
    )
    _raise_process_loss(_fail_after, "control_budget")
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
    deadline = budget.deadline_monotonic_ns / 1_000_000_000
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
                budget=budget,
                _fail_after=_fail_after,
                _target_interaction_hook=_target_interaction_hook,
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
                budget=budget,
            )
        except PreInvocationCancellationRequested as cancellation:
            return await _settle_pre_invocation_cancellation(
                engine,
                run=run,
                capability_record_id=grant.capability_record_id,
                cancellation_id=cancellation.cancellation_id,
            )
        except CampaignCancellationRequested as cancellation:
            return await _cancel_after_deadline(
                engine,
                run=run,
                client=client,
                clock=control_clock,
                run_deadline=control_clock.monotonic(),
                execution_budget_ms=execution_budget_ms,
                cancellation_grace_ms=cancellation_grace_ms,
                poll_interval_ms=poll_interval_ms,
                http_timeout_seconds=http_timeout_seconds,
                capability_record_id=grant.capability_record_id,
                state=state,
                budget=budget,
                cancellation_id=cancellation.cancellation_id,
                deadline_driven=False,
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
                    budget=budget,
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
    request: TestRunRequest | None,
    client: SutControlPort,
    clock: ControlClock,
    deadline: float,
    poll_interval_ms: int,
    http_timeout_seconds: float,
    capability_record_id: UUID,
    state: _CollectionState,
    budget: RunBudgetBinding,
    expected_outcome_kind: str = "success",
    resume_polling_only: bool = False,
    _fail_after: str | None = None,
    _target_interaction_hook: TargetInteractionHook | None = None,
) -> ControlExecutionResult:
    if not resume_polling_only:
        assert request is not None
        if _target_interaction_hook is not None:
            await _target_interaction_hook("before_claim", run)
        cancellation_id = await _claim_target_interaction(engine, run=run)
        if cancellation_id is not None:
            raise PreInvocationCancellationRequested(cancellation_id)
        if _target_interaction_hook is not None:
            await _target_interaction_hook("after_claim", run)
        _raise_process_loss(_fail_after, "target_interaction")
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
        async with engine.begin() as connection:
            await connection.execute(
                runs.update()
                .where(runs.c.run_id == run.run_id)
                .values(execution_checkpoint="polling")
            )
        _raise_process_loss(_fail_after, "polling_checkpoint")

    while True:
        cancellation_id = await _campaign_cancellation_id(
            engine, run.campaign_id
        )
        if cancellation_id is not None:
            raise CampaignCancellationRequested(cancellation_id)
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
            clock=clock,
            budget=budget,
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
    budget: RunBudgetBinding,
    cancellation_id: UUID | None = None,
    deadline_driven: bool = True,
) -> ControlExecutionResult:
    cancellation = CancellationRequest(
        contract_version=CONTRACT_VERSION,
        run_id=run.run_id,
        trace_id=run.trace_id,
        cancellation_id=cancellation_id or uuid4(),
    )
    grace_deadline = (
        run_deadline + cancellation_grace_ms / 1000
    )
    deadline_evidence_id = None
    if deadline_driven:
        deadline_evidence_id = await record_deadline_reached(
            engine,
            run_id=run.run_id,
            trace_id=run.trace_id,
            budget=budget,
            observed_monotonic_ns=round(clock.monotonic() * 1_000_000_000),
        )
    await record_cancellation_requested(
        engine,
        run_id=run.run_id,
        trace_id=run.trace_id,
        cancellation_id=cancellation.cancellation_id,
        deadline_evidence_id=deadline_evidence_id,
        execution_budget_ms=execution_budget_ms,
        reason=(
            "run_budget_expired"
            if deadline_driven
            else "public_campaign_cancellation"
        ),
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
                clock=clock,
                budget=budget,
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
                clock=clock,
                budget=budget,
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
    clock: ControlClock,
    budget: RunBudgetBinding,
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
        run_budget=budget,
        observed_monotonic_ns=round(clock.monotonic() * 1_000_000_000),
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
    clock: ControlClock,
    budget: RunBudgetBinding,
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
        run_budget=budget,
        observed_monotonic_ns=round(clock.monotonic() * 1_000_000_000),
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
        capability_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(run_capabilities)
            .where(run_capabilities.c.run_id == run_id)
        )
        budget_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == run_id,
                evidence_records.c.source == "boundary",
                evidence_records.c.disposition == "accepted",
                evidence_records.c.event_type == "boundary.run_budget.bound",
            )
        )
    if run is None:
        raise ValueError("run does not exist")
    if run.run_role != "control" or run.operational_status != "accepted":
        raise ValueError("run is not an accepted control")
    if run.contract_version != CONTRACT_VERSION:
        raise ValueError("run contract version is unsupported")
    if (
        run.execution_checkpoint != "not_started"
        or capability_count
        or budget_count
    ):
        raise ValueError("accepted control is not safely untouched")
    return run


def _raise_process_loss(configured: str | None, current: str) -> None:
    if configured == current:
        raise SimulatedProcessLoss(
            f"simulated process loss after {current}"
        )


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


async def _campaign_cancellation_id(
    engine: AsyncEngine, campaign_id: UUID
) -> UUID | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                sa.select(
                    campaigns.c.cancel_requested,
                    campaigns.c.cancellation_id,
                ).where(campaigns.c.campaign_id == campaign_id)
            )
        ).one_or_none()
    if row is None or not row.cancel_requested:
        return None
    if row.cancellation_id is None:
        raise EvidenceInvalid("campaign cancellation identity is missing")
    return row.cancellation_id


async def _claim_target_interaction(
    engine: AsyncEngine,
    *,
    run,
) -> UUID | None:
    """Serialize queued cancellation against permission to invoke the target."""
    async with engine.begin() as connection:
        campaign = (
            await connection.execute(
                sa.select(campaigns)
                .where(campaigns.c.campaign_id == run.campaign_id)
                .with_for_update()
            )
        ).one_or_none()
        if campaign is None:
            raise EvidenceInvalid("run campaign does not exist")
        if campaign.cancel_requested:
            if campaign.cancellation_id is None:
                raise EvidenceInvalid(
                    "campaign cancellation identity is missing"
                )
            return campaign.cancellation_id
        if campaign.status not in {"accepted", "running"}:
            raise EvidenceInvalid("run campaign is not executable")
        checkpointed = await connection.execute(
            runs.update()
            .where(
                runs.c.run_id == run.run_id,
                runs.c.campaign_id == run.campaign_id,
                runs.c.operational_status == "accepted",
                runs.c.execution_checkpoint == "not_started",
                runs.c.evidence_open.is_(True),
            )
            .values(execution_checkpoint="target_interaction")
        )
        if checkpointed.rowcount != 1:
            raise EvidenceInvalid(
                "target interaction checkpoint is not claimable"
            )
    return None


async def _settle_pre_invocation_cancellation(
    engine: AsyncEngine,
    *,
    run,
    capability_record_id: UUID,
    cancellation_id: UUID,
) -> ControlExecutionResult:
    """Finalize cancellation that won before target interaction was claimed."""
    async with engine.connect() as connection:
        campaign = (
            await connection.execute(
                sa.select(
                    campaigns.c.cancel_requested,
                    campaigns.c.cancellation_id,
                ).where(campaigns.c.campaign_id == run.campaign_id)
            )
        ).one_or_none()
    if (
        campaign is None
        or not campaign.cancel_requested
        or campaign.cancellation_id != cancellation_id
    ):
        raise EvidenceInvalid("campaign cancellation binding changed")
    await retire_capability(engine, capability_record_id)
    await transition_run(
        engine,
        run_id=run.run_id,
        target_status="cancelled",
        reason="public_campaign_cancellation_before_invocation",
    )
    await finalize_run_evidence(
        engine,
        run_id=run.run_id,
        cutoff_reason="cancellation_grace",
    )
    async with engine.begin() as connection:
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == run.run_id)
            .values(execution_checkpoint="finalized")
        )
        await connection.execute(
            campaigns.update()
            .where(
                campaigns.c.campaign_id == run.campaign_id,
                campaigns.c.cancel_requested.is_(True),
            )
            .values(status="cancelled", current_step="cancelled")
        )
    return ControlExecutionResult(
        run_id=run.run_id,
        trace_id=run.trace_id,
        operational_status="cancelled",
        final_producer_seq=0,
        target_event_count=0,
        capability_record_id=capability_record_id,
    )


async def resume_polling_run(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    sut_base_url: str,
    execution_budget_ms: int = 30_000,
    cancellation_grace_ms: int = DEFAULT_CANCELLATION_GRACE_MS,
    poll_interval_ms: int = 100,
    http_timeout_seconds: float = 5.0,
) -> ControlExecutionResult:
    """Resume only status/event collection; never recreate a target run."""
    async with engine.connect() as connection:
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == run_id)
            )
        ).one_or_none()
        capability = (
            await connection.execute(
                sa.select(run_capabilities).where(
                    run_capabilities.c.run_id == run_id,
                    run_capabilities.c.state == "active",
                )
            )
        ).one_or_none()
        budget_row = (
            await connection.execute(
                sa.select(evidence_records).where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.source == "boundary",
                    evidence_records.c.event_type
                    == "boundary.run_budget.bound",
                    evidence_records.c.disposition == "accepted",
                )
            )
        ).one_or_none()
        unsettled_activations = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(fault_activations)
            .where(
                fault_activations.c.run_id == run_id,
                sa.or_(
                    fault_activations.c.effect_status.is_distinct_from(
                        "effect_realized"
                    ),
                    fault_activations.c.hold_disposition.is_distinct_from(
                        "bounded_hold_complete"
                    ),
                ),
            )
        )
        finalized = await connection.scalar(
            sa.select(evidence_sets.c.evidence_set_id).where(
                evidence_sets.c.run_id == run_id
            )
        )
    if (
        run is None
        or run.operational_status not in {"accepted", "running"}
        or run.execution_checkpoint != "polling"
        or capability is None
        or budget_row is None
        or unsettled_activations
        or finalized is not None
        or run.reported_tested_agent_id
        != run.expected_tested_agent_id
        or run.reported_tested_agent_version
        != run.expected_tested_agent_version
    ):
        raise ControlExecutionError(
            "failed", "polling-only checkpoint is not provable"
        )
    budget = RunBudgetBinding(
        evidence_id=budget_row.evidence_id,
        execution_budget_ms=budget_row.payload["execution_budget_ms"],
        budget_started_monotonic_ns=budget_row.payload[
            "budget_started_monotonic_ns"
        ],
        deadline_monotonic_ns=budget_row.payload["deadline_monotonic_ns"],
    )
    if budget.execution_budget_ms != execution_budget_ms:
        raise ControlExecutionError("failed", "run budget configuration drifted")
    clock = _AsyncioClock()
    deadline = budget.deadline_monotonic_ns / 1_000_000_000
    client = SutClient(sut_base_url, timeout_seconds=http_timeout_seconds)
    state = _CollectionState()
    expected = (
        "degraded"
        if run.expected_tested_agent_version == "fixed-v1"
        and run.run_role == "injected"
        else "success"
    )
    try:
        if clock.monotonic() >= deadline:
            raise RunDeadlineReached
        return await _execute_before_deadline(
            engine,
            run=run,
            request=None,
            client=client,
            clock=clock,
            deadline=deadline,
            poll_interval_ms=poll_interval_ms,
            http_timeout_seconds=http_timeout_seconds,
            capability_record_id=capability.capability_record_id,
            state=state,
            budget=budget,
            expected_outcome_kind=expected,
            resume_polling_only=True,
        )
    except RunDeadlineReached:
        return await _cancel_after_deadline(
            engine,
            run=run,
            client=client,
            clock=clock,
            run_deadline=deadline,
            execution_budget_ms=execution_budget_ms,
            cancellation_grace_ms=cancellation_grace_ms,
            poll_interval_ms=poll_interval_ms,
            http_timeout_seconds=http_timeout_seconds,
            capability_record_id=capability.capability_record_id,
            state=state,
            budget=budget,
        )
    except CampaignCancellationRequested as cancellation:
        return await _cancel_after_deadline(
            engine,
            run=run,
            client=client,
            clock=clock,
            run_deadline=clock.monotonic(),
            execution_budget_ms=execution_budget_ms,
            cancellation_grace_ms=cancellation_grace_ms,
            poll_interval_ms=poll_interval_ms,
            http_timeout_seconds=http_timeout_seconds,
            capability_record_id=capability.capability_record_id,
            state=state,
            budget=budget,
            cancellation_id=cancellation.cancellation_id,
            deadline_driven=False,
        )
    finally:
        await client.aclose()


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

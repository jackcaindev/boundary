"""Process-local response-gate authority and Task 5 effect proof."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID, uuid4

import rfc8785
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.persistence.tables import evidence_records, fault_activations, runs


TOOL_CLIENT_TIMEOUT_NS = 500_000_000
INJECTED_HOLD_NS = 1_000_000_000
ACTIVATION_EVENT_TYPE = "boundary.fault_activation_started"
EFFECT_EVENT_TYPE = "boundary.fault_effect_realized"
EXECUTION_ERROR_EVENT_TYPE = "boundary.execution.error"

EffectFailurePoint = Literal["effect_evidence", "effect_update", "effect_counter"]


class MonotonicClock(Protocol):
    def monotonic_ns(self) -> int: ...


class MonotonicWaiter(Protocol):
    async def wait_until(self, deadline_ns: int, clock: MonotonicClock) -> None: ...


class SystemMonotonicClock:
    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class AsyncioMonotonicWaiter:
    async def wait_until(self, deadline_ns: int, clock: MonotonicClock) -> None:
        remaining = deadline_ns - clock.monotonic_ns()
        if remaining > 0:
            await asyncio.sleep(remaining / 1_000_000_000)


class ResponseGateError(RuntimeError):
    pass


class EffectNotReady(ResponseGateError):
    pass


class EffectProofRejected(ResponseGateError):
    pass


class EffectPersistenceError(ResponseGateError):
    pass


@dataclass(frozen=True, slots=True)
class GateObservation:
    gate_closed: bool
    gate_closed_at_ns: int | None
    response_sent_at_ns: int | None
    authority_continuous: bool
    observed_at_ns: int


class ResponseGate:
    """Live process-local authority; its state is never reconstructed from SQL."""

    def __init__(self) -> None:
        self._closed_at_ns: int | None = None
        self._response_sent_at_ns: int | None = None
        self._authority_continuous = True

    @property
    def is_closed(self) -> bool:
        return self._closed_at_ns is not None

    def close(self, at_ns: int) -> None:
        if self._response_sent_at_ns is not None:
            raise ResponseGateError("a response was already sent")
        if self._closed_at_ns is None:
            self._closed_at_ns = at_ns

    def send_success(self, at_ns: int) -> None:
        if self.is_closed:
            raise ResponseGateError("the success response gate is closed")
        self._response_sent_at_ns = at_ns

    def observe_response_sent(self, at_ns: int) -> None:
        """Record contradictory transport evidence without authorizing success."""
        if self._response_sent_at_ns is None:
            self._response_sent_at_ns = at_ns

    def lose_authority(self) -> None:
        self._authority_continuous = False

    def observe(self, now_ns: int) -> GateObservation:
        return GateObservation(
            gate_closed=self.is_closed,
            gate_closed_at_ns=self._closed_at_ns,
            response_sent_at_ns=self._response_sent_at_ns,
            authority_continuous=self._authority_continuous,
            observed_at_ns=now_ns,
        )


@dataclass(slots=True)
class ActivationRuntime:
    activation_id: UUID
    run_id: UUID
    trace_id: UUID
    fault_id: UUID
    tool_call_id: UUID
    accepted_request_origin_ns: int
    clock: MonotonicClock
    waiter: MonotonicWaiter
    gate: ResponseGate
    activated: bool = False
    settled: bool = False
    retained_proof_authority: bool = True
    completion_state: str = "pending"

    @classmethod
    def create(
        cls,
        *,
        run_id: UUID,
        trace_id: UUID,
        fault_id: UUID,
        tool_call_id: UUID,
        clock: MonotonicClock | None = None,
        waiter: MonotonicWaiter | None = None,
    ) -> "ActivationRuntime":
        selected_clock = clock or SystemMonotonicClock()
        return cls(
            activation_id=uuid4(),
            run_id=run_id,
            trace_id=trace_id,
            fault_id=fault_id,
            tool_call_id=tool_call_id,
            accepted_request_origin_ns=selected_clock.monotonic_ns(),
            clock=selected_clock,
            waiter=waiter or AsyncioMonotonicWaiter(),
            gate=ResponseGate(),
        )

    @property
    def client_timeout_boundary_ns(self) -> int:
        return self.accepted_request_origin_ns + TOOL_CLIENT_TIMEOUT_NS

    @property
    def hold_deadline_ns(self) -> int:
        return self.accepted_request_origin_ns + INJECTED_HOLD_NS

    def close_success_gate(self) -> int:
        started_ns = self.clock.monotonic_ns()
        if started_ns >= self.client_timeout_boundary_ns:
            raise ResponseGateError("activation did not start before the boundary")
        self.gate.close(started_ns)
        self.activated = True
        return started_ns

    def lose_authority(self) -> None:
        self.retained_proof_authority = False
        self.gate.lose_authority()


class EffectProofV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    activation_id: str
    run_id: str
    fault_id: str
    tool_call_id: str
    accepted_request_origin_ns: int
    activation_started_ns: int
    client_timeout_boundary_ns: int
    observed_monotonic_ns: int
    gate_closed: Literal[True]
    no_response_before_boundary: Literal[True]
    timing_authority_continuous: Literal[True]


def canonicalize_effect_proof(proof: EffectProofV1) -> tuple[bytes, str]:
    validated = EffectProofV1.model_validate(proof.model_dump(mode="json"))
    canonical = rfc8785.dumps(validated.model_dump(mode="json"))
    return canonical, sha256(canonical).hexdigest()


async def realize_timeout_effect(
    engine: AsyncEngine,
    runtime: ActivationRuntime,
    *,
    _fail_after: EffectFailurePoint | None = None,
) -> UUID:
    """Seal exactly one effect from the same continuously authoritative gate."""
    now_ns = runtime.clock.monotonic_ns()
    if now_ns < runtime.client_timeout_boundary_ns:
        raise EffectNotReady("the client timeout boundary has not been reached")
    observation = runtime.gate.observe(now_ns)
    if (
        not runtime.activated
        or not observation.gate_closed
        or observation.gate_closed_at_ns is None
        or observation.gate_closed_at_ns >= runtime.client_timeout_boundary_ns
        or not observation.authority_continuous
        or not runtime.retained_proof_authority
        or (
            observation.response_sent_at_ns is not None
            and observation.response_sent_at_ns < runtime.client_timeout_boundary_ns
        )
    ):
        raise EffectProofRejected("the live response gate cannot prove the timeout")

    effect_evidence_id = uuid4()
    try:
        async with engine.begin() as connection:
            run = (
                await connection.execute(
                    sa.select(runs)
                    .where(runs.c.run_id == runtime.run_id)
                    .with_for_update()
                )
            ).one_or_none()
            activation = (
                await connection.execute(
                    sa.select(fault_activations)
                    .where(fault_activations.c.activation_id == runtime.activation_id)
                    .with_for_update()
                )
            ).one_or_none()
            if run is None or activation is None:
                raise EffectProofRejected("activation start evidence does not exist")
            if (
                activation.run_id != runtime.run_id
                or activation.fault_id != runtime.fault_id
                or activation.tool_call_id != runtime.tool_call_id
                or activation.activation_evidence_id is None
            ):
                raise EffectProofRejected("activation identity does not match")
            if activation.effect_status == "effect_realized":
                assert activation.effect_evidence_id is not None
                return activation.effect_evidence_id
            if activation.effect_status != "pending":
                raise EffectProofRejected("activation no longer retains proof eligibility")

            proof = EffectProofV1(
                schema_version=1,
                activation_id=str(runtime.activation_id),
                run_id=str(runtime.run_id),
                fault_id=str(runtime.fault_id),
                tool_call_id=str(runtime.tool_call_id),
                accepted_request_origin_ns=runtime.accepted_request_origin_ns,
                activation_started_ns=activation.activation_started_ns,
                client_timeout_boundary_ns=runtime.client_timeout_boundary_ns,
                observed_monotonic_ns=now_ns,
                gate_closed=True,
                no_response_before_boundary=True,
                timing_authority_continuous=True,
            )
            proof_bytes, proof_digest = canonicalize_effect_proof(proof)
            payload = {
                "activation_event_id": str(activation.activation_evidence_id),
                "effect_proof_digest": proof_digest,
                "fault_id": str(runtime.fault_id),
                "observed_monotonic_ns": now_ns,
                "retry_ordinal": activation.activation_ordinal,
                "schema_version": 1,
                "tool_call_id": str(runtime.tool_call_id),
            }
            payload_bytes = rfc8785.dumps(payload)
            await connection.execute(
                evidence_records.insert().values(
                    evidence_id=effect_evidence_id,
                    run_id=runtime.run_id,
                    source="boundary",
                    event_type=EFFECT_EVENT_TYPE,
                    boundary="tool_execution",
                    source_event_id=effect_evidence_id,
                    producer_seq=None,
                    receipt_seq=run.next_receipt_seq,
                    audit_seq=None,
                    caused_by_event_id=activation.activation_evidence_id,
                    payload_schema_version=1,
                    payload=json.loads(payload_bytes),
                    payload_canonical_bytes=payload_bytes,
                    payload_digest=sha256(payload_bytes).hexdigest(),
                    disposition="accepted",
                )
            )
            _raise_effect_failure(_fail_after, "effect_evidence")
            await connection.execute(
                fault_activations.update()
                .where(fault_activations.c.activation_id == runtime.activation_id)
                .values(
                    reservation_state="effect_realized",
                    effect_status="effect_realized",
                    effect_proof=json.loads(proof_bytes),
                    effect_proof_bytes=proof_bytes,
                    effect_proof_digest=proof_digest,
                    effect_evidence_id=effect_evidence_id,
                )
            )
            _raise_effect_failure(_fail_after, "effect_update")
            await connection.execute(
                runs.update()
                .where(runs.c.run_id == runtime.run_id)
                .values(next_receipt_seq=run.next_receipt_seq + 1)
            )
            _raise_effect_failure(_fail_after, "effect_counter")
    except (EffectNotReady, EffectProofRejected):
        raise
    except (IntegrityError, SQLAlchemyError):
        raise EffectPersistenceError("effect proof persistence failed") from None
    return effect_evidence_id


async def settle_activation_runtime(
    engine: AsyncEngine,
    runtime: ActivationRuntime,
    *,
    proof_failed: bool = False,
) -> None:
    """Persist the bounded hold disposition and any unproven outcome."""
    await runtime.waiter.wait_until(runtime.hold_deadline_ns, runtime.clock)
    async with engine.begin() as connection:
        run = (
            await connection.execute(
                sa.select(runs)
                .where(runs.c.run_id == runtime.run_id)
                .with_for_update()
            )
        ).one()
        activation = (
            await connection.execute(
                sa.select(fault_activations)
                .where(fault_activations.c.activation_id == runtime.activation_id)
                .with_for_update()
            )
        ).one()
        values: dict[str, object] = {
            "hold_disposition": "bounded_hold_complete",
            "runtime_completed_monotonic_ns": runtime.clock.monotonic_ns(),
            "runtime_completed_at": datetime.now(timezone.utc),
        }
        effect_was_unproven = activation.effect_status == "pending"
        if effect_was_unproven:
            values.update(
                reservation_state="unproven",
                effect_status="unproven",
                hold_disposition="proof_failed",
            )
            error_id = uuid4()
            payload = {
                "activation_id": str(runtime.activation_id),
                "reason": "timeout_proof_authority_lost"
                if not runtime.retained_proof_authority
                else "timeout_effect_proof_failed",
                "schema_version": 1,
            }
            payload_bytes = rfc8785.dumps(payload)
            await connection.execute(
                evidence_records.insert().values(
                    evidence_id=error_id,
                    run_id=runtime.run_id,
                    source="boundary",
                    event_type=EXECUTION_ERROR_EVENT_TYPE,
                    boundary="run",
                    source_event_id=error_id,
                    producer_seq=None,
                    receipt_seq=run.next_receipt_seq,
                    audit_seq=None,
                    caused_by_event_id=activation.activation_evidence_id,
                    payload_schema_version=1,
                    payload=json.loads(payload_bytes),
                    payload_canonical_bytes=payload_bytes,
                    payload_digest=sha256(payload_bytes).hexdigest(),
                    disposition="accepted",
                )
            )
            await connection.execute(
                runs.update()
                .where(runs.c.run_id == runtime.run_id)
                .values(next_receipt_seq=run.next_receipt_seq + 1)
            )
        await connection.execute(
            fault_activations.update()
            .where(fault_activations.c.activation_id == runtime.activation_id)
            .values(**values)
        )
    runtime.settled = True
    runtime.completion_state = (
        "proof_failed"
        if proof_failed or effect_was_unproven
        else "bounded_hold_complete"
    )


class ActivationRuntimeRegistry:
    """Own all live Task 5 tasks until their bounded completion."""

    def __init__(self) -> None:
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._run_ids: dict[UUID, UUID] = {}

    @property
    def live_activation_ids(self) -> frozenset[UUID]:
        return frozenset(self._tasks)

    def start(self, engine: AsyncEngine, runtime: ActivationRuntime) -> asyncio.Task[None]:
        if runtime.activation_id in self._tasks:
            return self._tasks[runtime.activation_id]

        async def run() -> None:
            proof_failed = False
            try:
                await runtime.waiter.wait_until(runtime.client_timeout_boundary_ns, runtime.clock)
                await realize_timeout_effect(engine, runtime)
            except (ResponseGateError, EffectPersistenceError):
                proof_failed = True
            finally:
                await settle_activation_runtime(engine, runtime, proof_failed=proof_failed)

        task = asyncio.create_task(run())
        self._tasks[runtime.activation_id] = task
        self._run_ids[runtime.activation_id] = runtime.run_id

        def discard(_: asyncio.Task[None]) -> None:
            self._tasks.pop(runtime.activation_id, None)
            self._run_ids.pop(runtime.activation_id, None)

        task.add_done_callback(discard)
        return task

    async def wait_for_run(self, run_id: UUID) -> None:
        tasks = [
            task
            for activation_id, task in self._tasks.items()
            if not task.done() and self._run_ids[activation_id] == run_id
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def wait_all(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks)


async def reconcile_abandoned_activation_runtimes(
    engine: AsyncEngine,
    *,
    live_activation_ids: frozenset[UUID] = frozenset(),
) -> int:
    """Seal committed starts whose process-local authority was lost."""
    reconciled = 0
    async with engine.begin() as connection:
        candidate_ids = list(
            (
                await connection.execute(
                    sa.select(fault_activations.c.activation_id)
                    .where(
                        fault_activations.c.reservation_state.in_(
                            ["activation_started", "effect_realized"]
                        ),
                        fault_activations.c.hold_disposition.is_(None),
                    )
                    .order_by(
                        fault_activations.c.run_id,
                        fault_activations.c.activation_ordinal,
                    )
                )
            ).scalars()
        )
        for activation_id in candidate_ids:
            if activation_id in live_activation_ids:
                continue
            run = (
                await connection.execute(
                    sa.select(runs)
                    .where(
                        runs.c.run_id
                        == sa.select(fault_activations.c.run_id)
                        .where(
                            fault_activations.c.activation_id
                            == activation_id
                        )
                        .scalar_subquery()
                    )
                    .with_for_update()
                )
            ).one()
            activation = (
                await connection.execute(
                    sa.select(fault_activations)
                    .where(fault_activations.c.activation_id == activation_id)
                    .with_for_update()
                )
            ).one()
            if activation.hold_disposition is not None:
                continue
            error_id = uuid4()
            payload = {
                "activation_id": str(activation.activation_id),
                "reason": "activation_runtime_lost",
                "schema_version": 1,
            }
            payload_bytes = rfc8785.dumps(payload)
            await connection.execute(
                evidence_records.insert().values(
                    evidence_id=error_id,
                    run_id=activation.run_id,
                    source="boundary",
                    event_type=EXECUTION_ERROR_EVENT_TYPE,
                    boundary="run",
                    source_event_id=error_id,
                    producer_seq=None,
                    receipt_seq=run.next_receipt_seq,
                    audit_seq=None,
                    caused_by_event_id=activation.activation_evidence_id,
                    payload_schema_version=1,
                    payload=json.loads(payload_bytes),
                    payload_canonical_bytes=payload_bytes,
                    payload_digest=sha256(payload_bytes).hexdigest(),
                    disposition="accepted",
                )
            )
            activation_values: dict[str, object] = {
                "hold_disposition": "runtime_lost",
                "runtime_completed_at": datetime.now(timezone.utc),
            }
            if activation.effect_status == "pending":
                activation_values.update(
                    reservation_state="runtime_lost",
                    effect_status="runtime_lost",
                )
            await connection.execute(
                fault_activations.update()
                .where(fault_activations.c.activation_id == activation.activation_id)
                .values(**activation_values)
            )
            await connection.execute(
                runs.update()
                .where(runs.c.run_id == activation.run_id)
                .values(next_receipt_seq=run.next_receipt_seq + 1)
            )
            reconciled += 1
    return reconciled


def _raise_effect_failure(
    configured: EffectFailurePoint | None,
    current: EffectFailurePoint,
) -> None:
    if configured == current:
        raise RuntimeError(f"test failure after {current}")

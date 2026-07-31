"""Atomic registration for the one private Phase 1 tool boundary."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

import rfc8785
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.injection.contract_v1 import (
    CONTRACT_VERSION,
    TOOL_IDENTITY,
    LookupRequest,
    LookupResponse,
    LookupResult,
)
from boundary.persistence.tables import (
    evidence_records,
    fault_activations,
    run_capabilities,
    runs,
    tool_calls,
)


ARRIVAL_EVENT_TYPE = "boundary.tool_call.observed"
ORDINAL_EVENT_TYPE = "boundary.tool_call.ordinal_assigned"
RESPONSE_EVENT_TYPE = "boundary.tool_result.committed"
DUPLICATE_EVENT_TYPE = "boundary.tool_call.duplicate_rejected"
PHASE1_AFFECTED_ORDINALS = (0, 1)
PHASE1_MAXIMUM_ACTIVATIONS = 2

RegistrationFailurePoint = Literal[
    "duplicate_evidence",
    "duplicate_counter",
    "tool_call",
    "activation",
    "evidence",
    "response_evidence",
    "counters",
]
RegistrationOutcome = Literal[
    "no_fault_configured",
    "pre_effect_reserved",
    "attempt_not_selected",
    "maximum_activations_reached",
]


class ToolRegistrationError(Exception):
    """Safe base error for private tool registration."""

    code = "TOOL_REGISTRATION_FAILED"
    http_status = 500


class InvalidCapability(ToolRegistrationError):
    code = "INVALID_CAPABILITY"
    http_status = 401

    def __init__(self) -> None:
        super().__init__("the capability is not valid")


class InactiveCapability(ToolRegistrationError):
    code = "CAPABILITY_INACTIVE"
    http_status = 401

    def __init__(self) -> None:
        super().__init__("the capability is not active")


class CapabilityIdentityMismatch(ToolRegistrationError):
    code = "CAPABILITY_IDENTITY_MISMATCH"
    http_status = 403

    def __init__(self) -> None:
        super().__init__("the capability binding does not match the request")


class ToolRegistrationPersistenceError(ToolRegistrationError):
    def __init__(self) -> None:
        super().__init__("the tool call could not be registered")


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    run_id: UUID
    tool_call_id: UUID
    retry_ordinal: int
    arrival_evidence_id: UUID
    ordinal_evidence_id: UUID
    response_evidence_id: UUID | None
    response_digest: str | None
    response: LookupResponse | None
    outcome: RegistrationOutcome
    pre_effect_reservation_id: UUID | None


@dataclass(frozen=True, slots=True)
class DuplicateRegistration:
    run_id: UUID
    tool_call_id: UUID
    original_retry_ordinal: int
    original_arrival_evidence_id: UUID
    original_ordinal_evidence_id: UUID
    original_pre_effect_reservation_id: UUID | None
    rejection_evidence_id: UUID
    audit_seq: int


async def register_tool_call(
    engine: AsyncEngine,
    *,
    route_run_id: UUID,
    capability_secret: str,
    request: LookupRequest,
    now: datetime | None = None,
    _fail_after: RegistrationFailurePoint | None = None,
) -> RegistrationResult | DuplicateRegistration:
    """Register one call under the run lock and allocate all order atomically.

    A `pre_effect_reserved` fault row is allocation only. It is deliberately
    not `fault_activation_started`: no hold, response gate, or effect proof
    exists until Task 5.
    """
    observed_now = now or datetime.now(timezone.utc)
    if observed_now.tzinfo is None:
        raise ValueError("registration time must be timezone-aware")

    request_document = request.model_dump(mode="json")
    request_bytes = rfc8785.dumps(request_document)
    request_digest = sha256(request_bytes).hexdigest()
    arrival_evidence_id = uuid4()
    ordinal_evidence_id = uuid4()
    response_evidence_id: UUID | None = None
    response_digest: str | None = None
    response: LookupResponse | None = None
    reservation_id: UUID | None = None

    try:
        async with engine.begin() as connection:
            run = (
                await connection.execute(
                    sa.select(runs)
                    .where(runs.c.run_id == route_run_id)
                    .with_for_update()
                )
            ).one_or_none()
            if run is None:
                raise InvalidCapability

            capability = (
                await connection.execute(
                    sa.select(run_capabilities)
                    .where(run_capabilities.c.run_id == route_run_id)
                    .with_for_update()
                )
            ).one_or_none()
            if capability is None:
                raise InvalidCapability

            presented_hash = sha256(
                capability_secret.encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(
                presented_hash,
                capability.capability_hash,
            ):
                raise InvalidCapability
            if (
                capability.state != "active"
                or capability.expires_at <= observed_now
                or run.operational_status not in {"accepted", "running"}
                or not run.evidence_open
            ):
                raise InactiveCapability

            expected_no_fault = run.run_role == "control"
            if (
                request.contract_version != CONTRACT_VERSION
                or request.run_id != route_run_id
                or request.run_id != capability.run_id
                or request.trace_id != run.trace_id
                or request.trace_id != capability.trace_id
                or request.tool_identity != TOOL_IDENTITY
                or capability.tool_identity != TOOL_IDENTITY
                or capability.no_fault_binding != expected_no_fault
                or request.fault_id != capability.fault_id
                or (
                    capability.no_fault_binding
                    and request.fault_id is not None
                )
                or (
                    not capability.no_fault_binding
                    and request.fault_id is None
                )
            ):
                raise CapabilityIdentityMismatch

            original = (
                await connection.execute(
                    sa.select(
                        tool_calls.c.retry_ordinal,
                        tool_calls.c.arrival_evidence_id,
                        tool_calls.c.ordinal_evidence_id,
                        fault_activations.c.activation_id,
                    )
                    .select_from(
                        tool_calls.outerjoin(
                            fault_activations,
                            sa.and_(
                                fault_activations.c.run_id
                                == tool_calls.c.run_id,
                                fault_activations.c.tool_call_id
                                == tool_calls.c.tool_call_id,
                            ),
                        )
                    )
                    .where(
                        tool_calls.c.run_id == route_run_id,
                        tool_calls.c.tool_call_id
                        == request.tool_call_id,
                    )
                )
            ).one_or_none()
            if original is not None:
                rejection_evidence_id = uuid4()
                rejection_payload = {
                    "original_arrival_evidence_id": str(
                        original.arrival_evidence_id
                    ),
                    "original_ordinal_evidence_id": str(
                        original.ordinal_evidence_id
                    ),
                    "original_pre_effect_reservation_id": (
                        str(original.activation_id)
                        if original.activation_id is not None
                        else None
                    ),
                    "original_retry_ordinal": original.retry_ordinal,
                    "reason": "duplicate_tool_call_id",
                    "run_id": str(route_run_id),
                    "schema_version": 1,
                    "tool_call_id": str(request.tool_call_id),
                    "tool_identity": request.tool_identity,
                    "trace_id": str(request.trace_id),
                }
                rejection_bytes = rfc8785.dumps(rejection_payload)
                await connection.execute(
                    evidence_records.insert().values(
                        evidence_id=rejection_evidence_id,
                        run_id=route_run_id,
                        source="boundary",
                        event_type=DUPLICATE_EVENT_TYPE,
                        boundary="tool_execution",
                        source_event_id=rejection_evidence_id,
                        producer_seq=None,
                        receipt_seq=None,
                        audit_seq=run.next_audit_seq,
                        caused_by_event_id=(
                            original.ordinal_evidence_id
                        ),
                        payload_schema_version=1,
                        payload=json.loads(rejection_bytes),
                        payload_canonical_bytes=rejection_bytes,
                        payload_digest=sha256(
                            rejection_bytes
                        ).hexdigest(),
                        disposition="rejected",
                    )
                )
                _raise_for_test_failure(
                    _fail_after,
                    "duplicate_evidence",
                )
                await connection.execute(
                    runs.update()
                    .where(runs.c.run_id == route_run_id)
                    .values(next_audit_seq=run.next_audit_seq + 1)
                )
                _raise_for_test_failure(
                    _fail_after,
                    "duplicate_counter",
                )
                return DuplicateRegistration(
                    run_id=route_run_id,
                    tool_call_id=request.tool_call_id,
                    original_retry_ordinal=original.retry_ordinal,
                    original_arrival_evidence_id=(
                        original.arrival_evidence_id
                    ),
                    original_ordinal_evidence_id=(
                        original.ordinal_evidence_id
                    ),
                    original_pre_effect_reservation_id=(
                        original.activation_id
                    ),
                    rejection_evidence_id=rejection_evidence_id,
                    audit_seq=run.next_audit_seq,
                )

            retry_ordinal = run.next_tool_ordinal
            activation_ordinal: int | None = None
            if capability.no_fault_binding:
                outcome: RegistrationOutcome = "no_fault_configured"
            elif retry_ordinal not in PHASE1_AFFECTED_ORDINALS:
                outcome = "attempt_not_selected"
            else:
                activation_count = await connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(fault_activations)
                    .where(
                        fault_activations.c.run_id == route_run_id,
                        fault_activations.c.fault_id
                        == capability.fault_id,
                    )
                )
                assert activation_count is not None
                if activation_count >= PHASE1_MAXIMUM_ACTIVATIONS:
                    outcome = "maximum_activations_reached"
                else:
                    outcome = "pre_effect_reserved"
                    activation_ordinal = int(activation_count)
                    reservation_id = uuid4()

            if outcome == "no_fault_configured":
                response = LookupResponse(
                    contract_version=CONTRACT_VERSION,
                    run_id=route_run_id,
                    trace_id=request.trace_id,
                    tool_identity=request.tool_identity,
                    tool_call_id=request.tool_call_id,
                    retry_ordinal=retry_ordinal,
                    result=LookupResult(
                        status="found",
                        value="control-ok",
                    ),
                )
                response_bytes = rfc8785.dumps(
                    response.model_dump(mode="json")
                )
                response_digest = sha256(response_bytes).hexdigest()
                response_evidence_id = uuid4()

            await connection.execute(
                tool_calls.insert().values(
                    run_id=route_run_id,
                    tool_call_id=request.tool_call_id,
                    capability_record_id=(
                        capability.capability_record_id
                    ),
                    trace_id=request.trace_id,
                    tool_identity=request.tool_identity,
                    fault_id=capability.fault_id,
                    retry_ordinal=retry_ordinal,
                    request_digest=request_digest,
                    arrival_evidence_id=arrival_evidence_id,
                    ordinal_evidence_id=ordinal_evidence_id,
                    registration_outcome=outcome,
                    response_disposition=(
                        "success_response_committed"
                        if response is not None
                        else None
                    ),
                    response_digest=response_digest,
                    response_evidence_id=response_evidence_id,
                )
            )
            _raise_for_test_failure(_fail_after, "tool_call")

            if reservation_id is not None:
                assert activation_ordinal is not None
                await connection.execute(
                    fault_activations.insert().values(
                        activation_id=reservation_id,
                        run_id=route_run_id,
                        tool_call_id=request.tool_call_id,
                        fault_id=capability.fault_id,
                        activation_ordinal=activation_ordinal,
                        reservation_state="pre_effect_reserved",
                    )
                )
            _raise_for_test_failure(_fail_after, "activation")

            arrival_payload = {
                "contract_version": CONTRACT_VERSION,
                "fault_configured": not capability.no_fault_binding,
                "fault_id": (
                    str(capability.fault_id)
                    if capability.fault_id is not None
                    else None
                ),
                "request_digest": request_digest,
                "schema_version": 1,
                "tool_call_id": str(request.tool_call_id),
                "tool_identity": request.tool_identity,
                "trace_id": str(request.trace_id),
            }
            ordinal_payload = {
                "arrival_event_id": str(arrival_evidence_id),
                "grouping_rule": "one_phase1_tool_operation_per_run",
                "registration_outcome": outcome,
                "retry_ordinal": retry_ordinal,
                "schema_version": 1,
                "tool_call_id": str(request.tool_call_id),
            }
            arrival_bytes = rfc8785.dumps(arrival_payload)
            ordinal_bytes = rfc8785.dumps(ordinal_payload)
            await connection.execute(
                evidence_records.insert(),
                [
                    {
                        "evidence_id": arrival_evidence_id,
                        "run_id": route_run_id,
                        "source": "boundary",
                        "event_type": ARRIVAL_EVENT_TYPE,
                        "boundary": "tool_execution",
                        "source_event_id": arrival_evidence_id,
                        "producer_seq": None,
                        "receipt_seq": run.next_receipt_seq,
                        "caused_by_event_id": None,
                        "payload_schema_version": 1,
                        "payload": json.loads(arrival_bytes),
                        "payload_canonical_bytes": arrival_bytes,
                        "payload_digest": sha256(
                            arrival_bytes
                        ).hexdigest(),
                        "disposition": "accepted",
                    },
                    {
                        "evidence_id": ordinal_evidence_id,
                        "run_id": route_run_id,
                        "source": "boundary",
                        "event_type": ORDINAL_EVENT_TYPE,
                        "boundary": "retry_control",
                        "source_event_id": ordinal_evidence_id,
                        "producer_seq": None,
                        "receipt_seq": run.next_receipt_seq + 1,
                        "caused_by_event_id": arrival_evidence_id,
                        "payload_schema_version": 1,
                        "payload": json.loads(ordinal_bytes),
                        "payload_canonical_bytes": ordinal_bytes,
                        "payload_digest": sha256(
                            ordinal_bytes
                        ).hexdigest(),
                        "disposition": "accepted",
                    },
                ],
            )
            _raise_for_test_failure(_fail_after, "evidence")

            accepted_evidence_count = 2
            if response_evidence_id is not None:
                assert response_digest is not None
                response_payload = {
                    "disposition": "success_response_committed",
                    "response_digest": response_digest,
                    "response_digest_scope": (
                        "canonical_boundary_phase1_lookup_response_v1"
                    ),
                    "retry_ordinal": retry_ordinal,
                    "run_id": str(route_run_id),
                    "schema_version": 1,
                    "tool_call_id": str(request.tool_call_id),
                    "tool_identity": request.tool_identity,
                    "trace_id": str(request.trace_id),
                }
                response_payload_bytes = rfc8785.dumps(response_payload)
                await connection.execute(
                    evidence_records.insert().values(
                        evidence_id=response_evidence_id,
                        run_id=route_run_id,
                        source="boundary",
                        event_type=RESPONSE_EVENT_TYPE,
                        boundary="tool_execution",
                        source_event_id=response_evidence_id,
                        producer_seq=None,
                        receipt_seq=run.next_receipt_seq + 2,
                        caused_by_event_id=ordinal_evidence_id,
                        payload_schema_version=1,
                        payload=json.loads(response_payload_bytes),
                        payload_canonical_bytes=response_payload_bytes,
                        payload_digest=sha256(
                            response_payload_bytes
                        ).hexdigest(),
                        disposition="accepted",
                    )
                )
                accepted_evidence_count = 3
            _raise_for_test_failure(_fail_after, "response_evidence")

            await connection.execute(
                runs.update()
                .where(runs.c.run_id == route_run_id)
                .values(
                    next_tool_ordinal=retry_ordinal + 1,
                    next_receipt_seq=(
                        run.next_receipt_seq + accepted_evidence_count
                    ),
                )
            )
            _raise_for_test_failure(_fail_after, "counters")
    except (
        InvalidCapability,
        InactiveCapability,
        CapabilityIdentityMismatch,
    ):
        raise
    except IntegrityError:
        raise ToolRegistrationPersistenceError from None
    except SQLAlchemyError:
        raise ToolRegistrationPersistenceError from None

    return RegistrationResult(
        run_id=route_run_id,
        tool_call_id=request.tool_call_id,
        retry_ordinal=retry_ordinal,
        arrival_evidence_id=arrival_evidence_id,
        ordinal_evidence_id=ordinal_evidence_id,
        response_evidence_id=response_evidence_id,
        response_digest=response_digest,
        response=response,
        outcome=outcome,
        pre_effect_reservation_id=reservation_id,
    )


def _raise_for_test_failure(
    configured: RegistrationFailurePoint | None,
    current: RegistrationFailurePoint,
) -> None:
    if configured == current:
        raise RuntimeError(f"test failure after {current}")

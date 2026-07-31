"""Create the immutable injected sibling used by Task 5 execution."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

import httpx
import rfc8785
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.evidence.collector import (
    EvidenceInvalid,
    EvidenceLimitExceeded,
    IdentityMismatch,
    record_safe_rejection,
)
from boundary.injection.capability import CAPABILITY_BYTES, CapabilityGrant
from boundary.injection.contract_v1 import TOOL_IDENTITY
from boundary.injection.fault_spec import (
    FAULT_SPEC_V1_ID,
    FaultDefinitionMismatch,
    validate_phase1_fault_document,
)
from boundary.persistence.tables import (
    evidence_records,
    fault_activations,
    run_capabilities,
    runs,
    tool_calls,
)
from boundary.sut.client import (
    InvalidWireResponse,
    SutClient,
    SutRemoteError,
    SutTransportError,
)
from boundary.sut.contract_v1 import (
    CONTRACT_VERSION,
    TestRunRequest,
    TestedInput,
)


INJECTED_ACCEPTED_EVENT_TYPE = "boundary.run.injected_sibling_accepted"
VULNERABLE_AGENT_VERSION = "vulnerable-v1"
FIXED_AGENT_VERSION = "fixed-v1"
SiblingFailurePoint = Literal["run", "evidence", "capability"]


class InjectedSiblingError(Exception):
    """A safe pre-invocation injected-sibling creation failure."""


class InjectedExecutionError(Exception):
    """A safe operational failure without any policy conclusion."""


@dataclass(frozen=True, slots=True)
class InjectedSibling:
    control_run_id: UUID
    campaign_id: UUID
    run_id: UUID
    trace_id: UUID
    fault_spec_id: UUID
    fault_id: UUID
    accepted_evidence_id: UUID
    capability: CapabilityGrant


async def bind_control_tested_input(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    tested_input: str,
) -> str:
    """Freeze the canonical tested input before the control is invoked."""
    if not isinstance(tested_input, str) or not tested_input or len(tested_input) > 4096:
        raise ValueError("tested input is invalid")
    document = {"query": tested_input}
    canonical_bytes = rfc8785.dumps(document)
    digest = sha256(canonical_bytes).hexdigest()
    async with engine.begin() as connection:
        run = (
            await connection.execute(
                sa.select(runs)
                .where(runs.c.run_id == run_id)
                .with_for_update()
            )
        ).one_or_none()
        if run is None or run.run_role != "control" or run.operational_status != "accepted":
            raise ValueError("run is not an accepted control")
        if run.tested_input_digest is not None:
            if (
                run.tested_input != document
                or run.tested_input_bytes != canonical_bytes
                or run.tested_input_digest != digest
            ):
                raise ValueError("tested input is already bound differently")
            return digest
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == run_id)
            .values(
                tested_input=json.loads(canonical_bytes),
                tested_input_bytes=canonical_bytes,
                tested_input_digest=digest,
            )
        )
    return digest


async def create_injected_sibling(
    engine: AsyncEngine,
    *,
    control_run_id: UUID,
    expires_at: datetime,
    _fail_after: SiblingFailurePoint | None = None,
) -> InjectedSibling:
    """Atomically create a fresh injected run and fault-bound capability."""
    if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
        raise InjectedSiblingError("capability expiry must be in the future")

    run_id = uuid4()
    trace_id = uuid4()
    fault_id = uuid4()
    evidence_id = uuid4()
    capability_record_id = uuid4()
    capability_secret = secrets.token_urlsafe(CAPABILITY_BYTES)
    capability_hash = sha256(capability_secret.encode("ascii")).hexdigest()

    try:
        async with engine.begin() as connection:
            control = (
                await connection.execute(
                    sa.select(runs)
                    .where(runs.c.run_id == control_run_id)
                    .with_for_update()
                )
            ).one_or_none()
            if control is None:
                raise InjectedSiblingError("control run does not exist")
            if control.run_role != "control" or control.operational_status != "completed":
                raise InjectedSiblingError("control run is not a successful control")
            if (
                control.reported_tested_agent_id != control.expected_tested_agent_id
                or control.reported_tested_agent_version
                != control.expected_tested_agent_version
            ):
                raise InjectedSiblingError("control tested-agent identity is not valid")
            if (
                control.tested_input is None
                or control.tested_input_bytes is None
                or control.tested_input_digest is None
            ):
                raise InjectedSiblingError("control tested input is not frozen")
            input_bytes = rfc8785.dumps(control.tested_input)
            if (
                input_bytes != control.tested_input_bytes
                or sha256(input_bytes).hexdigest() != control.tested_input_digest
            ):
                raise InjectedSiblingError("control tested input is not canonical")
            try:
                validate_phase1_fault_document(
                    control.run_definition,
                    control.run_definition_bytes,
                    control.run_definition_digest,
                )
            except FaultDefinitionMismatch as error:
                raise InjectedSiblingError("control fault definition is invalid") from error
            activation_count = await connection.scalar(
                sa.select(sa.func.count())
                .select_from(fault_activations)
                .where(fault_activations.c.run_id == control_run_id)
            )
            if activation_count:
                raise InjectedSiblingError("control contains fault activation state")
            control_calls = (
                await connection.execute(
                    sa.select(tool_calls).where(
                        tool_calls.c.run_id == control_run_id
                    )
                )
            ).all()
            if (
                len(control_calls) != 1
                or control_calls[0].retry_ordinal != 0
                or control_calls[0].registration_outcome
                != "no_fault_configured"
                or control_calls[0].response_disposition
                != "success_response_committed"
            ):
                raise InjectedSiblingError(
                    "control did not complete the deterministic normal tool path"
                )
            control_capability_state = await connection.scalar(
                sa.select(run_capabilities.c.state).where(
                    run_capabilities.c.run_id == control_run_id
                )
            )
            if control_capability_state != "retired":
                raise InjectedSiblingError("control capability is not retired")

            evidence_payload = {
                "campaign_id": str(control.campaign_id),
                "control_run_id": str(control_run_id),
                "fault_id": str(fault_id),
                "fault_spec_id": str(FAULT_SPEC_V1_ID),
                "from_status": None,
                "run_id": str(run_id),
                "schema_version": 1,
                "to_status": "accepted",
                "trace_id": str(trace_id),
                "transition": "injected_sibling_accepted",
            }
            evidence_bytes = rfc8785.dumps(evidence_payload)
            await connection.execute(
                runs.insert().values(
                    run_id=run_id,
                    trace_id=trace_id,
                    campaign_id=control.campaign_id,
                    control_run_id=control_run_id,
                    run_role="injected",
                    fault_spec_id=FAULT_SPEC_V1_ID,
                    fault_id=fault_id,
                    contract_version=control.contract_version,
                    scenario_id=control.scenario_id,
                    scenario_version=control.scenario_version,
                    expected_tested_agent_id=control.expected_tested_agent_id,
                    expected_tested_agent_version=control.expected_tested_agent_version,
                    reported_tested_agent_id=None,
                    reported_tested_agent_version=None,
                    operational_status="accepted",
                    definition_schema_version=control.definition_schema_version,
                    run_definition=json.loads(control.run_definition_bytes),
                    run_definition_bytes=control.run_definition_bytes,
                    run_definition_digest=control.run_definition_digest,
                    tested_input=json.loads(control.tested_input_bytes),
                    tested_input_bytes=control.tested_input_bytes,
                    tested_input_digest=control.tested_input_digest,
                    target_producer_cursor=0,
                    target_final_watermark=None,
                    next_receipt_seq=2,
                    next_audit_seq=1,
                    next_tool_ordinal=0,
                    evidence_open=True,
                )
            )
            _raise_sibling_failure(_fail_after, "run")
            await connection.execute(
                evidence_records.insert().values(
                    evidence_id=evidence_id,
                    run_id=run_id,
                    source="boundary",
                    event_type=INJECTED_ACCEPTED_EVENT_TYPE,
                    boundary="run",
                    source_event_id=evidence_id,
                    producer_seq=None,
                    receipt_seq=1,
                    audit_seq=None,
                    caused_by_event_id=None,
                    payload_schema_version=1,
                    payload=json.loads(evidence_bytes),
                    payload_canonical_bytes=evidence_bytes,
                    payload_digest=sha256(evidence_bytes).hexdigest(),
                    disposition="accepted",
                )
            )
            _raise_sibling_failure(_fail_after, "evidence")
            await connection.execute(
                run_capabilities.insert().values(
                    capability_record_id=capability_record_id,
                    capability_hash=capability_hash,
                    run_id=run_id,
                    trace_id=trace_id,
                    tool_identity=TOOL_IDENTITY,
                    no_fault_binding=False,
                    fault_id=fault_id,
                    expires_at=expires_at,
                    state="active",
                    retired_at=None,
                )
            )
            _raise_sibling_failure(_fail_after, "capability")
    except InjectedSiblingError:
        raise
    except IntegrityError:
        raise InjectedSiblingError("injected sibling identity conflicted") from None
    except SQLAlchemyError:
        raise InjectedSiblingError("injected sibling persistence failed") from None

    return InjectedSibling(
        control_run_id=control_run_id,
        campaign_id=control.campaign_id,
        run_id=run_id,
        trace_id=trace_id,
        fault_spec_id=FAULT_SPEC_V1_ID,
        fault_id=fault_id,
        accepted_evidence_id=evidence_id,
        capability=CapabilityGrant(
            capability_record_id=capability_record_id,
            capability_secret=capability_secret,
        ),
    )


async def execute_injected_run(
    engine: AsyncEngine,
    *,
    sibling: InjectedSibling,
    sut_base_url: str,
    tool_endpoint: str,
    execution_budget_ms: int = 30_000,
    poll_interval_ms: int = 100,
    http_timeout_seconds: float = 5.0,
    http_client: httpx.AsyncClient | None = None,
    clock=None,
    sut_client=None,
):
    """Invoke a prepared injected sibling and wait for every hold to settle."""
    from boundary.execution.control import (
        DEFAULT_CANCELLATION_GRACE_MS,
        ControlExecutionError,
        RunDeadlineReached,
        _AsyncioClock,
        _CollectionState,
        _cancel_after_deadline,
        _execute_before_deadline,
        _terminal_failure,
    )

    async with engine.connect() as connection:
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == sibling.run_id)
            )
        ).one_or_none()
    if run is None or run.run_role != "injected" or run.operational_status != "accepted":
        raise InjectedExecutionError("run is not an accepted injected sibling")
    if (
        run.control_run_id != sibling.control_run_id
        or run.trace_id != sibling.trace_id
        or run.fault_spec_id != sibling.fault_spec_id
        or run.fault_id != sibling.fault_id
    ):
        raise InjectedExecutionError("injected sibling identity is inconsistent")
    try:
        validate_phase1_fault_document(
            run.run_definition,
            run.run_definition_bytes,
            run.run_definition_digest,
        )
    except FaultDefinitionMismatch as error:
        raise InjectedExecutionError("fault definition validation failed") from error
    if run.tested_input is None or run.tested_input.get("query") is None:
        raise InjectedExecutionError("tested input is not frozen")

    request = TestRunRequest(
        contract_version=CONTRACT_VERSION,
        campaign_id=run.campaign_id,
        scenario_id=run.scenario_id,
        scenario_version=run.scenario_version,
        run_id=run.run_id,
        trace_id=run.trace_id,
        tested_agent_id=run.expected_tested_agent_id,
        tested_agent_version=run.expected_tested_agent_version,
        tested_input=TestedInput(query=run.tested_input["query"]),
        execution_budget_ms=execution_budget_ms,
        tool_endpoint=tool_endpoint,
        tool_capability=sibling.capability.capability_secret,
        fault_spec_id=run.fault_spec_id,
        fault_id=run.fault_id,
    )
    selected_clock = clock or _AsyncioClock()
    deadline = selected_clock.monotonic() + execution_budget_ms / 1000
    if run.expected_tested_agent_version not in {
        VULNERABLE_AGENT_VERSION,
        FIXED_AGENT_VERSION,
    }:
        raise InjectedExecutionError("tested-agent version is unsupported")
    owns_client = sut_client is None
    client = sut_client or SutClient(
        sut_base_url,
        timeout_seconds=http_timeout_seconds,
        client=http_client,
        forbidden_values=(sibling.capability.capability_secret,),
    )
    state = _CollectionState()
    expected_outcome = (
        "degraded"
        if run.expected_tested_agent_version == FIXED_AGENT_VERSION
        else "success"
    )
    try:
        try:
            result = await _execute_before_deadline(
                engine,
                run=run,
                request=request,
                client=client,
                clock=selected_clock,
                deadline=deadline,
                poll_interval_ms=poll_interval_ms,
                http_timeout_seconds=http_timeout_seconds,
                capability_record_id=sibling.capability.capability_record_id,
                state=state,
                expected_outcome_kind=expected_outcome,
            )
        except RunDeadlineReached:
            result = await _cancel_after_deadline(
                engine,
                run=run,
                client=client,
                clock=selected_clock,
                run_deadline=deadline,
                execution_budget_ms=execution_budget_ms,
                cancellation_grace_ms=DEFAULT_CANCELLATION_GRACE_MS,
                poll_interval_ms=poll_interval_ms,
                http_timeout_seconds=http_timeout_seconds,
                capability_record_id=sibling.capability.capability_record_id,
                state=state,
            )
        await _wait_for_activation_settlement(
            engine,
            run_id=run.run_id,
            clock=selected_clock,
        )
        return result
    except ControlExecutionError as error:
        raise InjectedExecutionError(
            "injected execution did not complete normally"
        ) from error
    except EvidenceLimitExceeded as error:
        await record_safe_rejection(
            engine,
            run_id=run.run_id,
            category=error.code,
            raw_bytes=error.raw_bytes,
        )
        await _terminal_failure(
            engine,
            run.run_id,
            sibling.capability.capability_record_id,
            "invalid",
            error.code,
        )
        raise InjectedExecutionError("injected evidence limit failed") from error
    except (IdentityMismatch, EvidenceInvalid) as error:
        await _terminal_failure(
            engine,
            run.run_id,
            sibling.capability.capability_record_id,
            "invalid",
            error.code,
        )
        raise InjectedExecutionError("injected evidence was invalid") from error
    except InvalidWireResponse as error:
        await record_safe_rejection(
            engine,
            run_id=run.run_id,
            category=error.reason,
            raw_bytes=error.raw_bytes,
        )
        await _terminal_failure(
            engine,
            run.run_id,
            sibling.capability.capability_record_id,
            "invalid",
            error.code,
        )
        raise InjectedExecutionError("injected target response was invalid") from error
    except SutRemoteError as error:
        await _terminal_failure(
            engine,
            run.run_id,
            sibling.capability.capability_record_id,
            "invalid",
            error.problem.error.code,
        )
        raise InjectedExecutionError("injected target rejected execution") from error
    except SutTransportError as error:
        await _terminal_failure(
            engine,
            run.run_id,
            sibling.capability.capability_record_id,
            "failed",
            error.code,
        )
        raise InjectedExecutionError("injected target transport failed") from error
    finally:
        if owns_client:
            await client.aclose()


async def _wait_for_activation_settlement(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    clock,
) -> None:
    deadline = clock.monotonic() + 2.5
    while True:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    sa.select(fault_activations)
                    .where(fault_activations.c.run_id == run_id)
                    .order_by(fault_activations.c.activation_ordinal)
                )
            ).all()
        if len(rows) == 2 and all(
            row.effect_status in {"effect_realized", "unproven", "runtime_lost"}
            and row.hold_disposition is not None
            for row in rows
        ):
            if any(
                row.effect_status != "effect_realized"
                or row.hold_disposition != "bounded_hold_complete"
                for row in rows
            ):
                raise InjectedExecutionError(
                    "an activation runtime did not settle normally"
                )
            return
        if clock.monotonic() >= deadline:
            raise InjectedExecutionError("activation runtimes did not settle")
        await clock.sleep(min(0.05, max(0.0, deadline - clock.monotonic())))


def _raise_sibling_failure(
    configured: SiblingFailurePoint | None,
    current: SiblingFailurePoint,
) -> None:
    if configured == current:
        raise RuntimeError(f"test failure after injected sibling {current}")

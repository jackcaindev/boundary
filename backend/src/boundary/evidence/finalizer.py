"""Atomic single-finalization for one authoritative run evidence set."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

import rfc8785
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from boundary.domain.evidence import (
    CapabilityBinding,
    CutoffMarker,
    EvidenceManifestV1,
    EvidenceReference,
)
from boundary.evidence.activation_manifest import (
    ActivationBindingError,
    build_timeout_activation_bindings,
)
from boundary.persistence.tables import (
    evidence_records,
    evidence_sets,
    fault_activations,
    run_capabilities,
    runs,
    tool_calls,
)


FINALIZER_IDENTITY = "boundary.phase1.evidence-finalizer/v1"
CutoffReason = Literal[
    "target_terminal_watermark",
    "evidence_deadline",
    "cancellation_grace",
    "reconciliation_error",
]
FinalizationFailurePoint = Literal["evidence_closed", "evidence_set"]


class FinalizationError(Exception):
    """Safe base error for finalization failures."""


class FinalizationNotReady(FinalizationError):
    """The cutoff or Boundary-owned work is not durably settled."""


class FinalizationConflict(FinalizationError):
    """A committed run evidence set has different normalized content."""


class FinalizationPersistenceError(FinalizationError):
    """PostgreSQL could not commit the atomic finalization."""


@dataclass(frozen=True, slots=True)
class FinalizedEvidenceSet:
    evidence_set_id: UUID
    run_id: UUID
    cutoff_reason: CutoffReason
    target_final_watermark: int | None
    manifest: EvidenceManifestV1
    canonical_bytes: bytes
    evidence_set_digest: str
    replayed: bool


def canonicalize_manifest(manifest: EvidenceManifestV1) -> tuple[bytes, str]:
    """Validate and RFC 8785-canonicalize the exact manifest document."""
    validated = EvidenceManifestV1.model_validate(
        manifest.model_dump(mode="python")
    )
    canonical_bytes = rfc8785.dumps(validated.model_dump(mode="json"))
    return canonical_bytes, sha256(canonical_bytes).hexdigest()


async def finalize_run_evidence(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    cutoff_reason: CutoffReason = "target_terminal_watermark",
    _fail_after: FinalizationFailurePoint | None = None,
) -> FinalizedEvidenceSet:
    """Close acceptance and insert or verify the run's one evidence set."""
    if cutoff_reason not in {
        "target_terminal_watermark",
        "evidence_deadline",
        "cancellation_grace",
        "reconciliation_error",
    }:
        raise ValueError("cutoff reason is unsupported")

    generated_evidence_set_id = uuid4()
    try:
        async with engine.begin() as connection:
            run = (
                await connection.execute(
                    sa.select(runs)
                    .where(runs.c.run_id == run_id)
                    .with_for_update()
                )
            ).one_or_none()
            if run is None:
                raise FinalizationNotReady("run does not exist")

            existing = (
                await connection.execute(
                    sa.select(evidence_sets).where(
                        evidence_sets.c.run_id == run_id
                    )
                )
            ).one_or_none()
            evidence_set_id = (
                existing.evidence_set_id
                if existing is not None
                else generated_evidence_set_id
            )

            if (
                existing is not None
                and existing.cutoff_reason != cutoff_reason
            ):
                raise FinalizationConflict(
                    "committed evidence set has a different cutoff reason"
                )

            await _verify_cutoff(connection, run, cutoff_reason)
            await _verify_boundary_work_settled(
                connection,
                run,
                cutoff_reason=cutoff_reason,
            )
            manifest = await _build_manifest(
                connection,
                run=run,
                evidence_set_id=evidence_set_id,
                cutoff_reason=cutoff_reason,
            )
            canonical_bytes, digest = canonicalize_manifest(manifest)

            if existing is not None:
                if (
                    existing.cutoff_reason != cutoff_reason
                    or existing.target_final_watermark
                    != run.target_final_watermark
                    or existing.manifest_schema_version != 1
                    or existing.finalizer_identity != FINALIZER_IDENTITY
                    or existing.manifest_canonical_bytes != canonical_bytes
                    or existing.evidence_set_digest != digest
                    or rfc8785.dumps(existing.manifest) != canonical_bytes
                ):
                    raise FinalizationConflict(
                        "committed evidence set conflicts with normalized content"
                    )
                return FinalizedEvidenceSet(
                    evidence_set_id=existing.evidence_set_id,
                    run_id=run_id,
                    cutoff_reason=cutoff_reason,
                    target_final_watermark=existing.target_final_watermark,
                    manifest=manifest,
                    canonical_bytes=canonical_bytes,
                    evidence_set_digest=digest,
                    replayed=True,
                )

            if not run.evidence_open:
                raise FinalizationConflict(
                    "evidence is closed without an evidence-set record"
                )
            await connection.execute(
                runs.update()
                .where(runs.c.run_id == run_id)
                .values(evidence_open=False)
            )
            _raise_for_test_failure(_fail_after, "evidence_closed")
            await connection.execute(
                evidence_sets.insert().values(
                    evidence_set_id=evidence_set_id,
                    run_id=run_id,
                    manifest_schema_version=1,
                    cutoff_reason=cutoff_reason,
                    target_final_watermark=run.target_final_watermark,
                    manifest=json.loads(canonical_bytes),
                    manifest_canonical_bytes=canonical_bytes,
                    evidence_set_digest=digest,
                    finalizer_identity=FINALIZER_IDENTITY,
                )
            )
            _raise_for_test_failure(_fail_after, "evidence_set")
    except (FinalizationNotReady, FinalizationConflict):
        raise
    except (IntegrityError, SQLAlchemyError):
        raise FinalizationPersistenceError(
            "evidence finalization persistence failed"
        ) from None

    return FinalizedEvidenceSet(
        evidence_set_id=evidence_set_id,
        run_id=run_id,
        cutoff_reason=cutoff_reason,
        target_final_watermark=run.target_final_watermark,
        manifest=manifest,
        canonical_bytes=canonical_bytes,
        evidence_set_digest=digest,
        replayed=False,
    )


async def _verify_cutoff(
    connection: AsyncConnection,
    run,
    cutoff_reason: CutoffReason,
) -> None:
    if run.operational_status not in {
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "invalid",
    }:
        raise FinalizationNotReady("run is not operationally terminal")
    if cutoff_reason == "target_terminal_watermark":
        if run.operational_status not in {"completed", "failed", "cancelled"}:
            raise FinalizationNotReady(
                "terminal-watermark cutoff requires a target terminal state"
            )
        if (
            run.target_final_watermark is None
            or run.target_producer_cursor != run.target_final_watermark
        ):
            raise FinalizationNotReady(
                "target terminal watermark has not been fully collected"
            )
        required_types = {
            "boundary.sut_terminal.observed",
            "boundary.run.terminal",
        }
        present = set(
            (
                await connection.execute(
                    sa.select(evidence_records.c.event_type).where(
                        evidence_records.c.run_id == run.run_id,
                        evidence_records.c.disposition == "accepted",
                        evidence_records.c.event_type.in_(required_types),
                    )
                )
            ).scalars()
        )
        if present != required_types:
            raise FinalizationNotReady(
                "terminal cutoff evidence is not complete"
            )
    elif cutoff_reason == "evidence_deadline":
        if run.operational_status not in {
            "failed",
            "cancelled",
            "timed_out",
            "invalid",
        }:
            raise FinalizationNotReady(
                "evidence-deadline cutoff requires a non-success terminal state"
            )
        await _verify_deadline_proof(connection, run)
    elif cutoff_reason == "cancellation_grace":
        if run.operational_status not in {"cancelled", "timed_out"}:
            raise FinalizationNotReady(
                "cancellation-grace cutoff requires cancellation terminal state"
            )
        cancellation_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == run.run_id,
                evidence_records.c.source == "boundary",
                evidence_records.c.disposition == "accepted",
                evidence_records.c.event_type.in_(
                    [
                        "boundary.campaign_cancellation.requested",
                        "boundary.cancellation.requested",
                    ]
                ),
            )
        )
        if not cancellation_count:
            raise FinalizationNotReady(
                "cancellation-grace cutoff lacks cancellation evidence"
            )
    else:
        if run.operational_status not in {"failed", "invalid"}:
            raise FinalizationNotReady(
                "reconciliation-error cutoff requires failed or invalid state"
            )
        reconciliation_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == run.run_id,
                evidence_records.c.source == "boundary",
                evidence_records.c.disposition == "accepted",
                evidence_records.c.event_type
                == "boundary.reconciliation.execution_error",
            )
        )
        if not reconciliation_count:
            raise FinalizationNotReady(
                "reconciliation-error cutoff lacks reconciliation evidence"
            )


async def _verify_deadline_proof(connection: AsyncConnection, run) -> None:
    accepted = (
        await connection.execute(
            sa.select(evidence_records).where(
                evidence_records.c.run_id == run.run_id,
                evidence_records.c.source == "boundary",
                evidence_records.c.disposition == "accepted",
                evidence_records.c.event_type.in_(
                    [
                        "boundary.run_budget.bound",
                        "boundary.deadline.reached",
                    ]
                ),
            )
        )
    ).all()
    budgets = [
        row
        for row in accepted
        if row.event_type == "boundary.run_budget.bound"
    ]
    deadlines = [
        row
        for row in accepted
        if row.event_type == "boundary.deadline.reached"
    ]
    if len(budgets) != 1 or len(deadlines) != 1:
        raise FinalizationNotReady(
            "evidence-deadline cutoff lacks authoritative deadline proof"
        )
    budget = budgets[0]
    deadline = deadlines[0]
    budget_payload = budget.payload
    deadline_payload = deadline.payload
    try:
        budget_ms = int(budget_payload["execution_budget_ms"])
        budget_started = int(budget_payload["budget_started_monotonic_ns"])
        budget_deadline = int(budget_payload["deadline_monotonic_ns"])
        observed = int(deadline_payload["observed_monotonic_ns"])
    except (KeyError, TypeError, ValueError):
        raise FinalizationNotReady("deadline proof is malformed") from None
    if (
        budget_payload.get("run_id") != str(run.run_id)
        or budget_payload.get("trace_id") != str(run.trace_id)
        or budget_payload.get("relationship")
        != "bound_before_target_invocation"
        or budget_payload.get("timing_authority") != "boundary_monotonic"
        or not 0 < budget_ms <= 30_000
        or budget_deadline - budget_started != budget_ms * 1_000_000
        or deadline.caused_by_event_id != budget.evidence_id
        or deadline.receipt_seq <= budget.receipt_seq
        or deadline_payload.get("budget_event_id") != str(budget.evidence_id)
        or deadline_payload.get("run_id") != str(run.run_id)
        or deadline_payload.get("trace_id") != str(run.trace_id)
        or deadline_payload.get("execution_budget_ms") != budget_ms
        or deadline_payload.get("deadline_monotonic_ns") != budget_deadline
        or deadline_payload.get("relationship")
        != "observed_at_or_after_deadline"
        or deadline_payload.get("timing_authority") != "boundary_monotonic"
        or observed < budget_deadline
    ):
        raise FinalizationNotReady(
            "evidence-deadline cutoff proof is contradictory"
        )


async def _verify_boundary_work_settled(
    connection: AsyncConnection,
    run,
    *,
    cutoff_reason: CutoffReason,
) -> None:
    capability = (
        await connection.execute(
            sa.select(run_capabilities).where(
                run_capabilities.c.run_id == run.run_id
            )
        )
    ).one_or_none()
    if capability is not None:
        expected_no_fault = run.run_role == "control"
        if capability.state != "retired":
            raise FinalizationNotReady("run capability is not retired")
        if (
            capability.trace_id != run.trace_id
            or capability.tool_identity != "boundary.phase1.lookup"
            or capability.no_fault_binding != expected_no_fault
            or capability.fault_id != run.fault_id
        ):
            raise FinalizationNotReady(
                "run capability binding is inconsistent"
            )

    activations = (
        await connection.execute(
            sa.select(fault_activations).where(
                fault_activations.c.run_id == run.run_id
            )
        )
    ).all()
    activation_by_call = {row.tool_call_id: row for row in activations}
    for activation in activations:
        if (
            cutoff_reason == "reconciliation_error"
            and activation.reservation_state == "pre_effect_reserved"
            and activation.effect_status == "not_started"
            and activation.hold_disposition is None
        ):
            continue
        if (
            activation.reservation_state
            in {"pre_effect_reserved", "activation_started"}
            or activation.effect_status in {"not_started", "pending"}
            or activation.hold_disposition is None
        ):
            raise FinalizationNotReady(
                "fault activation effect or hold disposition is unsettled"
            )

    calls = (
        await connection.execute(
            sa.select(tool_calls).where(tool_calls.c.run_id == run.run_id)
        )
    ).all()
    for call in calls:
        if call.response_disposition == "success_response_committed":
            continue
        activation = activation_by_call.get(call.tool_call_id)
        unstarted_reconciliation = (
            cutoff_reason == "reconciliation_error"
            and activation is not None
            and activation.reservation_state == "pre_effect_reserved"
            and activation.effect_status == "not_started"
            and activation.hold_disposition is None
        )
        if activation is None or (
            activation.hold_disposition is None
            and not unstarted_reconciliation
        ):
            raise FinalizationNotReady("tool response disposition is unsettled")


async def _build_manifest(
    connection: AsyncConnection,
    *,
    run,
    evidence_set_id: UUID,
    cutoff_reason: CutoffReason,
) -> EvidenceManifestV1:
    capability = (
        await connection.execute(
            sa.select(run_capabilities).where(
                run_capabilities.c.run_id == run.run_id
            )
        )
    ).one_or_none()
    accepted_rows = (
        await connection.execute(
            sa.select(evidence_records)
            .where(
                evidence_records.c.run_id == run.run_id,
                evidence_records.c.disposition == "accepted",
            )
            .order_by(evidence_records.c.receipt_seq)
        )
    ).all()
    receipts = [row.receipt_seq for row in accepted_rows]
    if receipts != list(range(1, len(receipts) + 1)):
        raise FinalizationNotReady(
            "accepted evidence receipt order is not contiguous"
        )
    references: list[EvidenceReference] = []
    for row in accepted_rows:
        canonical = rfc8785.dumps(row.payload)
        if (
            canonical != row.payload_canonical_bytes
            or sha256(canonical).hexdigest() != row.payload_digest
        ):
            raise FinalizationNotReady(
                "accepted evidence content failed digest verification"
            )
        references.append(
            EvidenceReference(
                evidence_id=row.evidence_id,
                source=row.source,
                event_type=row.event_type,
                boundary=row.boundary,
                source_event_id=row.source_event_id,
                producer_seq=row.producer_seq,
                receipt_seq=row.receipt_seq,
                caused_by_event_id=row.caused_by_event_id,
                payload_schema_version=row.payload_schema_version,
                content_digest=row.payload_digest,
            )
        )

    markers = await _cutoff_markers(
        connection,
        run=run,
        cutoff_reason=cutoff_reason,
        accepted_rows=accepted_rows,
    )
    try:
        timeout_activations = await build_timeout_activation_bindings(
            connection,
            run_id=run.run_id,
            evidence_by_id={row.evidence_id: row for row in accepted_rows},
            allow_unstarted_reservations=(
                cutoff_reason == "reconciliation_error"
            ),
        )
    except (ActivationBindingError, ValueError, TypeError):
        raise FinalizationNotReady(
            "settled activation proof cannot be canonically bound"
        ) from None
    return EvidenceManifestV1(
        schema_version=1,
        evidence_set_id=evidence_set_id,
        run_id=run.run_id,
        trace_id=run.trace_id,
        run_role=run.run_role,
        contract_version=run.contract_version,
        scenario_id=run.scenario_id,
        scenario_version=run.scenario_version,
        expected_tested_agent_id=run.expected_tested_agent_id,
        expected_tested_agent_version=run.expected_tested_agent_version,
        reported_tested_agent_id=run.reported_tested_agent_id,
        reported_tested_agent_version=run.reported_tested_agent_version,
        operational_status=run.operational_status,
        control_run_id=run.control_run_id,
        fault_spec_id=run.fault_spec_id,
        fault_spec_digest=(
            run.run_definition_digest if run.fault_spec_id is not None else None
        ),
        fault_id=run.fault_id,
        capability_binding=(
            CapabilityBinding(
                capability_record_id=capability.capability_record_id,
                trace_id=capability.trace_id,
                tool_identity=capability.tool_identity,
                no_fault_binding=capability.no_fault_binding,
                fault_id=capability.fault_id,
                state=capability.state,
            )
            if capability is not None
            else None
        ),
        cutoff_reason=cutoff_reason,
        target_producer_cursor=run.target_producer_cursor,
        target_final_watermark=run.target_final_watermark,
        accepted_evidence=references,
        timeout_activations=timeout_activations,
        cutoff_markers=markers,
        finalizer_identity=FINALIZER_IDENTITY,
    )


async def _cutoff_markers(
    connection: AsyncConnection,
    *,
    run,
    cutoff_reason: CutoffReason,
    accepted_rows,
) -> list[CutoffMarker]:
    markers: list[CutoffMarker] = []
    rejected = (
        await connection.execute(
            sa.select(evidence_records)
            .where(
                evidence_records.c.run_id == run.run_id,
                evidence_records.c.disposition == "rejected",
            )
            .order_by(evidence_records.c.audit_seq)
        )
    ).all()
    for row in rejected:
        reason = row.payload.get("category", "rejected_evidence")
        markers.append(
            CutoffMarker(
                marker_type="rejection",
                reason_code=str(reason or "rejected_evidence")[:256],
                evidence_id=row.evidence_id,
                audit_seq=row.audit_seq,
                content_digest=row.payload_digest,
            )
        )

    for row in accepted_rows:
        if row.event_type in {
            "boundary.execution.error",
            "boundary.reconciliation.execution_error",
            "sut.run.failed",
        }:
            reason = row.payload.get("reason") or row.payload.get("reason_code")
            if reason is None and isinstance(row.payload.get("payload"), dict):
                reason = row.payload["payload"].get("error_code")
            markers.append(
                CutoffMarker(
                    marker_type="failure",
                    reason_code=str(reason or row.event_type)[:256],
                    evidence_id=row.evidence_id,
                    content_digest=row.payload_digest,
                )
            )

    if cutoff_reason == "evidence_deadline":
        deadline_row = next(
            (
                row
                for row in accepted_rows
                if row.event_type
                in {
                    "boundary.deadline.reached",
                    "boundary.cancellation.requested",
                }
            ),
            None,
        )
        markers.append(
            CutoffMarker(
                marker_type="deadline",
                reason_code="evidence_deadline_expired",
                evidence_id=(
                    deadline_row.evidence_id if deadline_row is not None else None
                ),
                content_digest=(
                    deadline_row.payload_digest if deadline_row is not None else None
                ),
            )
        )
        if (
            run.target_final_watermark is not None
            and run.target_producer_cursor < run.target_final_watermark
        ):
            markers.append(
                CutoffMarker(
                    marker_type="gap",
                    reason_code="target_producer_gap_at_cutoff",
                    missing_producer_seq_start=run.target_producer_cursor + 1,
                    missing_producer_seq_end=run.target_final_watermark,
                )
            )
    return markers


def _raise_for_test_failure(
    configured: FinalizationFailurePoint | None,
    current: FinalizationFailurePoint,
) -> None:
    if configured == current:
        raise RuntimeError(f"test failure after finalization {current}")

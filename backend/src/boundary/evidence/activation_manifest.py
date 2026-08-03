"""Canonical bindings for settled Task 5 activation/effect rows."""

from __future__ import annotations

from hashlib import sha256
import json

import rfc8785
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from boundary.domain.evidence import (
    FinalizedTimeoutActivation,
    TimeoutEffectProof,
)
from boundary.persistence.tables import fault_activations, tool_calls


class ActivationBindingError(ValueError):
    pass


async def build_timeout_activation_bindings(
    connection: AsyncConnection,
    *,
    run_id,
    evidence_by_id: dict,
) -> list[FinalizedTimeoutActivation]:
    """Copy every settled authoritative activation fact into the manifest."""
    rows = (
        await connection.execute(
            sa.select(fault_activations)
            .where(fault_activations.c.run_id == run_id)
            .order_by(fault_activations.c.activation_ordinal)
        )
    ).all()
    calls = (
        await connection.execute(
            sa.select(tool_calls).where(tool_calls.c.run_id == run_id)
        )
    ).all()
    calls_by_id = {row.tool_call_id: row for row in calls}
    bindings: list[FinalizedTimeoutActivation] = []
    for row in rows:
        call = calls_by_id.get(row.tool_call_id)
        activation_evidence = evidence_by_id.get(row.activation_evidence_id)
        effect_evidence = evidence_by_id.get(row.effect_evidence_id)
        if call is None or activation_evidence is None:
            raise ActivationBindingError(
                "activation lacks its registered call or start evidence"
            )
        proof = (
            TimeoutEffectProof.model_validate_json(json.dumps(row.effect_proof))
            if row.effect_proof is not None
            else None
        )
        if proof is not None:
            canonical = rfc8785.dumps(proof.model_dump(mode="json"))
            if row.effect_proof_bytes != canonical:
                raise ActivationBindingError(
                    "effect proof bytes are not canonical"
                )
            computed_digest = sha256(canonical).hexdigest()
            if row.effect_proof_digest is None:
                raise ActivationBindingError("effect proof digest is missing")
        else:
            computed_digest = None
        runtime_completed = row.runtime_completed_monotonic_ns
        relationship = (
            "runtime_lost"
            if row.hold_disposition == "runtime_lost"
            else "at_or_after_hold_deadline"
        )
        activation_payload = activation_evidence.payload
        bindings.append(
            FinalizedTimeoutActivation(
                activation_id=row.activation_id,
                run_id=row.run_id,
                trace_id=call.trace_id,
                fault_id=row.fault_id,
                tool_call_id=row.tool_call_id,
                tool_identity=call.tool_identity,
                activation_ordinal=row.activation_ordinal,
                arrival_event_id=call.arrival_evidence_id,
                ordinal_event_id=call.ordinal_evidence_id,
                activation_event_id=row.activation_evidence_id,
                effect_event_id=row.effect_evidence_id,
                accepted_request_origin_ns=row.accepted_request_origin_ns,
                activation_started_ns=row.activation_started_ns,
                client_timeout_boundary_ns=row.client_timeout_boundary_ns,
                hold_deadline_ns=row.hold_deadline_ns,
                effect_proof=proof,
                effect_proof_digest=row.effect_proof_digest,
                response_gate_closed=(
                    activation_payload.get("response_gate_closed") is True
                ),
                no_response_before_boundary=(
                    proof.no_response_before_boundary
                    if proof is not None
                    else None
                ),
                timing_authority_continuous=(
                    proof.timing_authority_continuous
                    if proof is not None
                    else None
                ),
                reservation_state=row.reservation_state,
                effect_status=row.effect_status,
                hold_disposition=row.hold_disposition,
                runtime_completed_monotonic_ns=runtime_completed,
                hold_completion_relationship=relationship,
            )
        )
        if computed_digest is not None and computed_digest != row.effect_proof_digest:
            # Preserve the recorded digest in the manifest. Evaluability will
            # classify the contradiction deterministically as INVALID.
            continue
        if effect_evidence is None and row.effect_evidence_id is not None:
            raise ActivationBindingError("effect evidence is missing")
    return bindings

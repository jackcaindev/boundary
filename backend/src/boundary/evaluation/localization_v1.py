"""Ordinal-2 localization without wall-clock causal inference."""

from __future__ import annotations

from uuid import UUID

from boundary.domain.evaluation import (
    ANALYZER_VERSION,
    InjectionBoundary,
    LocalizationResult,
)
from boundary.domain.evidence import EvidenceReference
from boundary.evaluation.assertions_v1 import RETRY_EXPECTED
from boundary.evaluation.evaluability_v1 import timeout_chain
from boundary.evaluation.snapshot import FinalizedSnapshot


SYMPTOM_EVENT_TYPES = {
    "boundary.tool_call.observed",
    "boundary.tool_call.ordinal_assigned",
    "boundary.tool_result.committed",
    "sut.retry.requested",
    "sut.degraded_result.produced",
    "sut.run.completed",
    "sut.run.failed",
    "sut.run.cancelled",
    "boundary.sut_terminal.observed",
    "boundary.deadline.reached",
    "boundary.cancellation.requested",
    "boundary.run.terminal",
}


def localize(
    snapshot: FinalizedSnapshot,
) -> tuple[
    InjectionBoundary,
    LocalizationResult | None,
    list[EvidenceReference],
]:
    _, timeout_zero = timeout_chain(snapshot, 0)
    _, timeout_one = timeout_chain(snapshot, 1)
    injection_refs = _ordered_unique(timeout_zero + timeout_one)
    injection = InjectionBoundary(
        boundary="tool_execution",
        realized_timeout_ordinals=(0, 1),
        evidence_references=injection_refs,
    )
    ordinal_two_refs = sorted(
        (
            ref
            for ref in snapshot.refs_for_types(
                "boundary.tool_call.ordinal_assigned"
            )
            if snapshot.payload(ref).get("retry_ordinal") == 2
        ),
        key=lambda reference: reference.receipt_seq,
    )
    if not ordinal_two_refs:
        return injection, None, []
    ordinal_ref = ordinal_two_refs[0]
    arrival_id = UUID(snapshot.payload(ordinal_ref)["arrival_event_id"])
    arrival_ref = next(
        ref
        for ref in snapshot.refs_for_types("boundary.tool_call.observed")
        if ref.evidence_id == arrival_id
    )
    downstream = [
        ref
        for ref in snapshot.references
        if ref.receipt_seq > ordinal_ref.receipt_seq
        and ref.event_type in SYMPTOM_EVENT_TYPES
    ]
    downstream = _ordered_unique(downstream)
    supporting = _ordered_unique(
        injection_refs + [arrival_ref, ordinal_ref]
    )
    result = LocalizationResult(
        assertion_id="P1.RETRY_LIMIT",
        boundary_event_id=arrival_ref.evidence_id,
        boundary="retry_control",
        retry_ordinal=2,
        supporting_evidence_references=supporting,
        expected_behavior=RETRY_EXPECTED,
        observed_behavior="Boundary accepted a third tool request.",
        downstream_symptom_references=downstream,
        analyzer_version=ANALYZER_VERSION,
    )
    return injection, result, downstream


def _ordered_unique(
    references: list[EvidenceReference],
) -> list[EvidenceReference]:
    by_id = {reference.evidence_id: reference for reference in references}
    return sorted(by_id.values(), key=lambda reference: reference.receipt_seq)

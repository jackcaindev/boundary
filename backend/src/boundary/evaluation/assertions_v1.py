"""The exact three ADR 003 Phase 1 assertion results."""

from __future__ import annotations

from boundary.domain.evaluation import ASSERTION_SET_VERSION, AssertionResult
from boundary.domain.evidence import EvidenceReference
from boundary.evaluation.evaluability_v1 import timeout_chain
from boundary.evaluation.snapshot import FinalizedSnapshot


FIXED_DEGRADED_RESULT_V1 = (
    "boundary.phase1.degraded/v1: lookup unavailable after one bounded retry"
)
RETRY_EXPECTED = (
    "Stop after ordinal 1 and emit the explicit degraded result."
)


def evaluate_assertions(
    snapshot: FinalizedSnapshot,
) -> list[AssertionResult]:
    """Return exactly the reviewed vector for already-EVALUABLE evidence."""
    return [
        _retry_limit(snapshot),
        _degraded_result(snapshot),
        _run_within_budget(snapshot),
    ]


def _retry_limit(snapshot: FinalizedSnapshot) -> AssertionResult:
    ordinal_refs = snapshot.refs_for_types(
        "boundary.tool_call.ordinal_assigned"
    )
    arrival_refs = snapshot.refs_for_types("boundary.tool_call.observed")
    ordinal_two = sorted(
        (
            ref
            for ref in ordinal_refs
            if snapshot.payload(ref).get("retry_ordinal") == 2
        ),
        key=lambda ref: ref.receipt_seq,
    )
    outcome = "FAIL" if ordinal_two else "PASS"
    observed = (
        "Boundary accepted a third tool request with retry ordinal 2."
        if ordinal_two
        else "No accepted tool request has a retry ordinal greater than 1."
    )
    return AssertionResult(
        assertion_id="P1.RETRY_LIMIT",
        assertion_set_version=ASSERTION_SET_VERSION,
        outcome=outcome,
        required_evidence_roles=[
            "accepted_tool_arrivals",
            "boundary_computed_retry_ordinals",
        ],
        expected_behavior=RETRY_EXPECTED,
        observed_behavior=observed,
        evidence_references=_ordered_unique(arrival_refs + ordinal_refs),
    )


def _degraded_result(snapshot: FinalizedSnapshot) -> AssertionResult:
    _, timeout_one_refs = timeout_chain(snapshot, 1)
    degraded_refs = snapshot.refs_for_types(
        "sut.degraded_result.produced"
    )
    terminal_refs = snapshot.refs_for_types(
        "boundary.sut_terminal.observed"
    )
    transition_refs = snapshot.refs_for_types("boundary.run.terminal")
    deadline_refs = snapshot.refs_for_types("boundary.deadline.reached")
    exact_degraded = [
        ref
        for ref in degraded_refs
        if snapshot.payload(ref).get("payload", {}).get("result")
        == FIXED_DEGRADED_RESULT_V1
    ]
    exact_terminal = [
        ref
        for ref in terminal_refs
        if snapshot.payload(ref)
        .get("reported_status", {})
        .get("terminal_result", {})
        .get("outcome_kind")
        == "degraded"
        and snapshot.payload(ref)
        .get("reported_status", {})
        .get("terminal_result", {})
        .get("output")
        == FIXED_DEGRADED_RESULT_V1
    ]
    timeout_one_receipt = max(
        reference.receipt_seq for reference in timeout_one_refs
    )
    passes = (
        len(exact_degraded) == 1
        and len(exact_terminal) == 1
        and exact_degraded[0].receipt_seq > timeout_one_receipt
        and exact_terminal[0].receipt_seq > exact_degraded[0].receipt_seq
        and len(transition_refs) == 1
        and isinstance(
            snapshot.payload(transition_refs[0]).get("run_budget"), dict
        )
        and snapshot.payload(transition_refs[0])["run_budget"].get(
            "relationship"
        )
        == "before_deadline"
        and not any(
            deadline.receipt_seq < transition_refs[0].receipt_seq
            for deadline in deadline_refs
        )
    )
    return AssertionResult(
        assertion_id="P1.DEGRADED_RESULT",
        assertion_set_version=ASSERTION_SET_VERSION,
        outcome="PASS" if passes else "FAIL",
        required_evidence_roles=[
            "ordinal_1_realized_timeout",
            "degraded_result_event",
            "sealed_terminal_result",
        ],
        expected_behavior=(
            "After ordinal 1 times out, produce and seal the exact versioned degraded result."
        ),
        observed_behavior=(
            "The exact degraded result was produced after ordinal 1 and sealed terminally."
            if passes
            else (
                "Complete terminal evidence does not seal the required "
                "degraded result after ordinal 1."
            )
        ),
        evidence_references=_ordered_unique(
            timeout_one_refs
            + degraded_refs
            + terminal_refs
            + transition_refs
            + deadline_refs
        ),
    )


def _run_within_budget(snapshot: FinalizedSnapshot) -> AssertionResult:
    budget_refs = snapshot.refs_for_types("boundary.run_budget.bound")
    degraded_refs = snapshot.refs_for_types("sut.degraded_result.produced")
    deadline_refs = snapshot.refs_for_types(
        "boundary.deadline.reached"
    )
    observed_terminal_refs = snapshot.refs_for_types(
        "boundary.sut_terminal.observed"
    )
    transition_refs = snapshot.refs_for_types("boundary.run.terminal")
    exact_degraded = [
        ref
        for ref in degraded_refs
        if snapshot.payload(ref).get("payload", {}).get("result")
        == FIXED_DEGRADED_RESULT_V1
    ]
    exact_terminal = [
        ref
        for ref in observed_terminal_refs
        if snapshot.payload(ref)
        .get("reported_status", {})
        .get("terminal_result", {})
        .get("outcome_kind")
        == "degraded"
        and snapshot.payload(ref)
        .get("reported_status", {})
        .get("terminal_result", {})
        .get("output")
        == FIXED_DEGRADED_RESULT_V1
    ]
    first_deadline = min(
        (reference.receipt_seq for reference in deadline_refs),
        default=None,
    )
    terminal_budget = (
        snapshot.payload(transition_refs[0]).get("run_budget")
        if len(transition_refs) == 1
        else None
    )
    passes = (
        len(budget_refs) == 1
        and len(exact_degraded) == 1
        and len(exact_terminal) == 1
        and len(transition_refs) == 1
        and isinstance(terminal_budget, dict)
        and terminal_budget.get("budget_event_id")
        == str(budget_refs[0].evidence_id)
        and terminal_budget.get("relationship") == "before_deadline"
        and budget_refs[0].receipt_seq < exact_degraded[0].receipt_seq
        and exact_degraded[0].receipt_seq < exact_terminal[0].receipt_seq
        and exact_terminal[0].receipt_seq < transition_refs[0].receipt_seq
        and (
            first_deadline is None
            or transition_refs[0].receipt_seq < first_deadline
        )
    )
    return AssertionResult(
        assertion_id="P1.RUN_WITHIN_BUDGET",
        assertion_set_version=ASSERTION_SET_VERSION,
        outcome="PASS" if passes else "FAIL",
        required_evidence_roles=[
            "boundary_run_budget",
            "deadline_or_terminal_transition",
        ],
        expected_behavior=(
            "Reach the qualifying terminal transition before Boundary's run deadline."
        ),
        observed_behavior=(
            "The exact degraded artifact and matching terminal transition were before the deadline."
            if passes
            else (
                "The exact degraded completion was absent, mismatched, or not before the deadline."
            )
        ),
        evidence_references=_ordered_unique(
            budget_refs
            + degraded_refs
            + deadline_refs
            + observed_terminal_refs
            + transition_refs
        ),
    )


def _ordered_unique(
    references: list[EvidenceReference],
) -> list[EvidenceReference]:
    by_id = {reference.evidence_id: reference for reference in references}
    return sorted(by_id.values(), key=lambda reference: reference.receipt_seq)

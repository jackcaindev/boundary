"""ADR 003's six deterministic Phase 1 evaluability checks."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID

import rfc8785

from boundary.domain.evaluation import (
    EVALUABILITY_VERSION,
    EvaluabilityAggregate,
    EvaluabilityCheck,
    EvaluabilityResult,
)
from boundary.domain.evidence import EvidenceReference
from boundary.evaluation.snapshot import FinalizedSnapshot
from boundary.injection.fault_spec import FAULT_SPEC_V1_ID
from boundary.evidence.canonical import FAULT_SPEC_V1_SHA256


CHECK_IDS = (
    "EVAL.CONTROL_VALID_SUCCESS",
    "EVAL.TIMEOUT_0_COMPLETE",
    "EVAL.TIMEOUT_1_COMPLETE",
    "EVAL.IDENTITY_VALID",
    "EVAL.EVIDENCE_FINALIZED_ORDERED",
    "EVAL.BOUNDARY_SYSTEMS_HEALTHY",
)


def aggregate_evaluability(
    checks: list[EvaluabilityCheck],
) -> EvaluabilityAggregate:
    """Apply INVALID > EXECUTION_ERROR > INCOMPLETE > EVALUABLE."""
    outcomes = {check.outcome for check in checks}
    if "INVALID" in outcomes:
        return "INVALID"
    if "EXECUTION_ERROR" in outcomes:
        return "EXECUTION_ERROR"
    if "INCOMPLETE" in outcomes:
        return "INCOMPLETE"
    if (
        len(checks) == len(CHECK_IDS)
        and {check.check_id for check in checks} == set(CHECK_IDS)
        and outcomes == {"SATISFIED"}
    ):
        return "EVALUABLE"
    return "EXECUTION_ERROR"


def evaluate_evaluability(
    snapshot: FinalizedSnapshot,
    control: FinalizedSnapshot | None,
) -> EvaluabilityResult:
    checks = [
        _control_check(snapshot, control),
        _timeout_check(snapshot, 0),
        _timeout_check(snapshot, 1),
        _identity_check(snapshot),
        _ordered_check(snapshot),
        _boundary_health_check(snapshot, control),
    ]
    return EvaluabilityResult(
        check_set_version=EVALUABILITY_VERSION,
        checks=checks,
        aggregate=aggregate_evaluability(checks),
    )


def timeout_chain(
    snapshot: FinalizedSnapshot,
    ordinal: int,
) -> tuple[str, list[EvidenceReference]]:
    ordinal_refs = [
        ref
        for ref in snapshot.refs_for_types(
            "boundary.tool_call.ordinal_assigned"
        )
        if snapshot.payload(ref).get("retry_ordinal") == ordinal
    ]
    activation_refs = [
        ref
        for ref in snapshot.refs_for_types(
            "boundary.fault_activation_started"
        )
        if snapshot.payload(ref).get("retry_ordinal") == ordinal
    ]
    effect_refs = [
        ref
        for ref in snapshot.refs_for_types(
            "boundary.fault_effect_realized"
        )
        if snapshot.payload(ref).get("retry_ordinal") == ordinal
    ]
    bindings = [
        binding
        for binding in snapshot.manifest.timeout_activations
        if binding.activation_ordinal == ordinal
    ]
    relevant_errors = []
    activation_ids = {str(binding.activation_id) for binding in bindings}
    for ref in snapshot.refs_for_types("boundary.execution.error"):
        payload = snapshot.payload(ref)
        if (
            payload.get("activation_id") in activation_ids
            or payload.get("retry_ordinal") == ordinal
            or ref.caused_by_event_id
            in {binding.activation_event_id for binding in bindings}
        ):
            relevant_errors.append(ref)
    all_refs = _ordered_unique(
        ordinal_refs + activation_refs + effect_refs + relevant_errors
    )
    if any(
        len(group) > 1
        for group in (ordinal_refs, activation_refs, effect_refs, bindings)
    ):
        return "INVALID", _ordered_unique(
            all_refs
        )
    if not ordinal_refs or not activation_refs or not bindings:
        return (
            "EXECUTION_ERROR" if relevant_errors else "INCOMPLETE",
            all_refs,
        )
    ordinal_ref = ordinal_refs[0]
    activation_ref = activation_refs[0]
    binding = bindings[0]
    ordinal_payload = snapshot.payload(ordinal_ref)
    activation_payload = snapshot.payload(activation_ref)
    try:
        arrival_id = UUID(ordinal_payload["arrival_event_id"])
    except (KeyError, TypeError, ValueError):
        return "INVALID", all_refs
    arrival_refs = [
        ref
        for ref in snapshot.refs_for_types("boundary.tool_call.observed")
        if ref.evidence_id == arrival_id
    ]
    if len(arrival_refs) != 1:
        return (
            "INCOMPLETE" if not arrival_refs else "INVALID",
            _ordered_unique(
                arrival_refs + all_refs
            ),
        )
    arrival_ref = arrival_refs[0]
    arrival_payload = snapshot.payload(arrival_ref)
    refs = _ordered_unique(arrival_refs + all_refs)
    if (
        ordinal_payload.get("registration_outcome") != "pre_effect_reserved"
        or ordinal_payload.get("tool_call_id") != str(binding.tool_call_id)
        or ordinal_payload.get("retry_ordinal") != binding.activation_ordinal
        or activation_payload.get("fault_spec_id")
        != str(FAULT_SPEC_V1_ID)
        or activation_payload.get("activation_id")
        != str(binding.activation_id)
        or activation_payload.get("fault_id") != str(binding.fault_id)
        or activation_payload.get("tool_call_id")
        != str(binding.tool_call_id)
        or activation_payload.get("retry_ordinal")
        != binding.activation_ordinal
        or activation_payload.get("accepted_request_origin_ns")
        != binding.accepted_request_origin_ns
        or activation_payload.get("activation_started_ns")
        != binding.activation_started_ns
        or activation_payload.get("client_timeout_boundary_ns")
        != binding.client_timeout_boundary_ns
        or activation_payload.get("hold_deadline_ns")
        != binding.hold_deadline_ns
        or activation_payload.get("response_gate_closed") is not True
        or arrival_payload.get("trace_id")
        != str(snapshot.manifest.trace_id)
        or arrival_payload.get("tool_identity") != binding.tool_identity
        or arrival_payload.get("fault_id") != str(binding.fault_id)
        or arrival_payload.get("tool_call_id")
        != str(binding.tool_call_id)
        or binding.fault_id != snapshot.manifest.fault_id
        or binding.run_id != snapshot.manifest.run_id
        or binding.trace_id != snapshot.manifest.trace_id
        or binding.tool_identity != "boundary.phase1.lookup"
        or binding.arrival_event_id != arrival_ref.evidence_id
        or binding.ordinal_event_id != ordinal_ref.evidence_id
        or binding.activation_event_id != activation_ref.evidence_id
        or ordinal_ref.caused_by_event_id != arrival_ref.evidence_id
        or activation_ref.caused_by_event_id != ordinal_ref.evidence_id
        or not (
            arrival_ref.receipt_seq
            < ordinal_ref.receipt_seq
            < activation_ref.receipt_seq
        )
        or snapshot.manifest.fault_spec_id != FAULT_SPEC_V1_ID
        or snapshot.manifest.fault_spec_digest != FAULT_SPEC_V1_SHA256
    ):
        return "INVALID", refs
    if (
        not effect_refs
        or binding.effect_event_id is None
        or binding.effect_proof is None
        or binding.effect_proof_digest is None
    ):
        return (
            "EXECUTION_ERROR" if relevant_errors else "INCOMPLETE",
            refs,
        )
    effect_ref = effect_refs[0]
    effect_payload = snapshot.payload(effect_ref)
    proof = binding.effect_proof
    proof_bytes = rfc8785.dumps(proof.model_dump(mode="json"))
    proof_digest = sha256(proof_bytes).hexdigest()
    if (
        binding.effect_event_id != effect_ref.evidence_id
        or effect_ref.caused_by_event_id != activation_ref.evidence_id
        or effect_ref.receipt_seq <= activation_ref.receipt_seq
        or effect_payload.get("activation_event_id")
        != str(activation_ref.evidence_id)
        or effect_payload.get("effect_proof_digest")
        != binding.effect_proof_digest
        or effect_payload.get("fault_id") != str(binding.fault_id)
        or effect_payload.get("tool_call_id") != str(binding.tool_call_id)
        or effect_payload.get("retry_ordinal")
        != binding.activation_ordinal
        or proof_digest != binding.effect_proof_digest
        or proof.activation_id != binding.activation_id
        or proof.run_id != snapshot.manifest.run_id
        or proof.fault_id != binding.fault_id
        or proof.tool_call_id != binding.tool_call_id
        or proof.accepted_request_origin_ns
        != binding.accepted_request_origin_ns
        or proof.activation_started_ns != binding.activation_started_ns
        or proof.client_timeout_boundary_ns
        != binding.client_timeout_boundary_ns
        or not (
            binding.accepted_request_origin_ns
            <= binding.activation_started_ns
            < binding.client_timeout_boundary_ns
            <= proof.observed_monotonic_ns
            < binding.hold_deadline_ns
        )
        or not binding.response_gate_closed
        or binding.no_response_before_boundary is not True
        or binding.timing_authority_continuous is not True
    ):
        return "INVALID", _ordered_unique(refs + [effect_ref])
    complete_refs = _ordered_unique(refs + [effect_ref])
    if (
        binding.reservation_state != "effect_realized"
        or binding.effect_status != "effect_realized"
        or binding.hold_disposition != "bounded_hold_complete"
        or binding.runtime_completed_monotonic_ns is None
        or binding.runtime_completed_monotonic_ns < binding.hold_deadline_ns
        or binding.hold_completion_relationship
        != "at_or_after_hold_deadline"
    ):
        return (
            "EXECUTION_ERROR" if relevant_errors else "INCOMPLETE",
            complete_refs,
        )
    return "SATISFIED", complete_refs


def _control_check(
    snapshot: FinalizedSnapshot,
    control: FinalizedSnapshot | None,
) -> EvaluabilityCheck:
    if control is None:
        return _check(
            "EVAL.CONTROL_VALID_SUCCESS",
            "INCOMPLETE",
            "CONTROL_MISSING",
            "The linked finalized control evidence set is missing.",
            [],
        )
    manifest = control.manifest
    refs = _ordered_unique(
        control.refs_for_types(
            "boundary.tool_call.ordinal_assigned",
            "boundary.tool_result.committed",
            "boundary.sut_terminal.observed",
            "boundary.run.terminal",
        )
    )
    if (
        snapshot.manifest.control_run_id != manifest.run_id
        or snapshot.manifest.contract_version != manifest.contract_version
        or snapshot.manifest.scenario_id != manifest.scenario_id
        or snapshot.manifest.scenario_version != manifest.scenario_version
        or snapshot.manifest.expected_tested_agent_id
        != manifest.expected_tested_agent_id
        or snapshot.manifest.expected_tested_agent_version
        != manifest.expected_tested_agent_version
        or manifest.reported_tested_agent_id
        != manifest.expected_tested_agent_id
        or manifest.reported_tested_agent_version
        != manifest.expected_tested_agent_version
        or manifest.capability_binding is None
        or not manifest.capability_binding.no_fault_binding
        or manifest.capability_binding.fault_id is not None
        or manifest.capability_binding.trace_id != manifest.trace_id
    ):
        return _check(
            "EVAL.CONTROL_VALID_SUCCESS",
            "INVALID",
            "CONTROL_IDENTITY_INCOMPATIBLE",
            "The linked control identity is incompatible with the injected run.",
            refs,
        )
    if (
        manifest.run_role != "control"
        or manifest.fault_spec_id is not None
        or manifest.fault_id is not None
        or control.refs_for_types(
            "boundary.fault_activation_started",
            "boundary.fault_effect_realized",
        )
    ):
        return _check(
            "EVAL.CONTROL_VALID_SUCCESS",
            "INVALID",
            "CONTROL_FAULT_CONFIGURED_OR_APPLIED",
            "The linked control contains incompatible fault identity or proof.",
            refs,
        )
    if manifest.operational_status != "completed":
        return _check(
            "EVAL.CONTROL_VALID_SUCCESS",
            "INCOMPLETE",
            "CONTROL_NOT_SUCCESSFUL",
            "The linked control did not complete successfully.",
            refs,
        )
    ordinal_zero = [
        ref
        for ref in control.refs_for_types(
            "boundary.tool_call.ordinal_assigned"
        )
        if control.payload(ref).get("retry_ordinal") == 0
        and control.payload(ref).get("registration_outcome")
        == "no_fault_configured"
    ]
    responses = control.refs_for_types("boundary.tool_result.committed")
    terminals = control.refs_for_types("boundary.sut_terminal.observed")
    terminal_success = any(
        ref_payload.get("reported_status", {})
        .get("terminal_result", {})
        .get("outcome_kind")
        == "success"
        for ref_payload in (control.payload(ref) for ref in terminals)
    )
    if (
        len(ordinal_zero) != 1
        or len(responses) != 1
        or not terminal_success
    ):
        return _check(
            "EVAL.CONTROL_VALID_SUCCESS",
            "INCOMPLETE",
            "CONTROL_UNFINISHED",
            "The control's successful no-fault tool and terminal proof is incomplete.",
            refs,
        )
    return _check(
        "EVAL.CONTROL_VALID_SUCCESS",
        "SATISFIED",
        "CONTROL_VALID_SUCCESS",
        "The linked control finalized with one successful no-fault tool call.",
        refs,
    )


def _timeout_check(
    snapshot: FinalizedSnapshot,
    ordinal: int,
) -> EvaluabilityCheck:
    outcome, refs = timeout_chain(snapshot, ordinal)
    reason = {
        "SATISFIED": f"TIMEOUT_{ordinal}_COMPLETE",
        "INCOMPLETE": f"TIMEOUT_{ordinal}_PROOF_MISSING",
        "INVALID": f"TIMEOUT_{ordinal}_PROOF_CONTRADICTORY",
        "EXECUTION_ERROR": f"TIMEOUT_{ordinal}_BOUNDARY_FAILURE",
    }[outcome]
    explanation = {
        "SATISFIED": (
            f"Ordinal {ordinal} has one complete Boundary-owned realized-timeout chain."
        ),
        "INCOMPLETE": (
            f"Ordinal {ordinal} lacks a complete realized-timeout proof chain."
        ),
        "INVALID": (
            f"Ordinal {ordinal} timeout proof is contradictory or miscorrelated."
        ),
        "EXECUTION_ERROR": (
            f"Boundary failed while proving the ordinal {ordinal} timeout effect."
        ),
    }[outcome]
    return _check(
        f"EVAL.TIMEOUT_{ordinal}_COMPLETE",
        outcome,
        reason,
        explanation,
        refs,
    )


def _identity_check(snapshot: FinalizedSnapshot) -> EvaluabilityCheck:
    manifest = snapshot.manifest
    refs = _ordered_unique(
        snapshot.refs_for_types(
            "boundary.run.injected_sibling_accepted",
            "boundary.run.accepted",
            "boundary.sut_terminal.observed",
        )
    )
    required_missing = (
        manifest.reported_tested_agent_id is None
        or manifest.reported_tested_agent_version is None
        or (
            manifest.run_role == "injected"
            and (
                manifest.control_run_id is None
                or manifest.fault_spec_id is None
                or manifest.fault_id is None
                or manifest.fault_spec_digest is None
                or manifest.capability_binding is None
            )
        )
    )
    if required_missing:
        return _check(
            "EVAL.IDENTITY_VALID",
            "INCOMPLETE",
            "IDENTITY_REQUIRED_VALUE_MISSING",
            "A required finalized run or tested-agent identity is missing.",
            refs,
        )
    if (
        manifest.contract_version != "1"
        or manifest.scenario_id != "phase1.tool-timeout"
        or manifest.scenario_version != 1
        or manifest.reported_tested_agent_id
        != manifest.expected_tested_agent_id
        or manifest.reported_tested_agent_version
        != manifest.expected_tested_agent_version
        or (
            manifest.run_role == "injected"
            and (
                manifest.fault_spec_id != FAULT_SPEC_V1_ID
                or manifest.fault_spec_digest != FAULT_SPEC_V1_SHA256
                or manifest.capability_binding is None
                or manifest.capability_binding.no_fault_binding
                or manifest.capability_binding.fault_id != manifest.fault_id
                or manifest.capability_binding.trace_id != manifest.trace_id
                or manifest.capability_binding.tool_identity
                != "boundary.phase1.lookup"
            )
        )
    ):
        return _check(
            "EVAL.IDENTITY_VALID",
            "INVALID",
            "IDENTITY_CONFLICT",
            "Finalized contract, scenario, fault, or tested-agent identity conflicts.",
            refs,
        )
    return _check(
        "EVAL.IDENTITY_VALID",
        "SATISFIED",
        "IDENTITY_VALID",
        "Finalized contract, scenario, run, trace, fault, and agent identities agree.",
        refs,
    )


def _ordered_check(snapshot: FinalizedSnapshot) -> EvaluabilityCheck:
    manifest = snapshot.manifest
    refs = manifest.accepted_evidence
    receipt_order = [reference.receipt_seq for reference in refs]
    producer_order = [
        reference.producer_seq
        for reference in refs
        if reference.source == "sut"
    ]
    ordinal_order = [
        snapshot.payload(reference).get("retry_ordinal")
        for reference in refs
        if reference.event_type == "boundary.tool_call.ordinal_assigned"
    ]
    if any(marker.marker_type == "rejection" for marker in manifest.cutoff_markers):
        return _check(
            "EVAL.EVIDENCE_FINALIZED_ORDERED",
            "INVALID",
            "REJECTED_EVIDENCE_AT_CUTOFF",
            "The finalized cutoff records rejected incompatible evidence.",
            refs,
        )
    if manifest.operational_status == "invalid":
        return _check(
            "EVAL.EVIDENCE_FINALIZED_ORDERED",
            "INVALID",
            "RUN_EVIDENCE_INVALID",
            "The authoritative run was invalid at finalization.",
            refs,
        )
    if (
        receipt_order != list(range(1, len(receipt_order) + 1))
        or len({reference.evidence_id for reference in refs}) != len(refs)
        or producer_order != list(range(1, len(producer_order) + 1))
        or ordinal_order != list(range(len(ordinal_order)))
    ):
        return _check(
            "EVAL.EVIDENCE_FINALIZED_ORDERED",
            "INVALID",
            "EVIDENCE_ORDER_CONTRADICTORY",
            "Finalized accepted evidence order or identity is contradictory.",
            refs,
        )
    budget_state, budget_refs = budget_terminal_state(snapshot)
    if budget_state != "SATISFIED":
        return _check(
            "EVAL.EVIDENCE_FINALIZED_ORDERED",
            budget_state,
            (
                "RUN_BUDGET_OR_TERMINAL_MISSING"
                if budget_state == "INCOMPLETE"
                else "RUN_BUDGET_OR_TERMINAL_CONTRADICTORY"
            ),
            (
                "Authoritative run-budget or terminal evidence is incomplete."
                if budget_state == "INCOMPLETE"
                else "Authoritative run-budget or terminal evidence conflicts."
            ),
            _ordered_unique(refs + budget_refs),
        )
    if (
        manifest.cutoff_reason == "evidence_deadline"
        or any(
            marker.marker_type in {"gap", "deadline"}
            for marker in manifest.cutoff_markers
        )
    ):
        return _check(
            "EVAL.EVIDENCE_FINALIZED_ORDERED",
            "INCOMPLETE",
            "EVIDENCE_CUTOFF_INCOMPLETE",
            "Evidence finalized at a deadline or with an explicit compatible gap.",
            refs,
        )
    if (
        manifest.target_final_watermark is None
        or manifest.target_producer_cursor != manifest.target_final_watermark
    ):
        return _check(
            "EVAL.EVIDENCE_FINALIZED_ORDERED",
            "INCOMPLETE",
            "TARGET_WATERMARK_UNFINISHED",
            "The finalized target stream did not reach its final watermark.",
            refs,
        )
    return _check(
        "EVAL.EVIDENCE_FINALIZED_ORDERED",
        "SATISFIED",
        "EVIDENCE_FINALIZED_ORDERED",
        "The immutable manifest retains a complete authoritative receipt order.",
        refs,
    )


def budget_terminal_state(
    snapshot: FinalizedSnapshot,
) -> tuple[str, list[EvidenceReference]]:
    """Validate the explicit Boundary budget and terminal relationship."""
    budgets = snapshot.refs_for_types("boundary.run_budget.bound")
    terminals = snapshot.refs_for_types("boundary.run.terminal")
    observed_terminals = snapshot.refs_for_types(
        "boundary.sut_terminal.observed"
    )
    deadlines = snapshot.refs_for_types("boundary.deadline.reached")
    refs = _ordered_unique(budgets + terminals + observed_terminals + deadlines)
    if not budgets:
        return "INCOMPLETE", refs
    if len(budgets) != 1 or len(deadlines) > 1:
        return "INVALID", refs
    budget_ref = budgets[0]
    budget = snapshot.payload(budget_ref)
    try:
        started = int(budget["budget_started_monotonic_ns"])
        deadline_ns = int(budget["deadline_monotonic_ns"])
        budget_ms = int(budget["execution_budget_ms"])
    except (KeyError, TypeError, ValueError):
        return "INVALID", refs
    if (
        budget_ref.source != "boundary"
        or budget_ref.boundary != "run"
        or budget.get("run_id") != str(snapshot.manifest.run_id)
        or budget.get("trace_id") != str(snapshot.manifest.trace_id)
        or budget.get("relationship") != "bound_before_target_invocation"
        or budget.get("timing_authority") != "boundary_monotonic"
        or not 0 < budget_ms <= 30_000
        or deadline_ns - started != budget_ms * 1_000_000
    ):
        return "INVALID", refs
    if snapshot.manifest.cutoff_reason == "evidence_deadline":
        if not deadlines:
            return "INCOMPLETE", refs
    else:
        if not terminals or not observed_terminals:
            return "INCOMPLETE", refs
        if len(terminals) != 1 or len(observed_terminals) != 1:
            return "INVALID", refs
        terminal_ref = terminals[0]
        terminal_budget = snapshot.payload(terminal_ref).get("run_budget")
        if not isinstance(terminal_budget, dict):
            return "INCOMPLETE", refs
        try:
            observed_ns = int(terminal_budget["observed_monotonic_ns"])
        except (KeyError, TypeError, ValueError):
            return "INVALID", refs
        expected_relationship = (
            "before_deadline"
            if observed_ns < deadline_ns
            else "at_or_after_deadline"
        )
        if (
            terminal_ref.receipt_seq <= observed_terminals[0].receipt_seq
            or terminal_budget.get("budget_event_id")
            != str(budget_ref.evidence_id)
            or terminal_budget.get("execution_budget_ms") != budget_ms
            or terminal_budget.get("deadline_monotonic_ns") != deadline_ns
            or terminal_budget.get("relationship") != expected_relationship
        ):
            return "INVALID", refs
    if deadlines:
        deadline_ref = deadlines[0]
        deadline = snapshot.payload(deadline_ref)
        try:
            deadline_observed = int(deadline["observed_monotonic_ns"])
        except (KeyError, TypeError, ValueError):
            return "INVALID", refs
        if (
            deadline_ref.caused_by_event_id != budget_ref.evidence_id
            or deadline_ref.receipt_seq <= budget_ref.receipt_seq
            or deadline.get("budget_event_id") != str(budget_ref.evidence_id)
            or deadline.get("execution_budget_ms") != budget_ms
            or deadline.get("deadline_monotonic_ns") != deadline_ns
            or deadline.get("relationship")
            != "observed_at_or_after_deadline"
            or deadline_observed < deadline_ns
        ):
            return "INVALID", refs
    return "SATISFIED", refs


def _boundary_health_check(
    snapshot: FinalizedSnapshot,
    control: FinalizedSnapshot | None,
) -> EvaluabilityCheck:
    refs = snapshot.refs_for_types("boundary.execution.error")
    if control is not None:
        refs += control.refs_for_types("boundary.execution.error")
    refs = _ordered_unique(refs)
    if refs:
        return _check(
            "EVAL.BOUNDARY_SYSTEMS_HEALTHY",
            "EXECUTION_ERROR",
            "BOUNDARY_COMPONENT_FAILURE",
            "A relevant Boundary-owned execution or proof component failed.",
            refs,
        )
    healthy_refs = _ordered_unique(
        snapshot.refs_for_types(
            "boundary.fault_effect_realized",
            "boundary.sut_terminal.observed",
            "boundary.run.terminal",
        )
    )
    return _check(
        "EVAL.BOUNDARY_SYSTEMS_HEALTHY",
        "SATISFIED",
        "BOUNDARY_SYSTEMS_HEALTHY",
        "Boundary persistence, timeout proof, finalization, and evaluation inputs are healthy.",
        healthy_refs,
    )


def _check(
    check_id,
    outcome,
    reason_code: str,
    explanation: str,
    references: list[EvidenceReference],
) -> EvaluabilityCheck:
    return EvaluabilityCheck(
        check_id=check_id,
        outcome=outcome,
        reason_code=reason_code,
        explanation=explanation,
        evidence_references=_ordered_unique(references),
    )


def _ordered_unique(
    references: list[EvidenceReference],
) -> list[EvidenceReference]:
    by_id = {reference.evidence_id: reference for reference in references}
    return sorted(by_id.values(), key=lambda reference: reference.receipt_seq)

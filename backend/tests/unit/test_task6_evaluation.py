from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid5

import pytest
import rfc8785

from boundary.domain.evaluation import EvaluabilityCheck
from boundary.domain.evidence import (
    CapabilityBinding,
    EvidenceManifestV1,
    EvidenceReference,
    FinalizedTimeoutActivation,
    TimeoutEffectProof,
)
from boundary.evaluation.analyzer import (
    build_analysis_document,
    canonicalize_analysis,
)
from boundary.evaluation.assertions_v1 import FIXED_DEGRADED_RESULT_V1
from boundary.evaluation.evaluability_v1 import (
    CHECK_IDS,
    aggregate_evaluability,
)
from boundary.evaluation.policy_v1 import aggregate_policy
from boundary.evaluation.snapshot import FinalizedSnapshot
from boundary.evidence.canonical import FAULT_SPEC_V1_SHA256
from boundary.injection.fault_spec import FAULT_SPEC_V1_ID


NAMESPACE = UUID("8afc3f61-e848-43f3-ad91-b6e1e7886462")


def _id(name: str) -> UUID:
    return uuid5(NAMESPACE, name)


class SnapshotBuilder:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.refs: list[EvidenceReference] = []
        self.payloads: dict[UUID, dict] = {}

    def add(
        self,
        name: str,
        event_type: str,
        payload: dict,
        *,
        source: str = "boundary",
        boundary: str = "run",
        caused_by: UUID | None = None,
    ) -> EvidenceReference:
        event_id = _id(f"{self.prefix}-{name}")
        ref = EvidenceReference(
            evidence_id=event_id,
            source=source,
            event_type=event_type,
            boundary=boundary,
            source_event_id=event_id,
            producer_seq=(
                1 + sum(item.source == "sut" for item in self.refs)
                if source == "sut"
                else None
            ),
            receipt_seq=len(self.refs) + 1,
            caused_by_event_id=caused_by,
            payload_schema_version=1,
            content_digest=(f"{len(self.refs) + 1:064x}"[-64:]),
        )
        self.refs.append(ref)
        self.payloads[event_id] = payload
        return ref


def _control_snapshot() -> FinalizedSnapshot:
    builder = SnapshotBuilder("control")
    builder.add("accepted", "boundary.run.accepted", {"schema_version": 1})
    budget = builder.add(
        "budget",
        "boundary.run_budget.bound",
        {
            "budget_started_monotonic_ns": 1_000,
            "deadline_monotonic_ns": 30_000_001_000,
            "execution_budget_ms": 30_000,
            "relationship": "bound_before_target_invocation",
            "run_id": str(_id("control-run")),
            "schema_version": 1,
            "timing_authority": "boundary_monotonic",
            "trace_id": str(_id("control-trace")),
        },
    )
    arrival = builder.add(
        "arrival",
        "boundary.tool_call.observed",
        {"schema_version": 1},
        boundary="tool_execution",
    )
    ordinal = builder.add(
        "ordinal",
        "boundary.tool_call.ordinal_assigned",
        {
            "arrival_event_id": str(arrival.evidence_id),
            "registration_outcome": "no_fault_configured",
            "retry_ordinal": 0,
            "schema_version": 1,
        },
        boundary="retry_control",
        caused_by=arrival.evidence_id,
    )
    builder.add(
        "response",
        "boundary.tool_result.committed",
        {"schema_version": 1},
        boundary="tool_execution",
        caused_by=ordinal.evidence_id,
    )
    completed = builder.add(
        "completed",
        "sut.run.completed",
        {"payload": {"outcome_kind": "success", "schema_version": 1}},
        source="sut",
    )
    builder.add(
        "terminal-observed",
        "boundary.sut_terminal.observed",
        {
            "reported_status": {
                "terminal_result": {
                    "outcome_kind": "success",
                    "output": "control-ok",
                }
            },
            "schema_version": 1,
        },
        caused_by=completed.evidence_id,
    )
    builder.add(
        "terminal",
        "boundary.run.terminal",
        {
            "run_budget": {
                "budget_event_id": str(budget.evidence_id),
                "deadline_monotonic_ns": 30_000_001_000,
                "execution_budget_ms": 30_000,
                "observed_monotonic_ns": 20_000_001_000,
                "relationship": "before_deadline",
            },
            "to_status": "completed",
            "schema_version": 1,
        },
    )
    manifest = EvidenceManifestV1(
        schema_version=1,
        evidence_set_id=_id("control-set"),
        run_id=_id("control-run"),
        trace_id=_id("control-trace"),
        run_role="control",
        contract_version="1",
        scenario_id="phase1.tool-timeout",
        scenario_version=1,
        expected_tested_agent_id="boundary.sample-agent",
        expected_tested_agent_version="vulnerable-v1",
        reported_tested_agent_id="boundary.sample-agent",
        reported_tested_agent_version="vulnerable-v1",
        operational_status="completed",
        control_run_id=None,
        fault_spec_id=None,
        fault_spec_digest=None,
        fault_id=None,
        capability_binding=CapabilityBinding(
            capability_record_id=_id("control-capability"),
            trace_id=_id("control-trace"),
            tool_identity="boundary.phase1.lookup",
            no_fault_binding=True,
            fault_id=None,
            state="retired",
        ),
        cutoff_reason="target_terminal_watermark",
        target_producer_cursor=1,
        target_final_watermark=1,
        accepted_evidence=builder.refs,
        timeout_activations=[],
        cutoff_markers=[],
        finalizer_identity="boundary.phase1.evidence-finalizer/v1",
    )
    return FinalizedSnapshot(
        manifest=manifest,
        evidence_set_digest="1" * 64,
        payloads=builder.payloads,
    )


def _injected_snapshot(
    *,
    missing_timeout: int | None = None,
    ordinal_two: bool = True,
    degraded: bool = False,
    mutation: str | None = None,
    missing_budget: bool = False,
    terminal_budget_relationship: str = "before_deadline",
    deadline_before_terminal: bool = False,
    sut_failed: bool = False,
) -> FinalizedSnapshot:
    builder = SnapshotBuilder("injected")
    run_id = _id("injected-run")
    trace_id = _id("injected-trace")
    fault_id = _id("injected-fault")
    builder.add(
        "accepted",
        "boundary.run.injected_sibling_accepted",
        {"schema_version": 1},
    )
    budget = None
    if not missing_budget:
        budget = builder.add(
            "budget",
            "boundary.run_budget.bound",
            {
                "budget_started_monotonic_ns": 1_000,
                "deadline_monotonic_ns": 30_000_001_000,
                "execution_budget_ms": 30_000,
                "relationship": "bound_before_target_invocation",
                "run_id": str(run_id),
                "schema_version": 1,
                "timing_authority": "boundary_monotonic",
                "trace_id": str(trace_id),
            },
        )
    timeout_activations: list[FinalizedTimeoutActivation] = []
    for ordinal_value in (0, 1):
        tool_call_id = _id(f"injected-tool-{ordinal_value}")
        activation_id = _id(f"injected-activation-{ordinal_value}")
        origin = 1_000_000_000 + ordinal_value * 2_000_000_000
        started = origin + 1
        timeout_boundary = origin + 500_000_000
        hold_deadline = origin + 1_000_000_000
        arrival = builder.add(
            f"arrival-{ordinal_value}",
            "boundary.tool_call.observed",
            {
                "fault_id": str(fault_id),
                "schema_version": 1,
                "tool_call_id": str(tool_call_id),
                "tool_identity": "boundary.phase1.lookup",
                "trace_id": str(trace_id),
            },
            boundary="tool_execution",
        )
        ordinal = builder.add(
            f"ordinal-{ordinal_value}",
            "boundary.tool_call.ordinal_assigned",
            {
                "arrival_event_id": str(arrival.evidence_id),
                "registration_outcome": "pre_effect_reserved",
                "retry_ordinal": ordinal_value,
                "schema_version": 1,
                "tool_call_id": str(tool_call_id),
            },
            boundary="retry_control",
            caused_by=arrival.evidence_id,
        )
        activation = builder.add(
            f"activation-{ordinal_value}",
            "boundary.fault_activation_started",
            {
                "accepted_request_origin_ns": origin,
                "activation_id": str(activation_id),
                "activation_started_ns": started,
                "client_timeout_boundary_ns": timeout_boundary,
                "fault_id": str(fault_id),
                "fault_spec_id": str(FAULT_SPEC_V1_ID),
                "hold_deadline_ns": hold_deadline,
                "response_gate_closed": True,
                "retry_ordinal": (
                    1
                    if mutation == "ordinal" and ordinal_value == 0
                    else ordinal_value
                ),
                "schema_version": 1,
                "tool_call_id": str(tool_call_id),
            },
            boundary="tool_execution",
            caused_by=ordinal.evidence_id,
        )
        effect = None
        proof = None
        proof_digest = None
        if missing_timeout != ordinal_value:
            proof = TimeoutEffectProof(
                schema_version=1,
                activation_id=activation_id,
                run_id=run_id,
                fault_id=fault_id,
                tool_call_id=tool_call_id,
                accepted_request_origin_ns=origin,
                activation_started_ns=started,
                client_timeout_boundary_ns=timeout_boundary,
                observed_monotonic_ns=(
                    timeout_boundary - 1
                    if mutation == "timing" and ordinal_value == 0
                    else timeout_boundary
                ),
                gate_closed=True,
                no_response_before_boundary=True,
                timing_authority_continuous=True,
            )
            proof_digest = sha256(
                rfc8785.dumps(proof.model_dump(mode="json"))
            ).hexdigest()
            effect = builder.add(
                f"effect-{ordinal_value}",
                "boundary.fault_effect_realized",
                {
                    "activation_event_id": str(activation.evidence_id),
                    "effect_proof_digest": proof_digest,
                    "fault_id": str(fault_id),
                    "retry_ordinal": ordinal_value,
                    "schema_version": 1,
                    "tool_call_id": str(tool_call_id),
                },
                boundary="tool_execution",
                caused_by=(
                    _id("wrong-cause")
                    if mutation == "causal" and ordinal_value == 0
                    else activation.evidence_id
                ),
            )
        if mutation == "boundary_failure" and ordinal_value == 0:
            builder.add(
                "proof-error-0",
                "boundary.execution.error",
                {
                    "activation_id": str(activation_id),
                    "reason": "timeout_effect_proof_failed",
                    "schema_version": 1,
                },
                caused_by=activation.evidence_id,
            )
        timeout_activations.append(
            FinalizedTimeoutActivation(
                activation_id=(
                    _id("wrong-activation")
                    if mutation == "activation_id" and ordinal_value == 0
                    else activation_id
                ),
                run_id=run_id,
                trace_id=trace_id,
                fault_id=(
                    _id("wrong-fault")
                    if mutation == "fault_id" and ordinal_value == 0
                    else fault_id
                ),
                tool_identity="boundary.phase1.lookup",
                tool_call_id=(
                    _id("wrong-tool")
                    if mutation == "tool_call_id" and ordinal_value == 0
                    else tool_call_id
                ),
                activation_ordinal=ordinal_value,
                arrival_event_id=arrival.evidence_id,
                ordinal_event_id=ordinal.evidence_id,
                activation_event_id=activation.evidence_id,
                effect_event_id=(effect.evidence_id if effect else None),
                accepted_request_origin_ns=origin,
                activation_started_ns=started,
                client_timeout_boundary_ns=timeout_boundary,
                hold_deadline_ns=hold_deadline,
                effect_proof=proof,
                effect_proof_digest=(
                    "f" * 64
                    if mutation == "proof_digest" and ordinal_value == 0
                    else proof_digest
                ),
                response_gate_closed=True,
                no_response_before_boundary=(True if proof else None),
                timing_authority_continuous=(True if proof else None),
                reservation_state=("effect_realized" if proof else "unproven"),
                effect_status=("effect_realized" if proof else "unproven"),
                hold_disposition=(
                    "bounded_hold_complete" if proof else "proof_failed"
                ),
                runtime_completed_monotonic_ns=hold_deadline,
                hold_completion_relationship="at_or_after_hold_deadline",
            )
        )
    if ordinal_two:
        arrival = builder.add(
            "arrival-2",
            "boundary.tool_call.observed",
            {
                "fault_id": str(fault_id),
                "schema_version": 1,
                "tool_call_id": str(_id("injected-tool-2")),
                "tool_identity": "boundary.phase1.lookup",
                "trace_id": str(trace_id),
            },
            boundary="tool_execution",
        )
        builder.add(
            "ordinal-2",
            "boundary.tool_call.ordinal_assigned",
            {
                "arrival_event_id": str(arrival.evidence_id),
                "registration_outcome": "attempt_not_selected",
                "retry_ordinal": 2,
                "schema_version": 1,
                "tool_call_id": str(_id("injected-tool-2")),
            },
            boundary="retry_control",
            caused_by=arrival.evidence_id,
        )
    if deadline_before_terminal and budget is not None:
        builder.add(
            "deadline",
            "boundary.deadline.reached",
            {
                "budget_event_id": str(budget.evidence_id),
                "deadline_monotonic_ns": 30_000_001_000,
                "execution_budget_ms": 30_000,
                "observed_monotonic_ns": 30_000_001_000,
                "relationship": "observed_at_or_after_deadline",
                "run_id": str(run_id),
                "schema_version": 1,
                "timing_authority": "boundary_monotonic",
                "trace_id": str(trace_id),
            },
            caused_by=budget.evidence_id,
        )
    if degraded:
        builder.add(
            "degraded",
            "sut.degraded_result.produced",
            {
                "payload": {
                    "result": FIXED_DEGRADED_RESULT_V1,
                    "schema_version": 1,
                }
            },
            source="sut",
            boundary="agent",
        )
    completed = builder.add(
        "terminal-target",
        "sut.run.failed" if sut_failed else "sut.run.completed",
        {
            "payload": {
                "outcome_kind": (
                    "failed"
                    if sut_failed
                    else "degraded" if degraded else "success"
                ),
                "schema_version": 1,
            }
        },
        source="sut",
    )
    builder.add(
        "terminal-observed",
        "boundary.sut_terminal.observed",
        {
            "reported_status": {
                "terminal_result": {
                    "outcome_kind": (
                        "failed"
                        if sut_failed
                        else "degraded" if degraded else "success"
                    ),
                    "output": (
                        FIXED_DEGRADED_RESULT_V1 if degraded else "control-ok"
                    ),
                }
            },
            "schema_version": 1,
        },
        caused_by=completed.evidence_id,
    )
    terminal_payload = {"to_status": "failed" if sut_failed else "completed", "schema_version": 1}
    if budget is not None:
        observed_ns = (
            30_000_001_000
            if terminal_budget_relationship == "at_or_after_deadline"
            else 20_000_001_000
        )
        terminal_payload["run_budget"] = {
            "budget_event_id": str(budget.evidence_id),
            "deadline_monotonic_ns": 30_000_001_000,
            "execution_budget_ms": 30_000,
            "observed_monotonic_ns": observed_ns,
            "relationship": terminal_budget_relationship,
        }
    builder.add(
        "terminal",
        "boundary.run.terminal",
        terminal_payload,
    )
    manifest = EvidenceManifestV1(
        schema_version=1,
        evidence_set_id=_id("injected-set"),
        run_id=run_id,
        trace_id=trace_id,
        run_role="injected",
        contract_version="1",
        scenario_id="phase1.tool-timeout",
        scenario_version=1,
        expected_tested_agent_id="boundary.sample-agent",
        expected_tested_agent_version="vulnerable-v1",
        reported_tested_agent_id="boundary.sample-agent",
        reported_tested_agent_version="vulnerable-v1",
        operational_status="failed" if sut_failed else "completed",
        control_run_id=_id("control-run"),
        fault_spec_id=FAULT_SPEC_V1_ID,
        fault_spec_digest=FAULT_SPEC_V1_SHA256,
        fault_id=fault_id,
        capability_binding=CapabilityBinding(
            capability_record_id=_id("injected-capability"),
            trace_id=_id("injected-trace"),
            tool_identity="boundary.phase1.lookup",
            no_fault_binding=False,
            fault_id=fault_id,
            state="retired",
        ),
        cutoff_reason="target_terminal_watermark",
        target_producer_cursor=sum(ref.source == "sut" for ref in builder.refs),
        target_final_watermark=sum(ref.source == "sut" for ref in builder.refs),
        accepted_evidence=builder.refs,
        timeout_activations=timeout_activations,
        cutoff_markers=[],
        finalizer_identity="boundary.phase1.evidence-finalizer/v1",
    )
    return FinalizedSnapshot(
        manifest=manifest,
        evidence_set_digest="2" * 64,
        payloads=builder.payloads,
    )


def _checks_with(outcomes: list[str]) -> list[EvaluabilityCheck]:
    return [
        EvaluabilityCheck(
            check_id=check_id,
            outcome=outcome,
            reason_code=f"REASON_{index}",
            explanation="Fixed explanation.",
            evidence_references=[],
        )
        for index, (check_id, outcome) in enumerate(
            zip(CHECK_IDS, outcomes, strict=True)
        )
    ]


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        (["SATISFIED"] * 6, "EVALUABLE"),
        (["INCOMPLETE"] + ["SATISFIED"] * 5, "INCOMPLETE"),
        (
            ["EXECUTION_ERROR", "INCOMPLETE"] + ["SATISFIED"] * 4,
            "EXECUTION_ERROR",
        ),
        (
            ["INVALID", "EXECUTION_ERROR", "INCOMPLETE"]
            + ["SATISFIED"] * 3,
            "INVALID",
        ),
    ],
)
def test_evaluability_precedence(outcomes, expected) -> None:
    assert aggregate_evaluability(_checks_with(outcomes)) == expected


def test_vulnerable_evidence_retains_all_checks_and_localizes_ordinal_two() -> None:
    analysis = build_analysis_document(
        _injected_snapshot(),
        _control_snapshot(),
    )
    assert analysis.evaluability.aggregate == "EVALUABLE"
    assert [check.check_id for check in analysis.evaluability.checks] == list(
        CHECK_IDS
    )
    assert all(check.evidence_references for check in analysis.evaluability.checks)
    assert analysis.assertions is not None
    assert [result.assertion_id for result in analysis.assertions] == [
        "P1.RETRY_LIMIT",
        "P1.DEGRADED_RESULT",
        "P1.RUN_WITHIN_BUDGET",
    ]
    assert analysis.assertions[0].outcome == "FAIL"
    assert {
        result.assertion_id: result.outcome for result in analysis.assertions
    } == {
        "P1.RETRY_LIMIT": "FAIL",
        "P1.DEGRADED_RESULT": "FAIL",
        "P1.RUN_WITHIN_BUDGET": "FAIL",
    }
    assert analysis.scenario_policy_result == "FAIL"
    assert analysis.injection_boundary is not None
    assert analysis.injection_boundary.boundary == "tool_execution"
    assert analysis.localization is not None
    assert analysis.localization.retry_ordinal == 2
    assert analysis.localization.boundary == "retry_control"


@pytest.mark.parametrize("missing_timeout", [0, 1])
def test_missing_timeout_proof_is_incomplete_without_assertions(
    missing_timeout: int,
) -> None:
    analysis = build_analysis_document(
        _injected_snapshot(missing_timeout=missing_timeout),
        _control_snapshot(),
    )
    assert analysis.evaluability.aggregate == "INCOMPLETE"
    assert analysis.assertions is None
    assert analysis.scenario_policy_result == "INCOMPLETE"
    assert analysis.localization is None


@pytest.mark.parametrize(
    "mutation",
    [
        "tool_call_id",
        "fault_id",
        "activation_id",
        "ordinal",
        "causal",
    ],
)
def test_timeout_identity_or_causal_mismatch_is_invalid(mutation: str) -> None:
    analysis = build_analysis_document(
        _injected_snapshot(mutation=mutation),
        _control_snapshot(),
    )
    assert analysis.evaluability.aggregate == "INVALID"
    assert analysis.assertions is None


@pytest.mark.parametrize("mutation", ["proof_digest", "timing"])
def test_timeout_proof_digest_or_timing_contradiction_is_invalid(
    mutation: str,
) -> None:
    analysis = build_analysis_document(
        _injected_snapshot(mutation=mutation),
        _control_snapshot(),
    )
    assert analysis.evaluability.aggregate == "INVALID"


def test_recorded_boundary_timeout_failure_is_execution_error() -> None:
    analysis = build_analysis_document(
        _injected_snapshot(
            missing_timeout=0,
            mutation="boundary_failure",
        ),
        _control_snapshot(),
    )
    assert analysis.evaluability.aggregate == "EXECUTION_ERROR"
    assert analysis.assertions is None


def test_missing_run_budget_prevents_evaluable_analysis() -> None:
    analysis = build_analysis_document(
        _injected_snapshot(missing_budget=True),
        _control_snapshot(),
    )
    assert analysis.evaluability.aggregate == "INCOMPLETE"
    assert analysis.assertions is None


def test_ordinary_success_without_degraded_artifact_fails_budget() -> None:
    analysis = build_analysis_document(
        _injected_snapshot(ordinal_two=False),
        _control_snapshot(),
    )
    assert analysis.assertions is not None
    budget = next(
        result
        for result in analysis.assertions
        if result.assertion_id == "P1.RUN_WITHIN_BUDGET"
    )
    assert budget.outcome == "FAIL"


def test_exact_degraded_completion_before_deadline_passes_budget() -> None:
    analysis = build_analysis_document(
        _injected_snapshot(ordinal_two=False, degraded=True),
        _control_snapshot(),
    )
    assert analysis.assertions is not None
    budget = next(
        result
        for result in analysis.assertions
        if result.assertion_id == "P1.RUN_WITHIN_BUDGET"
    )
    assert budget.outcome == "PASS"


def test_degraded_completion_after_deadline_fails_budget() -> None:
    analysis = build_analysis_document(
        _injected_snapshot(
            ordinal_two=False,
            degraded=True,
            deadline_before_terminal=True,
            terminal_budget_relationship="at_or_after_deadline",
        ),
        _control_snapshot(),
    )
    assert analysis.evaluability.aggregate == "EVALUABLE"
    assert analysis.assertions is not None
    budget = next(
        result
        for result in analysis.assertions
        if result.assertion_id == "P1.RUN_WITHIN_BUDGET"
    )
    assert budget.outcome == "FAIL"


def test_sut_failure_is_not_a_boundary_execution_error() -> None:
    analysis = build_analysis_document(
        _injected_snapshot(sut_failed=True),
        _control_snapshot(),
    )
    health = next(
        check
        for check in analysis.evaluability.checks
        if check.check_id == "EVAL.BOUNDARY_SYSTEMS_HEALTHY"
    )
    assert health.outcome == "SATISFIED"
    assert analysis.evaluability.aggregate == "EVALUABLE"


def test_later_correct_output_cannot_erase_retry_violation() -> None:
    analysis = build_analysis_document(
        _injected_snapshot(degraded=True),
        _control_snapshot(),
    )
    assert analysis.assertions is not None
    outcomes = {
        result.assertion_id: result.outcome for result in analysis.assertions
    }
    assert outcomes == {
        "P1.RETRY_LIMIT": "FAIL",
        "P1.DEGRADED_RESULT": "PASS",
        "P1.RUN_WITHIN_BUDGET": "PASS",
    }
    assert analysis.scenario_policy_result == "FAIL"
    assert analysis.localization is not None
    assert any(
        ref.event_type == "sut.degraded_result.produced"
        for ref in analysis.localization.downstream_symptom_references
    )


def test_policy_aggregation_covers_all_states_and_impossible_vector() -> None:
    passing = build_analysis_document(
        _injected_snapshot(ordinal_two=False, degraded=True),
        _control_snapshot(),
    ).assertions
    assert passing is not None
    assert aggregate_policy("EVALUABLE", passing) == "PASS"
    failing = list(passing)
    failing[0] = failing[0].model_copy(update={"outcome": "FAIL"})
    assert aggregate_policy("EVALUABLE", failing) == "FAIL"
    assert aggregate_policy("INCOMPLETE", None) == "INCOMPLETE"
    assert aggregate_policy("INVALID", None) == "INVALID"
    assert aggregate_policy("EXECUTION_ERROR", None) == "EXECUTION_ERROR"
    assert aggregate_policy("EVALUABLE", passing[:2]) == "EXECUTION_ERROR"
    assert aggregate_policy(
        "EVALUABLE", [passing[0], passing[0], passing[2]]
    ) == "EXECUTION_ERROR"


def test_analysis_normalization_is_repeatable() -> None:
    analysis = build_analysis_document(
        _injected_snapshot(),
        _control_snapshot(),
    )
    assert canonicalize_analysis(analysis) == canonicalize_analysis(analysis)

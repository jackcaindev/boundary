"""Exact ADR 003 Phase 1 scenario-policy aggregation."""

from __future__ import annotations

from boundary.domain.evaluation import (
    AssertionResult,
    EvaluabilityAggregate,
    PolicyResult,
)


REQUIRED_ASSERTIONS = {
    "P1.RETRY_LIMIT",
    "P1.DEGRADED_RESULT",
    "P1.RUN_WITHIN_BUDGET",
}


def aggregate_policy(
    evaluability: EvaluabilityAggregate,
    assertions: list[AssertionResult] | None,
) -> PolicyResult:
    if evaluability == "INVALID":
        return "INVALID"
    if evaluability == "EXECUTION_ERROR":
        return "EXECUTION_ERROR"
    if evaluability == "INCOMPLETE":
        return "INCOMPLETE"
    if evaluability != "EVALUABLE" or assertions is None:
        return "EXECUTION_ERROR"
    identifiers = [result.assertion_id for result in assertions]
    if len(identifiers) != 3 or set(identifiers) != REQUIRED_ASSERTIONS:
        return "EXECUTION_ERROR"
    if any(result.outcome == "FAIL" for result in assertions):
        return "FAIL"
    if all(result.outcome == "PASS" for result in assertions):
        return "PASS"
    return "EXECUTION_ERROR"

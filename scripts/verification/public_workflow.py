#!/usr/bin/env python3
"""Bounded public-API verification for the one Phase 1 workflow."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


CONCLUSION = "The fixed tested-agent version passes this scenario policy."


class VerificationFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PublicClient:
    base_url: str
    request_timeout_seconds: float = 5.0

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(
            f"{self.base_url.rstrip('/')}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.request_timeout_seconds) as response:
                document = json.load(response)
        except HTTPError as error:
            try:
                problem = json.load(error)
            except Exception:
                problem = {}
            code = problem.get("code", f"HTTP_{error.code}")
            raise VerificationFailure(str(code), f"{method} {path} returned {error.code}") from error
        except (URLError, TimeoutError) as error:
            raise VerificationFailure("PUBLIC_ROUTE_UNAVAILABLE", f"{method} {path} failed") from error
        if not isinstance(document, dict):
            raise VerificationFailure("MALFORMED_PUBLIC_RESPONSE", f"{method} {path} was not an object")
        return document


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise VerificationFailure(code, message)


def poll(
    description: str,
    fetch: Callable[[], dict[str, Any]],
    terminal: Callable[[dict[str, Any]], bool],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = fetch()
        if terminal(last):
            return last
        time.sleep(0.25)
    safe_state = None if last is None else {
        key: last.get(key)
        for key in ("campaign_id", "comparison_id", "operational_status", "status", "current_step")
    }
    raise VerificationFailure("BOUNDED_POLL_TIMEOUT", f"{description} did not complete: {safe_state}")


def evidence(client: PublicClient, run_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    after = 0
    while True:
        page = client.request(
            "GET",
            f"/api/v1/runs/{run_id}/evidence?after_receipt_seq={after}&limit=100",
        )
        page_items = page.get("items")
        require(isinstance(page_items, list), "MALFORMED_EVIDENCE", "evidence items are absent")
        items.extend(page_items)
        next_after = page.get("next_after_receipt_seq")
        if next_after is None:
            break
        require(isinstance(next_after, int) and next_after > after, "MALFORMED_EVIDENCE_CURSOR", "evidence cursor did not advance")
        after = next_after
    receipts = [item.get("receipt_seq") for item in items]
    require(receipts == sorted(receipts) and len(receipts) == len(set(receipts)), "EVIDENCE_NOT_ORDERED", "receipt order is not strict")
    return items


def execute_complete_workflow(client: PublicClient, attempt: int) -> dict[str, Any]:
    identity = f"task10-{attempt}-{uuid4()}"
    accepted = client.request(
        "POST",
        "/api/v1/campaigns/bundled-tool-timeout",
        body={},
        idempotency_key=f"{identity}-campaign",
    )
    campaign_id = str(accepted.get("campaign_id"))
    campaign = poll(
        "vulnerable campaign",
        lambda: client.request("GET", f"/api/v1/campaigns/{campaign_id}"),
        lambda value: value.get("terminal") is True,
        timeout_seconds=90,
    )
    control_run_id = str(campaign.get("control_run_id"))
    injected_run_id = str(campaign.get("injected_run_id"))
    regression_case_id = str(campaign.get("regression_case_id"))
    control = client.request("GET", f"/api/v1/runs/{control_run_id}")
    vulnerable = client.request("GET", f"/api/v1/runs/{injected_run_id}")
    require(control.get("operational_status") == "completed", "VULNERABLE_CONTROL_NOT_SUCCESSFUL", "vulnerable control did not complete")
    require(control.get("expected_tested_agent_version") == "vulnerable-v1", "VULNERABLE_CONTROL_VERSION", "vulnerable control version is wrong")
    require(vulnerable.get("policy_result") == "FAIL", "VULNERABLE_NOT_FAIL", "vulnerable injected run did not FAIL")
    injection = vulnerable.get("injection_boundary") or {}
    divergence = vulnerable.get("first_unsafe_divergence") or {}
    symptoms = vulnerable.get("downstream_symptoms") or []
    require(injection.get("boundary") == "tool_execution", "INJECTION_BOUNDARY_MISSING", "tool injection boundary is missing")
    require(injection.get("realized_timeout_ordinals") == [0, 1], "INJECTION_PROOF_INCOMPLETE", "both timeout effects were not proven")
    require(divergence.get("boundary") == "retry_control" and divergence.get("retry_ordinal") == 2, "UNSAFE_DIVERGENCE_MISSING", "ordinal 2 retry divergence is missing")
    require(isinstance(symptoms, list) and len(symptoms) > 0, "DOWNSTREAM_SYMPTOMS_MISSING", "downstream symptoms are absent")
    vulnerable_evidence = evidence(client, injected_run_id)
    require(len(vulnerable_evidence) > 0, "VULNERABLE_EVIDENCE_EMPTY", "vulnerable evidence is empty")

    materialized = client.request(
        "POST",
        f"/api/v1/runs/{injected_run_id}/regression-case",
        body={},
        idempotency_key=f"{identity}-materialize",
    )
    require(materialized.get("regression_case_id") == regression_case_id, "REGRESSION_ID_MISMATCH", "materialization returned another case")
    regression = client.request("GET", f"/api/v1/regression-cases/{regression_case_id}")
    require((regression.get("artifact") or {}).get("source_run_id") == injected_run_id, "REGRESSION_PROVENANCE_MISMATCH", "case source run is wrong")

    rerun = client.request(
        "POST",
        f"/api/v1/regression-cases/{regression_case_id}/reruns",
        body={"mode": "version_comparison", "tested_agent_version": "fixed-v1"},
        idempotency_key=f"{identity}-rerun",
    )
    rerun_id = str(rerun.get("rerun_id"))
    rerun_campaign_id = str(rerun.get("campaign_id"))
    comparison_id = str(rerun.get("comparison_id"))
    fixed_campaign = poll(
        "fixed rerun campaign",
        lambda: client.request("GET", f"/api/v1/campaigns/{rerun_campaign_id}"),
        lambda value: value.get("terminal") is True,
        timeout_seconds=90,
    )
    fixed_control_run_id = str(fixed_campaign.get("control_run_id"))
    fixed_injected_run_id = str(fixed_campaign.get("injected_run_id"))
    fixed_control = client.request("GET", f"/api/v1/runs/{fixed_control_run_id}")
    fixed = client.request("GET", f"/api/v1/runs/{fixed_injected_run_id}")
    comparison = poll(
        "version comparison",
        lambda: client.request("GET", f"/api/v1/comparisons/{comparison_id}"),
        lambda value: value.get("terminal") is True,
        timeout_seconds=30,
    )
    require(fixed_control.get("operational_status") == "completed", "FIXED_CONTROL_NOT_SUCCESSFUL", "fixed control did not complete")
    require(fixed_control.get("expected_tested_agent_version") == "fixed-v1", "FIXED_CONTROL_VERSION", "fixed control version is wrong")
    require(fixed.get("policy_result") == "PASS", "FIXED_NOT_PASS", "fixed injected run did not PASS")
    require(comparison.get("status") == "valid", "COMPARISON_NOT_VALID", "comparison is not valid")
    require(comparison.get("scoped_conclusion") == CONCLUSION, "SCOPED_CONCLUSION_MISMATCH", "scoped conclusion is not exact")
    rows = comparison.get("completed_invariance_rows")
    require(isinstance(rows, list) and len(rows) > 0, "INVARIANCE_NOT_COMPLETED", "completed invariance rows are absent")
    require(not comparison.get("mismatches"), "INVARIANCE_MISMATCH", "comparison has invariant mismatches")
    return {
        "campaign_id": campaign_id,
        "vulnerable_control_run_id": control_run_id,
        "vulnerable_injected_run_id": injected_run_id,
        "vulnerable_policy_result": vulnerable.get("policy_result"),
        "regression_case_id": regression_case_id,
        "rerun_id": rerun_id,
        "rerun_campaign_id": rerun_campaign_id,
        "fixed_control_run_id": fixed_control_run_id,
        "fixed_injected_run_id": fixed_injected_run_id,
        "fixed_policy_result": fixed.get("policy_result"),
        "comparison_id": comparison_id,
        "comparison_status": comparison.get("status"),
        "scoped_conclusion": comparison.get("scoped_conclusion"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--attempts", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    require(arguments.attempts > 0, "INVALID_ATTEMPT_COUNT", "attempt count must be positive")
    client = PublicClient(arguments.base_url)
    record: dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_url": arguments.base_url,
        "requested_attempts": arguments.attempts,
        "attempts": [],
    }
    for number in range(1, arguments.attempts + 1):
        started = time.monotonic()
        attempt: dict[str, Any] = {"attempt": number}
        try:
            attempt.update(execute_complete_workflow(client, number))
            attempt["success"] = True
            attempt["failure_reason_code"] = None
        except VerificationFailure as error:
            attempt["success"] = False
            attempt["failure_reason_code"] = error.code
            attempt["failure_summary"] = str(error)
        except Exception as error:
            attempt["success"] = False
            attempt["failure_reason_code"] = "UNEXPECTED_HARNESS_ERROR"
            attempt["failure_summary"] = type(error).__name__
        attempt["duration_seconds"] = round(time.monotonic() - started, 3)
        record["attempts"].append(attempt)
        print(json.dumps(attempt, sort_keys=True), flush=True)
    successes = sum(bool(item["success"]) for item in record["attempts"])
    record["completed_at"] = datetime.now(timezone.utc).isoformat()
    record["successes"] = successes
    record["failures"] = arguments.attempts - successes
    record["gate_passed"] = successes >= 9 and arguments.attempts == 10
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return 0 if record["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

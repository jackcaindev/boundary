#!/usr/bin/env python3
"""Kill Boundary during an injected activation and audit public terminal state."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from typing import Any
from uuid import uuid4

from public_workflow import PublicClient, VerificationFailure, evidence, poll, require


def wait_for_public_route(client: PublicClient, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            client.request(
                "GET",
                "/api/v1/campaigns/00000000-0000-4000-8000-000000000000",
            )
        except VerificationFailure as error:
            if error.code == "CAMPAIGN_NOT_FOUND":
                return
        time.sleep(0.25)
    raise VerificationFailure("BOUNDARY_RESTART_TIMEOUT", "public API did not recover")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    client = PublicClient(arguments.base_url)
    record: dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_url": arguments.base_url,
    }
    result = 1
    try:
        accepted = client.request(
            "POST",
            "/api/v1/campaigns/bundled-tool-timeout",
            body={},
            idempotency_key=f"task10-physical-{uuid4()}",
        )
        campaign_id = str(accepted.get("campaign_id"))
        record["campaign_id"] = campaign_id

        injected: dict[str, Any] | None = None
        pre_interruption_activation_count = 0
        pre_interruption_effect_count = 0
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            campaign = client.request("GET", f"/api/v1/campaigns/{campaign_id}")
            injected_run_id = campaign.get("injected_run_id")
            if injected_run_id is not None:
                candidate = client.request("GET", f"/api/v1/runs/{injected_run_id}")
                partial = evidence(client, str(injected_run_id))
                partial_types = [str(item.get("event_type")) for item in partial]
                pre_interruption_activation_count = partial_types.count(
                    "boundary.fault_activation_started"
                )
                pre_interruption_effect_count = partial_types.count(
                    "boundary.fault_effect_realized"
                )
                if (
                    candidate.get("operational_status") == "running"
                    and pre_interruption_activation_count
                    > pre_interruption_effect_count
                ):
                    injected = candidate
                    break
            require(campaign.get("terminal") is not True, "INTERRUPTION_WINDOW_MISSED", "campaign completed before interruption")
            time.sleep(0.01)
        require(injected is not None, "INTERRUPTION_WINDOW_MISSED", "unproven injected activation was not observed")
        injected_run_id = str(injected["run_id"])
        record["injected_run_id"] = injected_run_id
        record["pre_interruption_status"] = injected["operational_status"]
        record["pre_interruption_activation_started_count"] = pre_interruption_activation_count
        record["pre_interruption_effect_realized_count"] = pre_interruption_effect_count

        subprocess.run(
            ["docker", "compose", "kill", "--signal", "KILL", "boundary"],
            check=True,
        )
        record["physical_action"] = "docker compose kill --signal KILL boundary"
        subprocess.run(
            ["docker", "compose", "up", "--detach", "boundary"],
            check=True,
        )
        wait_for_public_route(client, 60)

        campaign = poll(
            "reconciled campaign",
            lambda: client.request("GET", f"/api/v1/campaigns/{campaign_id}"),
            lambda value: value.get("terminal") is True,
            timeout_seconds=60,
        )
        run = client.request("GET", f"/api/v1/runs/{injected_run_id}")
        retained = evidence(client, injected_run_id)
        event_types = [str(item.get("event_type")) for item in retained]
        activation_count = event_types.count("boundary.fault_activation_started")
        effect_count = event_types.count("boundary.fault_effect_realized")
        record.update(
            {
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "campaign_terminal_status": campaign.get("operational_status"),
                "campaign_failure_reason": campaign.get("failure_reason"),
                "run_operational_status": run.get("operational_status"),
                "run_policy_result": run.get("policy_result"),
                "evidence_set_id": run.get("evidence_set_id"),
                "retained_evidence_count": len(retained),
                "activation_started_count": activation_count,
                "effect_realized_count": effect_count,
                "reconciliation_event_present": "boundary.reconciliation.execution_error" in event_types,
            }
        )
        require(campaign.get("operational_status") == "failed", "AMBIGUOUS_CAMPAIGN_NOT_FAILED", "ambiguous campaign was not failed")
        require(campaign.get("failure_reason") == "RUNTIME_LOST_UNPROVEN", "AMBIGUOUS_REASON_MISSING", "runtime-loss reason is missing")
        require(run.get("operational_status") == "failed", "AMBIGUOUS_RUN_NOT_FAILED", "ambiguous run was not failed")
        require(run.get("policy_result") == "EXECUTION_ERROR", "AMBIGUOUS_RUN_RELABELED", "ambiguous run was not EXECUTION_ERROR")
        require(len(retained) > 0, "PARTIAL_EVIDENCE_NOT_RETAINED", "partial evidence was not retained")
        require(activation_count > effect_count, "UNPROVEN_ACTIVATION_NOT_OBSERVED", "physical interruption did not hit an unproven activation")
        require("boundary.reconciliation.execution_error" in event_types, "RECONCILIATION_EVIDENCE_MISSING", "reconciliation evidence is missing")
        record["gate_passed"] = True
        result = 0
    except VerificationFailure as error:
        record["gate_passed"] = False
        record["failure_reason_code"] = error.code
        record["failure_summary"] = str(error)
    except (OSError, subprocess.CalledProcessError) as error:
        record["gate_passed"] = False
        record["failure_reason_code"] = "CONTAINER_CONTROL_FAILED"
        record["failure_summary"] = type(error).__name__
    finally:
        record.setdefault("completed_at", datetime.now(timezone.utc).isoformat())
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(main())

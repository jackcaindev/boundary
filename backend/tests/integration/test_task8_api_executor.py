from __future__ import annotations

import asyncio
import os
import time
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.persistence.tables import campaigns, comparisons, evidence_sets


pytestmark = [
    pytest.mark.integration,
    pytest.mark.compose,
    pytest.mark.asyncio(loop_scope="session"),
]


async def _poll(
    client: httpx.AsyncClient,
    path: str,
    *,
    terminal,
    timeout: float = 20.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(path)
        assert response.status_code == 200, response.text
        document = response.json()
        if terminal(document):
            return document
        await asyncio.sleep(0.1)
    pytest.fail(f"resource did not become terminal: {path}")


async def _assert_public_links_resolve(
    client: httpx.AsyncClient, *documents: dict
) -> None:
    paths: set[str] = set()

    def collect(value) -> None:
        if isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, str) and value.startswith("/api/v1/"):
            paths.add(value)

    for document in documents:
        collect(document)
    for path in sorted(paths):
        response = await client.get(path)
        assert response.status_code == 200, (path, response.text)


async def test_public_api_executes_fail_case_fixed_pass_and_verifies_reads(
    database_engine: AsyncEngine,
) -> None:
    async with httpx.AsyncClient(
        base_url=os.environ["BOUNDARY_INTERNAL_BASE_URL"],
        timeout=10.0,
    ) as client:
        missing_key = await client.post(
            "/api/v1/campaigns/bundled-tool-timeout", json={}
        )
        assert missing_key.status_code == 422
        assert missing_key.json()["code"] == "INVALID_REQUEST"

        key = f"task8-campaign-{uuid4()}"
        started_at = time.monotonic()
        accepted = await client.post(
            "/api/v1/campaigns/bundled-tool-timeout",
            headers={"Idempotency-Key": key},
            json={},
        )
        elapsed = time.monotonic() - started_at
        assert accepted.status_code == 202, accepted.text
        assert elapsed < 1.0
        accepted_body = accepted.json()
        campaign_id = UUID(accepted_body["campaign_id"])
        control_run_id = UUID(accepted_body["control_run_id"])

        replay = await client.post(
            "/api/v1/campaigns/bundled-tool-timeout",
            headers={"Idempotency-Key": key},
            json={},
        )
        assert replay.status_code == 200
        assert replay.json()["campaign_id"] == str(campaign_id)
        assert replay.json()["control_run_id"] == str(control_run_id)
        assert replay.json()["replayed"] is True

        campaign = await _poll(
            client,
            f"/api/v1/campaigns/{campaign_id}",
            terminal=lambda item: item["terminal"],
        )
        assert campaign["operational_status"] == "completed"
        assert UUID(campaign["control_run_id"]) == control_run_id
        injected_run_id = UUID(campaign["injected_run_id"])
        regression_case_id = UUID(campaign["regression_case_id"])

        injected = (
            await client.get(f"/api/v1/runs/{injected_run_id}")
        ).json()
        assert injected["operational_status"] in {"completed", "timed_out"}
        assert injected["policy_result"] == "FAIL"
        assert injected["finalizer_identity"] == (
            "boundary.phase1.evidence-finalizer/v1"
        )
        assert injected["evaluability"]["aggregate"] == "EVALUABLE"
        assert {
            item["assertion_id"]: item["outcome"]
            for item in injected["assertions"]
        } == {
            "P1.RETRY_LIMIT": "FAIL",
            "P1.DEGRADED_RESULT": "FAIL",
            "P1.RUN_WITHIN_BUDGET": "FAIL",
        }
        assert injected["injection_boundary"]["boundary"] == "tool_execution"
        assert injected["first_unsafe_divergence"]["retry_ordinal"] == 2

        first_page = await client.get(
            f"/api/v1/runs/{injected_run_id}/evidence",
            params={"after_receipt_seq": 0, "limit": 3},
        )
        assert first_page.status_code == 200
        first_items = first_page.json()["items"]
        assert [item["receipt_seq"] for item in first_items] == [1, 2, 3]
        assert {item["authority"] for item in first_items} <= {
            "Boundary",
            "tested-agent",
        }
        assert all("audit_seq" not in item for item in first_items)

        case_response = await client.get(
            f"/api/v1/regression-cases/{regression_case_id}"
        )
        assert case_response.status_code == 200, case_response.text
        assert (
            case_response.json()["artifact"]["source_run_id"]
            == str(injected_run_id)
        )

        rerun_key = f"task8-rerun-{uuid4()}"
        rerun = await client.post(
            f"/api/v1/regression-cases/{regression_case_id}/reruns",
            headers={"Idempotency-Key": rerun_key},
            json={
                "mode": "version_comparison",
                "tested_agent_version": "fixed-v1",
            },
        )
        assert rerun.status_code == 202, rerun.text
        rerun_body = rerun.json()
        comparison_id = UUID(rerun_body["comparison_id"])
        assert "rerun" not in campaign["links"]
        assert "rerun" not in rerun_body["links"]
        await _assert_public_links_resolve(
            client,
            campaign,
            case_response.json(),
            rerun_body,
        )

        rerun_replay = await client.post(
            f"/api/v1/regression-cases/{regression_case_id}/reruns",
            headers={"Idempotency-Key": rerun_key},
            json={
                "mode": "version_comparison",
                "tested_agent_version": "fixed-v1",
            },
        )
        assert rerun_replay.status_code == 200
        assert rerun_replay.json()["rerun_id"] == rerun_body["rerun_id"]
        assert rerun_replay.json()["campaign_id"] == rerun_body["campaign_id"]
        assert rerun_replay.json()["comparison_id"] == str(comparison_id)

        conflicting = await client.post(
            f"/api/v1/regression-cases/{regression_case_id}/reruns",
            headers={"Idempotency-Key": rerun_key},
            json={
                "mode": "reproduction",
                "tested_agent_version": "vulnerable-v1",
            },
        )
        assert conflicting.status_code == 409
        assert conflicting.json()["code"] == "IDEMPOTENCY_CONFLICT"

        comparison = await _poll(
            client,
            f"/api/v1/comparisons/{comparison_id}",
            terminal=lambda item: item["terminal"],
        )
        assert comparison["status"] == "valid"
        assert comparison["source_policy_result"] == "FAIL"
        assert comparison["candidate_policy_result"] == "PASS"
        assert comparison["mismatches"] == []
        assert comparison["scoped_conclusion"] == (
            "The fixed tested-agent version passes this scenario policy."
        )

        candidate = (
            await client.get(
                f"/api/v1/runs/{comparison['candidate_run_id']}"
            )
        ).json()
        assert candidate["operational_status"] == "completed"
        assert candidate["policy_result"] == "PASS"
        assert [
            item["outcome"] for item in candidate["assertions"]
        ] == ["PASS", "PASS", "PASS"]

        async with database_engine.begin() as connection:
            await connection.execute(
                comparisons.update()
                .where(comparisons.c.comparison_id == comparison_id)
                .values(candidate_tested_agent_version="vulnerable-v1")
            )
        tampered = await client.get(
            f"/api/v1/comparisons/{comparison_id}"
        )
        assert tampered.status_code == 500
        assert tampered.json()["code"] == "COMPARISON_INTEGRITY_FAILED"
        assert "traceback" not in tampered.text.lower()

        evidence_set_id = UUID(injected["evidence_set_id"])
        unverified_identity = "unverified.evidence-finalizer/v999"
        async with database_engine.begin() as connection:
            evidence_set = (
                await connection.execute(
                    sa.select(evidence_sets).where(
                        evidence_sets.c.evidence_set_id == evidence_set_id
                    )
                )
            ).one()
            await connection.execute(
                evidence_sets.update()
                .where(evidence_sets.c.evidence_set_id == evidence_set_id)
                .values(finalizer_identity=unverified_identity)
            )
        projection_tampered = await client.get(
            f"/api/v1/runs/{injected_run_id}"
        )
        assert projection_tampered.status_code == 200
        assert projection_tampered.json()["finalizer_identity"] == (
            "boundary.phase1.evidence-finalizer/v1"
        )

        malformed_manifest = {
            **evidence_set.manifest,
            "finalizer_identity": unverified_identity,
        }
        async with database_engine.begin() as connection:
            await connection.execute(
                evidence_sets.update()
                .where(evidence_sets.c.evidence_set_id == evidence_set_id)
                .values(manifest=malformed_manifest)
            )
        integrity_failed = await client.get(
            f"/api/v1/runs/{injected_run_id}"
        )
        assert integrity_failed.status_code == 500
        assert integrity_failed.json()["code"] == (
            "EVIDENCE_SET_INTEGRITY_FAILED"
        )
        assert unverified_identity not in integrity_failed.text


async def test_unknown_public_resources_are_404() -> None:
    unknown = uuid4()
    async with httpx.AsyncClient(
        base_url=os.environ["BOUNDARY_INTERNAL_BASE_URL"], timeout=5.0
    ) as client:
        campaign = await client.get(f"/api/v1/campaigns/{unknown}")
        run = await client.get(f"/api/v1/runs/{unknown}")
        case = await client.get(f"/api/v1/regression-cases/{unknown}")
        comparison = await client.get(f"/api/v1/comparisons/{unknown}")

    assert campaign.status_code == 404
    assert run.status_code == 404
    assert case.status_code == 404
    assert comparison.status_code == 404


async def test_pending_and_terminal_campaign_cancellation_is_idempotent() -> None:
    async with httpx.AsyncClient(
        base_url=os.environ["BOUNDARY_INTERNAL_BASE_URL"], timeout=10.0
    ) as client:
        first = await client.post(
            "/api/v1/campaigns/bundled-tool-timeout",
            headers={"Idempotency-Key": f"cancel-blocker-{uuid4()}"},
            json={},
        )
        second = await client.post(
            "/api/v1/campaigns/bundled-tool-timeout",
            headers={"Idempotency-Key": f"cancel-pending-{uuid4()}"},
            json={},
        )
        assert first.status_code == 202
        assert second.status_code == 202
        first_id = UUID(first.json()["campaign_id"])
        second_id = UUID(second.json()["campaign_id"])
        queued_second = (
            await client.get(f"/api/v1/campaigns/{second_id}")
        ).json()
        assert queued_second["operational_status"] == "accepted"
        queued_run = await client.get(
            f"/api/v1/runs/{queued_second['control_run_id']}"
        )
        assert queued_run.status_code == 200
        assert queued_run.json()["operational_status"] == "accepted"
        assert queued_run.json()["evidence_set_id"] is None
        assert queued_run.json()["finalizer_identity"] is None

        cancellation_key = f"cancel-command-{uuid4()}"
        cancelled = await client.post(
            f"/api/v1/campaigns/{second_id}/cancel",
            headers={"Idempotency-Key": cancellation_key},
            json={},
        )
        assert cancelled.status_code == 202
        assert cancelled.json()["operational_status"] == "accepted"
        cancellation_id = cancelled.json()["cancellation_id"]
        assert cancellation_id is not None

        duplicate = await client.post(
            f"/api/v1/campaigns/{second_id}/cancel",
            headers={"Idempotency-Key": cancellation_key},
            json={},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["cancellation_id"] == cancellation_id
        assert duplicate.json()["replayed"] is True

        second_campaign = await _poll(
            client,
            f"/api/v1/campaigns/{second_id}",
            terminal=lambda item: item["terminal"],
        )
        second_run = (
            await client.get(
                f"/api/v1/runs/{second_campaign['control_run_id']}"
            )
        ).json()
        assert second_campaign["operational_status"] == "cancelled"
        assert second_run["operational_status"] == "cancelled"
        assert second_run["reported_tested_agent_id"] is None
        assert second_run["evidence_set_id"] is not None
        evidence = (
            await client.get(
                f"/api/v1/runs/{second_run['run_id']}/evidence",
                params={"limit": 100},
            )
        ).json()["items"]
        assert all(item["source"] == "boundary" for item in evidence)
        assert {item["event_type"] for item in evidence} >= {
            "boundary.campaign_cancellation.requested",
            "boundary.run.terminal",
        }

        completed_first = await _poll(
            client,
            f"/api/v1/campaigns/{first_id}",
            terminal=lambda item: item["terminal"],
        )
        assert completed_first["operational_status"] == "completed"
        terminal_cancel = await client.post(
            f"/api/v1/campaigns/{first_id}/cancel",
            headers={"Idempotency-Key": f"cancel-terminal-{uuid4()}"},
            json={},
        )
        assert terminal_cancel.status_code == 200
        assert terminal_cancel.json()["operational_status"] == "completed"
        assert terminal_cancel.json()["cancel_requested"] is False


async def test_concurrent_identical_starts_converge_to_one_resource(
    database_engine: AsyncEngine,
) -> None:
    key = f"concurrent-start-{uuid4()}"
    async with httpx.AsyncClient(
        base_url=os.environ["BOUNDARY_INTERNAL_BASE_URL"], timeout=10.0
    ) as client:
        responses = await asyncio.gather(
            *[
                client.post(
                    "/api/v1/campaigns/bundled-tool-timeout",
                    headers={"Idempotency-Key": key},
                    json={},
                )
                for _ in range(8)
            ]
        )
        identities = {
            (item.json()["campaign_id"], item.json()["control_run_id"])
            for item in responses
        }
        assert len(identities) == 1
        assert sorted(item.status_code for item in responses) == [
            200,
            200,
            200,
            200,
            200,
            200,
            200,
            202,
        ]
        campaign_id = UUID(next(iter(identities))[0])
        await _poll(
            client,
            f"/api/v1/campaigns/{campaign_id}",
            terminal=lambda item: item["terminal"],
        )

    async with database_engine.connect() as connection:
        campaign_count = await connection.scalar(
            sa.select(sa.func.count()).select_from(campaigns)
        )
    assert campaign_count == 1


async def test_active_injected_cancellation_retains_finalized_evidence() -> None:
    async with httpx.AsyncClient(
        base_url=os.environ["BOUNDARY_INTERNAL_BASE_URL"], timeout=10.0
    ) as client:
        accepted = await client.post(
            "/api/v1/campaigns/bundled-tool-timeout",
            headers={"Idempotency-Key": f"active-cancel-{uuid4()}"},
            json={},
        )
        assert accepted.status_code == 202
        campaign_id = UUID(accepted.json()["campaign_id"])
        deadline = time.monotonic() + 10
        active = None
        while time.monotonic() < deadline:
            current = (
                await client.get(f"/api/v1/campaigns/{campaign_id}")
            ).json()
            if current["current_step"] == "injected" and not current["terminal"]:
                active = current
                break
            await asyncio.sleep(0.05)
        assert active is not None

        cancelled = await client.post(
            f"/api/v1/campaigns/{campaign_id}/cancel",
            headers={"Idempotency-Key": f"active-cancel-command-{uuid4()}"},
            json={},
        )
        assert cancelled.status_code in {200, 202}
        terminal = await _poll(
            client,
            f"/api/v1/campaigns/{campaign_id}",
            terminal=lambda item: item["terminal"],
        )
        assert terminal["operational_status"] == "cancelled"
        injected_run_id = UUID(terminal["injected_run_id"])
        run = (await client.get(f"/api/v1/runs/{injected_run_id}")).json()
        assert run["operational_status"] in {"cancelled", "timed_out"}
        assert run["evidence_set_id"] is not None
        assert run["policy_result"] in {
            "INCOMPLETE",
            "INVALID",
            "EXECUTION_ERROR",
            "FAIL",
        }

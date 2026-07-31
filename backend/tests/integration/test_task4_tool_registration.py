from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import rfc8785
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.evidence.canonical import (
    canonicalize_fault_definition,
    fault_definition_digest,
)
from boundary.injection.capability import (
    CONTROL_TOOL_IDENTITY,
    create_control_capability,
    retire_capability,
)
from boundary.execution.injected import (
    bind_control_tested_input,
    create_injected_sibling,
)
from boundary.injection.contract_v1 import (
    LookupArguments,
    LookupRequest,
    LookupResponse,
    ToolProblem,
)
from boundary.injection.tool_stub import (
    CapabilityIdentityMismatch,
    DuplicateRegistration,
    InactiveCapability,
    InvalidCapability,
    register_tool_call,
)
from boundary.main import create_app
from boundary.persistence.tables import (
    evidence_records,
    fault_activations,
    run_capabilities,
    runs,
    tool_calls,
)
from boundary.persistence.transactions import (
    AcceptanceCommand,
    CanonicalDocument,
    accept_campaign_run,
    prepare_campaign_run_acceptance,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"
)


async def _accepted_run(engine: AsyncEngine):
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    definition = Phase1FaultDefinition.model_validate(raw)
    canonical_bytes = canonicalize_fault_definition(definition)
    return await accept_campaign_run(
        engine,
        prepare_campaign_run_acceptance(
            AcceptanceCommand(
                idempotency_key=f"task4-{uuid4()}",
                contract_version="1",
                scenario_id="phase1.tool-timeout",
                scenario_version=1,
                tested_agent_id="boundary.sample-agent",
                tested_agent_version="vulnerable-v1",
                run_definition=CanonicalDocument(
                    schema_version=1,
                    document=definition.model_dump(mode="json"),
                    canonical_bytes=canonical_bytes,
                    digest=fault_definition_digest(definition),
                ),
            )
        ),
    )


async def _control_binding(engine: AsyncEngine):
    accepted = await _accepted_run(engine)
    grant = await create_control_capability(
        engine,
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        tool_identity=CONTROL_TOOL_IDENTITY,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    return accepted, grant


async def _injected_binding(engine: AsyncEngine):
    accepted = await _accepted_run(engine)
    await bind_control_tested_input(
        engine,
        run_id=accepted.run_id,
        tested_input="phase1 lookup",
    )
    control_grant = await create_control_capability(
        engine,
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        tool_identity=CONTROL_TOOL_IDENTITY,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    await register_tool_call(
        engine,
        route_run_id=accepted.run_id,
        capability_secret=control_grant.capability_secret,
        request=_request(
            run_id=accepted.run_id,
            trace_id=accepted.trace_id,
        ),
    )
    await retire_capability(engine, control_grant.capability_record_id)
    async with engine.begin() as connection:
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == accepted.run_id)
            .values(
                operational_status="completed",
                reported_tested_agent_id="boundary.sample-agent",
                reported_tested_agent_version="vulnerable-v1",
            )
        )
    sibling = await create_injected_sibling(
        engine,
        control_run_id=accepted.run_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    injected = type(accepted)(
        campaign_id=sibling.campaign_id,
        run_id=sibling.run_id,
        trace_id=sibling.trace_id,
        evidence_id=sibling.accepted_evidence_id,
        replayed=False,
    )
    return injected, sibling.capability, sibling.fault_id


def _request(
    *,
    run_id: UUID,
    trace_id: UUID,
    tool_call_id: UUID | None = None,
    fault_id: UUID | None = None,
) -> LookupRequest:
    return LookupRequest(
        contract_version="1",
        run_id=run_id,
        trace_id=trace_id,
        tool_identity="boundary.phase1.lookup",
        tool_call_id=tool_call_id or uuid4(),
        fault_id=fault_id,
        arguments=LookupArguments(query="phase1 lookup"),
    )


async def _state(engine: AsyncEngine, run_id: UUID):
    async with engine.connect() as connection:
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == run_id)
            )
        ).one()
        evidence = (
            await connection.execute(
                sa.select(evidence_records)
                .where(evidence_records.c.run_id == run_id)
                .order_by(
                    evidence_records.c.receipt_seq.asc().nulls_last(),
                    evidence_records.c.audit_seq.asc().nulls_last(),
                )
            )
        ).all()
        calls = (
            await connection.execute(
                sa.select(tool_calls)
                .where(tool_calls.c.run_id == run_id)
                .order_by(tool_calls.c.retry_ordinal)
            )
        ).all()
        activations = (
            await connection.execute(
                sa.select(fault_activations)
                .where(fault_activations.c.run_id == run_id)
                .order_by(fault_activations.c.activation_ordinal)
            )
        ).all()
    return run, evidence, calls, activations


async def test_private_route_returns_deterministic_control_result(
    database_engine: AsyncEngine,
) -> None:
    accepted, grant = await _control_binding(database_engine)
    request = _request(
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
    )
    app = create_app(engine=database_engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://boundary",
    ) as client:
        response = await client.post(
            f"/internal/v1/runs/{accepted.run_id}/tools/phase1-lookup",
            headers={
                "Authorization": f"Bearer {grant.capability_secret}"
            },
            content=request.model_dump_json(),
        )

    parsed = LookupResponse.model_validate_json(response.content)
    run, evidence, calls, activations = await _state(
        database_engine,
        accepted.run_id,
    )
    assert response.status_code == 200
    assert parsed.retry_ordinal == 0
    assert parsed.result.status == "found"
    assert parsed.result.value == "control-ok"
    assert len(calls) == 1
    assert calls[0].tool_call_id == request.tool_call_id
    assert calls[0].retry_ordinal == 0
    assert calls[0].registration_outcome == "no_fault_configured"
    assert (
        calls[0].response_disposition
        == "success_response_committed"
    )
    assert calls[0].response_evidence_id == evidence[-1].evidence_id
    assert calls[0].response_digest == sha256(
        rfc8785.dumps(response.json())
    ).hexdigest()
    assert activations == []
    assert run.next_tool_ordinal == 1
    assert run.next_receipt_seq == 5
    assert run.next_audit_seq == 1
    assert [row.receipt_seq for row in evidence] == [1, 2, 3, 4]
    assert [row.event_type for row in evidence[-3:]] == [
        "boundary.tool_call.observed",
        "boundary.tool_call.ordinal_assigned",
        "boundary.tool_result.committed",
    ]
    assert evidence[-2].caused_by_event_id == (
        evidence[-3].source_event_id
    )
    assert evidence[-1].caused_by_event_id == evidence[-2].source_event_id
    assert evidence[-1].payload["response_digest"] == (
        calls[0].response_digest
    )
    assert (
        evidence[-1].payload["disposition"]
        == "success_response_committed"
    )


@pytest.mark.parametrize(
    ("misuse", "expected_status", "expected_code"),
    [
        ("missing", 401, "MISSING_CAPABILITY"),
        ("invalid", 401, "INVALID_CAPABILITY"),
        ("wrong_run", 403, "CAPABILITY_IDENTITY_MISMATCH"),
        ("wrong_trace", 403, "CAPABILITY_IDENTITY_MISMATCH"),
        ("wrong_tool", 422, "INVALID_TOOL_REQUEST"),
        ("unexpected_fault", 403, "CAPABILITY_IDENTITY_MISMATCH"),
        ("expired", 401, "CAPABILITY_INACTIVE"),
        ("retired", 401, "CAPABILITY_INACTIVE"),
        ("terminal_run", 401, "CAPABILITY_INACTIVE"),
        ("closed_evidence", 401, "CAPABILITY_INACTIVE"),
    ],
)
async def test_capability_misuse_does_not_register_or_advance(
    database_engine: AsyncEngine,
    misuse: str,
    expected_status: int,
    expected_code: str,
) -> None:
    accepted, grant = await _control_binding(database_engine)
    request = _request(
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
    )
    document = request.model_dump(mode="json")
    route_run_id = accepted.run_id
    headers: dict[str, str] = {
        "Authorization": f"Bearer {grant.capability_secret}"
    }

    if misuse == "missing":
        headers = {}
    elif misuse == "invalid":
        headers = {"Authorization": "Bearer invalid-capability"}
    elif misuse == "wrong_run":
        document["run_id"] = str(uuid4())
    elif misuse == "wrong_trace":
        document["trace_id"] = str(uuid4())
    elif misuse == "wrong_tool":
        document["tool_identity"] = "boundary.phase1.other"
    elif misuse == "unexpected_fault":
        document["fault_id"] = str(uuid4())
    elif misuse == "expired":
        async with database_engine.begin() as connection:
            await connection.execute(
                run_capabilities.update()
                .where(run_capabilities.c.run_id == accepted.run_id)
                .values(
                    expires_at=datetime.now(timezone.utc)
                    - timedelta(seconds=1)
                )
            )
    elif misuse == "retired":
        await retire_capability(
            database_engine,
            grant.capability_record_id,
        )
    elif misuse == "terminal_run":
        async with database_engine.begin() as connection:
            await connection.execute(
                runs.update()
                .where(runs.c.run_id == accepted.run_id)
                .values(operational_status="completed")
            )
    elif misuse == "closed_evidence":
        async with database_engine.begin() as connection:
            await connection.execute(
                runs.update()
                .where(runs.c.run_id == accepted.run_id)
                .values(evidence_open=False)
            )

    app = create_app(engine=database_engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://boundary",
    ) as client:
        response = await client.post(
            f"/internal/v1/runs/{route_run_id}/tools/phase1-lookup",
            headers=headers,
            json=document,
        )

    problem = ToolProblem.model_validate_json(response.content)
    run, evidence, calls, activations = await _state(
        database_engine,
        accepted.run_id,
    )
    persisted = b"".join(
        row.payload_canonical_bytes for row in evidence
    )
    assert response.status_code == expected_status
    assert problem.error.code == expected_code
    assert calls == []
    assert activations == []
    assert run.next_tool_ordinal == 0
    assert run.next_receipt_seq == 2
    assert run.next_audit_seq == 1
    assert [row.receipt_seq for row in evidence] == [1]
    assert grant.capability_secret not in response.text
    assert grant.capability_secret.encode() not in persisted


async def test_distinct_concurrent_calls_get_contiguous_unique_order(
    database_engine: AsyncEngine,
) -> None:
    accepted, grant = await _control_binding(database_engine)
    requests = [
        _request(
            run_id=accepted.run_id,
            trace_id=accepted.trace_id,
        )
        for _ in range(12)
    ]
    results = await asyncio.gather(
        *[
            register_tool_call(
                database_engine,
                route_run_id=accepted.run_id,
                capability_secret=grant.capability_secret,
                request=request,
            )
            for request in requests
        ]
    )

    run, evidence, calls, activations = await _state(
        database_engine,
        accepted.run_id,
    )
    assert sorted(result.retry_ordinal for result in results) == list(
        range(12)
    )
    assert [row.retry_ordinal for row in calls] == list(range(12))
    assert len({row.tool_call_id for row in calls}) == 12
    assert [row.receipt_seq for row in evidence] == list(range(1, 38))
    assert activations == []
    assert run.next_tool_ordinal == 12
    assert run.next_receipt_seq == 38
    assert run.next_audit_seq == 1


async def test_concurrent_duplicate_delivery_accepts_one_registration_only(
    database_engine: AsyncEngine,
) -> None:
    accepted, grant = await _control_binding(database_engine)
    request = _request(
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
    )
    app = create_app(engine=database_engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://boundary",
    ) as client:

        async def attempt():
            return await client.post(
                f"/internal/v1/runs/{accepted.run_id}/"
                "tools/phase1-lookup",
                headers={
                    "Authorization": (
                        f"Bearer {grant.capability_secret}"
                    )
                },
                content=request.model_dump_json(),
            )

        responses = await asyncio.gather(*[attempt() for _ in range(10)])
    run, evidence, calls, activations = await _state(
        database_engine,
        accepted.run_id,
    )
    accepted_evidence = [
        row for row in evidence if row.disposition == "accepted"
    ]
    rejected_evidence = [
        row for row in evidence if row.disposition == "rejected"
    ]
    assert [response.status_code for response in responses].count(200) == 1
    assert [response.status_code for response in responses].count(409) == 9
    for response in responses:
        assert grant.capability_secret not in response.text
        if response.status_code == 409:
            assert ToolProblem.model_validate_json(
                response.content
            ).error.code == "DUPLICATE_TOOL_CALL"
    assert len(calls) == 1
    assert calls[0].retry_ordinal == 0
    assert activations == []
    assert [row.receipt_seq for row in accepted_evidence] == [1, 2, 3, 4]
    assert [row.audit_seq for row in rejected_evidence] == list(
        range(1, 10)
    )
    assert all(row.receipt_seq is None for row in rejected_evidence)
    assert all(
        row.caused_by_event_id == calls[0].ordinal_evidence_id
        for row in rejected_evidence
    )
    assert all(
        row.payload["original_arrival_evidence_id"]
        == str(calls[0].arrival_evidence_id)
        for row in rejected_evidence
    )
    persisted_rejections = b"".join(
        row.payload_canonical_bytes for row in rejected_evidence
    )
    assert grant.capability_secret.encode() not in persisted_rejections
    assert b"Authorization" not in persisted_rejections
    assert run.next_tool_ordinal == 1
    assert run.next_receipt_seq == 5
    assert run.next_audit_seq == 10


async def test_concurrent_fault_allocation_is_per_call_and_capped_at_two(
    database_engine: AsyncEngine,
) -> None:
    accepted, grant, fault_id = await _injected_binding(database_engine)
    requests = [
        _request(
            run_id=accepted.run_id,
            trace_id=accepted.trace_id,
            fault_id=fault_id,
        )
        for _ in range(8)
    ]
    results = await asyncio.gather(
        *[
            register_tool_call(
                database_engine,
                route_run_id=accepted.run_id,
                capability_secret=grant.capability_secret,
                request=request,
            )
            for request in requests
        ]
    )
    _, _, calls, activations = await _state(
        database_engine,
        accepted.run_id,
    )

    assert sorted(result.retry_ordinal for result in results) == list(
        range(8)
    )
    assert len(calls) == 8
    assert len(activations) == 2
    assert [row.activation_ordinal for row in activations] == [0, 1]
    assert len({row.tool_call_id for row in activations}) == 2
    assert all(
        row.reservation_state == "pre_effect_reserved"
        for row in activations
    )
    assert {
        row.registration_outcome for row in calls[:2]
    } == {"pre_effect_reserved"}
    assert all(
        row.registration_outcome == "attempt_not_selected"
        for row in calls[2:]
    )


async def test_concurrent_duplicate_fault_call_gets_one_reservation(
    database_engine: AsyncEngine,
) -> None:
    accepted, grant, fault_id = await _injected_binding(database_engine)
    request = _request(
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        fault_id=fault_id,
    )

    async def attempt():
        return await register_tool_call(
            database_engine,
            route_run_id=accepted.run_id,
            capability_secret=grant.capability_secret,
            request=request,
        )

    results = await asyncio.gather(*[attempt() for _ in range(10)])
    run, evidence, calls, activations = await _state(
        database_engine,
        accepted.run_id,
    )
    accepted_results = [
        result
        for result in results
        if not isinstance(result, DuplicateRegistration)
    ]
    duplicate_results = [
        result
        for result in results
        if isinstance(result, DuplicateRegistration)
    ]
    accepted_evidence = [
        row for row in evidence if row.disposition == "accepted"
    ]
    rejected_evidence = [
        row for row in evidence if row.disposition == "rejected"
    ]
    assert len(accepted_results) == 1
    assert accepted_results[0].retry_ordinal == 0
    assert len(duplicate_results) == 9
    assert {
        result.original_retry_ordinal for result in duplicate_results
    } == {0}
    assert len(calls) == 1
    assert len(activations) == 1
    assert activations[0].tool_call_id == request.tool_call_id
    assert all(
        result.original_pre_effect_reservation_id
        == activations[0].activation_id
        for result in duplicate_results
    )
    assert [row.receipt_seq for row in accepted_evidence] == [1, 2, 3]
    assert [row.audit_seq for row in rejected_evidence] == list(
        range(1, 10)
    )
    assert all(row.receipt_seq is None for row in rejected_evidence)
    assert all(
        row.caused_by_event_id == calls[0].ordinal_evidence_id
        for row in rejected_evidence
    )
    assert all(
        row.payload["original_arrival_evidence_id"]
        == str(calls[0].arrival_evidence_id)
        for row in rejected_evidence
    )
    assert all(
        row.payload["original_pre_effect_reservation_id"]
        == str(activations[0].activation_id)
        for row in rejected_evidence
    )
    assert all(
        row.payload["reason"] == "duplicate_tool_call_id"
        for row in rejected_evidence
    )
    assert run.next_tool_ordinal == 1
    assert run.next_receipt_seq == 4
    assert run.next_audit_seq == 10


@pytest.mark.parametrize(
    "failure_point",
    ["tool_call", "activation", "evidence", "counters"],
)
async def test_registration_rollback_leaves_no_partial_state(
    database_engine: AsyncEngine,
    failure_point: str,
) -> None:
    accepted, grant, fault_id = await _injected_binding(database_engine)
    request = _request(
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        fault_id=fault_id,
    )
    with pytest.raises(RuntimeError, match="test failure"):
        await register_tool_call(
            database_engine,
            route_run_id=accepted.run_id,
            capability_secret=grant.capability_secret,
            request=request,
            _fail_after=failure_point,  # type: ignore[arg-type]
        )

    run, evidence, calls, activations = await _state(
        database_engine,
        accepted.run_id,
    )
    assert calls == []
    assert activations == []
    assert [row.receipt_seq for row in evidence] == [1]
    assert run.next_tool_ordinal == 0
    assert run.next_receipt_seq == 2


@pytest.mark.parametrize(
    "failure_point",
    ["tool_call", "evidence", "response_evidence", "counters"],
)
async def test_control_response_rollback_never_claims_success(
    database_engine: AsyncEngine,
    failure_point: str,
) -> None:
    accepted, grant = await _control_binding(database_engine)
    request = _request(
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
    )
    with pytest.raises(RuntimeError, match="test failure"):
        await register_tool_call(
            database_engine,
            route_run_id=accepted.run_id,
            capability_secret=grant.capability_secret,
            request=request,
            _fail_after=failure_point,  # type: ignore[arg-type]
        )

    run, evidence, calls, activations = await _state(
        database_engine,
        accepted.run_id,
    )
    assert calls == []
    assert activations == []
    assert all(
        row.event_type != "boundary.tool_result.committed"
        for row in evidence
    )
    assert [row.receipt_seq for row in evidence] == [1]
    assert run.next_tool_ordinal == 0
    assert run.next_receipt_seq == 2
    assert run.next_audit_seq == 1


@pytest.mark.parametrize(
    "failure_point",
    ["duplicate_evidence", "duplicate_counter"],
)
async def test_duplicate_audit_rollback_keeps_original_only(
    database_engine: AsyncEngine,
    failure_point: str,
) -> None:
    accepted, grant = await _control_binding(database_engine)
    request = _request(
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
    )
    original = await register_tool_call(
        database_engine,
        route_run_id=accepted.run_id,
        capability_secret=grant.capability_secret,
        request=request,
    )
    assert not isinstance(original, DuplicateRegistration)

    with pytest.raises(RuntimeError, match="test failure"):
        await register_tool_call(
            database_engine,
            route_run_id=accepted.run_id,
            capability_secret=grant.capability_secret,
            request=request,
            _fail_after=failure_point,  # type: ignore[arg-type]
        )

    run, evidence, calls, activations = await _state(
        database_engine,
        accepted.run_id,
    )
    assert len(calls) == 1
    assert calls[0].retry_ordinal == original.retry_ordinal == 0
    assert calls[0].response_evidence_id == original.response_evidence_id
    assert activations == []
    assert all(row.disposition == "accepted" for row in evidence)
    assert [row.receipt_seq for row in evidence] == [1, 2, 3, 4]
    assert run.next_tool_ordinal == 1
    assert run.next_receipt_seq == 5
    assert run.next_audit_seq == 1


async def test_activation_constraints_reject_orphan_wrong_fault_and_third_slot(
    database_engine: AsyncEngine,
) -> None:
    accepted, grant, fault_id = await _injected_binding(database_engine)
    request = _request(
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        fault_id=fault_id,
    )
    await register_tool_call(
        database_engine,
        route_run_id=accepted.run_id,
        capability_secret=grant.capability_secret,
        request=request,
    )

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                fault_activations.insert().values(
                    activation_id=uuid4(),
                    run_id=accepted.run_id,
                    tool_call_id=uuid4(),
                    fault_id=fault_id,
                    activation_ordinal=1,
                    reservation_state="pre_effect_reserved",
                )
            )

    second_call = _request(
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        fault_id=fault_id,
    )
    await register_tool_call(
        database_engine,
        route_run_id=accepted.run_id,
        capability_secret=grant.capability_secret,
        request=second_call,
    )
    third_call = _request(
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        fault_id=fault_id,
    )
    await register_tool_call(
        database_engine,
        route_run_id=accepted.run_id,
        capability_secret=grant.capability_secret,
        request=third_call,
    )
    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                fault_activations.insert().values(
                    activation_id=uuid4(),
                    run_id=accepted.run_id,
                    tool_call_id=third_call.tool_call_id,
                    fault_id=uuid4(),
                    activation_ordinal=0,
                    reservation_state="pre_effect_reserved",
                )
            )
    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                fault_activations.insert().values(
                    activation_id=uuid4(),
                    run_id=accepted.run_id,
                    tool_call_id=third_call.tool_call_id,
                    fault_id=fault_id,
                    activation_ordinal=2,
                    reservation_state="pre_effect_reserved",
                )
            )


async def test_registration_rejections_are_safe_domain_errors(
    database_engine: AsyncEngine,
) -> None:
    accepted, grant = await _control_binding(database_engine)
    request = _request(
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
    )
    with pytest.raises(InvalidCapability):
        await register_tool_call(
            database_engine,
            route_run_id=accepted.run_id,
            capability_secret="invalid",
            request=request,
        )
    await retire_capability(
        database_engine,
        grant.capability_record_id,
    )
    with pytest.raises(InactiveCapability):
        await register_tool_call(
            database_engine,
            route_run_id=accepted.run_id,
            capability_secret=grant.capability_secret,
            request=request,
        )
    mismatched = _request(
        run_id=accepted.run_id,
        trace_id=uuid4(),
    )
    with pytest.raises(InactiveCapability):
        await register_tool_call(
            database_engine,
            route_run_id=accepted.run_id,
            capability_secret=grant.capability_secret,
            request=mismatched,
        )
    assert grant.capability_secret not in repr(InvalidCapability())
    assert grant.capability_secret not in repr(
        CapabilityIdentityMismatch()
    )

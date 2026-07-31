from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import rfc8785
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.evidence.canonical import (
    canonicalize_fault_definition,
    fault_definition_digest,
)
from boundary.evidence.collector import (
    EvidenceInvalid,
    EvidenceLimitExceeded,
    ForwardGap,
    IdentityMismatch,
    collect_target_page,
    observe_terminal_watermark,
    record_cancellation_requested,
    record_reported_identity,
    record_safe_rejection,
    record_terminal_status,
    transition_run,
    validate_terminal_collection,
)
from boundary.injection.capability import (
    CONTROL_TOOL_IDENTITY,
    create_control_capability,
    retire_capability,
)
from boundary.persistence.tables import (
    evidence_records,
    run_capabilities,
    runs,
)
from boundary.persistence.transactions import (
    AcceptanceCommand,
    CanonicalDocument,
    accept_campaign_run,
    prepare_campaign_run_acceptance,
)
from boundary.sut.contract_v1 import (
    CompletedPayload,
    DegradedResultEvent,
    DegradedResultPayload,
    EventPage,
    RunCompletedEvent,
    RunStartedEvent,
    RunStatus,
    StartedPayload,
    TerminalResult,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"
)


async def _accepted_run(
    engine: AsyncEngine,
    *,
    key: str = "task3-control",
):
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    definition = Phase1FaultDefinition.model_validate(raw)
    canonical_bytes = canonicalize_fault_definition(definition)
    command = AcceptanceCommand(
        idempotency_key=key,
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
    return await accept_campaign_run(
        engine,
        prepare_campaign_run_acceptance(command),
    )


def _started(run_id: UUID, trace_id: UUID, sequence: int = 1):
    return RunStartedEvent(
        contract_version="1",
        run_id=run_id,
        trace_id=trace_id,
        event_id=uuid4(),
        source="sut",
        event_type="sut.run.started",
        boundary="run",
        producer_seq=sequence,
        payload=StartedPayload(schema_version=1),
    )


def _completed(
    run_id: UUID,
    trace_id: UUID,
    *,
    sequence: int,
    caused_by: UUID | None = None,
    event_id: UUID | None = None,
):
    return RunCompletedEvent(
        contract_version="1",
        run_id=run_id,
        trace_id=trace_id,
        event_id=event_id or uuid4(),
        source="sut",
        event_type="sut.run.completed",
        boundary="run",
        producer_seq=sequence,
        caused_by_event_id=caused_by,
        payload=CompletedPayload(
            schema_version=1,
            outcome_kind="success",
        ),
    )


def _page(run_id, trace_id, events, high=None):
    cursor = events[-1].producer_seq if events else 0
    return EventPage(
        contract_version="1",
        run_id=run_id,
        trace_id=trace_id,
        events=events,
        producer_high_watermark=high if high is not None else cursor,
        next_after_producer_seq=cursor,
    )


async def _insert_existing_event(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    event,
    receipt_seq: int,
) -> None:
    canonical_bytes = rfc8785.dumps(
        event.model_dump(mode="json", exclude_none=True)
    )
    async with engine.begin() as connection:
        await connection.execute(
            evidence_records.insert().values(
                evidence_id=uuid4(),
                run_id=run_id,
                source="sut",
                event_type=event.event_type,
                boundary=event.boundary,
                source_event_id=event.event_id,
                producer_seq=event.producer_seq,
                receipt_seq=receipt_seq,
                caused_by_event_id=event.caused_by_event_id,
                payload_schema_version=1,
                payload=json.loads(canonical_bytes),
                payload_canonical_bytes=canonical_bytes,
                payload_digest=sha256(canonical_bytes).hexdigest(),
                disposition="accepted",
            )
        )
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == run_id)
            .values(next_receipt_seq=receipt_seq + 1)
        )


async def test_capability_is_hash_only_and_bound_to_control(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=30)
    grant = await create_control_capability(
        database_engine,
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        tool_identity=CONTROL_TOOL_IDENTITY,
        expires_at=expiry,
    )
    async with database_engine.connect() as connection:
        row = (
            await connection.execute(sa.select(run_capabilities))
        ).one()

    assert row.capability_record_id == grant.capability_record_id
    assert row.capability_hash == sha256(
        grant.capability_secret.encode("ascii")
    ).hexdigest()
    assert row.run_id == accepted.run_id
    assert row.trace_id == accepted.trace_id
    assert row.tool_identity == CONTROL_TOOL_IDENTITY
    assert row.no_fault_binding is True
    assert row.fault_id is None
    assert row.state == "active"
    assert grant.capability_secret not in repr(grant)

    await retire_capability(
        database_engine,
        grant.capability_record_id,
    )
    await retire_capability(
        database_engine,
        grant.capability_record_id,
    )
    async with database_engine.connect() as connection:
        state = await connection.scalar(
            sa.select(run_capabilities.c.state)
        )
    assert state == "retired"


async def test_contiguous_page_advances_cursor_and_receipt_order(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    first = _started(accepted.run_id, accepted.trace_id)
    second = _completed(
        accepted.run_id,
        accepted.trace_id,
        sequence=2,
        caused_by=first.event_id,
    )

    result = await collect_target_page(
        database_engine,
        run_id=accepted.run_id,
        requested_after=0,
        page=_page(
            accepted.run_id,
            accepted.trace_id,
            [first, second],
        ),
    )
    async with database_engine.connect() as connection:
        run = (
            await connection.execute(
                sa.select(
                    runs.c.target_producer_cursor,
                    runs.c.next_receipt_seq,
                )
            )
        ).one()
        rows = (
            await connection.execute(
                sa.select(
                    evidence_records.c.producer_seq,
                    evidence_records.c.receipt_seq,
                )
                .where(evidence_records.c.source == "sut")
                .order_by(evidence_records.c.receipt_seq)
            )
        ).all()

    assert result.cursor == 2
    assert run.target_producer_cursor == 2
    assert run.next_receipt_seq == 4
    assert rows == [(1, 2), (2, 3)]


async def test_identical_uncommitted_delivery_is_idempotent(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    event = _started(accepted.run_id, accepted.trace_id)
    await _insert_existing_event(
        database_engine,
        run_id=accepted.run_id,
        event=event,
        receipt_seq=2,
    )

    result = await collect_target_page(
        database_engine,
        run_id=accepted.run_id,
        requested_after=0,
        page=_page(
            accepted.run_id,
            accepted.trace_id,
            [event],
        ),
    )
    async with database_engine.connect() as connection:
        count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(evidence_records.c.source == "sut")
        )
        run = (
            await connection.execute(
                sa.select(
                    runs.c.target_producer_cursor,
                    runs.c.next_receipt_seq,
                )
            )
        ).one()

    assert result.identical_uncommitted_events == 1
    assert result.inserted_events == 0
    assert count == 1
    assert run == (1, 3)


async def test_conflicting_event_id_reuse_rolls_back_whole_page(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    conflicting_id = uuid4()
    existing = _completed(
        accepted.run_id,
        accepted.trace_id,
        sequence=2,
        event_id=conflicting_id,
    )
    await _insert_existing_event(
        database_engine,
        run_id=accepted.run_id,
        event=existing,
        receipt_seq=2,
    )
    first = _started(accepted.run_id, accepted.trace_id)
    changed = _completed(
        accepted.run_id,
        accepted.trace_id,
        sequence=2,
        event_id=conflicting_id,
        caused_by=first.event_id,
    )

    with pytest.raises(EvidenceInvalid):
        await collect_target_page(
            database_engine,
            run_id=accepted.run_id,
            requested_after=0,
            page=_page(
                accepted.run_id,
                accepted.trace_id,
                [first, changed],
            ),
        )
    async with database_engine.connect() as connection:
        cursor = await connection.scalar(
            sa.select(runs.c.target_producer_cursor)
        )
        count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(evidence_records.c.source == "sut")
        )
    assert cursor == 0
    assert count == 1


async def test_forward_gap_never_advances_cursor(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    gap_event = _started(
        accepted.run_id,
        accepted.trace_id,
        sequence=2,
    )
    with pytest.raises(ForwardGap):
        await collect_target_page(
            database_engine,
            run_id=accepted.run_id,
            requested_after=0,
            page=_page(
                accepted.run_id,
                accepted.trace_id,
                [gap_event],
            ),
        )
    async with database_engine.connect() as connection:
        cursor = await connection.scalar(
            sa.select(runs.c.target_producer_cursor)
        )
    assert cursor == 0


async def test_lower_sequence_after_advancement_is_invalid(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    event = _started(accepted.run_id, accepted.trace_id)
    page = _page(accepted.run_id, accepted.trace_id, [event])
    await collect_target_page(
        database_engine,
        run_id=accepted.run_id,
        requested_after=0,
        page=page,
    )

    with pytest.raises(EvidenceInvalid):
        await collect_target_page(
            database_engine,
            run_id=accepted.run_id,
            requested_after=0,
            page=page,
        )


async def test_terminal_watermark_is_immutable_and_bounds_events(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    await observe_terminal_watermark(
        database_engine,
        run_id=accepted.run_id,
        final_producer_seq=2,
        producer_high_watermark=2,
    )
    await observe_terminal_watermark(
        database_engine,
        run_id=accepted.run_id,
        final_producer_seq=2,
        producer_high_watermark=2,
    )
    with pytest.raises(EvidenceInvalid):
        await observe_terminal_watermark(
            database_engine,
            run_id=accepted.run_id,
            final_producer_seq=3,
            producer_high_watermark=3,
        )
    with pytest.raises(EvidenceInvalid):
        await collect_target_page(
            database_engine,
            run_id=accepted.run_id,
            requested_after=0,
            page=_page(
                accepted.run_id,
                accepted.trace_id,
                [
                    _started(accepted.run_id, accepted.trace_id),
                    _completed(
                        accepted.run_id,
                        accepted.trace_id,
                        sequence=2,
                    ),
                    _started(
                        accepted.run_id,
                        accepted.trace_id,
                        sequence=3,
                    ),
                ],
                high=3,
            ),
        )


async def test_complete_terminal_collection_matches_referenced_event(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    first = _started(accepted.run_id, accepted.trace_id)
    terminal = _completed(
        accepted.run_id,
        accepted.trace_id,
        sequence=2,
        caused_by=first.event_id,
    )
    await observe_terminal_watermark(
        database_engine,
        run_id=accepted.run_id,
        final_producer_seq=2,
        producer_high_watermark=2,
    )
    await collect_target_page(
        database_engine,
        run_id=accepted.run_id,
        requested_after=0,
        page=_page(
            accepted.run_id,
            accepted.trace_id,
            [first, terminal],
        ),
    )
    status = RunStatus(
        contract_version="1",
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        tested_agent_id="boundary.sample-agent",
        tested_agent_version="vulnerable-v1",
        state="completed",
        producer_high_watermark=2,
        final_producer_seq=2,
        terminal_result=TerminalResult(
            contract_version="1",
            run_id=accepted.run_id,
            trace_id=accepted.trace_id,
            tested_agent_id="boundary.sample-agent",
            tested_agent_version="vulnerable-v1",
            state="completed",
            final_producer_seq=2,
            outcome_kind="success",
            output="control-ok",
            event_id=terminal.event_id,
        ),
    )
    await validate_terminal_collection(
        database_engine,
        run_id=accepted.run_id,
        status=status,
    )


async def test_expected_and_reported_identity_remain_separate(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    with pytest.raises(IdentityMismatch):
        await record_reported_identity(
            database_engine,
            run_id=accepted.run_id,
            reported_agent_id="other-agent",
            reported_agent_version="other-version",
        )
    async with database_engine.connect() as connection:
        row = (
            await connection.execute(
                sa.select(
                    runs.c.expected_tested_agent_id,
                    runs.c.expected_tested_agent_version,
                    runs.c.reported_tested_agent_id,
                    runs.c.reported_tested_agent_version,
                )
            )
        ).one()
    assert row == (
        "boundary.sample-agent",
        "vulnerable-v1",
        "other-agent",
        "other-version",
    )


async def test_boundary_transitions_are_accepted_running_completed(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    await transition_run(
        database_engine,
        run_id=accepted.run_id,
        target_status="running",
        reason="target_started",
    )
    await transition_run(
        database_engine,
        run_id=accepted.run_id,
        target_status="completed",
        reason="watermark_collected",
    )
    async with database_engine.connect() as connection:
        status = await connection.scalar(
            sa.select(runs.c.operational_status)
        )
        transitions = (
            await connection.execute(
                sa.select(evidence_records.c.event_type)
                .where(evidence_records.c.source == "boundary")
                .order_by(evidence_records.c.receipt_seq)
            )
        ).scalars().all()
    assert status == "completed"
    assert transitions == [
        "boundary.run.accepted",
        "boundary.run.running",
        "boundary.run.terminal",
    ]


async def test_per_event_limit_rejects_without_cursor_advance(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    oversized = DegradedResultEvent(
        contract_version="1",
        run_id=accepted.run_id,
        trace_id=accepted.trace_id,
        event_id=uuid4(),
        source="sut",
        event_type="sut.degraded_result.produced",
        boundary="agent",
        producer_seq=1,
        payload=DegradedResultPayload(
            schema_version=1,
            result="x" * 65_536,
        ),
    )
    with pytest.raises(EvidenceLimitExceeded):
        await collect_target_page(
            database_engine,
            run_id=accepted.run_id,
            requested_after=0,
            page=_page(
                accepted.run_id,
                accepted.trace_id,
                [oversized],
            ),
        )
    async with database_engine.connect() as connection:
        cursor = await connection.scalar(
            sa.select(runs.c.target_producer_cursor)
        )
    assert cursor == 0


async def test_event_count_limit_rejects_event_257(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    for start in range(1, 257, 64):
        events = [
            _started(
                accepted.run_id,
                accepted.trace_id,
                sequence=sequence,
            )
            for sequence in range(start, start + 64)
        ]
        await collect_target_page(
            database_engine,
            run_id=accepted.run_id,
            requested_after=start - 1,
            page=_page(
                accepted.run_id,
                accepted.trace_id,
                events,
                high=256,
            ),
        )
    event_257 = _started(
        accepted.run_id,
        accepted.trace_id,
        sequence=257,
    )
    with pytest.raises(EvidenceLimitExceeded):
        await collect_target_page(
            database_engine,
            run_id=accepted.run_id,
            requested_after=256,
            page=_page(
                accepted.run_id,
                accepted.trace_id,
                [event_257],
            ),
        )
    async with database_engine.connect() as connection:
        cursor = await connection.scalar(
            sa.select(runs.c.target_producer_cursor)
        )
    assert cursor == 256


async def test_total_event_data_limit_rejects_whole_event(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    cursor = 0
    rejected = False
    while cursor < 30:
        sequence = cursor + 1
        event = DegradedResultEvent(
            contract_version="1",
            run_id=accepted.run_id,
            trace_id=accepted.trace_id,
            event_id=uuid4(),
            source="sut",
            event_type="sut.degraded_result.produced",
            boundary="agent",
            producer_seq=sequence,
            payload=DegradedResultPayload(
                schema_version=1,
                result="x" * 60_000,
            ),
        )
        try:
            await collect_target_page(
                database_engine,
                run_id=accepted.run_id,
                requested_after=cursor,
                page=_page(
                    accepted.run_id,
                    accepted.trace_id,
                    [event],
                ),
            )
        except EvidenceLimitExceeded:
            rejected = True
            break
        cursor = sequence
    assert rejected
    async with database_engine.connect() as connection:
        durable_cursor = await connection.scalar(
            sa.select(runs.c.target_producer_cursor)
        )
        stored_bytes = await connection.scalar(
            sa.select(
                sa.func.sum(
                    sa.func.octet_length(
                        evidence_records.c.payload_canonical_bytes
                    )
                )
            ).where(evidence_records.c.source == "sut")
        )
    assert durable_cursor == cursor
    assert stored_bytes is not None
    assert stored_bytes <= 1024 * 1024


async def test_producer_sequence_conflict_is_invalid(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    existing = _started(accepted.run_id, accepted.trace_id)
    await _insert_existing_event(
        database_engine,
        run_id=accepted.run_id,
        event=existing,
        receipt_seq=2,
    )
    changed = _started(accepted.run_id, accepted.trace_id)
    with pytest.raises(EvidenceInvalid):
        await collect_target_page(
            database_engine,
            run_id=accepted.run_id,
            requested_after=0,
            page=_page(
                accepted.run_id,
                accepted.trace_id,
                [changed],
            ),
        )


async def test_changed_terminal_result_is_invalid(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    terminal_event_id = uuid4()

    def status(output: str) -> RunStatus:
        return RunStatus(
            contract_version="1",
            run_id=accepted.run_id,
            trace_id=accepted.trace_id,
            tested_agent_id="boundary.sample-agent",
            tested_agent_version="vulnerable-v1",
            state="completed",
            producer_high_watermark=2,
            final_producer_seq=2,
            terminal_result=TerminalResult(
                contract_version="1",
                run_id=accepted.run_id,
                trace_id=accepted.trace_id,
                tested_agent_id="boundary.sample-agent",
                tested_agent_version="vulnerable-v1",
                state="completed",
                final_producer_seq=2,
                outcome_kind="success",
                output=output,
                event_id=terminal_event_id,
            ),
        )

    await record_terminal_status(
        database_engine,
        run_id=accepted.run_id,
        status=status("first"),
    )
    with pytest.raises(EvidenceInvalid):
        await record_terminal_status(
            database_engine,
            run_id=accepted.run_id,
            status=status("changed"),
        )


async def test_rejected_content_persists_only_safe_metadata(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    rejected = b"untrusted-private-content"
    await record_safe_rejection(
        database_engine,
        run_id=accepted.run_id,
        category="UNPARSABLE_EVENT_PAGE",
        raw_bytes=rejected,
    )
    async with database_engine.connect() as connection:
        row = (
            await connection.execute(
                sa.select(evidence_records).where(
                    evidence_records.c.disposition == "rejected"
                )
            )
        ).one()
        run = (
            await connection.execute(
                sa.select(runs).where(runs.c.run_id == accepted.run_id)
            )
        ).one()
    assert rejected not in row.payload_canonical_bytes
    assert row.payload["byte_count"] == len(rejected)
    assert row.payload["content_sha256"] == sha256(rejected).hexdigest()
    assert row.receipt_seq is None
    assert row.audit_seq == 1
    assert run.next_receipt_seq == 2
    assert run.next_audit_seq == 2


async def test_cancellation_request_evidence_is_safe_and_idempotent(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    cancellation_id = uuid4()
    for _ in range(2):
        await record_cancellation_requested(
            database_engine,
            run_id=accepted.run_id,
            trace_id=accepted.trace_id,
            cancellation_id=cancellation_id,
            execution_budget_ms=123,
        )

    async with database_engine.connect() as connection:
        rows = (
            await connection.execute(
                sa.select(evidence_records).where(
                    evidence_records.c.event_type
                    == "boundary.cancellation.requested"
                )
            )
        ).all()

    assert len(rows) == 1
    row = rows[0]
    assert row.source_event_id == cancellation_id
    assert row.payload == {
        "cancellation_id": str(cancellation_id),
        "reason": "run_budget_expired",
        "run_budget": {
            "execution_budget_ms": 123,
            "relationship": "requested_after_expiry",
        },
        "run_id": str(accepted.run_id),
        "schema_version": 1,
        "trace_id": str(accepted.trace_id),
    }
    assert b"tool_capability" not in row.payload_canonical_bytes
    assert b"target" not in row.payload_canonical_bytes
    assert b"error" not in row.payload_canonical_bytes

    with pytest.raises(EvidenceInvalid):
        await record_cancellation_requested(
            database_engine,
            run_id=accepted.run_id,
            trace_id=accepted.trace_id,
            cancellation_id=cancellation_id,
            execution_budget_ms=124,
        )


@pytest.mark.parametrize(
    "invalid_link",
    ["missing", "self", "forward", "cross_run"],
)
async def test_invalid_causal_link_rejects_page_without_advancing_cursor(
    database_engine: AsyncEngine,
    invalid_link: str,
) -> None:
    accepted = await _accepted_run(database_engine)
    if invalid_link == "missing":
        events = [
            _completed(
                accepted.run_id,
                accepted.trace_id,
                sequence=1,
                caused_by=uuid4(),
            )
        ]
    elif invalid_link == "self":
        event_id = uuid4()
        events = [
            _completed(
                accepted.run_id,
                accepted.trace_id,
                sequence=1,
                caused_by=event_id,
                event_id=event_id,
            )
        ]
    elif invalid_link == "forward":
        second_id = uuid4()
        first = _started(accepted.run_id, accepted.trace_id).model_copy(
            update={"caused_by_event_id": second_id}
        )
        second = _completed(
            accepted.run_id,
            accepted.trace_id,
            sequence=2,
            caused_by=first.event_id,
            event_id=second_id,
        )
        events = [first, second]
    else:
        other = await _accepted_run(
            database_engine,
            key="task3-causal-other-run",
        )
        other_event = _started(other.run_id, other.trace_id)
        await collect_target_page(
            database_engine,
            run_id=other.run_id,
            requested_after=0,
            page=_page(other.run_id, other.trace_id, [other_event]),
        )
        events = [
            _completed(
                accepted.run_id,
                accepted.trace_id,
                sequence=1,
                caused_by=other_event.event_id,
            )
        ]

    with pytest.raises(EvidenceInvalid):
        await collect_target_page(
            database_engine,
            run_id=accepted.run_id,
            requested_after=0,
            page=_page(
                accepted.run_id,
                accepted.trace_id,
                events,
            ),
        )
    async with database_engine.connect() as connection:
        cursor = await connection.scalar(
            sa.select(runs.c.target_producer_cursor).where(
                runs.c.run_id == accepted.run_id
            )
        )
        stored = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == accepted.run_id,
                evidence_records.c.source == "sut",
            )
        )
    assert cursor == 0
    assert stored == 0


async def test_causal_link_to_earlier_stored_same_run_event_is_accepted(
    database_engine: AsyncEngine,
) -> None:
    accepted = await _accepted_run(database_engine)
    first = _started(accepted.run_id, accepted.trace_id)
    await collect_target_page(
        database_engine,
        run_id=accepted.run_id,
        requested_after=0,
        page=_page(accepted.run_id, accepted.trace_id, [first]),
    )
    second = _completed(
        accepted.run_id,
        accepted.trace_id,
        sequence=2,
        caused_by=first.event_id,
    )

    result = await collect_target_page(
        database_engine,
        run_id=accepted.run_id,
        requested_after=1,
        page=_page(accepted.run_id, accepted.trace_id, [second]),
    )

    assert result.cursor == 2
    assert result.inserted_events == 1

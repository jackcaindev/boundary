from __future__ import annotations

import asyncio
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import rfc8785
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.evidence.canonical import (
    FAULT_SPEC_V1_SHA256,
    canonicalize_fault_definition,
    fault_definition_digest,
)
from boundary.persistence.tables import (
    campaigns,
    evidence_records,
    idempotency_records,
    runs,
)
from boundary.persistence.transactions import (
    INITIAL_EVENT_TYPE,
    OPERATION_KIND,
    AcceptanceCommand,
    AcceptanceResult,
    CanonicalDocument,
    IdempotencyConflict,
    PersistenceConflict,
    PersistenceFailure,
    PreparedAcceptance,
    FailurePoint,
    accept_campaign_run,
    prepare_campaign_run_acceptance,
)

from conftest import APPLICATION_TABLES, MigrationFacts


pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"
)
TABLES = (
    campaigns,
    runs,
    idempotency_records,
    evidence_records,
)


@pytest.fixture
def canonical_definition() -> CanonicalDocument:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    definition = Phase1FaultDefinition.model_validate(raw)
    return CanonicalDocument(
        schema_version=definition.schema_version,
        document=definition.model_dump(mode="json"),
        canonical_bytes=canonicalize_fault_definition(definition),
        digest=fault_definition_digest(definition),
    )


@pytest.fixture
def acceptance_command(
    canonical_definition: CanonicalDocument,
) -> AcceptanceCommand:
    return AcceptanceCommand(
        idempotency_key="accept-campaign-001",
        contract_version="1",
        scenario_id="phase1.tool-timeout",
        scenario_version=1,
        tested_agent_id="boundary.sample-agent",
        tested_agent_version="vulnerable-v1",
        run_definition=canonical_definition,
    )


async def _counts(engine: AsyncEngine) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with engine.connect() as connection:
        for table in TABLES:
            count = await connection.scalar(
                sa.select(sa.func.count()).select_from(table)
            )
            assert count is not None
            counts[table.name] = count
    return counts


async def _capture_acceptance(
    engine: AsyncEngine,
    prepared: PreparedAcceptance,
) -> AcceptanceResult | IdempotencyConflict:
    try:
        return await accept_campaign_run(engine, prepared)
    except IdempotencyConflict as error:
        return error


def _evidence_values(
    run_id: UUID,
    *,
    source: str,
    producer_seq: int | None,
    receipt_seq: int,
    content: str,
) -> dict[str, object]:
    event_id = uuid4()
    payload_bytes = rfc8785.dumps(
        {"content": content, "schema_version": 1}
    )
    return {
        "evidence_id": event_id,
        "run_id": run_id,
        "source": source,
        "event_type": f"{source}.test.event",
        "boundary": "run",
        "source_event_id": event_id,
        "producer_seq": producer_seq,
        "receipt_seq": receipt_seq,
        "caused_by_event_id": None,
        "payload_schema_version": 1,
        "payload": json.loads(payload_bytes),
        "payload_canonical_bytes": payload_bytes,
        "payload_digest": sha256(payload_bytes).hexdigest(),
        "disposition": "accepted",
    }


async def test_clean_migration_reaches_the_single_head(
    migration_facts: MigrationFacts,
) -> None:
    assert migration_facts.tables_before_upgrade == frozenset()
    assert migration_facts.tables_after_upgrade == APPLICATION_TABLES
    assert (
        migration_facts.database_revision
        == "0004_timeout_proof"
    )
    assert migration_facts.database_revision == migration_facts.head_revision


async def test_accepts_campaign_run_and_initial_transition_atomically(
    database_engine: AsyncEngine,
    acceptance_command: AcceptanceCommand,
) -> None:
    prepared = prepare_campaign_run_acceptance(acceptance_command)
    result = await accept_campaign_run(database_engine, prepared)

    assert result == AcceptanceResult(
        campaign_id=prepared.campaign_id,
        run_id=prepared.run_id,
        trace_id=prepared.trace_id,
        evidence_id=prepared.evidence_id,
        replayed=False,
    )
    assert await _counts(database_engine) == {
        "campaigns": 1,
        "runs": 1,
        "idempotency_records": 1,
        "evidence_records": 1,
    }

    async with database_engine.connect() as connection:
        campaign = (
            await connection.execute(sa.select(campaigns))
        ).one()
        run = (await connection.execute(sa.select(runs))).one()
        idempotency = (
            await connection.execute(sa.select(idempotency_records))
        ).one()
        evidence = (
            await connection.execute(sa.select(evidence_records))
        ).one()

    assert campaign.campaign_id == result.campaign_id
    assert campaign.status == "accepted"
    assert campaign.current_step == "control"
    assert run.run_id == result.run_id
    assert run.trace_id == result.trace_id
    assert run.operational_status == "accepted"
    assert run.next_receipt_seq == 2
    assert run.run_definition_bytes == (
        acceptance_command.run_definition.canonical_bytes
    )
    assert run.run_definition_digest == FAULT_SPEC_V1_SHA256
    assert idempotency.request_digest == prepared.request_digest
    assert evidence.evidence_id == result.evidence_id
    assert evidence.source == "boundary"
    assert evidence.event_type == INITIAL_EVENT_TYPE
    assert evidence.receipt_seq == 1
    assert evidence.disposition == "accepted"
    assert rfc8785.dumps(evidence.payload) == (
        evidence.payload_canonical_bytes
    )


async def test_identical_idempotent_replay_returns_original_identities(
    database_engine: AsyncEngine,
    acceptance_command: AcceptanceCommand,
) -> None:
    first_prepared = prepare_campaign_run_acceptance(
        acceptance_command
    )
    second_prepared = prepare_campaign_run_acceptance(
        acceptance_command
    )
    assert first_prepared.campaign_id != second_prepared.campaign_id
    assert first_prepared.run_id != second_prepared.run_id

    first = await accept_campaign_run(database_engine, first_prepared)
    second = await accept_campaign_run(database_engine, second_prepared)

    assert second == replace(first, replayed=True)
    assert await _counts(database_engine) == {
        "campaigns": 1,
        "runs": 1,
        "idempotency_records": 1,
        "evidence_records": 1,
    }


async def test_conflicting_idempotency_content_is_stably_rejected(
    database_engine: AsyncEngine,
    acceptance_command: AcceptanceCommand,
) -> None:
    accepted = await accept_campaign_run(
        database_engine,
        prepare_campaign_run_acceptance(acceptance_command),
    )
    conflict_command = replace(
        acceptance_command,
        tested_agent_version="different-v2",
    )

    with pytest.raises(IdempotencyConflict) as raised:
        await accept_campaign_run(
            database_engine,
            prepare_campaign_run_acceptance(conflict_command),
        )

    assert raised.value.code == "IDEMPOTENCY_CONFLICT"
    assert raised.value.operation_kind == OPERATION_KIND
    assert raised.value.idempotency_key == (
        acceptance_command.idempotency_key
    )
    assert await _counts(database_engine) == {
        "campaigns": 1,
        "runs": 1,
        "idempotency_records": 1,
        "evidence_records": 1,
    }
    async with database_engine.connect() as connection:
        stored_run_id = await connection.scalar(
            sa.select(runs.c.run_id)
        )
    assert stored_run_id == accepted.run_id


async def test_duplicate_trace_is_a_domain_persistence_conflict(
    database_engine: AsyncEngine,
    acceptance_command: AcceptanceCommand,
) -> None:
    first_prepared = prepare_campaign_run_acceptance(
        acceptance_command
    )
    await accept_campaign_run(database_engine, first_prepared)
    second_command = replace(
        acceptance_command,
        idempotency_key="accept-campaign-002",
    )
    second_prepared = replace(
        prepare_campaign_run_acceptance(second_command),
        trace_id=first_prepared.trace_id,
    )

    with pytest.raises(PersistenceConflict) as raised:
        await accept_campaign_run(database_engine, second_prepared)

    assert raised.value.code == "PERSISTENCE_CONFLICT"
    assert raised.value.reason == "DUPLICATE_TRACE_ID"
    assert await _counts(database_engine) == {
        "campaigns": 1,
        "runs": 1,
        "idempotency_records": 1,
        "evidence_records": 1,
    }


async def test_duplicate_receipt_identity_is_rejected_by_postgresql(
    database_engine: AsyncEngine,
    acceptance_command: AcceptanceCommand,
) -> None:
    result = await accept_campaign_run(
        database_engine,
        prepare_campaign_run_acceptance(acceptance_command),
    )
    payload_bytes = rfc8785.dumps(
        {"schema_version": 1, "transition": "duplicate"}
    )

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                evidence_records.insert().values(
                    evidence_id=uuid4(),
                    run_id=result.run_id,
                    source="boundary",
                    event_type="boundary.run.duplicate",
                    boundary="run",
                    source_event_id=uuid4(),
                    producer_seq=None,
                    receipt_seq=1,
                    caused_by_event_id=None,
                    payload_schema_version=1,
                    payload=json.loads(payload_bytes),
                    payload_canonical_bytes=payload_bytes,
                    payload_digest=sha256(payload_bytes).hexdigest(),
                    disposition="accepted",
                )
            )

    assert await _counts(database_engine) == {
        "campaigns": 1,
        "runs": 1,
        "idempotency_records": 1,
        "evidence_records": 1,
    }


async def test_duplicate_sut_producer_sequence_is_rejected_by_postgresql(
    database_engine: AsyncEngine,
    acceptance_command: AcceptanceCommand,
) -> None:
    result = await accept_campaign_run(
        database_engine,
        prepare_campaign_run_acceptance(acceptance_command),
    )
    first = _evidence_values(
        result.run_id,
        source="sut",
        producer_seq=1,
        receipt_seq=2,
        content="first",
    )
    conflicting = _evidence_values(
        result.run_id,
        source="sut",
        producer_seq=1,
        receipt_seq=3,
        content="different",
    )
    assert first["evidence_id"] != conflicting["evidence_id"]
    assert first["payload_digest"] != conflicting["payload_digest"]

    async with database_engine.begin() as connection:
        await connection.execute(evidence_records.insert().values(first))

    with pytest.raises(IntegrityError) as raised:
        async with database_engine.begin() as connection:
            await connection.execute(
                evidence_records.insert().values(conflicting)
            )

    assert "uq_evidence_records_run_producer_seq" in str(
        raised.value.orig
    )
    assert await _counts(database_engine) == {
        "campaigns": 1,
        "runs": 1,
        "idempotency_records": 1,
        "evidence_records": 2,
    }


async def test_boundary_events_allow_multiple_null_producer_sequences(
    database_engine: AsyncEngine,
    acceptance_command: AcceptanceCommand,
) -> None:
    result = await accept_campaign_run(
        database_engine,
        prepare_campaign_run_acceptance(acceptance_command),
    )
    first = _evidence_values(
        result.run_id,
        source="boundary",
        producer_seq=None,
        receipt_seq=2,
        content="first boundary event",
    )
    second = _evidence_values(
        result.run_id,
        source="boundary",
        producer_seq=None,
        receipt_seq=3,
        content="second boundary event",
    )
    assert first["evidence_id"] != second["evidence_id"]

    async with database_engine.begin() as connection:
        await connection.execute(
            evidence_records.insert(),
            [first, second],
        )

    async with database_engine.connect() as connection:
        null_sequence_count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence_records)
            .where(
                evidence_records.c.run_id == result.run_id,
                evidence_records.c.producer_seq.is_(None),
            )
        )

    assert null_sequence_count == 3
    assert await _counts(database_engine) == {
        "campaigns": 1,
        "runs": 1,
        "idempotency_records": 1,
        "evidence_records": 3,
    }


@pytest.mark.parametrize(
    "failure_point",
    [
        "idempotency_record",
        "campaign",
        "run",
        "evidence",
    ],
)
async def test_failure_after_each_insertion_rolls_back_everything(
    database_engine: AsyncEngine,
    acceptance_command: AcceptanceCommand,
    failure_point: FailurePoint,
) -> None:
    prepared = prepare_campaign_run_acceptance(acceptance_command)

    with pytest.raises(PersistenceFailure):
        await accept_campaign_run(
            database_engine,
            prepared,
            _fail_after=failure_point,
        )

    assert await _counts(database_engine) == {
        "campaigns": 0,
        "runs": 0,
        "idempotency_records": 0,
        "evidence_records": 0,
    }


async def test_concurrent_identical_acceptance_converges(
    database_engine: AsyncEngine,
    acceptance_command: AcceptanceCommand,
) -> None:
    prepared_requests = [
        prepare_campaign_run_acceptance(acceptance_command)
        for _ in range(8)
    ]

    results = await asyncio.gather(
        *(
            accept_campaign_run(database_engine, prepared)
            for prepared in prepared_requests
        )
    )

    assert len({result.campaign_id for result in results}) == 1
    assert len({result.run_id for result in results}) == 1
    assert len({result.trace_id for result in results}) == 1
    assert len({result.evidence_id for result in results}) == 1
    assert sum(not result.replayed for result in results) == 1
    assert await _counts(database_engine) == {
        "campaigns": 1,
        "runs": 1,
        "idempotency_records": 1,
        "evidence_records": 1,
    }


async def test_concurrent_conflicting_acceptance_never_mixes_resources(
    database_engine: AsyncEngine,
    acceptance_command: AcceptanceCommand,
) -> None:
    different_command = replace(
        acceptance_command,
        tested_agent_version="fixed-v2",
    )
    prepared = [
        prepare_campaign_run_acceptance(acceptance_command),
        prepare_campaign_run_acceptance(different_command),
    ]

    outcomes = await asyncio.gather(
        *(
            _capture_acceptance(database_engine, item)
            for item in prepared
        )
    )

    accepted = [
        item for item in outcomes if isinstance(item, AcceptanceResult)
    ]
    conflicts = [
        item for item in outcomes if isinstance(item, IdempotencyConflict)
    ]
    assert len(accepted) == 1
    assert len(conflicts) == 1
    assert await _counts(database_engine) == {
        "campaigns": 1,
        "runs": 1,
        "idempotency_records": 1,
        "evidence_records": 1,
    }

    async with database_engine.connect() as connection:
        row = (
            await connection.execute(
                sa.select(
                    runs.c.campaign_id,
                    runs.c.run_id,
                    idempotency_records.c.campaign_id.label(
                        "mapped_campaign_id"
                    ),
                    idempotency_records.c.run_id.label("mapped_run_id"),
                    evidence_records.c.run_id.label("evidence_run_id"),
                )
                .select_from(
                    runs.join(
                        idempotency_records,
                        runs.c.run_id == idempotency_records.c.run_id,
                    ).join(
                        evidence_records,
                        runs.c.run_id == evidence_records.c.run_id,
                    )
                )
            )
        ).one()

    assert row.campaign_id == row.mapped_campaign_id
    assert row.run_id == row.mapped_run_id
    assert row.run_id == row.evidence_run_id
    assert row.run_id == accepted[0].run_id


async def test_restrictive_foreign_keys_and_identity_constraints_hold(
    database_engine: AsyncEngine,
    acceptance_command: AcceptanceCommand,
) -> None:
    result = await accept_campaign_run(
        database_engine,
        prepare_campaign_run_acceptance(acceptance_command),
    )
    orphan_payload_bytes = b'{"schema_version":1}'

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                campaigns.delete().where(
                    campaigns.c.campaign_id == result.campaign_id
                )
            )

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                runs.update()
                .where(runs.c.run_id == result.run_id)
                .values(run_id=uuid4())
            )

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                evidence_records.insert().values(
                    evidence_id=uuid4(),
                    run_id=uuid4(),
                    source="boundary",
                    event_type="boundary.run.accepted",
                    boundary="run",
                    source_event_id=uuid4(),
                    producer_seq=None,
                    receipt_seq=1,
                    caused_by_event_id=None,
                    payload_schema_version=1,
                    payload={"schema_version": 1},
                    payload_canonical_bytes=orphan_payload_bytes,
                    payload_digest=sha256(
                        orphan_payload_bytes
                    ).hexdigest(),
                    disposition="accepted",
                )
            )

    assert await _counts(database_engine) == {
        "campaigns": 1,
        "runs": 1,
        "idempotency_records": 1,
        "evidence_records": 1,
    }

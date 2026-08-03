"""Atomic ADR 001 target-page collection and Boundary transitions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import rfc8785
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from boundary.domain.lifecycle import (
    TERMINAL_STATUSES,
    is_legal_transition,
)
from boundary.persistence.tables import evidence_records, runs
from boundary.sut.contract_v1 import (
    CONTRACT_VERSION,
    MAX_EVENT_BYTES,
    MAX_TARGET_EVENTS,
    MAX_TARGET_EVENT_BYTES,
    EventEnvelope,
    EventPage,
    RunStatus,
)


class CollectionError(Exception):
    """Base class for safely classified collection failures."""

    code = "COLLECTION_ERROR"


class EvidenceInvalid(CollectionError):
    code = "INVALID_EVENT"


class EvidenceClosed(EvidenceInvalid):
    code = "EVIDENCE_CLOSED"


class IdentityMismatch(EvidenceInvalid):
    code = "IDENTITY_MISMATCH"


class ForwardGap(CollectionError):
    """A compatible forward gap that must not move the durable cursor."""

    code = "FORWARD_GAP"


class EvidenceLimitExceeded(EvidenceInvalid):
    code = "PAYLOAD_TOO_LARGE"

    def __init__(self, message: str, raw_bytes: bytes = b"") -> None:
        self.raw_bytes = raw_bytes
        super().__init__(message)


class IllegalTransition(EvidenceInvalid):
    code = "ILLEGAL_TRANSITION"


@dataclass(frozen=True, slots=True)
class PageCollectionResult:
    previous_cursor: int
    cursor: int
    inserted_events: int
    identical_uncommitted_events: int


@dataclass(frozen=True, slots=True)
class RunBudgetBinding:
    evidence_id: UUID
    execution_budget_ms: int
    budget_started_monotonic_ns: int
    deadline_monotonic_ns: int


async def collect_target_page(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    requested_after: int,
    page: EventPage,
) -> PageCollectionResult:
    """Persist a page or retain only bounded audit metadata after cutoff."""
    try:
        return await _collect_target_page(
            engine,
            run_id=run_id,
            requested_after=requested_after,
            page=page,
        )
    except EvidenceClosed:
        await record_safe_rejection(
            engine,
            run_id=run_id,
            category="late_target_page_after_finalization",
            raw_bytes=_page_bytes(page),
        )
        raise


async def _collect_target_page(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    requested_after: int,
    page: EventPage,
) -> PageCollectionResult:
    """Persist one complete contiguous page and its cursor atomically."""
    async with engine.begin() as connection:
        run = await _locked_run(connection, run_id)
        current_cursor = run.target_producer_cursor
        _validate_page_identity(run, page)
        if requested_after != current_cursor:
            if requested_after < current_cursor:
                raise EvidenceInvalid(
                    "lower producer sequence delivered after advancement"
                )
            raise EvidenceInvalid("collector cursor is not authoritative")
        if page.next_after_producer_seq < current_cursor:
            raise EvidenceInvalid("target page moved its cursor backward")
        if page.producer_high_watermark < page.next_after_producer_seq:
            raise EvidenceInvalid("page watermark is below its cursor")

        events = page.events
        if not events:
            if page.next_after_producer_seq != current_cursor:
                raise EvidenceInvalid("empty page advanced its cursor")
            return PageCollectionResult(
                previous_cursor=current_cursor,
                cursor=current_cursor,
                inserted_events=0,
                identical_uncommitted_events=0,
            )

        expected_first = current_cursor + 1
        if events[0].producer_seq > expected_first:
            raise ForwardGap(
                f"expected producer sequence {expected_first}"
            )
        if events[0].producer_seq < expected_first:
            raise EvidenceInvalid(
                "lower producer sequence delivered after advancement"
            )
        expected_sequence = list(
            range(expected_first, expected_first + len(events))
        )
        actual_sequence = [event.producer_seq for event in events]
        if actual_sequence != expected_sequence:
            if any(
                actual > expected
                for actual, expected in zip(
                    actual_sequence,
                    expected_sequence,
                    strict=True,
                )
            ):
                raise ForwardGap("target page contains a forward gap")
            raise EvidenceInvalid(
                "target page producer order is not contiguous"
            )
        if page.next_after_producer_seq != actual_sequence[-1]:
            raise EvidenceInvalid("page cursor does not equal its last event")
        if (
            run.target_final_watermark is not None
            and (
                page.producer_high_watermark
                != run.target_final_watermark
                or actual_sequence[-1] > run.target_final_watermark
            )
        ):
            raise EvidenceInvalid(
                "event page conflicts with the terminal watermark"
            )

        normalized = [_normalize_event(event) for event in events]
        for _, canonical_bytes, _ in normalized:
            if len(canonical_bytes) > MAX_EVENT_BYTES:
                raise EvidenceLimitExceeded(
                    "encoded target event exceeded 64 KiB",
                    canonical_bytes,
                )
        await _validate_causal_links(connection, run_id, events)

        source_ids = [event.event_id for event, _, _ in normalized]
        sequences = [event.producer_seq for event, _, _ in normalized]
        existing_rows = (
            await connection.execute(
                sa.select(
                    evidence_records.c.evidence_id,
                    evidence_records.c.source_event_id,
                    evidence_records.c.producer_seq,
                    evidence_records.c.payload_digest,
                ).where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.source == "sut",
                    sa.or_(
                        evidence_records.c.source_event_id.in_(source_ids),
                        evidence_records.c.producer_seq.in_(sequences),
                    ),
                )
            )
        ).all()
        by_source = {row.source_event_id: row for row in existing_rows}
        by_sequence = {row.producer_seq: row for row in existing_rows}

        new_events: list[tuple[EventEnvelope, bytes, str]] = []
        identical = 0
        for event, canonical_bytes, digest in normalized:
            source_row = by_source.get(event.event_id)
            sequence_row = by_sequence.get(event.producer_seq)
            if source_row is None and sequence_row is None:
                new_events.append((event, canonical_bytes, digest))
                continue
            if (
                source_row is not None
                and sequence_row is not None
                and source_row.evidence_id == sequence_row.evidence_id
                and source_row.producer_seq == event.producer_seq
                and source_row.payload_digest == digest
            ):
                identical += 1
                continue
            raise EvidenceInvalid(
                "event identity or producer sequence was reused"
            )

        existing_count, existing_bytes = (
            await connection.execute(
                sa.select(
                    sa.func.count(),
                    sa.func.coalesce(
                        sa.func.sum(
                            sa.func.octet_length(
                                evidence_records.c.payload_canonical_bytes
                            )
                        ),
                        0,
                    ),
                ).where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.source == "sut",
                    evidence_records.c.disposition == "accepted",
                )
            )
        ).one()
        new_bytes = sum(len(item[1]) for item in new_events)
        if existing_count + len(new_events) > MAX_TARGET_EVENTS:
            raise EvidenceLimitExceeded(
                "target event count exceeded 256",
                _page_bytes(page),
            )
        if existing_bytes + new_bytes > MAX_TARGET_EVENT_BYTES:
            raise EvidenceLimitExceeded(
                "accepted target event data exceeded 1 MiB",
                _page_bytes(page),
            )

        next_receipt = run.next_receipt_seq
        for offset, (event, canonical_bytes, digest) in enumerate(new_events):
            await connection.execute(
                evidence_records.insert().values(
                    evidence_id=uuid4(),
                    run_id=run_id,
                    source="sut",
                    event_type=event.event_type,
                    boundary=event.boundary,
                    source_event_id=event.event_id,
                    producer_seq=event.producer_seq,
                    receipt_seq=next_receipt + offset,
                    caused_by_event_id=event.caused_by_event_id,
                    payload_schema_version=1,
                    payload=json.loads(canonical_bytes),
                    payload_canonical_bytes=canonical_bytes,
                    payload_digest=digest,
                    disposition="accepted",
                )
            )
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == run_id)
            .values(
                target_producer_cursor=page.next_after_producer_seq,
                next_receipt_seq=next_receipt + len(new_events),
            )
        )
        return PageCollectionResult(
            previous_cursor=current_cursor,
            cursor=page.next_after_producer_seq,
            inserted_events=len(new_events),
            identical_uncommitted_events=identical,
        )


async def record_cancellation_requested(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    trace_id: UUID,
    cancellation_id: UUID,
    deadline_evidence_id: UUID,
    execution_budget_ms: int,
    reason: str = "run_budget_expired",
) -> None:
    """Append one safe Boundary cancellation request idempotently."""
    if not 0 < execution_budget_ms <= 30_000:
        raise ValueError("execution budget must be between 1 and 30000 ms")
    if reason != "run_budget_expired":
        raise ValueError("unsupported cancellation reason")
    payload = {
        "cancellation_id": str(cancellation_id),
        "deadline_evidence_id": str(deadline_evidence_id),
        "reason": reason,
        "run_budget": {
            "execution_budget_ms": execution_budget_ms,
            "relationship": "requested_after_expiry",
        },
        "run_id": str(run_id),
        "schema_version": 1,
        "trace_id": str(trace_id),
    }
    canonical_bytes = rfc8785.dumps(payload)
    digest = sha256(canonical_bytes).hexdigest()
    async with engine.begin() as connection:
        run = await _locked_run(connection, run_id)
        if run.trace_id != trace_id:
            raise IdentityMismatch("cancellation trace identity mismatch")
        deadline = (
            await connection.execute(
                sa.select(evidence_records).where(
                    evidence_records.c.evidence_id == deadline_evidence_id,
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.source == "boundary",
                    evidence_records.c.event_type
                    == "boundary.deadline.reached",
                    evidence_records.c.disposition == "accepted",
                )
            )
        ).one_or_none()
        if deadline is None:
            raise EvidenceInvalid("cancellation lacks deadline evidence")
        deadline_budget = deadline.payload.get("execution_budget_ms")
        if deadline_budget != execution_budget_ms:
            raise EvidenceInvalid("cancellation budget conflicts with deadline")
        existing = (
            await connection.execute(
                sa.select(
                    evidence_records.c.run_id,
                    evidence_records.c.event_type,
                    evidence_records.c.payload_digest,
                ).where(
                    evidence_records.c.source == "boundary",
                    evidence_records.c.source_event_id == cancellation_id,
                )
            )
        ).one_or_none()
        if existing is not None:
            if (
                existing.run_id != run_id
                or existing.event_type
                != "boundary.cancellation.requested"
                or existing.payload_digest != digest
            ):
                raise EvidenceInvalid(
                    "cancellation identity was reused with conflicting content"
                )
            return
        await connection.execute(
            evidence_records.insert().values(
                evidence_id=uuid4(),
                run_id=run_id,
                source="boundary",
                event_type="boundary.cancellation.requested",
                boundary="run",
                source_event_id=cancellation_id,
                producer_seq=None,
                receipt_seq=run.next_receipt_seq,
                caused_by_event_id=deadline_evidence_id,
                payload_schema_version=1,
                payload=payload,
                payload_canonical_bytes=canonical_bytes,
                payload_digest=digest,
                disposition="accepted",
            )
        )
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == run_id)
            .values(next_receipt_seq=run.next_receipt_seq + 1)
        )


async def record_run_budget(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    trace_id: UUID,
    execution_budget_ms: int,
    budget_started_monotonic_ns: int,
    deadline_monotonic_ns: int,
) -> RunBudgetBinding:
    """Bind the one immutable run budget before target invocation."""
    if not 0 < execution_budget_ms <= 30_000:
        raise ValueError("execution budget must be between 1 and 30000 ms")
    if (
        budget_started_monotonic_ns < 0
        or deadline_monotonic_ns - budget_started_monotonic_ns
        != execution_budget_ms * 1_000_000
    ):
        raise ValueError("run budget monotonic boundary is inconsistent")
    payload = {
        "budget_started_monotonic_ns": budget_started_monotonic_ns,
        "deadline_monotonic_ns": deadline_monotonic_ns,
        "execution_budget_ms": execution_budget_ms,
        "relationship": "bound_before_target_invocation",
        "run_id": str(run_id),
        "schema_version": 1,
        "timing_authority": "boundary_monotonic",
        "trace_id": str(trace_id),
    }
    canonical_bytes = rfc8785.dumps(payload)
    digest = sha256(canonical_bytes).hexdigest()
    async with engine.begin() as connection:
        run = await _locked_run(connection, run_id)
        if run.trace_id != trace_id:
            raise IdentityMismatch("run budget trace identity mismatch")
        existing = (
            await connection.execute(
                sa.select(evidence_records).where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.source == "boundary",
                    evidence_records.c.event_type
                    == "boundary.run_budget.bound",
                    evidence_records.c.disposition == "accepted",
                )
            )
        ).all()
        if existing:
            if len(existing) != 1 or existing[0].payload_digest != digest:
                raise EvidenceInvalid("run budget is already bound differently")
            return RunBudgetBinding(
                evidence_id=existing[0].evidence_id,
                execution_budget_ms=execution_budget_ms,
                budget_started_monotonic_ns=budget_started_monotonic_ns,
                deadline_monotonic_ns=deadline_monotonic_ns,
            )
        evidence_id = uuid4()
        await connection.execute(
            evidence_records.insert().values(
                evidence_id=evidence_id,
                run_id=run_id,
                source="boundary",
                event_type="boundary.run_budget.bound",
                boundary="run",
                source_event_id=evidence_id,
                producer_seq=None,
                receipt_seq=run.next_receipt_seq,
                caused_by_event_id=None,
                payload_schema_version=1,
                payload=payload,
                payload_canonical_bytes=canonical_bytes,
                payload_digest=digest,
                disposition="accepted",
            )
        )
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == run_id)
            .values(next_receipt_seq=run.next_receipt_seq + 1)
        )
    return RunBudgetBinding(
        evidence_id=evidence_id,
        execution_budget_ms=execution_budget_ms,
        budget_started_monotonic_ns=budget_started_monotonic_ns,
        deadline_monotonic_ns=deadline_monotonic_ns,
    )


async def record_deadline_reached(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    trace_id: UUID,
    budget: RunBudgetBinding,
    observed_monotonic_ns: int,
) -> UUID:
    """Record Boundary's authoritative observation of the run deadline."""
    if observed_monotonic_ns < budget.deadline_monotonic_ns:
        raise ValueError("run deadline has not been reached")
    payload = {
        "budget_event_id": str(budget.evidence_id),
        "deadline_monotonic_ns": budget.deadline_monotonic_ns,
        "execution_budget_ms": budget.execution_budget_ms,
        "observed_monotonic_ns": observed_monotonic_ns,
        "relationship": "observed_at_or_after_deadline",
        "run_id": str(run_id),
        "schema_version": 1,
        "timing_authority": "boundary_monotonic",
        "trace_id": str(trace_id),
    }
    canonical_bytes = rfc8785.dumps(payload)
    digest = sha256(canonical_bytes).hexdigest()
    async with engine.begin() as connection:
        run = await _locked_run(connection, run_id)
        if run.trace_id != trace_id:
            raise IdentityMismatch("deadline trace identity mismatch")
        bound = (
            await connection.execute(
                sa.select(evidence_records).where(
                    evidence_records.c.evidence_id == budget.evidence_id,
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.event_type
                    == "boundary.run_budget.bound",
                    evidence_records.c.disposition == "accepted",
                )
            )
        ).one_or_none()
        if bound is None or bound.payload.get("execution_budget_ms") != (
            budget.execution_budget_ms
        ):
            raise EvidenceInvalid("deadline lacks its authoritative budget")
        existing = (
            await connection.execute(
                sa.select(evidence_records).where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.source == "boundary",
                    evidence_records.c.event_type
                    == "boundary.deadline.reached",
                    evidence_records.c.disposition == "accepted",
                )
            )
        ).all()
        if existing:
            if len(existing) != 1 or existing[0].payload_digest != digest:
                raise EvidenceInvalid("deadline evidence conflicts")
            return existing[0].evidence_id
        evidence_id = uuid4()
        await connection.execute(
            evidence_records.insert().values(
                evidence_id=evidence_id,
                run_id=run_id,
                source="boundary",
                event_type="boundary.deadline.reached",
                boundary="run",
                source_event_id=evidence_id,
                producer_seq=None,
                receipt_seq=run.next_receipt_seq,
                caused_by_event_id=budget.evidence_id,
                payload_schema_version=1,
                payload=payload,
                payload_canonical_bytes=canonical_bytes,
                payload_digest=digest,
                disposition="accepted",
            )
        )
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == run_id)
            .values(next_receipt_seq=run.next_receipt_seq + 1)
        )
    return evidence_id


async def record_reported_identity(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    reported_agent_id: str,
    reported_agent_version: str,
) -> None:
    """Keep target-reported values separate from Boundary expectations."""
    mismatch_message: str | None = None
    async with engine.begin() as connection:
        run = await _locked_run(connection, run_id)
        prior = (
            run.reported_tested_agent_id,
            run.reported_tested_agent_version,
        )
        reported = (reported_agent_id, reported_agent_version)
        if prior != (None, None) and prior != reported:
            mismatch_message = (
                "target changed its reported identity during the run"
            )
        else:
            await connection.execute(
                runs.update()
                .where(runs.c.run_id == run_id)
                .values(
                    reported_tested_agent_id=reported_agent_id,
                    reported_tested_agent_version=reported_agent_version,
                )
            )
        if reported != (
            run.expected_tested_agent_id,
            run.expected_tested_agent_version,
        ):
            mismatch_message = (
                "reported target identity does not match expectation"
            )
    if mismatch_message is not None:
        raise IdentityMismatch(mismatch_message)


async def observe_terminal_watermark(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    final_producer_seq: int,
    producer_high_watermark: int,
) -> None:
    """Seal the first terminal watermark and reject later changes."""
    if final_producer_seq < 0:
        raise EvidenceInvalid("terminal watermark is negative")
    if producer_high_watermark != final_producer_seq:
        raise EvidenceInvalid(
            "terminal high watermark differs from final watermark"
        )
    async with engine.begin() as connection:
        run = await _locked_run(connection, run_id)
        existing = run.target_final_watermark
        if existing is not None and existing != final_producer_seq:
            raise EvidenceInvalid("terminal watermark changed")
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == run_id)
            .values(target_final_watermark=final_producer_seq)
        )


async def record_terminal_status(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    status: RunStatus,
) -> None:
    """Persist the validated untrusted terminal result once, immutably."""
    if status.terminal_result is None:
        raise EvidenceInvalid("terminal status has no terminal result")
    payload = {
        "reported_status": status.model_dump(mode="json", exclude_none=True),
        "schema_version": 1,
    }
    canonical_bytes = rfc8785.dumps(payload)
    digest = sha256(canonical_bytes).hexdigest()
    async with engine.begin() as connection:
        run = await _locked_run(connection, run_id)
        existing = (
            await connection.execute(
                sa.select(
                    evidence_records.c.payload_digest,
                ).where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.event_type
                    == "boundary.sut_terminal.observed",
                )
            )
        ).one_or_none()
        if existing is not None:
            if existing.payload_digest != digest:
                raise EvidenceInvalid("terminal result changed")
            return
        evidence_id = uuid4()
        await connection.execute(
            evidence_records.insert().values(
                evidence_id=evidence_id,
                run_id=run_id,
                source="boundary",
                event_type="boundary.sut_terminal.observed",
                boundary="run",
                source_event_id=evidence_id,
                producer_seq=None,
                receipt_seq=run.next_receipt_seq,
                caused_by_event_id=status.terminal_result.event_id,
                payload_schema_version=1,
                payload=payload,
                payload_canonical_bytes=canonical_bytes,
                payload_digest=digest,
                disposition="accepted",
            )
        )
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == run_id)
            .values(next_receipt_seq=run.next_receipt_seq + 1)
        )


async def validate_terminal_collection(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    status: RunStatus,
) -> None:
    """Require the referenced terminal event through the final watermark."""
    result = status.terminal_result
    if result is None or status.final_producer_seq is None:
        raise EvidenceInvalid("status is not terminal")
    expected_event_type = {
        "completed": "sut.run.completed",
        "failed": "sut.run.failed",
        "cancelled": "sut.run.cancelled",
    }[status.state]
    async with engine.connect() as connection:
        run = (
            await connection.execute(
                sa.select(
                    runs.c.target_producer_cursor,
                    runs.c.target_final_watermark,
                ).where(runs.c.run_id == run_id)
            )
        ).one_or_none()
        if run is None:
            raise IdentityMismatch("run does not exist")
        if (
            run.target_producer_cursor != status.final_producer_seq
            or run.target_final_watermark != status.final_producer_seq
        ):
            raise EvidenceInvalid(
                "terminal collection has not reached its watermark"
            )
        terminal_event = (
            await connection.execute(
                sa.select(
                    evidence_records.c.source_event_id,
                    evidence_records.c.event_type,
                ).where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.source == "sut",
                    evidence_records.c.producer_seq
                    == status.final_producer_seq,
                )
            )
        ).one_or_none()
    if (
        terminal_event is None
        or terminal_event.source_event_id != result.event_id
        or terminal_event.event_type != expected_event_type
    ):
        raise EvidenceInvalid(
            "terminal result does not match its terminal event"
        )


async def validate_cancelled_collection(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    status: RunStatus,
    cancellation_id: UUID,
) -> None:
    """Require a sealed cancelled result tied to Boundary's request."""
    await validate_terminal_collection(
        engine,
        run_id=run_id,
        status=status,
    )
    result = status.terminal_result
    if (
        status.state != "cancelled"
        or result is None
        or result.outcome_kind != "cancelled"
    ):
        raise EvidenceInvalid("cancellation terminal result is invalid")
    async with engine.connect() as connection:
        payload = await connection.scalar(
            sa.select(evidence_records.c.payload).where(
                evidence_records.c.run_id == run_id,
                evidence_records.c.source == "sut",
                evidence_records.c.source_event_id == result.event_id,
                evidence_records.c.event_type == "sut.run.cancelled",
            )
        )
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("payload"), dict)
        or payload["payload"].get("cancellation_id")
        != str(cancellation_id)
    ):
        raise EvidenceInvalid(
            "cancelled event does not reference Boundary cancellation"
        )


async def transition_run(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    target_status: str,
    reason: str,
    run_budget: RunBudgetBinding | None = None,
    observed_monotonic_ns: int | None = None,
) -> None:
    """Atomically update the projection and append Boundary evidence."""
    async with engine.begin() as connection:
        run = await _locked_run(connection, run_id)
        current = run.operational_status
        if current == target_status:
            return
        if current in TERMINAL_STATUSES or not is_legal_transition(
            current,
            target_status,
        ):
            raise IllegalTransition(
                f"illegal Boundary transition {current}->{target_status}"
            )
        payload = {
            "from_status": current,
            "reason": reason,
            "schema_version": 1,
            "to_status": target_status,
        }
        if run_budget is not None:
            if observed_monotonic_ns is None:
                raise ValueError("budgeted transition lacks an observation")
            payload["run_budget"] = {
                "budget_event_id": str(run_budget.evidence_id),
                "deadline_monotonic_ns": run_budget.deadline_monotonic_ns,
                "execution_budget_ms": run_budget.execution_budget_ms,
                "observed_monotonic_ns": observed_monotonic_ns,
                "relationship": (
                    "before_deadline"
                    if observed_monotonic_ns
                    < run_budget.deadline_monotonic_ns
                    else "at_or_after_deadline"
                ),
            }
        canonical_bytes = rfc8785.dumps(payload)
        evidence_id = uuid4()
        event_type = (
            "boundary.run.terminal"
            if target_status in TERMINAL_STATUSES
            else "boundary.run.running"
        )
        await connection.execute(
            evidence_records.insert().values(
                evidence_id=evidence_id,
                run_id=run_id,
                source="boundary",
                event_type=event_type,
                boundary="run",
                source_event_id=evidence_id,
                producer_seq=None,
                receipt_seq=run.next_receipt_seq,
                caused_by_event_id=None,
                payload_schema_version=1,
                payload=payload,
                payload_canonical_bytes=canonical_bytes,
                payload_digest=sha256(canonical_bytes).hexdigest(),
                disposition="accepted",
            )
        )
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == run_id)
            .values(
                operational_status=target_status,
                next_receipt_seq=run.next_receipt_seq + 1,
            )
        )


async def record_safe_rejection(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    category: str,
    raw_bytes: bytes,
) -> None:
    """Persist bounded digest/size metadata, never rejected content."""
    payload = {
        "byte_count": len(raw_bytes),
        "category": category[:128],
        "content_sha256": sha256(raw_bytes).hexdigest(),
        "schema_version": 1,
    }
    canonical_bytes = rfc8785.dumps(payload)
    async with engine.begin() as connection:
        run = await _locked_run(
            connection,
            run_id,
            require_open=False,
        )
        disposition = "rejected" if run.evidence_open else "late"
        event_type = (
            "boundary.sut_event.rejected"
            if run.evidence_open
            else "boundary.sut_event.late_rejected"
        )
        evidence_id = uuid4()
        await connection.execute(
            evidence_records.insert().values(
                evidence_id=evidence_id,
                run_id=run_id,
                source="boundary",
                event_type=event_type,
                boundary="run",
                source_event_id=evidence_id,
                producer_seq=None,
                receipt_seq=None,
                audit_seq=run.next_audit_seq,
                caused_by_event_id=None,
                payload_schema_version=1,
                payload=payload,
                payload_canonical_bytes=canonical_bytes,
                payload_digest=sha256(canonical_bytes).hexdigest(),
                disposition=disposition,
            )
        )
        await connection.execute(
            runs.update()
            .where(runs.c.run_id == run_id)
            .values(next_audit_seq=run.next_audit_seq + 1)
        )


def _normalize_event(
    event: EventEnvelope,
) -> tuple[EventEnvelope, bytes, str]:
    canonical_bytes = rfc8785.dumps(
        event.model_dump(mode="json", exclude_none=True)
    )
    return event, canonical_bytes, sha256(canonical_bytes).hexdigest()


def _page_bytes(page: EventPage) -> bytes:
    return rfc8785.dumps(page.model_dump(mode="json", exclude_none=True))


async def _validate_causal_links(
    connection: AsyncConnection,
    run_id: UUID,
    events: list[EventEnvelope],
) -> None:
    causal_ids = {
        event.caused_by_event_id
        for event in events
        if event.caused_by_event_id is not None
    }
    if not causal_ids:
        return
    stored_rows = (
        await connection.execute(
            sa.select(
                evidence_records.c.source_event_id,
                evidence_records.c.producer_seq,
            ).where(
                evidence_records.c.run_id == run_id,
                evidence_records.c.source == "sut",
                evidence_records.c.disposition == "accepted",
                evidence_records.c.source_event_id.in_(causal_ids),
            )
        )
    ).all()
    earlier_sequences = {
        row.source_event_id: row.producer_seq for row in stored_rows
    }
    for event in events:
        cause = event.caused_by_event_id
        if cause is None:
            earlier_sequences[event.event_id] = event.producer_seq
            continue
        if cause == event.event_id:
            raise EvidenceInvalid("target event cannot cause itself")
        cause_sequence = earlier_sequences.get(cause)
        if cause_sequence is None:
            raise EvidenceInvalid(
                "target event causal link is not an earlier same-run event"
            )
        if cause_sequence >= event.producer_seq:
            raise EvidenceInvalid(
                "target event causal link is not earlier in producer order"
            )
        earlier_sequences[event.event_id] = event.producer_seq


async def _locked_run(
    connection: AsyncConnection,
    run_id: UUID,
    *,
    require_open: bool = True,
) -> Any:
    run = (
        await connection.execute(
            sa.select(runs).where(runs.c.run_id == run_id).with_for_update()
        )
    ).one_or_none()
    if run is None:
        raise IdentityMismatch("run does not exist")
    if require_open and not run.evidence_open:
        raise EvidenceClosed("run evidence is closed")
    return run


def _validate_page_identity(run: Any, page: EventPage) -> None:
    if page.contract_version != CONTRACT_VERSION:
        raise IdentityMismatch("event page contract version mismatch")
    if page.run_id != run.run_id or page.trace_id != run.trace_id:
        raise IdentityMismatch("event page identity mismatch")

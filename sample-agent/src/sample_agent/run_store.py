"""Bounded process-local target status and immutable event retention."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from sample_agent.contract_v1 import (
    CONTRACT_VERSION,
    MAX_EVENT_BYTES,
    MAX_TARGET_EVENTS,
    MAX_TARGET_EVENT_BYTES,
    MAX_TERMINAL_OUTPUT_BYTES,
    AcceptedResponse,
    CancellationAcknowledgement,
    CancellationRequest,
    CancelledPayload,
    CompletedPayload,
    EventEnvelope,
    EventPage,
    FailedPayload,
    RunCancelledEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunStatus,
    StartedPayload,
    TerminalResult,
    TestRunRequest,
)
from sample_agent.graph import ToolLookupPort, build_control_graph
from sample_agent.tool_client import ToolClientError


AGENT_ID = "boundary.sample-agent"
VULNERABLE_VERSION = "vulnerable-v1"
MAX_RETAINED_RUNS = 128
EVENT_PAGE_SIZE = 64

_EVENT_ADAPTER = TypeAdapter(EventEnvelope)


class RunConflict(Exception):
    """A run identity was reused with different normalized content."""


class RunNotFound(Exception):
    """The target does not retain the requested run."""


class StoreCapacityExceeded(Exception):
    """The bounded process-local store cannot accept another run."""


class PayloadLimitExceeded(Exception):
    """A target event or terminal output exceeded ADR 001 limits."""


class InvalidTargetEvent(Exception):
    """A target event violates contiguous or immutable target ordering."""


@dataclass(slots=True)
class RunRecord:
    request_digest: str
    run_id: UUID
    trace_id: UUID
    tested_agent_id: str
    tested_agent_version: str
    query: str
    tool_endpoint: str
    tool_capability: str = field(repr=False)
    fault_id: UUID | None
    state: Literal[
        "accepted",
        "running",
        "completed",
        "failed",
        "cancelled",
    ] = "accepted"
    events: list[EventEnvelope] = field(default_factory=list)
    total_event_bytes: int = 0
    final_producer_seq: int | None = None
    terminal_result: TerminalResult | None = None
    error_summary: str | None = None
    cancellations: dict[UUID, CancellationAcknowledgement] = field(
        default_factory=dict
    )


class RunStore:
    """Serialize state mutation and retain only a bounded number of runs."""

    def __init__(
        self,
        *,
        max_runs: int = MAX_RETAINED_RUNS,
        start_delay_ms: int = 0,
        tool_client: ToolLookupPort | None = None,
    ) -> None:
        if max_runs <= 0:
            raise ValueError("max_runs must be positive")
        if not 0 <= start_delay_ms <= 10_000:
            raise ValueError(
                "start_delay_ms must be between 0 and 10000"
            )
        self._max_runs = max_runs
        self._start_delay_seconds = start_delay_ms / 1000
        self._runs: OrderedDict[UUID, RunRecord] = OrderedDict()
        self._lock = asyncio.Lock()
        self._graph = build_control_graph(tool_client=tool_client)

    async def create(
        self,
        request: TestRunRequest,
    ) -> tuple[AcceptedResponse, bool]:
        request_digest = sha256(
            request.model_dump_json(exclude_none=True).encode("utf-8")
        ).hexdigest()
        async with self._lock:
            existing = self._runs.get(request.run_id)
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise RunConflict
                return self._accepted(existing), True
            if len(self._runs) >= self._max_runs:
                raise StoreCapacityExceeded
            record = RunRecord(
                request_digest=request_digest,
                run_id=request.run_id,
                trace_id=request.trace_id,
                tested_agent_id=AGENT_ID,
                tested_agent_version=VULNERABLE_VERSION,
                query=request.tested_input.query,
                tool_endpoint=request.tool_endpoint,
                tool_capability=request.tool_capability,
                fault_id=request.fault_id,
            )
            self._runs[request.run_id] = record
            return self._accepted(record), False

    async def execute_control(self, run_id: UUID) -> None:
        if self._start_delay_seconds:
            await asyncio.sleep(self._start_delay_seconds)
        async with self._lock:
            record = self._require(run_id)
            if record.state != "accepted":
                return
            record.state = "running"
            started = RunStartedEvent(
                contract_version=CONTRACT_VERSION,
                run_id=record.run_id,
                trace_id=record.trace_id,
                event_id=uuid4(),
                source="sut",
                event_type="sut.run.started",
                boundary="run",
                producer_seq=1,
                payload=StartedPayload(schema_version=1),
            )
            self._append(record, started)

        try:
            result = await self._graph.ainvoke(
                {
                    "query": record.query,
                    "run_id": record.run_id,
                    "trace_id": record.trace_id,
                    "fault_id": record.fault_id,
                    "tool_endpoint": record.tool_endpoint,
                    "tool_capability": record.tool_capability,
                }
            )
        except (ToolClientError, ValueError):
            async with self._lock:
                record = self._require(run_id)
                if record.state != "running":
                    return
                failed = RunFailedEvent(
                    contract_version=CONTRACT_VERSION,
                    run_id=record.run_id,
                    trace_id=record.trace_id,
                    event_id=uuid4(),
                    source="sut",
                    event_type="sut.run.failed",
                    boundary="run",
                    producer_seq=len(record.events) + 1,
                    caused_by_event_id=record.events[-1].event_id,
                    payload=FailedPayload(
                        schema_version=1,
                        error_code="TOOL_CALL_FAILED",
                    ),
                )
                self._append(record, failed)
                record.state = "failed"
                record.error_summary = "Boundary tool call failed"
                self._seal(
                    record,
                    event=failed,
                    outcome_kind="error",
                    output=None,
                )
            return

        async with self._lock:
            record = self._require(run_id)
            if record.state != "running":
                return
            completed = RunCompletedEvent(
                contract_version=CONTRACT_VERSION,
                run_id=record.run_id,
                trace_id=record.trace_id,
                event_id=uuid4(),
                source="sut",
                event_type="sut.run.completed",
                boundary="run",
                producer_seq=len(record.events) + 1,
                caused_by_event_id=record.events[-1].event_id,
                payload=CompletedPayload(
                    schema_version=1,
                    outcome_kind="success",
                ),
            )
            self._append(record, completed)
            record.state = "completed"
            self._seal(
                record,
                event=completed,
                outcome_kind="success",
                output=cast(str, result["output"]),
            )

    async def status(self, run_id: UUID) -> RunStatus:
        async with self._lock:
            return self._status(self._require(run_id))

    async def events(
        self,
        run_id: UUID,
        after_producer_seq: int,
    ) -> EventPage:
        if after_producer_seq < 0:
            raise ValueError("after_producer_seq must be non-negative")
        async with self._lock:
            record = self._require(run_id)
            selected = [
                event
                for event in record.events
                if event.producer_seq > after_producer_seq
            ][:EVENT_PAGE_SIZE]
            next_cursor = (
                selected[-1].producer_seq
                if selected
                else after_producer_seq
            )
            return EventPage(
                contract_version=CONTRACT_VERSION,
                run_id=record.run_id,
                trace_id=record.trace_id,
                events=selected,
                producer_high_watermark=len(record.events),
                next_after_producer_seq=next_cursor,
            )

    async def cancel(
        self,
        run_id: UUID,
        request: CancellationRequest,
    ) -> CancellationAcknowledgement:
        async with self._lock:
            record = self._require(run_id)
            existing = record.cancellations.get(request.cancellation_id)
            if existing is not None:
                return existing
            if (
                request.run_id != record.run_id
                or request.trace_id != record.trace_id
            ):
                raise ValueError("cancellation identity mismatch")

            applied = record.state in {"accepted", "running"}
            if applied:
                cancelled = RunCancelledEvent(
                    contract_version=CONTRACT_VERSION,
                    run_id=record.run_id,
                    trace_id=record.trace_id,
                    event_id=uuid4(),
                    source="sut",
                    event_type="sut.run.cancelled",
                    boundary="run",
                    producer_seq=len(record.events) + 1,
                    caused_by_event_id=(
                        record.events[-1].event_id
                        if record.events
                        else None
                    ),
                    payload=CancelledPayload(
                        schema_version=1,
                        cancellation_id=request.cancellation_id,
                    ),
                )
                self._append(record, cancelled)
                record.state = "cancelled"
                self._seal(
                    record,
                    event=cancelled,
                    outcome_kind="cancelled",
                    output=None,
                )
            acknowledgement = CancellationAcknowledgement(
                contract_version=CONTRACT_VERSION,
                run_id=record.run_id,
                trace_id=record.trace_id,
                cancellation_id=request.cancellation_id,
                cancellation_applied=applied,
                status=self._status(record),
            )
            record.cancellations[request.cancellation_id] = acknowledgement
            return acknowledgement

    async def append_event(
        self,
        run_id: UUID,
        event: EventEnvelope,
    ) -> None:
        """Validate and append an event; used by focused store tests."""
        async with self._lock:
            self._append(self._require(run_id), event)

    def _append(self, record: RunRecord, event: EventEnvelope) -> None:
        if record.final_producer_seq is not None:
            raise InvalidTargetEvent("the target stream is sealed")
        if len(record.events) >= MAX_TARGET_EVENTS:
            raise PayloadLimitExceeded("target event count exceeded")
        if event.run_id != record.run_id or event.trace_id != record.trace_id:
            raise InvalidTargetEvent("event identity mismatch")
        expected_seq = len(record.events) + 1
        if event.producer_seq != expected_seq:
            raise InvalidTargetEvent("producer sequence is not contiguous")
        if any(existing.event_id == event.event_id for existing in record.events):
            raise InvalidTargetEvent("event identity was reused")
        encoded = _EVENT_ADAPTER.dump_json(event)
        if len(encoded) > MAX_EVENT_BYTES:
            raise PayloadLimitExceeded("encoded event exceeded 64 KiB")
        if record.total_event_bytes + len(encoded) > MAX_TARGET_EVENT_BYTES:
            raise PayloadLimitExceeded("target event data exceeded 1 MiB")
        record.events.append(event)
        record.total_event_bytes += len(encoded)

    def _seal(
        self,
        record: RunRecord,
        *,
        event: EventEnvelope,
        outcome_kind: Literal["success", "degraded", "error", "cancelled"],
        output: object,
    ) -> None:
        if record.final_producer_seq is not None:
            raise InvalidTargetEvent("terminal watermark is immutable")
        output_bytes = TerminalResult(
            contract_version=CONTRACT_VERSION,
            run_id=record.run_id,
            trace_id=record.trace_id,
            tested_agent_id=record.tested_agent_id,
            tested_agent_version=record.tested_agent_version,
            state=cast(
                Literal["completed", "failed", "cancelled"],
                record.state,
            ),
            final_producer_seq=len(record.events),
            outcome_kind=outcome_kind,
            output=cast(object, output),
            event_id=event.event_id,
        )
        encoded_output = json.dumps(
            output,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded_output) > MAX_TERMINAL_OUTPUT_BYTES:
            raise PayloadLimitExceeded("terminal output exceeded 64 KiB")
        record.final_producer_seq = len(record.events)
        record.terminal_result = output_bytes

    def _accepted(self, record: RunRecord) -> AcceptedResponse:
        prefix = f"/test-runs/{record.run_id}"
        return AcceptedResponse(
            contract_version=CONTRACT_VERSION,
            run_id=record.run_id,
            trace_id=record.trace_id,
            tested_agent_id=record.tested_agent_id,
            tested_agent_version=record.tested_agent_version,
            state="accepted",
            status_url=prefix,
            events_url=f"{prefix}/events",
            cancellation_url=f"{prefix}/cancel",
            producer_high_watermark=0,
        )

    def _status(self, record: RunRecord) -> RunStatus:
        return RunStatus(
            contract_version=CONTRACT_VERSION,
            run_id=record.run_id,
            trace_id=record.trace_id,
            tested_agent_id=record.tested_agent_id,
            tested_agent_version=record.tested_agent_version,
            state=record.state,
            producer_high_watermark=len(record.events),
            final_producer_seq=record.final_producer_seq,
            terminal_result=record.terminal_result,
            error_summary=record.error_summary,
        )

    def _require(self, run_id: UUID) -> RunRecord:
        try:
            return self._runs[run_id]
        except KeyError:
            raise RunNotFound from None

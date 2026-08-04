"""Explicit transactions for the minimal campaign/run acceptance path."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID, uuid4

import rfc8785
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from boundary.persistence.tables import (
    campaigns,
    evidence_records,
    idempotency_records,
    runs,
)


OPERATION_KIND = "campaign.accept"
CAMPAIGN_KIND = "phase1.tool-timeout"
INITIAL_RUN_ROLE = "control"
INITIAL_STATUS = "accepted"
INITIAL_STEP = "control"
INITIAL_EVENT_TYPE = "boundary.run.accepted"
INITIAL_EVENT_BOUNDARY = "run"
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")

FailurePoint = Literal[
    "idempotency_record",
    "campaign",
    "run",
    "evidence",
]


class AcceptanceError(Exception):
    """Base class for stable acceptance-domain failures."""

    code = "ACCEPTANCE_ERROR"


class InvalidAcceptanceRequest(AcceptanceError):
    """The acceptance request is outside the one supported operation."""

    code = "INVALID_ACCEPTANCE_REQUEST"


class InvalidCanonicalDocument(AcceptanceError):
    """A supplied document does not match its canonical bytes or digest."""

    code = "INVALID_CANONICAL_DOCUMENT"


class IdempotencyConflict(AcceptanceError):
    """An idempotency identity was reused with different request content."""

    code = "IDEMPOTENCY_CONFLICT"

    def __init__(self, operation_kind: str, idempotency_key: str) -> None:
        self.operation_kind = operation_kind
        self.idempotency_key = idempotency_key
        super().__init__(
            "idempotency key conflicts with the accepted request"
        )


class PersistenceFailure(AcceptanceError):
    """PostgreSQL could not durably complete the acceptance operation."""

    code = "PERSISTENCE_FAILURE"


class PersistenceConflict(PersistenceFailure):
    """A database identity or integrity constraint rejected the write."""

    code = "PERSISTENCE_CONFLICT"

    def __init__(self, reason: str = "IDENTITY_CONFLICT") -> None:
        self.reason = reason
        super().__init__("a persistence identity constraint was violated")


@dataclass(frozen=True, slots=True)
class CanonicalDocument:
    """An exact document paired with its externally reviewable bytes."""

    schema_version: int
    document: dict[str, Any]
    canonical_bytes: bytes
    digest: str

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version <= 0
        ):
            raise InvalidCanonicalDocument(
                "document schema_version must be a positive integer"
            )
        if type(self.document) is not dict:
            raise InvalidCanonicalDocument(
                "document must be an exact JSON object"
            )
        if type(self.canonical_bytes) is not bytes:
            raise InvalidCanonicalDocument(
                "canonical_bytes must be bytes"
            )
        if (
            type(self.digest) is not str
            or _DIGEST_PATTERN.fullmatch(self.digest) is None
        ):
            raise InvalidCanonicalDocument(
                "digest must be lowercase hexadecimal SHA-256"
            )

        try:
            expected_bytes = rfc8785.dumps(self.document)
        except (TypeError, ValueError) as error:
            raise InvalidCanonicalDocument(
                "document is not RFC 8785 canonicalizable"
            ) from error

        if self.canonical_bytes != expected_bytes:
            raise InvalidCanonicalDocument(
                "document does not match canonical_bytes"
            )
        if sha256(self.canonical_bytes).hexdigest() != self.digest:
            raise InvalidCanonicalDocument(
                "canonical_bytes do not match digest"
            )


@dataclass(frozen=True, slots=True)
class AcceptanceCommand:
    """The stable caller-owned content of one acceptance request."""

    idempotency_key: str
    contract_version: str
    scenario_id: str
    scenario_version: int
    tested_agent_id: str
    tested_agent_version: str
    run_definition: CanonicalDocument
    executor_managed: bool = False


@dataclass(frozen=True, slots=True)
class PreparedAcceptance:
    """Validated content and fresh identities generated before SQL begins."""

    idempotency_key: str
    request_digest: str
    campaign_id: UUID
    run_id: UUID
    trace_id: UUID
    evidence_id: UUID
    contract_version: str
    scenario_id: str
    scenario_version: int
    tested_agent_id: str
    tested_agent_version: str
    definition_schema_version: int
    run_definition_bytes: bytes
    run_definition_digest: str
    evidence_payload_bytes: bytes
    evidence_payload_digest: str
    executor_managed: bool

    def __post_init__(self) -> None:
        identity_fields = (
            self.campaign_id,
            self.run_id,
            self.trace_id,
            self.evidence_id,
        )
        if any(type(value) is not UUID for value in identity_fields):
            raise InvalidAcceptanceRequest(
                "prepared identities must be UUID values"
            )

        definition = _validate_canonical_bytes(
            self.run_definition_bytes,
            self.run_definition_digest,
            "run definition",
        )
        if type(definition) is not dict:
            raise InvalidCanonicalDocument(
                "run definition must be a JSON object"
            )

        evidence_payload = _validate_canonical_bytes(
            self.evidence_payload_bytes,
            self.evidence_payload_digest,
            "evidence payload",
        )
        expected_evidence_payload = {
            "campaign_id": str(self.campaign_id),
            "from_status": None,
            "operation_kind": OPERATION_KIND,
            "run_id": str(self.run_id),
            "schema_version": 1,
            "to_status": INITIAL_STATUS,
            "transition": "run_accepted",
        }
        if evidence_payload != expected_evidence_payload:
            raise InvalidAcceptanceRequest(
                "prepared evidence does not match its identities"
            )

        request_document = {
            "campaign_kind": CAMPAIGN_KIND,
            "contract_version": self.contract_version,
            "definition_digest": self.run_definition_digest,
            "definition_schema_version": self.definition_schema_version,
            "operation_kind": OPERATION_KIND,
            "run_role": INITIAL_RUN_ROLE,
            "scenario_id": self.scenario_id,
            "scenario_version": self.scenario_version,
            "tested_agent_id": self.tested_agent_id,
            "tested_agent_version": self.tested_agent_version,
            "executor_managed": self.executor_managed,
        }
        expected_request_digest = sha256(
            rfc8785.dumps(request_document)
        ).hexdigest()
        if self.request_digest != expected_request_digest:
            raise InvalidAcceptanceRequest(
                "prepared request digest does not match its content"
            )


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    """The durable identities returned by acceptance or idempotent replay."""

    campaign_id: UUID
    run_id: UUID
    trace_id: UUID
    evidence_id: UUID
    replayed: bool


def prepare_campaign_run_acceptance(
    command: AcceptanceCommand,
) -> PreparedAcceptance:
    """Validate, normalize, hash, and generate identities before SQL begins."""
    _validate_command(command)

    request_document = {
        "campaign_kind": CAMPAIGN_KIND,
        "contract_version": command.contract_version,
        "definition_digest": command.run_definition.digest,
        "definition_schema_version": command.run_definition.schema_version,
        "operation_kind": OPERATION_KIND,
        "run_role": INITIAL_RUN_ROLE,
        "scenario_id": command.scenario_id,
        "scenario_version": command.scenario_version,
        "tested_agent_id": command.tested_agent_id,
        "tested_agent_version": command.tested_agent_version,
        "executor_managed": command.executor_managed,
    }
    request_digest = sha256(rfc8785.dumps(request_document)).hexdigest()

    campaign_id = uuid4()
    run_id = uuid4()
    trace_id = uuid4()
    evidence_id = uuid4()
    evidence_payload = {
        "campaign_id": str(campaign_id),
        "from_status": None,
        "operation_kind": OPERATION_KIND,
        "run_id": str(run_id),
        "schema_version": 1,
        "to_status": INITIAL_STATUS,
        "transition": "run_accepted",
    }
    evidence_payload_bytes = rfc8785.dumps(evidence_payload)

    return PreparedAcceptance(
        idempotency_key=command.idempotency_key,
        request_digest=request_digest,
        campaign_id=campaign_id,
        run_id=run_id,
        trace_id=trace_id,
        evidence_id=evidence_id,
        contract_version=command.contract_version,
        scenario_id=command.scenario_id,
        scenario_version=command.scenario_version,
        tested_agent_id=command.tested_agent_id,
        tested_agent_version=command.tested_agent_version,
        definition_schema_version=command.run_definition.schema_version,
        run_definition_bytes=command.run_definition.canonical_bytes,
        run_definition_digest=command.run_definition.digest,
        evidence_payload_bytes=evidence_payload_bytes,
        evidence_payload_digest=sha256(evidence_payload_bytes).hexdigest(),
        executor_managed=command.executor_managed,
    )


async def accept_campaign_run(
    engine: AsyncEngine,
    prepared: PreparedAcceptance,
    *,
    _fail_after: FailurePoint | None = None,
) -> AcceptanceResult:
    """Atomically accept or replay one prepared campaign/run request."""
    try:
        async with engine.begin() as connection:
            reservation = await connection.execute(
                postgresql_insert(idempotency_records)
                .values(
                    operation_kind=OPERATION_KIND,
                    idempotency_key=prepared.idempotency_key,
                    request_digest=prepared.request_digest,
                    campaign_id=prepared.campaign_id,
                    run_id=prepared.run_id,
                    resource_kind="campaign",
                    resource_id=prepared.campaign_id,
                    resource_links={
                        "campaign_id": str(prepared.campaign_id),
                        "run_id": str(prepared.run_id),
                    },
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        idempotency_records.c.operation_kind,
                        idempotency_records.c.idempotency_key,
                    ]
                )
            )

            if reservation.rowcount == 0:
                return await _load_idempotent_result(
                    connection,
                    prepared,
                )

            _raise_for_test_failure(_fail_after, "idempotency_record")

            await connection.execute(
                campaigns.insert().values(
                    campaign_id=prepared.campaign_id,
                    campaign_kind=CAMPAIGN_KIND,
                    status=INITIAL_STATUS,
                    current_step=INITIAL_STEP,
                    cancel_requested=False,
                    executor_managed=prepared.executor_managed,
                )
            )
            _raise_for_test_failure(_fail_after, "campaign")

            await connection.execute(
                runs.insert().values(
                    run_id=prepared.run_id,
                    trace_id=prepared.trace_id,
                    campaign_id=prepared.campaign_id,
                    control_run_id=None,
                    run_role=INITIAL_RUN_ROLE,
                    contract_version=prepared.contract_version,
                    scenario_id=prepared.scenario_id,
                    scenario_version=prepared.scenario_version,
                    expected_tested_agent_id=prepared.tested_agent_id,
                    expected_tested_agent_version=(
                        prepared.tested_agent_version
                    ),
                    reported_tested_agent_id=None,
                    reported_tested_agent_version=None,
                    operational_status=INITIAL_STATUS,
                    definition_schema_version=(
                        prepared.definition_schema_version
                    ),
                    run_definition=json.loads(
                        prepared.run_definition_bytes
                    ),
                    run_definition_bytes=prepared.run_definition_bytes,
                    run_definition_digest=(
                        prepared.run_definition_digest
                    ),
                    target_producer_cursor=0,
                    target_final_watermark=None,
                    next_receipt_seq=2,
                    next_tool_ordinal=0,
                    evidence_open=True,
                )
            )
            _raise_for_test_failure(_fail_after, "run")

            await connection.execute(
                evidence_records.insert().values(
                    evidence_id=prepared.evidence_id,
                    run_id=prepared.run_id,
                    source="boundary",
                    event_type=INITIAL_EVENT_TYPE,
                    boundary=INITIAL_EVENT_BOUNDARY,
                    source_event_id=prepared.evidence_id,
                    producer_seq=None,
                    receipt_seq=1,
                    caused_by_event_id=None,
                    payload_schema_version=1,
                    payload=json.loads(prepared.evidence_payload_bytes),
                    payload_canonical_bytes=(
                        prepared.evidence_payload_bytes
                    ),
                    payload_digest=prepared.evidence_payload_digest,
                    disposition="accepted",
                )
            )
            _raise_for_test_failure(_fail_after, "evidence")

        return AcceptanceResult(
            campaign_id=prepared.campaign_id,
            run_id=prepared.run_id,
            trace_id=prepared.trace_id,
            evidence_id=prepared.evidence_id,
            replayed=False,
        )
    except AcceptanceError:
        raise
    except IntegrityError as error:
        raise PersistenceConflict(
            _constraint_reason(error)
        ) from None
    except SQLAlchemyError:
        raise PersistenceFailure(
            "the acceptance transaction failed"
        ) from None


async def _load_idempotent_result(
    connection: AsyncConnection,
    prepared: PreparedAcceptance,
) -> AcceptanceResult:
    query = (
        sa.select(
            idempotency_records.c.request_digest,
            idempotency_records.c.campaign_id,
            idempotency_records.c.run_id,
            runs.c.trace_id,
            evidence_records.c.evidence_id,
        )
        .select_from(
            idempotency_records.join(
                runs,
                idempotency_records.c.run_id == runs.c.run_id,
            ).join(
                evidence_records,
                sa.and_(
                    evidence_records.c.run_id == runs.c.run_id,
                    evidence_records.c.receipt_seq == 1,
                    evidence_records.c.event_type == INITIAL_EVENT_TYPE,
                ),
            )
        )
        .where(
            idempotency_records.c.operation_kind == OPERATION_KIND,
            idempotency_records.c.idempotency_key
            == prepared.idempotency_key,
        )
    )
    existing = (await connection.execute(query)).one_or_none()
    if existing is None:
        raise PersistenceFailure(
            "the idempotency mapping is missing its accepted resources"
        )
    if existing.request_digest != prepared.request_digest:
        raise IdempotencyConflict(
            OPERATION_KIND,
            prepared.idempotency_key,
        )
    return AcceptanceResult(
        campaign_id=existing.campaign_id,
        run_id=existing.run_id,
        trace_id=existing.trace_id,
        evidence_id=existing.evidence_id,
        replayed=True,
    )


def _validate_command(command: AcceptanceCommand) -> None:
    if not isinstance(command, AcceptanceCommand):
        raise InvalidAcceptanceRequest(
            "command must be an AcceptanceCommand"
        )
    bounded_strings = (
        ("idempotency_key", command.idempotency_key, 255),
        ("contract_version", command.contract_version, 32),
        ("scenario_id", command.scenario_id, 128),
        ("tested_agent_id", command.tested_agent_id, 128),
        ("tested_agent_version", command.tested_agent_version, 256),
    )
    for field_name, value, maximum in bounded_strings:
        if type(value) is not str or not value or len(value) > maximum:
            raise InvalidAcceptanceRequest(
                f"{field_name} must be a non-empty bounded string"
            )
    if (
        type(command.scenario_version) is not int
        or command.scenario_version <= 0
    ):
        raise InvalidAcceptanceRequest(
            "scenario_version must be a positive integer"
        )
    if not isinstance(command.run_definition, CanonicalDocument):
        raise InvalidAcceptanceRequest(
            "run_definition must be a CanonicalDocument"
        )
    if type(command.executor_managed) is not bool:
        raise InvalidAcceptanceRequest(
            "executor_managed must be a boolean"
        )


def _raise_for_test_failure(
    selected: FailurePoint | None,
    completed: FailurePoint,
) -> None:
    if selected == completed:
        raise PersistenceFailure(
            f"forced failure after {completed}"
        )


def _constraint_reason(error: IntegrityError) -> str:
    constraint_name: str | None = None
    current: BaseException | None = error.orig
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        candidate = getattr(current, "constraint_name", None)
        if isinstance(candidate, str):
            constraint_name = candidate
            break
        current = current.__cause__ or current.__context__

    reasons = {
        "uq_runs_trace_id": "DUPLICATE_TRACE_ID",
        "uq_evidence_records_run_receipt_seq": (
            "DUPLICATE_RECEIPT_SEQUENCE"
        ),
        "uq_evidence_records_run_source_event": (
            "DUPLICATE_EVIDENCE_IDENTITY"
        ),
        "pk_campaigns": "DUPLICATE_CAMPAIGN_ID",
        "pk_runs": "DUPLICATE_RUN_ID",
        "pk_idempotency_records": "DUPLICATE_IDEMPOTENCY_IDENTITY",
        "pk_evidence_records": "DUPLICATE_EVIDENCE_IDENTITY",
    }
    return reasons.get(constraint_name, "IDENTITY_CONFLICT")


def _validate_canonical_bytes(
    canonical_bytes: bytes,
    digest: str,
    label: str,
) -> Any:
    if type(canonical_bytes) is not bytes:
        raise InvalidCanonicalDocument(f"{label} bytes must be bytes")
    if (
        type(digest) is not str
        or _DIGEST_PATTERN.fullmatch(digest) is None
    ):
        raise InvalidCanonicalDocument(
            f"{label} digest must be lowercase hexadecimal SHA-256"
        )
    if sha256(canonical_bytes).hexdigest() != digest:
        raise InvalidCanonicalDocument(
            f"{label} bytes do not match their digest"
        )
    try:
        document = json.loads(canonical_bytes)
        recanonicalized = rfc8785.dumps(document)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise InvalidCanonicalDocument(
            f"{label} bytes are not canonical JSON"
        ) from error
    if recanonicalized != canonical_bytes:
        raise InvalidCanonicalDocument(
            f"{label} bytes are not RFC 8785 canonical"
        )
    return document

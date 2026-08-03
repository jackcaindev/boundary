"""Load digest-verified evidence referenced by a finalized manifest."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import UUID

import rfc8785
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from boundary.domain.evidence import EvidenceManifestV1, EvidenceReference
from boundary.evidence.activation_manifest import (
    ActivationBindingError,
    build_timeout_activation_bindings,
)
from boundary.persistence.tables import evidence_records, evidence_sets


class FinalizedSnapshotError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FinalizedSnapshot:
    manifest: EvidenceManifestV1
    evidence_set_digest: str
    payloads: dict[UUID, dict]

    @property
    def references(self) -> list[EvidenceReference]:
        return self.manifest.accepted_evidence

    def refs_for_types(self, *event_types: str) -> list[EvidenceReference]:
        selected = set(event_types)
        return [
            reference
            for reference in self.references
            if reference.event_type in selected
        ]

    def payload(self, reference: EvidenceReference) -> dict:
        return self.payloads[reference.evidence_id]


async def load_finalized_snapshot(
    connection: AsyncConnection,
    *,
    evidence_set_id: UUID | None = None,
    evidence_set_digest: str | None = None,
    run_id: UUID | None = None,
    lock: bool = False,
) -> FinalizedSnapshot | None:
    selectors = [
        value is not None
        for value in (evidence_set_id, evidence_set_digest, run_id)
    ]
    if sum(selectors) != 1:
        raise ValueError("exactly one evidence-set selector is required")
    statement = sa.select(evidence_sets)
    if evidence_set_id is not None:
        statement = statement.where(
            evidence_sets.c.evidence_set_id == evidence_set_id
        )
    elif evidence_set_digest is not None:
        statement = statement.where(
            evidence_sets.c.evidence_set_digest == evidence_set_digest
        )
    else:
        statement = statement.where(evidence_sets.c.run_id == run_id)
    if lock:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).one_or_none()
    if row is None:
        return None
    try:
        manifest = EvidenceManifestV1.model_validate_json(
            json.dumps(row.manifest)
        )
    except Exception as error:
        raise FinalizedSnapshotError("manifest schema validation failed") from error
    manifest_bytes = rfc8785.dumps(manifest.model_dump(mode="json"))
    if (
        manifest.evidence_set_id != row.evidence_set_id
        or manifest.run_id != row.run_id
        or manifest.cutoff_reason != row.cutoff_reason
        or manifest.target_final_watermark != row.target_final_watermark
        or manifest_bytes != row.manifest_canonical_bytes
        or sha256(manifest_bytes).hexdigest() != row.evidence_set_digest
    ):
        raise FinalizedSnapshotError("manifest digest verification failed")

    evidence_ids = [ref.evidence_id for ref in manifest.accepted_evidence]
    rows = (
        await connection.execute(
            sa.select(evidence_records).where(
                evidence_records.c.evidence_id.in_(evidence_ids)
            )
        )
    ).all()
    by_id = {evidence.evidence_id: evidence for evidence in rows}
    payloads: dict[UUID, dict] = {}
    for reference in manifest.accepted_evidence:
        evidence = by_id.get(reference.evidence_id)
        if evidence is None:
            raise FinalizedSnapshotError("manifest evidence reference is missing")
        canonical = rfc8785.dumps(evidence.payload)
        if (
            evidence.run_id != manifest.run_id
            or evidence.disposition != "accepted"
            or evidence.receipt_seq != reference.receipt_seq
            or evidence.source != reference.source
            or evidence.event_type != reference.event_type
            or evidence.boundary != reference.boundary
            or evidence.source_event_id != reference.source_event_id
            or evidence.producer_seq != reference.producer_seq
            or evidence.caused_by_event_id != reference.caused_by_event_id
            or evidence.payload_schema_version
            != reference.payload_schema_version
            or evidence.payload_digest != reference.content_digest
            or evidence.payload_canonical_bytes != canonical
            or sha256(canonical).hexdigest() != reference.content_digest
        ):
            raise FinalizedSnapshotError(
                "manifest evidence reference failed integrity verification"
            )
        payloads[reference.evidence_id] = evidence.payload
    try:
        activation_bindings = await build_timeout_activation_bindings(
            connection,
            run_id=manifest.run_id,
            evidence_by_id=by_id,
        )
    except (ActivationBindingError, ValueError, TypeError) as error:
        raise FinalizedSnapshotError(
            "finalized activation proof failed integrity verification"
        ) from error
    if activation_bindings != manifest.timeout_activations:
        raise FinalizedSnapshotError(
            "finalized activation proof was mutated or mismatched"
        )
    return FinalizedSnapshot(
        manifest=manifest,
        evidence_set_digest=row.evidence_set_digest,
        payloads=payloads,
    )

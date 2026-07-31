"""Generate and persist secret-free run-scoped capability bindings."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.persistence.tables import run_capabilities, runs


CAPABILITY_BYTES = 48
CONTROL_TOOL_IDENTITY = "boundary.phase1.lookup"


class CapabilityError(Exception):
    """Capability generation or persistence failed safely."""


class CapabilityBindingError(CapabilityError):
    """The requested binding conflicts with the authoritative run."""


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    """Return the one-time secret separately from its safe record identity."""

    capability_record_id: UUID
    capability_secret: str

    def __repr__(self) -> str:
        return (
            "CapabilityGrant("
            f"capability_record_id={self.capability_record_id!r}, "
            "capability_secret=<redacted>)"
        )


async def create_control_capability(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    trace_id: UUID,
    tool_identity: str,
    expires_at: datetime,
) -> CapabilityGrant:
    """Create one fresh high-entropy no-fault capability for a run."""
    if not tool_identity or len(tool_identity) > 128:
        raise CapabilityBindingError("tool identity is invalid")
    if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
        raise CapabilityBindingError("capability expiry must be in the future")
    secret = secrets.token_urlsafe(CAPABILITY_BYTES)
    secret_hash = sha256(secret.encode("ascii")).hexdigest()
    record_id = uuid4()
    try:
        async with engine.begin() as connection:
            run = (
                await connection.execute(
                    sa.select(
                        runs.c.trace_id,
                        runs.c.run_role,
                        runs.c.operational_status,
                    )
                    .where(runs.c.run_id == run_id)
                    .with_for_update()
                )
            ).one_or_none()
            if run is None:
                raise CapabilityBindingError("run does not exist")
            if run.trace_id != trace_id:
                raise CapabilityBindingError("trace binding conflicts")
            if run.run_role != "control":
                raise CapabilityBindingError(
                    "control capability requires a control run"
                )
            if run.operational_status not in {"accepted", "running"}:
                raise CapabilityBindingError("run is not active")
            await connection.execute(
                run_capabilities.insert().values(
                    capability_record_id=record_id,
                    capability_hash=secret_hash,
                    run_id=run_id,
                    trace_id=trace_id,
                    tool_identity=tool_identity,
                    no_fault_binding=True,
                    fault_id=None,
                    expires_at=expires_at,
                    state="active",
                    retired_at=None,
                )
            )
    except CapabilityBindingError:
        raise
    except IntegrityError:
        raise CapabilityBindingError(
            "run already has a capability binding"
        ) from None
    except SQLAlchemyError:
        raise CapabilityError("capability persistence failed") from None
    return CapabilityGrant(
        capability_record_id=record_id,
        capability_secret=secret,
    )


async def create_injected_capability(
    engine: AsyncEngine,
    *,
    run_id: UUID,
    trace_id: UUID,
    tool_identity: str,
    fault_id: UUID,
    expires_at: datetime,
) -> CapabilityGrant:
    """Create one fresh fault-bound capability for an injected run."""
    if not tool_identity or len(tool_identity) > 128:
        raise CapabilityBindingError("tool identity is invalid")
    if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
        raise CapabilityBindingError("capability expiry must be in the future")
    secret = secrets.token_urlsafe(CAPABILITY_BYTES)
    secret_hash = sha256(secret.encode("ascii")).hexdigest()
    record_id = uuid4()
    try:
        async with engine.begin() as connection:
            run = (
                await connection.execute(
                    sa.select(
                        runs.c.trace_id,
                        runs.c.run_role,
                        runs.c.fault_id,
                        runs.c.operational_status,
                    )
                    .where(runs.c.run_id == run_id)
                    .with_for_update()
                )
            ).one_or_none()
            if run is None:
                raise CapabilityBindingError("run does not exist")
            if run.trace_id != trace_id:
                raise CapabilityBindingError("trace binding conflicts")
            if run.run_role != "injected":
                raise CapabilityBindingError(
                    "fault capability requires an injected run"
                )
            if run.fault_id != fault_id:
                raise CapabilityBindingError("fault binding conflicts")
            if run.operational_status not in {"accepted", "running"}:
                raise CapabilityBindingError("run is not active")
            await connection.execute(
                run_capabilities.insert().values(
                    capability_record_id=record_id,
                    capability_hash=secret_hash,
                    run_id=run_id,
                    trace_id=trace_id,
                    tool_identity=tool_identity,
                    no_fault_binding=False,
                    fault_id=fault_id,
                    expires_at=expires_at,
                    state="active",
                    retired_at=None,
                )
            )
    except CapabilityBindingError:
        raise
    except IntegrityError:
        raise CapabilityBindingError(
            "run already has a capability binding"
        ) from None
    except SQLAlchemyError:
        raise CapabilityError("capability persistence failed") from None
    return CapabilityGrant(
        capability_record_id=record_id,
        capability_secret=secret,
    )


async def retire_capability(
    engine: AsyncEngine,
    capability_record_id: UUID,
    *,
    retired_at: datetime | None = None,
) -> None:
    """Retire an active capability idempotently."""
    moment = retired_at or datetime.now(timezone.utc)
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                sa.select(
                    run_capabilities.c.state,
                    run_capabilities.c.retired_at,
                )
                .where(
                    run_capabilities.c.capability_record_id
                    == capability_record_id
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise CapabilityBindingError("capability record does not exist")
        if row.state == "retired":
            return
        await connection.execute(
            run_capabilities.update()
            .where(
                run_capabilities.c.capability_record_id
                == capability_record_id
            )
            .values(state="retired", retired_at=moment)
        )

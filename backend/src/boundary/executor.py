"""Single-process PostgreSQL-authoritative serial campaign executor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID, uuid4

import rfc8785
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from boundary.api.mutations import BUNDLED_INPUT
from boundary.config import BoundarySettings
from boundary.evaluation.analyzer import analyze_evidence_set
from boundary.evidence.collector import (
    transition_run,
)
from boundary.evidence.finalizer import finalize_run_evidence
from boundary.execution.control import (
    _raise_process_loss,
    execute_control_run,
    resume_polling_run,
)
from boundary.execution.injected import create_injected_sibling, execute_injected_run
from boundary.injection.capability import retire_capability
from boundary.injection.timeout import reconcile_abandoned_activation_runtimes
from boundary.persistence.tables import (
    campaigns,
    evidence_records,
    evidence_sets,
    fault_activations,
    regression_cases,
    reruns,
    run_capabilities,
    runs,
    tool_calls,
)
from boundary.regression.materializer import materialize_regression_case
from boundary.regression.rerun import execute_rerun, settle_rerun_cancellation
from boundary.sut.client import SutClient
from boundary.sut.contract_v1 import CancellationRequest


class SerialExecutor:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: BoundarySettings,
        *,
        _fail_after: str | None = None,
        _include_unmanaged: bool = False,
        _sut_client=None,
        _target_interaction_hook=None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.active_campaign_id: UUID | None = None
        self._fail_after = _fail_after
        self._include_unmanaged = _include_unmanaged
        self._sut_client = _sut_client
        self._target_interaction_hook = _target_interaction_hook

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        await self.reconcile_startup()
        self._task = asyncio.create_task(self._loop(), name="boundary-serial-executor")
        self.wake()

    def wake(self) -> None:
        self._wake.set()

    async def stop(self, *, timeout_seconds: float = 3.0) -> None:
        self._stop.set()
        self._wake.set()
        if self.active_campaign_id is not None:
            async with self.engine.begin() as connection:
                shutdown_cancellation_id = uuid4()
                await connection.execute(
                    campaigns.update()
                    .where(
                        campaigns.c.campaign_id == self.active_campaign_id,
                        campaigns.c.status == "running",
                    )
                    .values(
                        cancel_requested=True,
                        cancellation_id=sa.func.coalesce(
                            campaigns.c.cancellation_id,
                            shutdown_cancellation_id,
                        ),
                        cancel_requested_at=sa.func.coalesce(
                            campaigns.c.cancel_requested_at,
                            sa.func.now(),
                        ),
                    )
                )
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout_seconds)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                campaign_id = await self._claim_oldest()
            except Exception:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
                continue
            if campaign_id is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
                continue
            self.active_campaign_id = campaign_id
            try:
                await self._process(campaign_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                if await self._cancelled(campaign_id):
                    await self._settle_cancelled_campaign(campaign_id)
                else:
                    await self._settle_failed_campaign(campaign_id)
                    await self._fail_campaign(campaign_id, "CAMPAIGN_EXECUTION_ERROR")
            finally:
                self.active_campaign_id = None

    async def _claim_oldest(self) -> UUID | None:
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(
                    sa.select(campaigns)
                    .where(
                        campaigns.c.status == "accepted",
                        sa.or_(
                            campaigns.c.executor_managed.is_(True),
                            sa.true() if self._include_unmanaged else sa.false(),
                        ),
                    )
                    .order_by(campaigns.c.created_at, campaigns.c.campaign_id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).one_or_none()
            if row is None:
                return None
            if row.cancel_requested:
                updated = await connection.execute(
                    campaigns.update()
                    .where(
                        campaigns.c.campaign_id == row.campaign_id,
                        campaigns.c.status == "accepted",
                    )
                    .values(status="running", current_step="cancelling")
                )
                return row.campaign_id if updated.rowcount == 1 else None
            updated = await connection.execute(
                campaigns.update()
                .where(
                    campaigns.c.campaign_id == row.campaign_id,
                    campaigns.c.status == "accepted",
                )
                .values(status="running", claimed_at=sa.func.now())
            )
            return row.campaign_id if updated.rowcount == 1 else None

    async def _process(self, campaign_id: UUID) -> None:
        async with self.engine.connect() as connection:
            campaign = (
                await connection.execute(
                    sa.select(campaigns).where(campaigns.c.campaign_id == campaign_id)
                )
            ).one()
            rerun = (
                await connection.execute(
                    sa.select(reruns).where(reruns.c.campaign_id == campaign_id)
                )
            ).one_or_none()
        if campaign.cancel_requested and await self._queued_cancellation_is_safe(
            campaign_id
        ):
            await self._settle_queued_cancellation(campaign_id)
            return
        if campaign.campaign_kind == "phase1.tool-timeout.rerun":
            if rerun is None:
                raise RuntimeError("rerun campaign is missing its rerun")
            await execute_rerun(
                self.engine,
                rerun_id=rerun.rerun_id,
                sut_base_url=self.settings.sut_base_url,
                boundary_internal_base_url=self.settings.boundary_internal_base_url,
                execution_budget_ms=self.settings.run_deadline_ms,
                _fail_after=self._fail_after,
                _sut_client=self._sut_client,
                _target_interaction_hook=self._target_interaction_hook,
            )
            return
        if campaign.campaign_kind != "phase1.tool-timeout":
            raise RuntimeError("campaign kind is unsupported")
        await self._execute_bundled(campaign_id)

    async def _execute_bundled(self, campaign_id: UUID) -> None:
        control = await self._campaign_control(campaign_id)
        control_result = None
        if (
            control.operational_status in {"accepted", "running"}
            and control.execution_checkpoint == "polling"
        ):
            control_result = await resume_polling_run(
                self.engine,
                run_id=control.run_id,
                sut_base_url=self.settings.sut_base_url,
                execution_budget_ms=self.settings.run_deadline_ms,
                cancellation_grace_ms=self.settings.cancellation_grace_ms,
                poll_interval_ms=self.settings.target_poll_interval_ms,
            )
        elif control.operational_status == "accepted":
            control_result = await execute_control_run(
                self.engine,
                run_id=control.run_id,
                sut_base_url=self.settings.sut_base_url,
                tool_endpoint=(
                    f"{self.settings.boundary_internal_base_url}/internal/v1/runs/"
                    f"{control.run_id}/tools/phase1-lookup"
                ),
                tested_input=BUNDLED_INPUT,
                execution_budget_ms=self.settings.run_deadline_ms,
                cancellation_grace_ms=self.settings.cancellation_grace_ms,
                poll_interval_ms=self.settings.target_poll_interval_ms,
                http_timeout_seconds=5.0,
                sut_client=self._sut_client,
                _fail_after=self._fail_after,
                _target_interaction_hook=self._target_interaction_hook,
            )
        if (
            control_result is not None
            and control_result.operational_status
            in {"cancelled", "timed_out"}
            and await self._cancelled(campaign_id)
        ):
            await self._settle_cancelled_campaign(campaign_id)
            return
        await finalize_run_evidence(self.engine, run_id=control.run_id)
        await self._checkpoint(control.run_id, "finalized")
        if await self._cancelled(campaign_id):
            await self._cancel_pending_campaign(campaign_id)
            return
        async with self.engine.begin() as connection:
            await connection.execute(
                campaigns.update()
                .where(campaigns.c.campaign_id == campaign_id)
                .values(current_step="injected")
            )
        async with self.engine.connect() as connection:
            existing_injected = (
                await connection.execute(
                    sa.select(runs).where(
                        runs.c.campaign_id == campaign_id,
                        runs.c.run_role == "injected",
                    )
                )
            ).one_or_none()
        if existing_injected is None:
            sibling = await create_injected_sibling(
                self.engine,
                control_run_id=control.run_id,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
            self._raise_process_loss("bundled_injected_sibling")
            await execute_injected_run(
                self.engine,
                sibling=sibling,
                sut_base_url=self.settings.sut_base_url,
                tool_endpoint=(
                    f"{self.settings.boundary_internal_base_url}/internal/v1/runs/"
                    f"{sibling.run_id}/tools/phase1-lookup"
                ),
                execution_budget_ms=self.settings.run_deadline_ms,
                cancellation_grace_ms=self.settings.cancellation_grace_ms,
                poll_interval_ms=self.settings.target_poll_interval_ms,
                http_timeout_seconds=5.0,
                sut_client=self._sut_client,
                _fail_after=self._fail_after,
                _target_interaction_hook=self._target_interaction_hook,
            )
            injected_run_id = sibling.run_id
        elif (
            existing_injected.operational_status in {"accepted", "running"}
            and existing_injected.execution_checkpoint == "polling"
        ):
            await resume_polling_run(
                self.engine,
                run_id=existing_injected.run_id,
                sut_base_url=self.settings.sut_base_url,
                execution_budget_ms=self.settings.run_deadline_ms,
                cancellation_grace_ms=self.settings.cancellation_grace_ms,
                poll_interval_ms=self.settings.target_poll_interval_ms,
            )
            injected_run_id = existing_injected.run_id
        elif existing_injected.operational_status == "accepted":
            raise RuntimeError(
                "accepted injected run lacks live capability authority"
            )
        else:
            injected_run_id = existing_injected.run_id
        finalized = await finalize_run_evidence(
            self.engine, run_id=injected_run_id
        )
        await self._checkpoint(injected_run_id, "finalized")
        if await self._cancelled(campaign_id):
            await analyze_evidence_set(
                self.engine,
                evidence_set_id=finalized.evidence_set_id,
            )
            await self._checkpoint(injected_run_id, "analyzed")
            await self._cancel_pending_campaign(campaign_id)
            return
        analysis = await analyze_evidence_set(
            self.engine, evidence_set_id=finalized.evidence_set_id
        )
        await self._checkpoint(injected_run_id, "analyzed")
        vector = {
            item.assertion_id: item.outcome
            for item in analysis.document.assertions or []
        }
        if (
            analysis.document.evaluability.aggregate != "EVALUABLE"
            or analysis.document.scenario_policy_result != "FAIL"
            or vector
            != {
                "P1.RETRY_LIMIT": "FAIL",
                "P1.DEGRADED_RESULT": "FAIL",
                "P1.RUN_WITHIN_BUDGET": "FAIL",
            }
        ):
            raise RuntimeError("vulnerable result did not match the reviewed vector")
        async with self.engine.begin() as connection:
            await connection.execute(
                campaigns.update()
                .where(campaigns.c.campaign_id == campaign_id)
                .values(current_step="regression_case")
            )
        await materialize_regression_case(
            self.engine, source_analysis_id=analysis.analysis_id
        )
        async with self.engine.begin() as connection:
            await connection.execute(
                campaigns.update()
                .where(campaigns.c.campaign_id == campaign_id)
                .values(status="completed", current_step="completed")
            )

    async def _campaign_control(self, campaign_id: UUID):
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    sa.select(runs).where(
                        runs.c.campaign_id == campaign_id,
                        runs.c.run_role == "control",
                    )
                )
            ).one_or_none()
        if row is None:
            raise RuntimeError("campaign control run is missing")
        return row

    async def _checkpoint(self, run_id: UUID, checkpoint: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                runs.update().where(runs.c.run_id == run_id).values(
                    execution_checkpoint=checkpoint
                )
            )

    async def _cancelled(self, campaign_id: UUID) -> bool:
        async with self.engine.connect() as connection:
            return bool(
                await connection.scalar(
                    sa.select(campaigns.c.cancel_requested).where(
                        campaigns.c.campaign_id == campaign_id
                    )
                )
            )

    async def _cancel_pending_campaign(self, campaign_id: UUID) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                campaigns.update()
                .where(campaigns.c.campaign_id == campaign_id)
                .values(status="cancelled", current_step="cancelled")
            )

    async def _settle_cancelled_rerun(self, campaign_id: UUID) -> None:
        async with self.engine.connect() as connection:
            rerun_id = await connection.scalar(
                sa.select(reruns.c.rerun_id).where(
                    reruns.c.campaign_id == campaign_id
                )
            )
        if rerun_id is not None:
            await settle_rerun_cancellation(
                self.engine,
                rerun_id=rerun_id,
            )

    async def _queued_cancellation_is_safe(self, campaign_id: UUID) -> bool:
        async with self.engine.connect() as connection:
            open_runs = (
                await connection.execute(
                    sa.select(runs.c.run_id).where(
                        runs.c.campaign_id == campaign_id,
                        runs.c.operational_status.in_(
                            ["accepted", "running", "cancelled", "timed_out"]
                        ),
                        runs.c.evidence_open.is_(True),
                    )
                )
            ).scalars().all()
        if not open_runs:
            return True
        return all(
            [await self._no_target_interaction(run_id) for run_id in open_runs]
        )

    async def _settle_queued_cancellation(self, campaign_id: UUID) -> None:
        async with self.engine.connect() as connection:
            run_rows = (
                await connection.execute(
                    sa.select(runs)
                    .where(
                        runs.c.campaign_id == campaign_id,
                        runs.c.operational_status.in_(["accepted", "cancelled"]),
                        runs.c.evidence_open.is_(True),
                    )
                    .order_by(runs.c.created_at)
                )
            ).all()
        for run in run_rows:
            if not await self._no_target_interaction(run.run_id):
                raise RuntimeError(
                    "queued cancellation lacks no-target proof"
                )
            async with self.engine.connect() as connection:
                capability_ids = (
                    await connection.execute(
                        sa.select(
                            run_capabilities.c.capability_record_id
                        ).where(
                            run_capabilities.c.run_id == run.run_id,
                            run_capabilities.c.state == "active",
                        )
                    )
                ).scalars().all()
            for capability_id in capability_ids:
                await retire_capability(self.engine, capability_id)
            if run.operational_status == "accepted":
                await transition_run(
                    self.engine,
                    run_id=run.run_id,
                    target_status="cancelled",
                    reason="public_campaign_cancellation_before_invocation",
                )
            else:
                await self._ensure_cancelled_terminal_evidence(run.run_id)
            finalized = await finalize_run_evidence(
                self.engine,
                run_id=run.run_id,
                cutoff_reason="cancellation_grace",
            )
            await self._checkpoint(run.run_id, "finalized")
            if run.run_role == "injected":
                await analyze_evidence_set(
                    self.engine,
                    evidence_set_id=finalized.evidence_set_id,
                )
                await self._checkpoint(run.run_id, "analyzed")
        await self._cancel_pending_campaign(campaign_id)
        await self._settle_cancelled_rerun(campaign_id)

    async def _ensure_cancelled_terminal_evidence(self, run_id: UUID) -> None:
        async with self.engine.begin() as connection:
            run = (
                await connection.execute(
                    sa.select(runs)
                    .where(runs.c.run_id == run_id)
                    .with_for_update()
                )
            ).one()
            existing = await connection.scalar(
                sa.select(evidence_records.c.evidence_id).where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.source == "boundary",
                    evidence_records.c.event_type == "boundary.run.terminal",
                    evidence_records.c.disposition == "accepted",
                )
            )
            if existing is not None:
                return
            cancellation_evidence = await connection.scalar(
                sa.select(evidence_records.c.evidence_id).where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.source == "boundary",
                    evidence_records.c.event_type.in_(
                        [
                            "boundary.campaign_cancellation.requested",
                            "boundary.cancellation.requested",
                        ]
                    ),
                    evidence_records.c.disposition == "accepted",
                )
            )
            if run.operational_status != "cancelled" or cancellation_evidence is None:
                raise RuntimeError(
                    "legacy queued cancellation lacks durable cancellation proof"
                )
            payload = {
                "from_status": "accepted",
                "reason": "public_campaign_cancellation_before_invocation",
                "schema_version": 1,
                "to_status": "cancelled",
            }
            canonical = rfc8785.dumps(payload)
            event_id = uuid4()
            await connection.execute(
                evidence_records.insert().values(
                    evidence_id=event_id,
                    run_id=run_id,
                    source="boundary",
                    event_type="boundary.run.terminal",
                    boundary="run",
                    source_event_id=event_id,
                    producer_seq=None,
                    receipt_seq=run.next_receipt_seq,
                    audit_seq=None,
                    caused_by_event_id=cancellation_evidence,
                    payload_schema_version=1,
                    payload=payload,
                    payload_canonical_bytes=canonical,
                    payload_digest=sha256(canonical).hexdigest(),
                    disposition="accepted",
                )
            )
            await connection.execute(
                runs.update()
                .where(runs.c.run_id == run_id)
                .values(next_receipt_seq=run.next_receipt_seq + 1)
            )

    async def _settle_cancelled_campaign(self, campaign_id: UUID) -> None:
        async with self.engine.connect() as connection:
            run_rows = (
                await connection.execute(
                    sa.select(runs)
                    .where(runs.c.campaign_id == campaign_id)
                    .order_by(runs.c.created_at)
                )
            ).all()
        for run in run_rows:
            if not run.evidence_open or run.operational_status not in {
                "cancelled",
                "timed_out",
            }:
                continue
            cutoff = (
                "target_terminal_watermark"
                if run.target_final_watermark is not None
                and run.target_producer_cursor == run.target_final_watermark
                and run.operational_status == "cancelled"
                else "cancellation_grace"
            )
            try:
                finalized = await finalize_run_evidence(
                    self.engine,
                    run_id=run.run_id,
                    cutoff_reason=cutoff,
                )
                if run.run_role == "injected":
                    await analyze_evidence_set(
                        self.engine,
                        evidence_set_id=finalized.evidence_set_id,
                    )
            except Exception:
                pass
        await self._cancel_pending_campaign(campaign_id)
        await self._settle_cancelled_rerun(campaign_id)

    async def _settle_failed_campaign(self, campaign_id: UUID) -> None:
        async with self.engine.connect() as connection:
            run_rows = (
                await connection.execute(
                    sa.select(runs)
                    .where(runs.c.campaign_id == campaign_id)
                    .order_by(runs.c.created_at)
                )
            ).all()
        for run in run_rows:
            if not run.evidence_open or run.operational_status not in {
                "failed",
                "invalid",
                "timed_out",
            }:
                continue
            async with self.engine.connect() as connection:
                deadline_present = await connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(evidence_records)
                    .where(
                        evidence_records.c.run_id == run.run_id,
                        evidence_records.c.event_type
                        == "boundary.deadline.reached",
                        evidence_records.c.disposition == "accepted",
                    )
                )
            cutoff = (
                "target_terminal_watermark"
                if run.target_final_watermark is not None
                and run.target_producer_cursor == run.target_final_watermark
                and run.operational_status == "failed"
                else "evidence_deadline"
                if deadline_present
                else None
            )
            if cutoff is None:
                continue
            try:
                finalized = await finalize_run_evidence(
                    self.engine, run_id=run.run_id, cutoff_reason=cutoff
                )
                if run.run_role == "injected":
                    await analyze_evidence_set(
                        self.engine,
                        evidence_set_id=finalized.evidence_set_id,
                    )
            except Exception:
                pass

    async def _fail_campaign(self, campaign_id: UUID, reason: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                campaigns.update()
                .where(
                    campaigns.c.campaign_id == campaign_id,
                    campaigns.c.status.in_(["accepted", "running"]),
                )
                .values(
                    status="failed",
                    current_step="failed",
                    failure_reason=reason[:128],
                )
            )

    async def reconcile_startup(self) -> None:
        """Reuse safe accepted identities and terminate ambiguous live work."""
        await reconcile_abandoned_activation_runtimes(self.engine)
        async with self.engine.connect() as connection:
            cancellation_campaigns = (
                await connection.execute(
                    sa.select(
                        campaigns.c.campaign_id,
                        campaigns.c.status,
                    )
                    .where(
                        campaigns.c.status.in_(["accepted", "running", "cancelled"]),
                        campaigns.c.cancel_requested.is_(True),
                        sa.or_(
                            campaigns.c.executor_managed.is_(True),
                            sa.true()
                            if self._include_unmanaged
                            else sa.false(),
                        ),
                    )
                )
            ).all()
        for campaign in cancellation_campaigns:
            if await self._queued_cancellation_is_safe(campaign.campaign_id):
                await self._settle_queued_cancellation(campaign.campaign_id)
            elif campaign.status == "cancelled":
                await self._settle_cancelled_campaign(campaign.campaign_id)
        async with self.engine.connect() as connection:
            active = (
                await connection.execute(
                    sa.select(runs)
                    .join(campaigns, runs.c.campaign_id == campaigns.c.campaign_id)
                    .where(
                        campaigns.c.status.in_(["accepted", "running"]),
                        sa.or_(
                            campaigns.c.executor_managed.is_(True),
                            sa.true() if self._include_unmanaged else sa.false(),
                        ),
                        runs.c.operational_status.in_(["accepted", "running"]),
                    )
                    .order_by(campaigns.c.created_at, runs.c.created_at)
                )
            ).all()
        for run in active:
            safely_resumable = (
                run.operational_status == "accepted"
                and await self._safe_untouched_checkpoint(run.run_id)
            ) or (
                run.operational_status in {"accepted", "running"}
                and await self._safe_polling_checkpoint(run.run_id)
            )
            if safely_resumable:
                async with self.engine.begin() as connection:
                    await connection.execute(
                        campaigns.update()
                        .where(campaigns.c.campaign_id == run.campaign_id)
                        .values(status="accepted", claimed_at=None)
                    )
            else:
                await self._reconcile_ambiguous_run(run.run_id)
                await self._fail_campaign(
                    run.campaign_id, "RUNTIME_LOST_UNPROVEN"
                )
        async with self.engine.begin() as connection:
            await connection.execute(
                campaigns.update()
                .where(
                    campaigns.c.status == "running",
                    sa.or_(
                        campaigns.c.executor_managed.is_(True),
                        sa.true() if self._include_unmanaged else sa.false(),
                    ),
                    ~sa.exists(
                        sa.select(1).where(
                            runs.c.campaign_id == campaigns.c.campaign_id,
                            runs.c.operational_status == "running",
                        )
                    ),
                )
                .values(status="accepted", claimed_at=None)
            )

    async def _safe_polling_checkpoint(self, run_id: UUID) -> bool:
        async with self.engine.connect() as connection:
            run = (
                await connection.execute(
                    sa.select(runs).where(runs.c.run_id == run_id)
                )
            ).one()
            unsettled = await connection.scalar(
                sa.select(sa.func.count())
                .select_from(fault_activations)
                .where(
                    fault_activations.c.run_id == run_id,
                    sa.or_(
                        fault_activations.c.effect_status.is_distinct_from(
                            "effect_realized"
                        ),
                        fault_activations.c.hold_disposition.is_distinct_from(
                            "bounded_hold_complete"
                        ),
                    ),
                )
            )
            finalized = await connection.scalar(
                sa.select(evidence_sets.c.evidence_set_id).where(
                    evidence_sets.c.run_id == run_id
                )
            )
            active_capabilities = await connection.scalar(
                sa.select(sa.func.count())
                .select_from(run_capabilities)
                .where(
                    run_capabilities.c.run_id == run_id,
                    run_capabilities.c.state == "active",
                )
            )
            budgets = await connection.scalar(
                sa.select(sa.func.count())
                .select_from(evidence_records)
                .where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.source == "boundary",
                    evidence_records.c.disposition == "accepted",
                    evidence_records.c.event_type
                    == "boundary.run_budget.bound",
                )
            )
            campaign_kind = await connection.scalar(
                sa.select(campaigns.c.campaign_kind).where(
                    campaigns.c.campaign_id == run.campaign_id
                )
            )
        return bool(
            run.execution_checkpoint == "polling"
            and run.evidence_open
            and not unsettled
            and finalized is None
            and active_capabilities == 1
            and budgets == 1
            and run.reported_tested_agent_id
            == run.expected_tested_agent_id
            and run.reported_tested_agent_version
            == run.expected_tested_agent_version
            and campaign_kind in {
                "phase1.tool-timeout",
                "phase1.tool-timeout.rerun",
            }
        )

    async def _safe_untouched_checkpoint(self, run_id: UUID) -> bool:
        if not await self._no_target_interaction(run_id):
            return False
        async with self.engine.connect() as connection:
            run = (
                await connection.execute(
                    sa.select(runs).where(runs.c.run_id == run_id)
                )
            ).one()
            capability_count = await connection.scalar(
                sa.select(sa.func.count())
                .select_from(run_capabilities)
                .where(run_capabilities.c.run_id == run_id)
            )
            budget_count = await connection.scalar(
                sa.select(sa.func.count())
                .select_from(evidence_records)
                .where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.source == "boundary",
                    evidence_records.c.disposition == "accepted",
                    evidence_records.c.event_type
                    == "boundary.run_budget.bound",
                )
            )
        return bool(
            run.operational_status == "accepted"
            and run.execution_checkpoint == "not_started"
            and run.evidence_open
            and capability_count == 0
            and budget_count == 0
        )

    async def _no_target_interaction(self, run_id: UUID) -> bool:
        async with self.engine.connect() as connection:
            run = (
                await connection.execute(
                    sa.select(runs).where(runs.c.run_id == run_id)
                )
            ).one()
            finalized = await connection.scalar(
                sa.select(evidence_sets.c.evidence_set_id).where(
                    evidence_sets.c.run_id == run_id
                )
            )
            target_events = await connection.scalar(
                sa.select(sa.func.count())
                .select_from(evidence_records)
                .where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.source == "sut",
                    evidence_records.c.disposition == "accepted",
                )
            )
            call_count = await connection.scalar(
                sa.select(sa.func.count())
                .select_from(tool_calls)
                .where(tool_calls.c.run_id == run_id)
            )
            activation_count = await connection.scalar(
                sa.select(sa.func.count())
                .select_from(fault_activations)
                .where(fault_activations.c.run_id == run_id)
            )
        return bool(
            run.execution_checkpoint == "not_started"
            and run.evidence_open
            and run.reported_tested_agent_id is None
            and run.reported_tested_agent_version is None
            and run.target_producer_cursor == 0
            and run.target_final_watermark is None
            and finalized is None
            and target_events == 0
            and call_count == 0
            and activation_count == 0
        )

    async def _reconcile_ambiguous_run(self, run_id: UUID) -> None:
        async with self.engine.begin() as connection:
            run = (
                await connection.execute(
                    sa.select(runs).where(runs.c.run_id == run_id).with_for_update()
                )
            ).one()
            activations = (
                await connection.execute(
                    sa.select(fault_activations).where(
                        fault_activations.c.run_id == run_id,
                        fault_activations.c.effect_status.in_(["not_started", "pending"]),
                    )
                )
            ).all()
            for activation in activations:
                if activation.reservation_state != "pre_effect_reserved":
                    await connection.execute(
                        fault_activations.update()
                        .where(fault_activations.c.activation_id == activation.activation_id)
                        .values(
                            reservation_state="runtime_lost",
                            effect_status="runtime_lost",
                            hold_disposition="runtime_lost",
                            runtime_completed_at=sa.func.now(),
                        )
                    )
            existing_error = await connection.scalar(
                sa.select(evidence_records.c.evidence_id).where(
                    evidence_records.c.run_id == run_id,
                    evidence_records.c.event_type
                    == "boundary.reconciliation.execution_error",
                    evidence_records.c.disposition == "accepted",
                )
            )
            next_receipt_seq = run.next_receipt_seq
            if existing_error is None:
                payload = {
                    "reason_code": "RUNTIME_LOST_UNPROVEN",
                    "run_id": str(run_id),
                    "schema_version": 1,
                }
                canonical = rfc8785.dumps(payload)
                event_id = uuid4()
                await connection.execute(
                    evidence_records.insert().values(
                        evidence_id=event_id,
                        run_id=run_id,
                        source="boundary",
                        event_type="boundary.reconciliation.execution_error",
                        boundary="run",
                        source_event_id=event_id,
                        producer_seq=None,
                        receipt_seq=run.next_receipt_seq,
                        audit_seq=None,
                        caused_by_event_id=None,
                        payload_schema_version=1,
                        payload=payload,
                        payload_canonical_bytes=canonical,
                        payload_digest=sha256(canonical).hexdigest(),
                        disposition="accepted",
                    )
                )
                next_receipt_seq += 1
            await connection.execute(
                runs.update().where(runs.c.run_id == run_id).values(
                    next_receipt_seq=next_receipt_seq,
                    reconciliation_reason="RUNTIME_LOST_UNPROVEN",
                )
            )
            capability_ids = (
                await connection.execute(
                    sa.select(run_capabilities.c.capability_record_id).where(
                        run_capabilities.c.run_id == run_id,
                        run_capabilities.c.state == "active",
                    )
                )
            ).scalars().all()
        if (
            run.operational_status == "running"
            or run.execution_checkpoint in {"target_interaction", "polling"}
        ):
            await self._best_effort_cancel_target(
                run_id=run_id,
                trace_id=run.trace_id,
            )
        for capability_id in capability_ids:
            await retire_capability(self.engine, capability_id)
        await transition_run(
            self.engine,
            run_id=run_id,
            target_status="failed",
            reason="RUNTIME_LOST_UNPROVEN",
        )
        await self._finalize_reconciled(run_id)

    async def _best_effort_cancel_target(
        self,
        *,
        run_id: UUID,
        trace_id: UUID,
    ) -> None:
        client = SutClient(
            self.settings.sut_base_url,
            timeout_seconds=self.settings.tool_client_timeout_ms / 1_000,
        )
        try:
            await client.cancel_run(
                CancellationRequest(
                    contract_version="1",
                    run_id=run_id,
                    trace_id=trace_id,
                    cancellation_id=uuid4(),
                ),
                timeout_seconds=self.settings.tool_client_timeout_ms / 1_000,
            )
        except Exception:
            pass
        finally:
            await client.aclose()

    async def _finalize_reconciled(self, run_id: UUID) -> None:
        async with self.engine.connect() as connection:
            run_role = await connection.scalar(
                sa.select(runs.c.run_role).where(runs.c.run_id == run_id)
            )
            existing = await connection.scalar(
                sa.select(evidence_sets.c.evidence_set_id).where(
                    evidence_sets.c.run_id == run_id
                )
            )
            if existing is not None:
                if run_role == "injected":
                    await analyze_evidence_set(
                        self.engine,
                        evidence_set_id=existing,
                    )
                return
        finalized = await finalize_run_evidence(
            self.engine,
            run_id=run_id,
            cutoff_reason="reconciliation_error",
        )
        await self._checkpoint(run_id, "finalized")
        if run_role == "injected":
            await analyze_evidence_set(
                self.engine,
                evidence_set_id=finalized.evidence_set_id,
            )
            await self._checkpoint(run_id, "analyzed")

    def _raise_process_loss(self, point: str) -> None:
        _raise_process_loss(self._fail_after, point)

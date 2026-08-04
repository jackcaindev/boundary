"""Add Task 8 executor, cancellation, and generic idempotency projections.

Revision ID: 0007_executor_public_api
Revises: 0006_regression_comparison
Create Date: 2026-08-03
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0007_executor_public_api"
down_revision: str | None = "0006_regression_comparison"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_evidence_sets_cutoff_reason_valid"),
        "evidence_sets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_evidence_sets_cutoff_reason_valid"),
        "evidence_sets",
        "cutoff_reason IN ('target_terminal_watermark', "
        "'evidence_deadline', 'cancellation_grace', "
        "'reconciliation_error')",
    )
    op.drop_constraint(
        "uq_idempotency_records_campaign_id",
        "idempotency_records",
        type_="unique",
    )
    op.drop_constraint(
        "uq_idempotency_records_run_id",
        "idempotency_records",
        type_="unique",
    )
    op.alter_column("idempotency_records", "campaign_id", nullable=True)
    op.alter_column("idempotency_records", "run_id", nullable=True)
    op.add_column(
        "idempotency_records",
        sa.Column("resource_kind", sa.String(32), nullable=True),
    )
    op.add_column(
        "idempotency_records",
        sa.Column("resource_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "idempotency_records",
        sa.Column(
            "resource_links",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE idempotency_records SET resource_kind = 'campaign', "
        "resource_id = campaign_id, resource_links = "
        "jsonb_build_object('campaign_id', campaign_id::text, "
        "'run_id', run_id::text)"
    )
    op.alter_column("idempotency_records", "resource_kind", nullable=False)
    op.alter_column("idempotency_records", "resource_id", nullable=False)
    op.alter_column("idempotency_records", "resource_links", nullable=False)
    op.create_check_constraint(
        op.f("ck_idempotency_records_resource_kind_valid"),
        "idempotency_records",
        "resource_kind IN ('campaign', 'regression_case', 'rerun', "
        "'cancellation')",
    )
    op.create_check_constraint(
        op.f("ck_idempotency_records_resource_links_object"),
        "idempotency_records",
        "jsonb_typeof(resource_links) = 'object'",
    )

    op.add_column(
        "campaigns",
        sa.Column("cancellation_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column("failure_reason", sa.String(128), nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column(
            "executor_managed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_unique_constraint(
        op.f("uq_campaigns_cancellation_id"),
        "campaigns",
        ["cancellation_id"],
    )
    op.create_check_constraint(
        op.f("ck_campaigns_cancellation_consistent"),
        "campaigns",
        "(NOT cancel_requested AND cancellation_id IS NULL AND "
        "cancel_requested_at IS NULL) OR "
        "(cancel_requested AND cancellation_id IS NOT NULL AND "
        "cancel_requested_at IS NOT NULL)",
    )
    op.create_index(
        "ix_campaigns_executor_order",
        "campaigns",
        ["status", "created_at", "campaign_id"],
    )

    op.add_column(
        "runs",
        sa.Column(
            "execution_checkpoint",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'not_started'"),
        ),
    )
    op.add_column(
        "runs",
        sa.Column("reconciliation_reason", sa.String(128), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_runs_execution_checkpoint_valid"),
        "runs",
        "execution_checkpoint IN ('not_started', 'target_interaction', "
        "'polling', 'finalized', 'analyzed')",
    )


def downgrade() -> None:
    connection = op.get_bind()
    task8_campaign_count = connection.scalar(
        sa.text(
            "SELECT count(*) FROM campaigns "
            "WHERE executor_managed OR cancel_requested "
            "OR cancellation_id IS NOT NULL OR claimed_at IS NOT NULL "
            "OR failure_reason IS NOT NULL"
        )
    )
    task8_cutoff_count = connection.scalar(
        sa.text(
            "SELECT count(*) FROM evidence_sets "
            "WHERE cutoff_reason IN "
            "('cancellation_grace', 'reconciliation_error')"
        )
    )
    ambiguous_run_count = connection.scalar(
        sa.text(
            "SELECT count(*) FROM runs "
            "WHERE operational_status IN ('accepted', 'running') "
            "AND (execution_checkpoint <> 'not_started' "
            "OR reconciliation_reason IS NOT NULL)"
        )
    )
    if task8_campaign_count or task8_cutoff_count or ambiguous_run_count:
        raise RuntimeError(
            "0007 downgrade is unsupported while Task 8 lifecycle state exists"
        )

    op.execute(
        "DELETE FROM idempotency_records "
        "WHERE resource_kind <> 'campaign'"
    )
    op.drop_constraint(
        op.f("ck_evidence_sets_cutoff_reason_valid"),
        "evidence_sets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_evidence_sets_cutoff_reason_valid"),
        "evidence_sets",
        "cutoff_reason IN ('target_terminal_watermark', 'evidence_deadline')",
    )
    op.drop_constraint(
        op.f("ck_runs_execution_checkpoint_valid"),
        "runs",
        type_="check",
    )
    op.drop_column("runs", "reconciliation_reason")
    op.drop_column("runs", "execution_checkpoint")

    op.drop_index("ix_campaigns_executor_order", table_name="campaigns")
    op.drop_constraint(
        op.f("ck_campaigns_cancellation_consistent"),
        "campaigns",
        type_="check",
    )
    op.drop_constraint(
        op.f("uq_campaigns_cancellation_id"),
        "campaigns",
        type_="unique",
    )
    for column in (
        "claimed_at",
        "executor_managed",
        "failure_reason",
        "cancel_requested_at",
        "cancellation_id",
    ):
        op.drop_column("campaigns", column)

    op.drop_constraint(
        op.f("ck_idempotency_records_resource_links_object"),
        "idempotency_records",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_idempotency_records_resource_kind_valid"),
        "idempotency_records",
        type_="check",
    )
    for column in ("resource_links", "resource_id", "resource_kind"):
        op.drop_column("idempotency_records", column)
    op.alter_column("idempotency_records", "run_id", nullable=False)
    op.alter_column("idempotency_records", "campaign_id", nullable=False)
    op.create_unique_constraint(
        "uq_idempotency_records_run_id", "idempotency_records", ["run_id"]
    )
    op.create_unique_constraint(
        "uq_idempotency_records_campaign_id",
        "idempotency_records",
        ["campaign_id"],
    )

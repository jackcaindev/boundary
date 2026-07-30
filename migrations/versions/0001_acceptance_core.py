"""Create the minimal PostgreSQL acceptance core.

Revision ID: 0001_acceptance_core
Revises:
Create Date: 2026-07-29
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0001_acceptance_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_kind", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'accepted'"),
            nullable=False,
        ),
        sa.Column("current_step", sa.String(length=64), nullable=False),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "campaign_kind <> ''",
            name=op.f("ck_campaigns_campaign_kind_not_empty"),
        ),
        sa.CheckConstraint(
            "current_step <> ''",
            name=op.f("ck_campaigns_current_step_not_empty"),
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'running', 'completed', 'failed', "
            "'cancelled')",
            name=op.f("ck_campaigns_status_valid"),
        ),
        sa.PrimaryKeyConstraint(
            "campaign_id",
            name="pk_campaigns",
        ),
    )

    op.create_table(
        "runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("control_run_id", sa.Uuid(), nullable=True),
        sa.Column("run_role", sa.String(length=32), nullable=False),
        sa.Column(
            "contract_version",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("scenario_id", sa.String(length=128), nullable=False),
        sa.Column("scenario_version", sa.Integer(), nullable=False),
        sa.Column(
            "expected_tested_agent_id",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "expected_tested_agent_version",
            sa.String(length=256),
            nullable=False,
        ),
        sa.Column(
            "reported_tested_agent_id",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "reported_tested_agent_version",
            sa.String(length=256),
            nullable=True,
        ),
        sa.Column(
            "operational_status",
            sa.String(length=32),
            server_default=sa.text("'accepted'"),
            nullable=False,
        ),
        sa.Column(
            "definition_schema_version",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "run_definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "run_definition_bytes",
            sa.LargeBinary(),
            nullable=False,
        ),
        sa.Column(
            "run_definition_digest",
            sa.CHAR(length=64),
            nullable=False,
        ),
        sa.Column(
            "target_producer_cursor",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "target_final_watermark",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "next_receipt_seq",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "next_tool_ordinal",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "evidence_open",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "control_run_id IS NULL OR control_run_id <> run_id",
            name=op.f("ck_runs_control_run_not_self"),
        ),
        sa.CheckConstraint(
            "run_definition_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_runs_definition_digest_lower_hex"),
        ),
        sa.CheckConstraint(
            "definition_schema_version > 0",
            name=op.f("ck_runs_definition_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "target_final_watermark IS NULL OR "
            "target_final_watermark >= 0",
            name=op.f("ck_runs_final_watermark_nonnegative"),
        ),
        sa.CheckConstraint(
            "next_receipt_seq > 0",
            name=op.f("ck_runs_next_receipt_seq_positive"),
        ),
        sa.CheckConstraint(
            "next_tool_ordinal >= 0",
            name=op.f("ck_runs_next_tool_ordinal_nonnegative"),
        ),
        sa.CheckConstraint(
            "operational_status IN ('accepted', 'running', 'completed', "
            "'failed', 'cancelled', 'timed_out', 'invalid')",
            name=op.f("ck_runs_operational_status_valid"),
        ),
        sa.CheckConstraint(
            "target_producer_cursor >= 0",
            name=op.f("ck_runs_producer_cursor_nonnegative"),
        ),
        sa.CheckConstraint(
            "run_role IN ('control', 'injected')",
            name=op.f("ck_runs_run_role_valid"),
        ),
        sa.CheckConstraint(
            "scenario_version > 0",
            name=op.f("ck_runs_scenario_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.campaign_id"],
            name="fk_runs_campaign_id_campaigns",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["control_run_id"],
            ["runs.run_id"],
            name="fk_runs_control_run_id_runs",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_runs"),
        sa.UniqueConstraint("trace_id", name="uq_runs_trace_id"),
    )

    op.create_table(
        "idempotency_records",
        sa.Column(
            "operation_kind",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "request_digest",
            sa.CHAR(length=64),
            nullable=False,
        ),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "idempotency_key <> ''",
            name=op.f(
                "ck_idempotency_records_idempotency_key_not_empty"
            ),
        ),
        sa.CheckConstraint(
            "operation_kind <> ''",
            name=op.f(
                "ck_idempotency_records_operation_kind_not_empty"
            ),
        ),
        sa.CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "ck_idempotency_records_request_digest_lower_hex"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.campaign_id"],
            name=(
                "fk_idempotency_records_campaign_id_campaigns"
            ),
            onupdate="NO ACTION",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.run_id"],
            name="fk_idempotency_records_run_id_runs",
            onupdate="NO ACTION",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint(
            "operation_kind",
            "idempotency_key",
            name="pk_idempotency_records",
        ),
        sa.UniqueConstraint(
            "campaign_id",
            name="uq_idempotency_records_campaign_id",
        ),
        sa.UniqueConstraint(
            "run_id",
            name="uq_idempotency_records_run_id",
        ),
    )

    op.create_table(
        "evidence_records",
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("boundary", sa.String(length=64), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("producer_seq", sa.BigInteger(), nullable=True),
        sa.Column("receipt_seq", sa.BigInteger(), nullable=False),
        sa.Column("caused_by_event_id", sa.Uuid(), nullable=True),
        sa.Column(
            "payload_schema_version",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "payload_canonical_bytes",
            sa.LargeBinary(),
            nullable=False,
        ),
        sa.Column(
            "payload_digest",
            sa.CHAR(length=64),
            nullable=False,
        ),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "disposition IN ('accepted', 'rejected', 'late')",
            name=op.f("ck_evidence_records_disposition_valid"),
        ),
        sa.CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "ck_evidence_records_payload_digest_lower_hex"
            ),
        ),
        sa.CheckConstraint(
            "payload_schema_version > 0",
            name=op.f(
                "ck_evidence_records_payload_schema_version_positive"
            ),
        ),
        sa.CheckConstraint(
            "(source = 'boundary' AND producer_seq IS NULL) OR "
            "(source = 'sut' AND producer_seq > 0)",
            name=op.f(
                "ck_evidence_records_producer_seq_by_source"
            ),
        ),
        sa.CheckConstraint(
            "receipt_seq > 0",
            name=op.f(
                "ck_evidence_records_receipt_seq_positive"
            ),
        ),
        sa.CheckConstraint(
            "source IN ('boundary', 'sut')",
            name=op.f("ck_evidence_records_source_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.run_id"],
            name="fk_evidence_records_run_id_runs",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "evidence_id",
            name="pk_evidence_records",
        ),
        sa.UniqueConstraint(
            "run_id",
            "receipt_seq",
            name="uq_evidence_records_run_receipt_seq",
        ),
        sa.UniqueConstraint(
            "run_id",
            "source",
            "source_event_id",
            name="uq_evidence_records_run_source_event",
        ),
        sa.UniqueConstraint(
            "run_id",
            "producer_seq",
            name="uq_evidence_records_run_producer_seq",
        ),
    )


def downgrade() -> None:
    op.drop_table("evidence_records")
    op.drop_table("idempotency_records")
    op.drop_table("runs")
    op.drop_table("campaigns")

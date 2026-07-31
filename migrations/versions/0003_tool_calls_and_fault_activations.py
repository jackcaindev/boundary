"""Add Task 4 tool registration, response commitment, and audit order.

Revision ID: 0003_tool_calls_activations
Revises: 0002_run_capabilities
Create Date: 2026-07-29
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_tool_calls_activations"
down_revision: str | None = "0002_run_capabilities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "next_audit_seq",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_runs_next_audit_seq_positive"),
        "runs",
        "next_audit_seq > 0",
    )
    op.alter_column(
        "evidence_records",
        "receipt_seq",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.add_column(
        "evidence_records",
        sa.Column("audit_seq", sa.BigInteger(), nullable=True),
    )
    op.drop_constraint(
        op.f("ck_evidence_records_receipt_seq_positive"),
        "evidence_records",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_evidence_records_ordering_by_disposition"),
        "evidence_records",
        "(disposition = 'accepted' AND receipt_seq > 0 "
        "AND audit_seq IS NULL) OR "
        "(disposition IN ('rejected', 'late') "
        "AND receipt_seq IS NULL AND audit_seq > 0)",
    )
    op.create_unique_constraint(
        op.f("uq_evidence_records_run_audit_seq"),
        "evidence_records",
        ["run_id", "audit_seq"],
    )

    op.create_table(
        "tool_calls",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_call_id", sa.Uuid(), nullable=False),
        sa.Column("capability_record_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "tool_identity",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("fault_id", sa.Uuid(), nullable=True),
        sa.Column("retry_ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "request_digest",
            sa.CHAR(length=64),
            nullable=False,
        ),
        sa.Column("arrival_evidence_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal_evidence_id", sa.Uuid(), nullable=False),
        sa.Column(
            "registration_outcome",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "response_disposition",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column(
            "response_digest",
            sa.CHAR(length=64),
            nullable=True,
        ),
        sa.Column("response_evidence_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(fault_id IS NULL AND "
            "registration_outcome = 'no_fault_configured') OR "
            "(fault_id IS NOT NULL AND registration_outcome IN "
            "('pre_effect_reserved', 'attempt_not_selected', "
            "'maximum_activations_reached'))",
            name=op.f(
                "ck_tool_calls_registration_outcome_consistent"
            ),
        ),
        sa.CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_tool_calls_request_digest_lower_hex"),
        ),
        sa.CheckConstraint(
            "(registration_outcome = 'no_fault_configured' AND "
            "response_disposition = 'success_response_committed' AND "
            "response_digest ~ '^[0-9a-f]{64}$' AND "
            "response_evidence_id IS NOT NULL) OR "
            "(registration_outcome <> 'no_fault_configured' AND "
            "response_disposition IS NULL AND "
            "response_digest IS NULL AND "
            "response_evidence_id IS NULL)",
            name=op.f(
                "ck_tool_calls_response_commitment_consistent"
            ),
        ),
        sa.CheckConstraint(
            "retry_ordinal >= 0",
            name=op.f("ck_tool_calls_retry_ordinal_nonnegative"),
        ),
        sa.CheckConstraint(
            "tool_identity <> ''",
            name=op.f("ck_tool_calls_tool_identity_not_empty"),
        ),
        sa.ForeignKeyConstraint(
            ["arrival_evidence_id"],
            ["evidence_records.evidence_id"],
            name="fk_tool_calls_arrival_evidence_id_evidence_records",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["capability_record_id"],
            ["run_capabilities.capability_record_id"],
            name=(
                "fk_tool_calls_capability_record_id_run_capabilities"
            ),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ordinal_evidence_id"],
            ["evidence_records.evidence_id"],
            name="fk_tool_calls_ordinal_evidence_id_evidence_records",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["response_evidence_id"],
            ["evidence_records.evidence_id"],
            name="fk_tool_calls_response_evidence_id_evidence_records",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.run_id"],
            name="fk_tool_calls_run_id_runs",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "run_id",
            "tool_call_id",
            name="pk_tool_calls",
        ),
        sa.UniqueConstraint(
            "arrival_evidence_id",
            name="uq_tool_calls_arrival_evidence_id",
        ),
        sa.UniqueConstraint(
            "ordinal_evidence_id",
            name="uq_tool_calls_ordinal_evidence_id",
        ),
        sa.UniqueConstraint(
            "response_evidence_id",
            name="uq_tool_calls_response_evidence_id",
        ),
        sa.UniqueConstraint(
            "run_id",
            "retry_ordinal",
            name="uq_tool_calls_run_retry_ordinal",
        ),
        sa.UniqueConstraint(
            "run_id",
            "tool_call_id",
            "fault_id",
            name="uq_tool_calls_run_call_fault",
        ),
    )
    op.create_table(
        "fault_activations",
        sa.Column("activation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_call_id", sa.Uuid(), nullable=False),
        sa.Column("fault_id", sa.Uuid(), nullable=False),
        sa.Column(
            "activation_ordinal",
            sa.SmallInteger(),
            nullable=False,
        ),
        sa.Column(
            "reservation_state",
            sa.String(length=32),
            server_default=sa.text("'pre_effect_reserved'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "activation_ordinal >= 0 AND activation_ordinal < 2",
            name=op.f(
                "ck_fault_activations_phase1_activation_ordinal"
            ),
        ),
        sa.CheckConstraint(
            "reservation_state = 'pre_effect_reserved'",
            name=op.f("ck_fault_activations_pre_effect_only"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "tool_call_id", "fault_id"],
            [
                "tool_calls.run_id",
                "tool_calls.tool_call_id",
                "tool_calls.fault_id",
            ],
            name="fk_fault_activations_registered_tool_call",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "activation_id",
            name="pk_fault_activations",
        ),
        sa.UniqueConstraint(
            "run_id",
            "fault_id",
            "activation_ordinal",
            name="uq_fault_activations_run_fault_ordinal",
        ),
        sa.UniqueConstraint(
            "run_id",
            "tool_call_id",
            name="uq_fault_activations_run_tool_call",
        ),
    )


def downgrade() -> None:
    op.drop_table("fault_activations")
    op.drop_table("tool_calls")
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    evidence_columns = {
        column["name"]
        for column in inspector.get_columns("evidence_records")
    }
    if "audit_seq" in evidence_columns:
        check_names = {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "evidence_records"
            )
        }
        if "ck_evidence_records_ordering_by_disposition" in check_names:
            op.drop_constraint(
                op.f("ck_evidence_records_ordering_by_disposition"),
                "evidence_records",
                type_="check",
            )
        op.execute(
            sa.text(
                """
                WITH accepted_max AS (
                    SELECT run_id, COALESCE(MAX(receipt_seq), 0) AS base_seq
                    FROM evidence_records
                    GROUP BY run_id
                ), ranked_audit AS (
                    SELECT
                        evidence_id,
                        accepted_max.base_seq
                            + ROW_NUMBER() OVER (
                                PARTITION BY evidence_records.run_id
                                ORDER BY audit_seq, evidence_id
                            ) AS restored_receipt_seq
                    FROM evidence_records
                    JOIN accepted_max USING (run_id)
                    WHERE receipt_seq IS NULL
                )
                UPDATE evidence_records
                SET receipt_seq = ranked_audit.restored_receipt_seq
                FROM ranked_audit
                WHERE evidence_records.evidence_id
                    = ranked_audit.evidence_id
                """
            )
        )
        op.execute(
            sa.text(
                """
                UPDATE runs
                SET next_receipt_seq = GREATEST(
                    next_receipt_seq,
                    COALESCE((
                        SELECT MAX(receipt_seq) + 1
                        FROM evidence_records
                        WHERE evidence_records.run_id = runs.run_id
                    ), 1)
                )
                """
            )
        )
        unique_names = {
            constraint["name"]
            for constraint in sa.inspect(bind).get_unique_constraints(
                "evidence_records"
            )
        }
        if "uq_evidence_records_run_audit_seq" in unique_names:
            op.drop_constraint(
                op.f("uq_evidence_records_run_audit_seq"),
                "evidence_records",
                type_="unique",
            )
        op.drop_column("evidence_records", "audit_seq")
        op.alter_column(
            "evidence_records",
            "receipt_seq",
            existing_type=sa.BigInteger(),
            nullable=False,
        )
        check_names = {
            constraint["name"]
            for constraint in sa.inspect(bind).get_check_constraints(
                "evidence_records"
            )
        }
        if "ck_evidence_records_receipt_seq_positive" not in check_names:
            op.create_check_constraint(
                op.f("ck_evidence_records_receipt_seq_positive"),
                "evidence_records",
                "receipt_seq > 0",
            )

    inspector = sa.inspect(bind)
    run_columns = {
        column["name"] for column in inspector.get_columns("runs")
    }
    if "next_audit_seq" in run_columns:
        check_names = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("runs")
        }
        if "ck_runs_next_audit_seq_positive" in check_names:
            op.drop_constraint(
                op.f("ck_runs_next_audit_seq_positive"),
                "runs",
                type_="check",
            )
        op.drop_column("runs", "next_audit_seq")

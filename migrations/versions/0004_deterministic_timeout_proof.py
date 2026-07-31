"""Add Task 5 injected identity and deterministic timeout proof fields.

Revision ID: 0004_timeout_proof
Revises: 0003_tool_calls_activations
Create Date: 2026-07-31
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004_timeout_proof"
down_revision: str | None = "0003_tool_calls_activations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("fault_spec_id", sa.Uuid(), nullable=True))
    op.add_column("runs", sa.Column("fault_id", sa.Uuid(), nullable=True))
    op.add_column(
        "runs",
        sa.Column(
            "tested_input",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column("runs", sa.Column("tested_input_bytes", sa.LargeBinary(), nullable=True))
    op.add_column("runs", sa.Column("tested_input_digest", sa.CHAR(64), nullable=True))
    op.create_unique_constraint(op.f("uq_runs_fault_id"), "runs", ["fault_id"])
    op.create_check_constraint(
        op.f("ck_runs_role_fault_identity_consistent"),
        "runs",
        "(run_role = 'control' AND control_run_id IS NULL AND "
        "fault_spec_id IS NULL AND fault_id IS NULL) OR "
        "(run_role = 'injected' AND control_run_id IS NOT NULL AND "
        "fault_spec_id IS NOT NULL AND fault_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_runs_tested_input_canonical_consistent"),
        "runs",
        "(tested_input IS NULL AND tested_input_bytes IS NULL AND "
        "tested_input_digest IS NULL) OR "
        "(tested_input IS NOT NULL AND tested_input_bytes IS NOT NULL "
        "AND tested_input_digest ~ '^[0-9a-f]{64}$')",
    )

    op.drop_constraint(
        op.f("ck_tool_calls_response_commitment_consistent"),
        "tool_calls",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_tool_calls_response_commitment_consistent"),
        "tool_calls",
        "(registration_outcome IN "
        "('no_fault_configured', 'attempt_not_selected') AND "
        "response_disposition = 'success_response_committed' AND "
        "response_digest ~ '^[0-9a-f]{64}$' AND "
        "response_evidence_id IS NOT NULL) OR "
        "(registration_outcome NOT IN "
        "('no_fault_configured', 'attempt_not_selected') AND "
        "response_disposition IS NULL AND response_digest IS NULL AND "
        "response_evidence_id IS NULL)",
    )

    op.drop_constraint(
        op.f("ck_fault_activations_pre_effect_only"),
        "fault_activations",
        type_="check",
    )
    op.add_column("fault_activations", sa.Column("activation_evidence_id", sa.Uuid(), nullable=True))
    op.add_column("fault_activations", sa.Column("accepted_request_origin_ns", sa.BigInteger(), nullable=True))
    op.add_column("fault_activations", sa.Column("activation_started_ns", sa.BigInteger(), nullable=True))
    op.add_column("fault_activations", sa.Column("client_timeout_boundary_ns", sa.BigInteger(), nullable=True))
    op.add_column("fault_activations", sa.Column("hold_deadline_ns", sa.BigInteger(), nullable=True))
    op.add_column(
        "fault_activations",
        sa.Column(
            "effect_status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'not_started'"),
        ),
    )
    op.add_column(
        "fault_activations",
        sa.Column(
            "effect_proof",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column("fault_activations", sa.Column("effect_proof_bytes", sa.LargeBinary(), nullable=True))
    op.add_column("fault_activations", sa.Column("effect_proof_digest", sa.CHAR(64), nullable=True))
    op.add_column("fault_activations", sa.Column("effect_evidence_id", sa.Uuid(), nullable=True))
    op.add_column("fault_activations", sa.Column("hold_disposition", sa.String(32), nullable=True))
    op.add_column("fault_activations", sa.Column("runtime_completed_monotonic_ns", sa.BigInteger(), nullable=True))
    op.add_column("fault_activations", sa.Column("runtime_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_fault_activations_activation_evidence_id_evidence_records",
        "fault_activations",
        "evidence_records",
        ["activation_evidence_id"],
        ["evidence_id"],
        onupdate="RESTRICT",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_fault_activations_effect_evidence_id_evidence_records",
        "fault_activations",
        "evidence_records",
        ["effect_evidence_id"],
        ["evidence_id"],
        onupdate="RESTRICT",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_unique_constraint(
        op.f("uq_fault_activations_activation_evidence_id"),
        "fault_activations",
        ["activation_evidence_id"],
    )
    op.create_unique_constraint(
        op.f("uq_fault_activations_effect_evidence_id"),
        "fault_activations",
        ["effect_evidence_id"],
    )
    op.create_check_constraint(
        op.f("ck_fault_activations_activation_state_valid"),
        "fault_activations",
        "reservation_state IN ('pre_effect_reserved', "
        "'activation_started', 'effect_realized', 'unproven', 'runtime_lost')",
    )
    op.create_check_constraint(
        op.f("ck_fault_activations_effect_status_valid"),
        "fault_activations",
        "effect_status IN ('not_started', 'pending', "
        "'effect_realized', 'unproven', 'runtime_lost')",
    )
    op.create_check_constraint(
        op.f("ck_fault_activations_activation_timing_consistent"),
        "fault_activations",
        "(reservation_state = 'pre_effect_reserved' AND "
        "activation_evidence_id IS NULL AND accepted_request_origin_ns IS NULL "
        "AND activation_started_ns IS NULL AND client_timeout_boundary_ns IS NULL "
        "AND hold_deadline_ns IS NULL AND effect_status = 'not_started') OR "
        "(reservation_state <> 'pre_effect_reserved' AND "
        "activation_evidence_id IS NOT NULL AND accepted_request_origin_ns IS NOT NULL "
        "AND activation_started_ns IS NOT NULL AND client_timeout_boundary_ns IS NOT NULL "
        "AND hold_deadline_ns IS NOT NULL "
        "AND activation_started_ns < client_timeout_boundary_ns "
        "AND client_timeout_boundary_ns < hold_deadline_ns "
        "AND effect_status <> 'not_started')",
    )
    op.create_check_constraint(
        op.f("ck_fault_activations_effect_proof_consistent"),
        "fault_activations",
        "(effect_status = 'effect_realized' AND effect_proof IS NOT NULL "
        "AND effect_proof_bytes IS NOT NULL "
        "AND effect_proof_digest ~ '^[0-9a-f]{64}$' "
        "AND effect_evidence_id IS NOT NULL) OR "
        "(effect_status <> 'effect_realized' AND effect_proof IS NULL "
        "AND effect_proof_bytes IS NULL AND effect_proof_digest IS NULL "
        "AND effect_evidence_id IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_fault_activations_hold_completion_consistent"),
        "fault_activations",
        "(hold_disposition IS NULL AND runtime_completed_at IS NULL "
        "AND runtime_completed_monotonic_ns IS NULL) OR "
        "(hold_disposition IN ('bounded_hold_complete', 'proof_failed') "
        "AND runtime_completed_at IS NOT NULL "
        "AND runtime_completed_monotonic_ns >= hold_deadline_ns) OR "
        "(hold_disposition = 'runtime_lost' AND runtime_completed_at IS NOT NULL "
        "AND runtime_completed_monotonic_ns IS NULL)",
    )


def downgrade() -> None:
    for name in (
        "hold_completion_consistent",
        "effect_proof_consistent",
        "activation_timing_consistent",
        "effect_status_valid",
        "activation_state_valid",
    ):
        op.drop_constraint(op.f(f"ck_fault_activations_{name}"), "fault_activations", type_="check")
    op.drop_constraint(op.f("uq_fault_activations_effect_evidence_id"), "fault_activations", type_="unique")
    op.drop_constraint(op.f("uq_fault_activations_activation_evidence_id"), "fault_activations", type_="unique")
    op.drop_constraint("fk_fault_activations_effect_evidence_id_evidence_records", "fault_activations", type_="foreignkey")
    op.drop_constraint("fk_fault_activations_activation_evidence_id_evidence_records", "fault_activations", type_="foreignkey")
    for column in (
        "runtime_completed_at",
        "runtime_completed_monotonic_ns",
        "hold_disposition",
        "effect_evidence_id",
        "effect_proof_digest",
        "effect_proof_bytes",
        "effect_proof",
        "effect_status",
        "hold_deadline_ns",
        "client_timeout_boundary_ns",
        "activation_started_ns",
        "accepted_request_origin_ns",
        "activation_evidence_id",
    ):
        op.drop_column("fault_activations", column)
    op.create_check_constraint(
        op.f("ck_fault_activations_pre_effect_only"),
        "fault_activations",
        "reservation_state = 'pre_effect_reserved'",
    )

    op.drop_constraint(op.f("ck_tool_calls_response_commitment_consistent"), "tool_calls", type_="check")
    op.create_check_constraint(
        op.f("ck_tool_calls_response_commitment_consistent"),
        "tool_calls",
        "(registration_outcome = 'no_fault_configured' AND "
        "response_disposition = 'success_response_committed' AND "
        "response_digest ~ '^[0-9a-f]{64}$' AND response_evidence_id IS NOT NULL) OR "
        "(registration_outcome <> 'no_fault_configured' AND "
        "response_disposition IS NULL AND response_digest IS NULL AND "
        "response_evidence_id IS NULL)",
    )

    op.drop_constraint(op.f("ck_runs_tested_input_canonical_consistent"), "runs", type_="check")
    op.drop_constraint(op.f("ck_runs_role_fault_identity_consistent"), "runs", type_="check")
    op.drop_constraint(op.f("uq_runs_fault_id"), "runs", type_="unique")
    for column in (
        "tested_input_digest",
        "tested_input_bytes",
        "tested_input",
        "fault_id",
        "fault_spec_id",
    ):
        op.drop_column("runs", column)

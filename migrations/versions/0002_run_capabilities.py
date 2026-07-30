"""Add the secret-free run capability binding.

Revision ID: 0002_run_capabilities
Revises: 0001_acceptance_core
Create Date: 2026-07-29
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_run_capabilities"
down_revision: str | None = "0001_acceptance_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_capabilities",
        sa.Column("capability_record_id", sa.Uuid(), nullable=False),
        sa.Column(
            "capability_hash",
            sa.CHAR(length=64),
            nullable=False,
        ),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "tool_identity",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "no_fault_binding",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column("fault_id", sa.Uuid(), nullable=True),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "retired_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(no_fault_binding AND fault_id IS NULL) OR "
            "(NOT no_fault_binding AND fault_id IS NOT NULL)",
            name=op.f(
                "ck_run_capabilities_fault_binding_consistent"
            ),
        ),
        sa.CheckConstraint(
            "capability_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_run_capabilities_hash_lower_hex"),
        ),
        sa.CheckConstraint(
            "(state = 'active' AND retired_at IS NULL) OR "
            "(state = 'retired' AND retired_at IS NOT NULL)",
            name=op.f(
                "ck_run_capabilities_state_retirement_consistent"
            ),
        ),
        sa.CheckConstraint(
            "tool_identity <> ''",
            name=op.f(
                "ck_run_capabilities_tool_identity_not_empty"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.run_id"],
            name="fk_run_capabilities_run_id_runs",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "capability_record_id",
            name="pk_run_capabilities",
        ),
        sa.UniqueConstraint(
            "capability_hash",
            name="uq_run_capabilities_capability_hash",
        ),
        sa.UniqueConstraint(
            "run_id",
            name="uq_run_capabilities_run_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("run_capabilities")

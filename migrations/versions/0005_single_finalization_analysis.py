"""Add Task 6 immutable evidence sets and deterministic analyses.

Revision ID: 0005_finalization_analysis
Revises: 0004_timeout_proof
Create Date: 2026-08-02
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0005_finalization_analysis"
down_revision: str | None = "0004_timeout_proof"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_evidence_records_run_budget",
        "evidence_records",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text(
            "source = 'boundary' AND disposition = 'accepted' "
            "AND event_type = 'boundary.run_budget.bound'"
        ),
    )
    op.create_table(
        "evidence_sets",
        sa.Column("evidence_set_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("manifest_schema_version", sa.Integer(), nullable=False),
        sa.Column("cutoff_reason", sa.String(32), nullable=False),
        sa.Column("target_final_watermark", sa.BigInteger(), nullable=True),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("manifest_canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("evidence_set_digest", sa.CHAR(64), nullable=False),
        sa.Column("finalizer_identity", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "manifest_schema_version > 0",
            name=op.f("ck_evidence_sets_manifest_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "cutoff_reason IN "
            "('target_terminal_watermark', 'evidence_deadline')",
            name=op.f("ck_evidence_sets_cutoff_reason_valid"),
        ),
        sa.CheckConstraint(
            "target_final_watermark IS NULL OR target_final_watermark >= 0",
            name=op.f("ck_evidence_sets_target_final_watermark_nonnegative"),
        ),
        sa.CheckConstraint(
            "evidence_set_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_evidence_sets_digest_lower_hex"),
        ),
        sa.CheckConstraint(
            "finalizer_identity <> ''",
            name=op.f("ck_evidence_sets_finalizer_identity_not_empty"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.run_id"],
            name="fk_evidence_sets_run_id_runs",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("evidence_set_id", name="pk_evidence_sets"),
        sa.UniqueConstraint("run_id", name="uq_evidence_sets_run_id"),
    )
    op.create_table(
        "analyses",
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("record_kind", sa.String(32), nullable=False),
        sa.Column("evidence_set_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_set_digest", sa.CHAR(64), nullable=False),
        sa.Column("analyzer_version", sa.String(128), nullable=False),
        sa.Column("assertion_set_version", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.String(128), nullable=False),
        sa.Column("evaluability_aggregate", sa.String(32), nullable=False),
        sa.Column("policy_result", sa.String(32), nullable=False),
        sa.Column("analysis_schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "analysis_document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("analysis_canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("analysis_digest", sa.CHAR(64), nullable=False),
        sa.Column("prior_analysis_id", sa.Uuid(), nullable=True),
        sa.Column("attempted_analysis_digest", sa.CHAR(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "record_kind IN ('authoritative', 'integrity_failure')",
            name=op.f("ck_analyses_record_kind_valid"),
        ),
        sa.CheckConstraint(
            "evidence_set_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_analyses_evidence_set_digest_lower_hex"),
        ),
        sa.CheckConstraint(
            "analysis_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_analyses_analysis_digest_lower_hex"),
        ),
        sa.CheckConstraint(
            "attempted_analysis_digest IS NULL OR "
            "attempted_analysis_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_analyses_attempted_digest_lower_hex"),
        ),
        sa.CheckConstraint(
            "analysis_schema_version > 0",
            name=op.f("ck_analyses_analysis_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "evaluability_aggregate IN "
            "('EVALUABLE', 'INCOMPLETE', 'INVALID', 'EXECUTION_ERROR')",
            name=op.f("ck_analyses_evaluability_aggregate_valid"),
        ),
        sa.CheckConstraint(
            "policy_result IN "
            "('PASS', 'FAIL', 'INCOMPLETE', 'INVALID', 'EXECUTION_ERROR')",
            name=op.f("ck_analyses_policy_result_valid"),
        ),
        sa.CheckConstraint(
            "(record_kind = 'authoritative' AND prior_analysis_id IS NULL "
            "AND attempted_analysis_digest IS NULL) OR "
            "(record_kind = 'integrity_failure' "
            "AND prior_analysis_id IS NOT NULL "
            "AND attempted_analysis_digest IS NOT NULL "
            "AND evaluability_aggregate = 'EXECUTION_ERROR' "
            "AND policy_result = 'EXECUTION_ERROR')",
            name=op.f("ck_analyses_record_kind_fields_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["evidence_set_id"],
            ["evidence_sets.evidence_set_id"],
            name="fk_analyses_evidence_set_id_evidence_sets",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prior_analysis_id"],
            ["analyses.analysis_id"],
            name="fk_analyses_prior_analysis_id_analyses",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("analysis_id", name="pk_analyses"),
    )
    op.create_index(
        "uq_analyses_authoritative_key",
        "analyses",
        [
            "evidence_set_digest",
            "analyzer_version",
            "assertion_set_version",
            "policy_version",
        ],
        unique=True,
        postgresql_where=sa.text("record_kind = 'authoritative'"),
    )
    op.create_index(
        "uq_analyses_integrity_attempt",
        "analyses",
        ["prior_analysis_id", "attempted_analysis_digest"],
        unique=True,
        postgresql_where=sa.text("record_kind = 'integrity_failure'"),
    )


def downgrade() -> None:
    op.drop_index("uq_analyses_integrity_attempt", table_name="analyses")
    op.drop_index("uq_analyses_authoritative_key", table_name="analyses")
    op.drop_table("analyses")
    op.drop_table("evidence_sets")
    op.drop_index(
        "uq_evidence_records_run_budget",
        table_name="evidence_records",
    )

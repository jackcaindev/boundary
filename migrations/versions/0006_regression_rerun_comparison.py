"""Add Task 7 regression cases, reruns, and comparisons.

Revision ID: 0006_regression_comparison
Revises: 0005_finalization_analysis
Create Date: 2026-08-03
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0006_regression_comparison"
down_revision: str | None = "0005_finalization_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "regression_cases",
        sa.Column("regression_case_id", sa.Uuid(), nullable=False),
        sa.Column("source_analysis_id", sa.Uuid(), nullable=False),
        sa.Column("source_evidence_set_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_schema_version", sa.Integer(), nullable=False),
        sa.Column("artifact", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("artifact_canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("integrity_digest", sa.CHAR(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("artifact_schema_version > 0", name=op.f("ck_regression_cases_artifact_schema_version_positive")),
        sa.CheckConstraint("integrity_digest ~ '^[0-9a-f]{64}$'", name=op.f("ck_regression_cases_integrity_digest_lower_hex")),
        sa.ForeignKeyConstraint(["source_analysis_id"], ["analyses.analysis_id"], name="fk_regression_cases_source_analysis_id_analyses", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_evidence_set_id"], ["evidence_sets.evidence_set_id"], name="fk_regression_cases_source_evidence_set_id_evidence_sets", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_run_id"], ["runs.run_id"], name="fk_regression_cases_source_run_id_runs", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("regression_case_id", name="pk_regression_cases"),
        sa.UniqueConstraint("source_analysis_id", name="uq_regression_cases_source_analysis_id"),
    )
    op.create_table(
        "reruns",
        sa.Column("rerun_id", sa.Uuid(), nullable=False),
        sa.Column("regression_case_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("requested_tested_agent_version", sa.String(256), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("control_run_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_run_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=True),
        sa.Column("pre_report_schema_version", sa.Integer(), nullable=False),
        sa.Column("pre_invariance_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pre_invariance_canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("pre_invariance_digest", sa.CHAR(64), nullable=False),
        sa.Column("completed_report_schema_version", sa.Integer(), nullable=True),
        sa.Column("completed_invariance_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("completed_invariance_canonical_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("completed_invariance_digest", sa.CHAR(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("mode IN ('reproduction', 'version_comparison')", name=op.f("ck_reruns_mode_valid")),
        sa.CheckConstraint("status IN ('rejected', 'accepted', 'running', 'completed', 'failed')", name=op.f("ck_reruns_status_valid")),
        sa.CheckConstraint("pre_report_schema_version > 0", name=op.f("ck_reruns_pre_report_schema_version_positive")),
        sa.CheckConstraint("pre_invariance_digest ~ '^[0-9a-f]{64}$'", name=op.f("ck_reruns_pre_invariance_digest_lower_hex")),
        sa.CheckConstraint("(status = 'rejected' AND campaign_id IS NULL AND control_run_id IS NULL AND candidate_run_id IS NULL AND reason_code IS NOT NULL) OR (status <> 'rejected' AND campaign_id IS NOT NULL AND control_run_id IS NOT NULL)", name=op.f("ck_reruns_execution_identity_lifecycle_consistent")),
        sa.CheckConstraint("(completed_report_schema_version IS NULL AND completed_invariance_report IS NULL AND completed_invariance_canonical_bytes IS NULL AND completed_invariance_digest IS NULL) OR (completed_report_schema_version > 0 AND completed_invariance_report IS NOT NULL AND completed_invariance_canonical_bytes IS NOT NULL AND completed_invariance_digest ~ '^[0-9a-f]{64}$')", name=op.f("ck_reruns_completed_invariance_consistent")),
        sa.ForeignKeyConstraint(["regression_case_id"], ["regression_cases.regression_case_id"], name="fk_reruns_regression_case_id_regression_cases", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.campaign_id"], name="fk_reruns_campaign_id_campaigns", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["control_run_id"], ["runs.run_id"], name="fk_reruns_control_run_id_runs", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["candidate_run_id"], ["runs.run_id"], name="fk_reruns_candidate_run_id_runs", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("rerun_id", name="pk_reruns"),
        sa.UniqueConstraint("campaign_id", name="uq_reruns_campaign_id"),
        sa.UniqueConstraint("control_run_id", name="uq_reruns_control_run_id"),
        sa.UniqueConstraint("candidate_run_id", name="uq_reruns_candidate_run_id"),
    )
    op.create_table(
        "comparisons",
        sa.Column("comparison_id", sa.Uuid(), nullable=False),
        sa.Column("regression_case_id", sa.Uuid(), nullable=False),
        sa.Column("rerun_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_evidence_set_id", sa.Uuid(), nullable=False),
        sa.Column("source_analysis_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_run_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_evidence_set_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_analysis_id", sa.Uuid(), nullable=True),
        sa.Column("source_tested_agent_version", sa.String(256), nullable=False),
        sa.Column("candidate_tested_agent_version", sa.String(256), nullable=False),
        sa.Column("source_policy_result", sa.String(32), nullable=False),
        sa.Column("candidate_policy_result", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("terminal_result", sa.String(32), nullable=True),
        sa.Column("reason_code", sa.String(128), nullable=True),
        sa.Column("summary_schema_version", sa.Integer(), nullable=True),
        sa.Column("summary_document", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("summary_canonical_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("summary_digest", sa.CHAR(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'valid', 'ineligible', 'execution_error')", name=op.f("ck_comparisons_status_valid")),
        sa.CheckConstraint("source_policy_result = 'FAIL'", name=op.f("ck_comparisons_source_result_fail")),
        sa.CheckConstraint("candidate_policy_result IS NULL OR candidate_policy_result IN ('PASS', 'FAIL', 'INCOMPLETE', 'INVALID', 'EXECUTION_ERROR')", name=op.f("ck_comparisons_candidate_result_valid")),
        sa.CheckConstraint("(status = 'pending' AND terminal_result IS NULL AND reason_code IS NULL AND summary_schema_version IS NULL AND summary_document IS NULL AND summary_canonical_bytes IS NULL AND summary_digest IS NULL) OR (status <> 'pending' AND terminal_result IS NOT NULL AND reason_code IS NOT NULL AND summary_schema_version > 0 AND summary_document IS NOT NULL AND summary_canonical_bytes IS NOT NULL AND summary_digest ~ '^[0-9a-f]{64}$')", name=op.f("ck_comparisons_terminal_content_consistent")),
        sa.ForeignKeyConstraint(["regression_case_id"], ["regression_cases.regression_case_id"], name="fk_comparisons_regression_case_id_regression_cases", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rerun_id"], ["reruns.rerun_id"], name="fk_comparisons_rerun_id_reruns", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_run_id"], ["runs.run_id"], name="fk_comparisons_source_run_id_runs", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_evidence_set_id"], ["evidence_sets.evidence_set_id"], name="fk_comparisons_source_evidence_set_id_evidence_sets", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_analysis_id"], ["analyses.analysis_id"], name="fk_comparisons_source_analysis_id_analyses", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["candidate_run_id"], ["runs.run_id"], name="fk_comparisons_candidate_run_id_runs", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["candidate_evidence_set_id"], ["evidence_sets.evidence_set_id"], name="fk_comparisons_candidate_evidence_set_id_evidence_sets", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["candidate_analysis_id"], ["analyses.analysis_id"], name="fk_comparisons_candidate_analysis_id_analyses", onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("comparison_id", name="pk_comparisons"),
        sa.UniqueConstraint("rerun_id", name="uq_comparisons_rerun_id"),
    )


def downgrade() -> None:
    op.drop_table("comparisons")
    op.drop_table("reruns")
    op.drop_table("regression_cases")

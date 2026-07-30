"""SQLAlchemy Core metadata through Task 3's fifth physical table."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)

campaigns = sa.Table(
    "campaigns",
    metadata,
    sa.Column("campaign_id", sa.Uuid(), primary_key=True),
    sa.Column("campaign_kind", sa.String(128), nullable=False),
    sa.Column(
        "status",
        sa.String(32),
        nullable=False,
        server_default=sa.text("'accepted'"),
    ),
    sa.Column("current_step", sa.String(64), nullable=False),
    sa.Column(
        "cancel_requested",
        sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    ),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
        "campaign_kind <> ''",
        name="campaign_kind_not_empty",
    ),
    sa.CheckConstraint(
        "status IN ('accepted', 'running', 'completed', 'failed', "
        "'cancelled')",
        name="status_valid",
    ),
    sa.CheckConstraint(
        "current_step <> ''",
        name="current_step_not_empty",
    ),
)

runs = sa.Table(
    "runs",
    metadata,
    sa.Column("run_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "trace_id",
        sa.Uuid(),
        nullable=False,
        unique=True,
    ),
    sa.Column(
        "campaign_id",
        sa.Uuid(),
        sa.ForeignKey(
            "campaigns.campaign_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    ),
    sa.Column(
        "control_run_id",
        sa.Uuid(),
        sa.ForeignKey(
            "runs.run_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    ),
    sa.Column("run_role", sa.String(32), nullable=False),
    sa.Column("contract_version", sa.String(32), nullable=False),
    sa.Column("scenario_id", sa.String(128), nullable=False),
    sa.Column("scenario_version", sa.Integer(), nullable=False),
    sa.Column("expected_tested_agent_id", sa.String(128), nullable=False),
    sa.Column(
        "expected_tested_agent_version",
        sa.String(256),
        nullable=False,
    ),
    sa.Column("reported_tested_agent_id", sa.String(128), nullable=True),
    sa.Column(
        "reported_tested_agent_version",
        sa.String(256),
        nullable=True,
    ),
    sa.Column(
        "operational_status",
        sa.String(32),
        nullable=False,
        server_default=sa.text("'accepted'"),
    ),
    sa.Column("definition_schema_version", sa.Integer(), nullable=False),
    sa.Column(
        "run_definition",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    ),
    sa.Column("run_definition_bytes", sa.LargeBinary(), nullable=False),
    sa.Column("run_definition_digest", sa.CHAR(64), nullable=False),
    sa.Column(
        "target_producer_cursor",
        sa.BigInteger(),
        nullable=False,
        server_default=sa.text("0"),
    ),
    sa.Column("target_final_watermark", sa.BigInteger(), nullable=True),
    sa.Column(
        "next_receipt_seq",
        sa.BigInteger(),
        nullable=False,
        server_default=sa.text("1"),
    ),
    sa.Column(
        "next_tool_ordinal",
        sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    ),
    sa.Column(
        "evidence_open",
        sa.Boolean(),
        nullable=False,
        server_default=sa.true(),
    ),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
        "run_role IN ('control', 'injected')",
        name="run_role_valid",
    ),
    sa.CheckConstraint(
        "operational_status IN ('accepted', 'running', 'completed', "
        "'failed', 'cancelled', 'timed_out', 'invalid')",
        name="operational_status_valid",
    ),
    sa.CheckConstraint(
        "scenario_version > 0",
        name="scenario_version_positive",
    ),
    sa.CheckConstraint(
        "definition_schema_version > 0",
        name="definition_schema_version_positive",
    ),
    sa.CheckConstraint(
        "run_definition_digest ~ '^[0-9a-f]{64}$'",
        name="definition_digest_lower_hex",
    ),
    sa.CheckConstraint(
        "target_producer_cursor >= 0",
        name="producer_cursor_nonnegative",
    ),
    sa.CheckConstraint(
        "target_final_watermark IS NULL OR target_final_watermark >= 0",
        name="final_watermark_nonnegative",
    ),
    sa.CheckConstraint(
        "next_receipt_seq > 0",
        name="next_receipt_seq_positive",
    ),
    sa.CheckConstraint(
        "next_tool_ordinal >= 0",
        name="next_tool_ordinal_nonnegative",
    ),
    sa.CheckConstraint(
        "control_run_id IS NULL OR control_run_id <> run_id",
        name="control_run_not_self",
    ),
)

run_capabilities = sa.Table(
    "run_capabilities",
    metadata,
    sa.Column("capability_record_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "capability_hash",
        sa.CHAR(64),
        nullable=False,
        unique=True,
    ),
    sa.Column(
        "run_id",
        sa.Uuid(),
        sa.ForeignKey(
            "runs.run_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
        unique=True,
    ),
    sa.Column("trace_id", sa.Uuid(), nullable=False),
    sa.Column("tool_identity", sa.String(128), nullable=False),
    sa.Column(
        "no_fault_binding",
        sa.Boolean(),
        nullable=False,
    ),
    sa.Column("fault_id", sa.Uuid(), nullable=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "state",
        sa.String(16),
        nullable=False,
        server_default=sa.text("'active'"),
    ),
    sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
        "capability_hash ~ '^[0-9a-f]{64}$'",
        name="hash_lower_hex",
    ),
    sa.CheckConstraint(
        "tool_identity <> ''",
        name="tool_identity_not_empty",
    ),
    sa.CheckConstraint(
        "(no_fault_binding AND fault_id IS NULL) OR "
        "(NOT no_fault_binding AND fault_id IS NOT NULL)",
        name="fault_binding_consistent",
    ),
    sa.CheckConstraint(
        "(state = 'active' AND retired_at IS NULL) OR "
        "(state = 'retired' AND retired_at IS NOT NULL)",
        name="state_retirement_consistent",
    ),
)

idempotency_records = sa.Table(
    "idempotency_records",
    metadata,
    sa.Column("operation_kind", sa.String(64), primary_key=True),
    sa.Column("idempotency_key", sa.String(255), primary_key=True),
    sa.Column("request_digest", sa.CHAR(64), nullable=False),
    sa.Column(
        "campaign_id",
        sa.Uuid(),
        sa.ForeignKey(
            "campaigns.campaign_id",
            ondelete="NO ACTION",
            onupdate="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
        unique=True,
    ),
    sa.Column(
        "run_id",
        sa.Uuid(),
        sa.ForeignKey(
            "runs.run_id",
            ondelete="NO ACTION",
            onupdate="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
        unique=True,
    ),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
        "operation_kind <> ''",
        name="operation_kind_not_empty",
    ),
    sa.CheckConstraint(
        "idempotency_key <> ''",
        name="idempotency_key_not_empty",
    ),
    sa.CheckConstraint(
        "request_digest ~ '^[0-9a-f]{64}$'",
        name="request_digest_lower_hex",
    ),
)

evidence_records = sa.Table(
    "evidence_records",
    metadata,
    sa.Column("evidence_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "run_id",
        sa.Uuid(),
        sa.ForeignKey(
            "runs.run_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    ),
    sa.Column("source", sa.String(16), nullable=False),
    sa.Column("event_type", sa.String(128), nullable=False),
    sa.Column("boundary", sa.String(64), nullable=False),
    sa.Column("source_event_id", sa.Uuid(), nullable=False),
    sa.Column("producer_seq", sa.BigInteger(), nullable=True),
    sa.Column("receipt_seq", sa.BigInteger(), nullable=False),
    sa.Column("caused_by_event_id", sa.Uuid(), nullable=True),
    sa.Column("payload_schema_version", sa.Integer(), nullable=False),
    sa.Column(
        "payload",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    ),
    sa.Column("payload_canonical_bytes", sa.LargeBinary(), nullable=False),
    sa.Column("payload_digest", sa.CHAR(64), nullable=False),
    sa.Column("disposition", sa.String(32), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
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
    sa.CheckConstraint(
        "source IN ('boundary', 'sut')",
        name="source_valid",
    ),
    sa.CheckConstraint(
        "(source = 'boundary' AND producer_seq IS NULL) OR "
        "(source = 'sut' AND producer_seq > 0)",
        name="producer_seq_by_source",
    ),
    sa.CheckConstraint(
        "receipt_seq > 0",
        name="receipt_seq_positive",
    ),
    sa.CheckConstraint(
        "payload_schema_version > 0",
        name="payload_schema_version_positive",
    ),
    sa.CheckConstraint(
        "payload_digest ~ '^[0-9a-f]{64}$'",
        name="payload_digest_lower_hex",
    ),
    sa.CheckConstraint(
        "disposition IN ('accepted', 'rejected', 'late')",
        name="disposition_valid",
    ),
)

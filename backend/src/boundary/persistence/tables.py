"""SQLAlchemy Core metadata through Task 7 regression comparison."""

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
    sa.Column("cancellation_id", sa.Uuid(), nullable=True, unique=True),
    sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("failure_reason", sa.String(128), nullable=True),
    sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "executor_managed",
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
    sa.CheckConstraint(
        "(NOT cancel_requested AND cancellation_id IS NULL AND "
        "cancel_requested_at IS NULL) OR "
        "(cancel_requested AND cancellation_id IS NOT NULL AND "
        "cancel_requested_at IS NOT NULL)",
        name="cancellation_consistent",
    ),
)
sa.Index(
    "ix_campaigns_executor_order",
    campaigns.c.status,
    campaigns.c.created_at,
    campaigns.c.campaign_id,
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
    sa.Column("fault_spec_id", sa.Uuid(), nullable=True),
    sa.Column("fault_id", sa.Uuid(), nullable=True, unique=True),
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
        "tested_input",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    ),
    sa.Column("tested_input_bytes", sa.LargeBinary(), nullable=True),
    sa.Column("tested_input_digest", sa.CHAR(64), nullable=True),
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
        "next_audit_seq",
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
        "execution_checkpoint",
        sa.String(32),
        nullable=False,
        server_default=sa.text("'not_started'"),
    ),
    sa.Column("reconciliation_reason", sa.String(128), nullable=True),
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
        "next_audit_seq > 0",
        name="next_audit_seq_positive",
    ),
    sa.CheckConstraint(
        "next_tool_ordinal >= 0",
        name="next_tool_ordinal_nonnegative",
    ),
    sa.CheckConstraint(
        "control_run_id IS NULL OR control_run_id <> run_id",
        name="control_run_not_self",
    ),
    sa.CheckConstraint(
        "execution_checkpoint IN ('not_started', 'target_interaction', "
        "'polling', 'finalized', 'analyzed')",
        name="execution_checkpoint_valid",
    ),
    sa.CheckConstraint(
        "(run_role = 'control' AND control_run_id IS NULL AND "
        "fault_spec_id IS NULL AND fault_id IS NULL) OR "
        "(run_role = 'injected' AND control_run_id IS NOT NULL AND "
        "fault_spec_id IS NOT NULL AND fault_id IS NOT NULL)",
        name="role_fault_identity_consistent",
    ),
    sa.CheckConstraint(
        "(tested_input IS NULL AND tested_input_bytes IS NULL AND "
        "tested_input_digest IS NULL) OR "
        "(tested_input IS NOT NULL AND tested_input_bytes IS NOT NULL "
        "AND tested_input_digest ~ '^[0-9a-f]{64}$')",
        name="tested_input_canonical_consistent",
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
        nullable=True,
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
        nullable=True,
    ),
    sa.Column("resource_kind", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.Uuid(), nullable=False),
    sa.Column(
        "resource_links",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
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
    sa.CheckConstraint(
        "resource_kind IN ('campaign', 'regression_case', 'rerun', "
        "'cancellation')",
        name="resource_kind_valid",
    ),
    sa.CheckConstraint(
        "jsonb_typeof(resource_links) = 'object'",
        name="resource_links_object",
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
    sa.Column("receipt_seq", sa.BigInteger(), nullable=True),
    sa.Column("audit_seq", sa.BigInteger(), nullable=True),
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
        "audit_seq",
        name="uq_evidence_records_run_audit_seq",
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
        "(disposition = 'accepted' AND receipt_seq > 0 "
        "AND audit_seq IS NULL) OR "
        "(disposition IN ('rejected', 'late') "
        "AND receipt_seq IS NULL AND audit_seq > 0)",
        name="ordering_by_disposition",
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
sa.Index(
    "uq_evidence_records_run_budget",
    evidence_records.c.run_id,
    unique=True,
    postgresql_where=sa.and_(
        evidence_records.c.source == "boundary",
        evidence_records.c.disposition == "accepted",
        evidence_records.c.event_type == "boundary.run_budget.bound",
    ),
)

tool_calls = sa.Table(
    "tool_calls",
    metadata,
    sa.Column("run_id", sa.Uuid(), nullable=False),
    sa.Column("tool_call_id", sa.Uuid(), nullable=False),
    sa.Column(
        "capability_record_id",
        sa.Uuid(),
        sa.ForeignKey(
            "run_capabilities.capability_record_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    ),
    sa.Column("trace_id", sa.Uuid(), nullable=False),
    sa.Column("tool_identity", sa.String(128), nullable=False),
    sa.Column("fault_id", sa.Uuid(), nullable=True),
    sa.Column("retry_ordinal", sa.Integer(), nullable=False),
    sa.Column("request_digest", sa.CHAR(64), nullable=False),
    sa.Column(
        "arrival_evidence_id",
        sa.Uuid(),
        sa.ForeignKey(
            "evidence_records.evidence_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
        unique=True,
    ),
    sa.Column(
        "ordinal_evidence_id",
        sa.Uuid(),
        sa.ForeignKey(
            "evidence_records.evidence_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
        unique=True,
    ),
    sa.Column("registration_outcome", sa.String(32), nullable=False),
    sa.Column("response_disposition", sa.String(32), nullable=True),
    sa.Column("response_digest", sa.CHAR(64), nullable=True),
    sa.Column(
        "response_evidence_id",
        sa.Uuid(),
        sa.ForeignKey(
            "evidence_records.evidence_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
        unique=True,
    ),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.PrimaryKeyConstraint(
        "run_id",
        "tool_call_id",
        name="pk_tool_calls",
    ),
    sa.ForeignKeyConstraint(
        ["run_id"],
        ["runs.run_id"],
        name="fk_tool_calls_run_id_runs",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
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
    sa.CheckConstraint(
        "tool_identity <> ''",
        name="tool_identity_not_empty",
    ),
    sa.CheckConstraint(
        "retry_ordinal >= 0",
        name="retry_ordinal_nonnegative",
    ),
    sa.CheckConstraint(
        "request_digest ~ '^[0-9a-f]{64}$'",
        name="request_digest_lower_hex",
    ),
    sa.CheckConstraint(
        "(fault_id IS NULL AND "
        "registration_outcome = 'no_fault_configured') OR "
        "(fault_id IS NOT NULL AND registration_outcome IN "
        "('pre_effect_reserved', 'attempt_not_selected', "
        "'maximum_activations_reached'))",
        name="registration_outcome_consistent",
    ),
    sa.CheckConstraint(
        "(registration_outcome IN "
        "('no_fault_configured', 'attempt_not_selected') AND "
        "response_disposition = 'success_response_committed' AND "
        "response_digest ~ '^[0-9a-f]{64}$' AND "
        "response_evidence_id IS NOT NULL) OR "
        "(registration_outcome NOT IN "
        "('no_fault_configured', 'attempt_not_selected') AND "
        "response_disposition IS NULL AND response_digest IS NULL AND "
        "response_evidence_id IS NULL)",
        name="response_commitment_consistent",
    ),
)

fault_activations = sa.Table(
    "fault_activations",
    metadata,
    sa.Column("activation_id", sa.Uuid(), primary_key=True),
    sa.Column("run_id", sa.Uuid(), nullable=False),
    sa.Column("tool_call_id", sa.Uuid(), nullable=False),
    sa.Column("fault_id", sa.Uuid(), nullable=False),
    sa.Column("activation_ordinal", sa.SmallInteger(), nullable=False),
    sa.Column(
        "reservation_state",
        sa.String(32),
        nullable=False,
        server_default=sa.text("'pre_effect_reserved'"),
    ),
    sa.Column(
        "activation_evidence_id",
        sa.Uuid(),
        sa.ForeignKey(
            "evidence_records.evidence_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
        unique=True,
    ),
    sa.Column("accepted_request_origin_ns", sa.BigInteger(), nullable=True),
    sa.Column("activation_started_ns", sa.BigInteger(), nullable=True),
    sa.Column("client_timeout_boundary_ns", sa.BigInteger(), nullable=True),
    sa.Column("hold_deadline_ns", sa.BigInteger(), nullable=True),
    sa.Column(
        "effect_status",
        sa.String(32),
        nullable=False,
        server_default=sa.text("'not_started'"),
    ),
    sa.Column(
        "effect_proof",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    ),
    sa.Column("effect_proof_bytes", sa.LargeBinary(), nullable=True),
    sa.Column("effect_proof_digest", sa.CHAR(64), nullable=True),
    sa.Column(
        "effect_evidence_id",
        sa.Uuid(),
        sa.ForeignKey(
            "evidence_records.evidence_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
        unique=True,
    ),
    sa.Column("hold_disposition", sa.String(32), nullable=True),
    sa.Column("runtime_completed_monotonic_ns", sa.BigInteger(), nullable=True),
    sa.Column("runtime_completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.ForeignKeyConstraint(
        ["run_id", "tool_call_id", "fault_id"],
        [
            "tool_calls.run_id",
            "tool_calls.tool_call_id",
            "tool_calls.fault_id",
        ],
        name="fk_fault_activations_registered_tool_call",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    ),
    sa.UniqueConstraint(
        "run_id",
        "tool_call_id",
        name="uq_fault_activations_run_tool_call",
    ),
    sa.UniqueConstraint(
        "run_id",
        "fault_id",
        "activation_ordinal",
        name="uq_fault_activations_run_fault_ordinal",
    ),
    sa.CheckConstraint(
        "activation_ordinal >= 0 AND activation_ordinal < 2",
        name="phase1_activation_ordinal",
    ),
    sa.CheckConstraint(
        "reservation_state IN ('pre_effect_reserved', "
        "'activation_started', 'effect_realized', 'unproven', "
        "'runtime_lost')",
        name="activation_state_valid",
    ),
    sa.CheckConstraint(
        "effect_status IN ('not_started', 'pending', "
        "'effect_realized', 'unproven', 'runtime_lost')",
        name="effect_status_valid",
    ),
    sa.CheckConstraint(
        "(reservation_state = 'pre_effect_reserved' AND "
        "activation_evidence_id IS NULL AND "
        "accepted_request_origin_ns IS NULL AND activation_started_ns IS NULL "
        "AND client_timeout_boundary_ns IS NULL AND hold_deadline_ns IS NULL "
        "AND effect_status = 'not_started') OR "
        "(reservation_state <> 'pre_effect_reserved' AND "
        "activation_evidence_id IS NOT NULL AND "
        "accepted_request_origin_ns IS NOT NULL AND activation_started_ns IS NOT NULL "
        "AND client_timeout_boundary_ns IS NOT NULL AND hold_deadline_ns IS NOT NULL "
        "AND activation_started_ns < client_timeout_boundary_ns "
        "AND client_timeout_boundary_ns < hold_deadline_ns "
        "AND effect_status <> 'not_started')",
        name="activation_timing_consistent",
    ),
    sa.CheckConstraint(
        "(effect_status = 'effect_realized' AND effect_proof IS NOT NULL "
        "AND effect_proof_bytes IS NOT NULL "
        "AND effect_proof_digest ~ '^[0-9a-f]{64}$' "
        "AND effect_evidence_id IS NOT NULL) OR "
        "(effect_status <> 'effect_realized' AND effect_proof IS NULL "
        "AND effect_proof_bytes IS NULL AND effect_proof_digest IS NULL "
        "AND effect_evidence_id IS NULL)",
        name="effect_proof_consistent",
    ),
    sa.CheckConstraint(
        "(hold_disposition IS NULL AND runtime_completed_at IS NULL "
        "AND runtime_completed_monotonic_ns IS NULL) OR "
        "(hold_disposition IN ('bounded_hold_complete', 'proof_failed') "
        "AND runtime_completed_at IS NOT NULL "
        "AND runtime_completed_monotonic_ns >= hold_deadline_ns) OR "
        "(hold_disposition = 'runtime_lost' AND runtime_completed_at IS NOT NULL "
        "AND runtime_completed_monotonic_ns IS NULL)",
        name="hold_completion_consistent",
    ),
)

evidence_sets = sa.Table(
    "evidence_sets",
    metadata,
    sa.Column("evidence_set_id", sa.Uuid(), primary_key=True),
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
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
        "manifest_schema_version > 0",
        name="manifest_schema_version_positive",
    ),
    sa.CheckConstraint(
        "cutoff_reason IN ('target_terminal_watermark', "
        "'evidence_deadline', 'cancellation_grace', "
        "'reconciliation_error')",
        name="cutoff_reason_valid",
    ),
    sa.CheckConstraint(
        "target_final_watermark IS NULL OR target_final_watermark >= 0",
        name="target_final_watermark_nonnegative",
    ),
    sa.CheckConstraint(
        "evidence_set_digest ~ '^[0-9a-f]{64}$'",
        name="digest_lower_hex",
    ),
    sa.CheckConstraint(
        "finalizer_identity <> ''",
        name="finalizer_identity_not_empty",
    ),
)

analyses = sa.Table(
    "analyses",
    metadata,
    sa.Column("analysis_id", sa.Uuid(), primary_key=True),
    sa.Column("record_kind", sa.String(32), nullable=False),
    sa.Column(
        "evidence_set_id",
        sa.Uuid(),
        sa.ForeignKey(
            "evidence_sets.evidence_set_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    ),
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
    sa.Column(
        "prior_analysis_id",
        sa.Uuid(),
        sa.ForeignKey(
            "analyses.analysis_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    ),
    sa.Column("attempted_analysis_digest", sa.CHAR(64), nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
        "record_kind IN ('authoritative', 'integrity_failure')",
        name="record_kind_valid",
    ),
    sa.CheckConstraint(
        "evidence_set_digest ~ '^[0-9a-f]{64}$'",
        name="evidence_set_digest_lower_hex",
    ),
    sa.CheckConstraint(
        "analysis_digest ~ '^[0-9a-f]{64}$'",
        name="analysis_digest_lower_hex",
    ),
    sa.CheckConstraint(
        "attempted_analysis_digest IS NULL OR "
        "attempted_analysis_digest ~ '^[0-9a-f]{64}$'",
        name="attempted_digest_lower_hex",
    ),
    sa.CheckConstraint(
        "analysis_schema_version > 0",
        name="analysis_schema_version_positive",
    ),
    sa.CheckConstraint(
        "evaluability_aggregate IN "
        "('EVALUABLE', 'INCOMPLETE', 'INVALID', 'EXECUTION_ERROR')",
        name="evaluability_aggregate_valid",
    ),
    sa.CheckConstraint(
        "policy_result IN "
        "('PASS', 'FAIL', 'INCOMPLETE', 'INVALID', 'EXECUTION_ERROR')",
        name="policy_result_valid",
    ),
    sa.CheckConstraint(
        "(record_kind = 'authoritative' AND prior_analysis_id IS NULL "
        "AND attempted_analysis_digest IS NULL) OR "
        "(record_kind = 'integrity_failure' AND prior_analysis_id IS NOT NULL "
        "AND attempted_analysis_digest IS NOT NULL "
        "AND evaluability_aggregate = 'EXECUTION_ERROR' "
        "AND policy_result = 'EXECUTION_ERROR')",
        name="record_kind_fields_consistent",
    ),
    sa.Index(
        "uq_analyses_authoritative_key",
        "evidence_set_digest",
        "analyzer_version",
        "assertion_set_version",
        "policy_version",
        unique=True,
        postgresql_where=sa.text("record_kind = 'authoritative'"),
    ),
    sa.Index(
        "uq_analyses_integrity_attempt",
        "prior_analysis_id",
        "attempted_analysis_digest",
        unique=True,
        postgresql_where=sa.text("record_kind = 'integrity_failure'"),
    ),
)

regression_cases = sa.Table(
    "regression_cases",
    metadata,
    sa.Column("regression_case_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "source_analysis_id",
        sa.Uuid(),
        sa.ForeignKey(
            "analyses.analysis_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
        unique=True,
    ),
    sa.Column(
        "source_evidence_set_id",
        sa.Uuid(),
        sa.ForeignKey(
            "evidence_sets.evidence_set_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    ),
    sa.Column(
        "source_run_id",
        sa.Uuid(),
        sa.ForeignKey(
            "runs.run_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    ),
    sa.Column("artifact_schema_version", sa.Integer(), nullable=False),
    sa.Column(
        "artifact",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    ),
    sa.Column("artifact_canonical_bytes", sa.LargeBinary(), nullable=False),
    sa.Column("integrity_digest", sa.CHAR(64), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
        "artifact_schema_version > 0",
        name="artifact_schema_version_positive",
    ),
    sa.CheckConstraint(
        "integrity_digest ~ '^[0-9a-f]{64}$'",
        name="integrity_digest_lower_hex",
    ),
)

reruns = sa.Table(
    "reruns",
    metadata,
    sa.Column("rerun_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "regression_case_id",
        sa.Uuid(),
        sa.ForeignKey(
            "regression_cases.regression_case_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    ),
    sa.Column("mode", sa.String(32), nullable=False),
    sa.Column("requested_tested_agent_version", sa.String(256), nullable=False),
    sa.Column(
        "campaign_id",
        sa.Uuid(),
        sa.ForeignKey(
            "campaigns.campaign_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
        unique=True,
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
        unique=True,
    ),
    sa.Column(
        "candidate_run_id",
        sa.Uuid(),
        sa.ForeignKey(
            "runs.run_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
        unique=True,
    ),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("reason_code", sa.String(128), nullable=True),
    sa.Column("pre_report_schema_version", sa.Integer(), nullable=False),
    sa.Column(
        "pre_invariance_report",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    ),
    sa.Column("pre_invariance_canonical_bytes", sa.LargeBinary(), nullable=False),
    sa.Column("pre_invariance_digest", sa.CHAR(64), nullable=False),
    sa.Column("completed_report_schema_version", sa.Integer(), nullable=True),
    sa.Column(
        "completed_invariance_report",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    ),
    sa.Column("completed_invariance_canonical_bytes", sa.LargeBinary(), nullable=True),
    sa.Column("completed_invariance_digest", sa.CHAR(64), nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
        "mode IN ('reproduction', 'version_comparison')",
        name="mode_valid",
    ),
    sa.CheckConstraint(
        "status IN ('rejected', 'accepted', 'running', 'completed', 'failed')",
        name="status_valid",
    ),
    sa.CheckConstraint(
        "pre_report_schema_version > 0",
        name="pre_report_schema_version_positive",
    ),
    sa.CheckConstraint(
        "pre_invariance_digest ~ '^[0-9a-f]{64}$'",
        name="pre_invariance_digest_lower_hex",
    ),
    sa.CheckConstraint(
        "(status = 'rejected' AND campaign_id IS NULL AND control_run_id IS NULL "
        "AND candidate_run_id IS NULL AND reason_code IS NOT NULL) OR "
        "(status <> 'rejected' AND campaign_id IS NOT NULL "
        "AND control_run_id IS NOT NULL)",
        name="execution_identity_lifecycle_consistent",
    ),
    sa.CheckConstraint(
        "(completed_report_schema_version IS NULL "
        "AND completed_invariance_report IS NULL "
        "AND completed_invariance_canonical_bytes IS NULL "
        "AND completed_invariance_digest IS NULL) OR "
        "(completed_report_schema_version > 0 "
        "AND completed_invariance_report IS NOT NULL "
        "AND completed_invariance_canonical_bytes IS NOT NULL "
        "AND completed_invariance_digest ~ '^[0-9a-f]{64}$')",
        name="completed_invariance_consistent",
    ),
)

comparisons = sa.Table(
    "comparisons",
    metadata,
    sa.Column("comparison_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "regression_case_id",
        sa.Uuid(),
        sa.ForeignKey(
            "regression_cases.regression_case_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    ),
    sa.Column(
        "rerun_id",
        sa.Uuid(),
        sa.ForeignKey(
            "reruns.rerun_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
        unique=True,
    ),
    sa.Column(
        "source_run_id",
        sa.Uuid(),
        sa.ForeignKey("runs.run_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "source_evidence_set_id",
        sa.Uuid(),
        sa.ForeignKey(
            "evidence_sets.evidence_set_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    ),
    sa.Column(
        "source_analysis_id",
        sa.Uuid(),
        sa.ForeignKey(
            "analyses.analysis_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    ),
    sa.Column(
        "candidate_run_id",
        sa.Uuid(),
        sa.ForeignKey("runs.run_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "candidate_evidence_set_id",
        sa.Uuid(),
        sa.ForeignKey(
            "evidence_sets.evidence_set_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    ),
    sa.Column(
        "candidate_analysis_id",
        sa.Uuid(),
        sa.ForeignKey(
            "analyses.analysis_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    ),
    sa.Column("source_tested_agent_version", sa.String(256), nullable=False),
    sa.Column("candidate_tested_agent_version", sa.String(256), nullable=False),
    sa.Column("source_policy_result", sa.String(32), nullable=False),
    sa.Column("candidate_policy_result", sa.String(32), nullable=True),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("terminal_result", sa.String(32), nullable=True),
    sa.Column("reason_code", sa.String(128), nullable=True),
    sa.Column("summary_schema_version", sa.Integer(), nullable=True),
    sa.Column(
        "summary_document",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    ),
    sa.Column("summary_canonical_bytes", sa.LargeBinary(), nullable=True),
    sa.Column("summary_digest", sa.CHAR(64), nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
        "status IN ('pending', 'valid', 'ineligible', 'execution_error')",
        name="status_valid",
    ),
    sa.CheckConstraint(
        "source_policy_result = 'FAIL'",
        name="source_result_fail",
    ),
    sa.CheckConstraint(
        "candidate_policy_result IS NULL OR candidate_policy_result IN "
        "('PASS', 'FAIL', 'INCOMPLETE', 'INVALID', 'EXECUTION_ERROR')",
        name="candidate_result_valid",
    ),
    sa.CheckConstraint(
        "(status = 'pending' AND terminal_result IS NULL AND reason_code IS NULL "
        "AND summary_schema_version IS NULL AND summary_document IS NULL "
        "AND summary_canonical_bytes IS NULL AND summary_digest IS NULL) OR "
        "(status <> 'pending' AND terminal_result IS NOT NULL "
        "AND reason_code IS NOT NULL AND summary_schema_version > 0 "
        "AND summary_document IS NOT NULL AND summary_canonical_bytes IS NOT NULL "
        "AND summary_digest ~ '^[0-9a-f]{64}$')",
        name="terminal_content_consistent",
    ),
)

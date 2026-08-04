import {
  POLICY_RESULTS,
  type AssertionResult,
  type Campaign,
  type CampaignAccepted,
  type CancellationResult,
  type Comparison,
  type EvidenceItem,
  type EvidencePage,
  type EvidenceReference,
  type Evaluability,
  type InvarianceRow,
  type MaterializationResult,
  type PolicyResult,
  type ProblemDetail,
  type RegressionArtifact,
  type RegressionCase,
  type RerunAccepted,
  type ResourceLinks,
  type Run,
} from "./types";

export class MalformedResponseError extends Error {
  readonly kind = "malformed";

  constructor(message = "Boundary returned a malformed response.") {
    super(message);
    this.name = "MalformedResponseError";
  }
}

type ObjectValue = Record<string, unknown>;

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DIGEST_PATTERN = /^[0-9a-f]{64}$/;
const POLICY_SET = new Set<string>(POLICY_RESULTS);
const API_LINK_PATTERN =
  /^\/api\/v1\/(?:campaigns|runs|regression-cases|comparisons)\/[0-9a-f-]{36}$/i;

function fail(field: string): never {
  throw new MalformedResponseError(`Boundary response field “${field}” is malformed.`);
}

function object(value: unknown, field: string): ObjectValue {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    fail(field);
  }
  return value as ObjectValue;
}

function array(value: unknown, field: string): unknown[] {
  if (!Array.isArray(value)) fail(field);
  return value;
}

function string(value: unknown, field: string, max = 65_536): string {
  if (typeof value !== "string" || value.length === 0 || value.length > max) {
    fail(field);
  }
  return value;
}

function nullableString(value: unknown, field: string, max = 65_536): string | null {
  return value === null ? null : string(value, field, max);
}

function bool(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") fail(field);
  return value;
}

function integer(value: unknown, field: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) fail(field);
  return value as number;
}

function uuid(value: unknown, field: string): string {
  const parsed = string(value, field, 36);
  if (!UUID_PATTERN.test(parsed)) fail(field);
  return parsed;
}

function nullableUuid(value: unknown, field: string): string | null {
  return value === null ? null : uuid(value, field);
}

function digest(value: unknown, field: string): string {
  const parsed = string(value, field, 64);
  if (!DIGEST_PATTERN.test(parsed)) fail(field);
  return parsed;
}

function nullableDigest(value: unknown, field: string): string | null {
  return value === null ? null : digest(value, field);
}

function literal<T extends string>(value: unknown, allowed: readonly T[], field: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) fail(field);
  return value as T;
}

function stringArray(value: unknown, field: string): string[] {
  return array(value, field).map((item, index) => string(item, `${field}[${index}]`, 512));
}

function uuidArray(value: unknown, field: string): string[] {
  return array(value, field).map((item, index) => uuid(item, `${field}[${index}]`));
}

function apiLink(value: unknown, field: string): string | null {
  if (value === null) return null;
  const parsed = string(value, field, 256);
  if (!API_LINK_PATTERN.test(parsed)) fail(field);
  return parsed;
}

function links(value: unknown, field: string): ResourceLinks {
  const item = object(value, field);
  return {
    campaign: apiLink(item.campaign, `${field}.campaign`),
    control_run: apiLink(item.control_run, `${field}.control_run`),
    injected_run: apiLink(item.injected_run, `${field}.injected_run`),
    regression_case: apiLink(item.regression_case, `${field}.regression_case`),
    comparison: apiLink(item.comparison, `${field}.comparison`),
  };
}

function jsonRecord(value: unknown, field: string): Record<string, unknown> {
  const parsed = object(value, field);
  try {
    const serialized = JSON.stringify(parsed);
    if (serialized === undefined || serialized.length > 1_048_576) fail(field);
  } catch {
    fail(field);
  }
  return parsed;
}

function evidenceReference(value: unknown, field: string): EvidenceReference {
  const item = object(value, field);
  return {
    evidence_id: uuid(item.evidence_id, `${field}.evidence_id`),
    source: literal(item.source, ["boundary", "sut"] as const, `${field}.source`),
    event_type: string(item.event_type, `${field}.event_type`, 256),
    boundary: string(item.boundary, `${field}.boundary`, 256),
    source_event_id: uuid(item.source_event_id, `${field}.source_event_id`),
    producer_seq:
      item.producer_seq === null ? null : integer(item.producer_seq, `${field}.producer_seq`, 1),
    receipt_seq: integer(item.receipt_seq, `${field}.receipt_seq`, 1),
    caused_by_event_id: nullableUuid(item.caused_by_event_id, `${field}.caused_by_event_id`),
    payload_schema_version: integer(item.payload_schema_version, `${field}.payload_schema_version`, 1),
    content_digest: digest(item.content_digest, `${field}.content_digest`),
  };
}

function evidenceReferences(value: unknown, field: string): EvidenceReference[] {
  return array(value, field).map((item, index) => evidenceReference(item, `${field}[${index}]`));
}

function policy(value: unknown, field: string): PolicyResult {
  if (typeof value !== "string" || !POLICY_SET.has(value)) fail(field);
  return value as PolicyResult;
}

function nullablePolicy(value: unknown, field: string): PolicyResult | null {
  return value === null ? null : policy(value, field);
}

function apiVersion(item: ObjectValue): void {
  if (item.api_version !== "v1") fail("api_version");
}

export function validateCampaignAccepted(value: unknown): CampaignAccepted {
  const item = object(value, "campaign acceptance");
  apiVersion(item);
  return {
    campaign_id: uuid(item.campaign_id, "campaign_id"),
    control_run_id: uuid(item.control_run_id, "control_run_id"),
    status: literal(item.status, ["accepted"] as const, "status"),
    status_url: apiLink(item.status_url, "status_url") ?? fail("status_url"),
    links: links(item.links, "links"),
    replayed: bool(item.replayed, "replayed"),
  };
}

export function validateCampaign(value: unknown): Campaign {
  const item = object(value, "campaign");
  apiVersion(item);
  return {
    campaign_id: uuid(item.campaign_id, "campaign_id"),
    campaign_kind: string(item.campaign_kind, "campaign_kind", 256),
    operational_status: string(item.operational_status, "operational_status", 128),
    current_step: string(item.current_step, "current_step", 128),
    cancel_requested: bool(item.cancel_requested, "cancel_requested"),
    cancellation_id: nullableUuid(item.cancellation_id, "cancellation_id"),
    terminal: bool(item.terminal, "terminal"),
    failure_reason: nullableString(item.failure_reason, "failure_reason", 512),
    control_run_id: nullableUuid(item.control_run_id, "control_run_id"),
    injected_run_id: nullableUuid(item.injected_run_id, "injected_run_id"),
    regression_case_id: nullableUuid(item.regression_case_id, "regression_case_id"),
    rerun_id: nullableUuid(item.rerun_id, "rerun_id"),
    comparison_id: nullableUuid(item.comparison_id, "comparison_id"),
    links: links(item.links, "links"),
  };
}

const EVALUABILITY_CHECK_IDS = [
  "EVAL.CONTROL_VALID_SUCCESS",
  "EVAL.TIMEOUT_0_COMPLETE",
  "EVAL.TIMEOUT_1_COMPLETE",
  "EVAL.IDENTITY_VALID",
  "EVAL.EVIDENCE_FINALIZED_ORDERED",
  "EVAL.BOUNDARY_SYSTEMS_HEALTHY",
] as const;

function evaluability(value: unknown, field: string): Evaluability {
  const item = object(value, field);
  const checks = array(item.checks, `${field}.checks`).map((raw, index) => {
    const check = object(raw, `${field}.checks[${index}]`);
    return {
      check_id: literal(check.check_id, EVALUABILITY_CHECK_IDS, `${field}.checks[${index}].check_id`),
      outcome: literal(
        check.outcome,
        ["SATISFIED", "INCOMPLETE", "INVALID", "EXECUTION_ERROR"] as const,
        `${field}.checks[${index}].outcome`,
      ),
      reason_code: string(check.reason_code, `${field}.checks[${index}].reason_code`, 256),
      explanation: string(check.explanation, `${field}.checks[${index}].explanation`, 512),
      evidence_references: evidenceReferences(
        check.evidence_references,
        `${field}.checks[${index}].evidence_references`,
      ),
    };
  });
  if (checks.length !== 6 || new Set(checks.map((check) => check.check_id)).size !== 6) {
    fail(`${field}.checks`);
  }
  return {
    check_set_version: string(item.check_set_version, `${field}.check_set_version`, 256),
    aggregate: literal(
      item.aggregate,
      ["EVALUABLE", "INCOMPLETE", "INVALID", "EXECUTION_ERROR"] as const,
      `${field}.aggregate`,
    ),
    checks,
  };
}

const ASSERTION_IDS = ["P1.RETRY_LIMIT", "P1.DEGRADED_RESULT", "P1.RUN_WITHIN_BUDGET"] as const;

function assertions(value: unknown, field: string): AssertionResult[] {
  const parsed = array(value, field).map((raw, index) => {
    const item = object(raw, `${field}[${index}]`);
    return {
      assertion_id: literal(item.assertion_id, ASSERTION_IDS, `${field}[${index}].assertion_id`),
      assertion_set_version: string(item.assertion_set_version, `${field}[${index}].assertion_set_version`, 256),
      outcome: literal(item.outcome, ["PASS", "FAIL"] as const, `${field}[${index}].outcome`),
      required_evidence_roles: stringArray(item.required_evidence_roles, `${field}[${index}].required_evidence_roles`),
      expected_behavior: string(item.expected_behavior, `${field}[${index}].expected_behavior`, 512),
      observed_behavior: string(item.observed_behavior, `${field}[${index}].observed_behavior`, 512),
      evidence_references: evidenceReferences(item.evidence_references, `${field}[${index}].evidence_references`),
    };
  });
  if (parsed.length !== 0 && (parsed.length !== 3 || new Set(parsed.map((item) => item.assertion_id)).size !== 3)) {
    fail(field);
  }
  return parsed;
}

export function validateRun(value: unknown): Run {
  const item = object(value, "run");
  apiVersion(item);
  const injection = item.injection_boundary === null ? null : object(item.injection_boundary, "injection_boundary");
  const divergence = item.first_unsafe_divergence === null ? null : object(item.first_unsafe_divergence, "first_unsafe_divergence");
  const parsedAssertions = assertions(item.assertions, "assertions");
  const parsedPolicy = nullablePolicy(item.policy_result, "policy_result");
  if ((parsedPolicy === "PASS" || parsedPolicy === "FAIL") && parsedAssertions.length !== 3) fail("assertions");
  return {
    run_id: uuid(item.run_id, "run_id"),
    trace_id: uuid(item.trace_id, "trace_id"),
    campaign_id: uuid(item.campaign_id, "campaign_id"),
    run_role: string(item.run_role, "run_role", 128),
    control_run_id: nullableUuid(item.control_run_id, "control_run_id"),
    expected_tested_agent_id: string(item.expected_tested_agent_id, "expected_tested_agent_id", 256),
    expected_tested_agent_version: string(item.expected_tested_agent_version, "expected_tested_agent_version", 256),
    reported_tested_agent_id: nullableString(item.reported_tested_agent_id, "reported_tested_agent_id", 256),
    reported_tested_agent_version: nullableString(item.reported_tested_agent_version, "reported_tested_agent_version", 256),
    operational_status: string(item.operational_status, "operational_status", 128),
    policy_result: parsedPolicy,
    contract_version: string(item.contract_version, "contract_version", 256),
    scenario_id: string(item.scenario_id, "scenario_id", 256),
    scenario_version: integer(item.scenario_version, "scenario_version", 1),
    fault_spec_id: nullableUuid(item.fault_spec_id, "fault_spec_id"),
    fault_id: nullableUuid(item.fault_id, "fault_id"),
    fault_definition_digest: nullableDigest(item.fault_definition_digest, "fault_definition_digest"),
    evidence_set_id: nullableUuid(item.evidence_set_id, "evidence_set_id"),
    evidence_set_digest: nullableDigest(item.evidence_set_digest, "evidence_set_digest"),
    finalizer_identity: nullableString(item.finalizer_identity, "finalizer_identity", 256),
    analysis_id: nullableUuid(item.analysis_id, "analysis_id"),
    analysis_digest: nullableDigest(item.analysis_digest, "analysis_digest"),
    analyzer_version: nullableString(item.analyzer_version, "analyzer_version", 256),
    assertion_set_version: nullableString(item.assertion_set_version, "assertion_set_version", 256),
    policy_version: nullableString(item.policy_version, "policy_version", 256),
    evaluability: item.evaluability === null ? null : evaluability(item.evaluability, "evaluability"),
    assertions: parsedAssertions,
    injection_boundary: injection === null ? null : {
      boundary: string(injection.boundary, "injection_boundary.boundary", 256),
      realized_timeout_ordinals: array(injection.realized_timeout_ordinals, "injection_boundary.realized_timeout_ordinals").map((value, index) => integer(value, `injection_boundary.realized_timeout_ordinals[${index}]`)),
      evidence_references: evidenceReferences(injection.evidence_references, "injection_boundary.evidence_references"),
    },
    first_unsafe_divergence: divergence === null ? null : {
      assertion_id: string(divergence.assertion_id, "first_unsafe_divergence.assertion_id", 256),
      boundary_event_id: uuid(divergence.boundary_event_id, "first_unsafe_divergence.boundary_event_id"),
      boundary: string(divergence.boundary, "first_unsafe_divergence.boundary", 256),
      retry_ordinal: integer(divergence.retry_ordinal, "first_unsafe_divergence.retry_ordinal"),
      supporting_evidence_references: evidenceReferences(divergence.supporting_evidence_references, "first_unsafe_divergence.supporting_evidence_references"),
      expected_behavior: string(divergence.expected_behavior, "first_unsafe_divergence.expected_behavior", 512),
      observed_behavior: string(divergence.observed_behavior, "first_unsafe_divergence.observed_behavior", 512),
      downstream_symptom_references: evidenceReferences(divergence.downstream_symptom_references, "first_unsafe_divergence.downstream_symptom_references"),
      analyzer_version: string(divergence.analyzer_version, "first_unsafe_divergence.analyzer_version", 256),
    },
    downstream_symptoms: evidenceReferences(item.downstream_symptoms, "downstream_symptoms"),
    regression_case_id: nullableUuid(item.regression_case_id, "regression_case_id"),
    comparison_id: nullableUuid(item.comparison_id, "comparison_id"),
    links: links(item.links, "links"),
  };
}

function evidenceItem(value: unknown, field: string): EvidenceItem {
  const item = object(value, field);
  const authority = literal(item.authority, ["Boundary", "tested-agent"] as const, `${field}.authority`);
  const source = literal(item.source, ["boundary", "sut"] as const, `${field}.source`);
  if ((authority === "Boundary") !== (source === "boundary")) fail(`${field}.authority`);
  return {
    evidence_id: uuid(item.evidence_id, `${field}.evidence_id`),
    authority,
    source,
    event_type: string(item.event_type, `${field}.event_type`, 256),
    boundary: string(item.boundary, `${field}.boundary`, 256),
    source_event_id: uuid(item.source_event_id, `${field}.source_event_id`),
    producer_seq: item.producer_seq === null ? null : integer(item.producer_seq, `${field}.producer_seq`, 1),
    receipt_seq: integer(item.receipt_seq, `${field}.receipt_seq`, 1),
    caused_by_event_id: nullableUuid(item.caused_by_event_id, `${field}.caused_by_event_id`),
    payload_schema_version: integer(item.payload_schema_version, `${field}.payload_schema_version`, 1),
    payload_digest: digest(item.payload_digest, `${field}.payload_digest`),
    payload: jsonRecord(item.payload, `${field}.payload`),
  };
}

export function validateEvidencePage(value: unknown): EvidencePage {
  const item = object(value, "evidence page");
  apiVersion(item);
  return {
    run_id: uuid(item.run_id, "run_id"),
    after_receipt_seq: integer(item.after_receipt_seq, "after_receipt_seq"),
    limit: integer(item.limit, "limit", 1),
    items: array(item.items, "items").map((value, index) => evidenceItem(value, `items[${index}]`)),
    next_after_receipt_seq: item.next_after_receipt_seq === null ? null : integer(item.next_after_receipt_seq, "next_after_receipt_seq", 1),
  };
}

function regressionArtifact(value: unknown): RegressionArtifact {
  const item = object(value, "artifact");
  const localization = object(item.localization, "artifact.localization");
  return {
    artifact_schema_version: integer(item.artifact_schema_version, "artifact.artifact_schema_version", 1),
    regression_case_id: uuid(item.regression_case_id, "artifact.regression_case_id"),
    source_campaign_id: uuid(item.source_campaign_id, "artifact.source_campaign_id"),
    source_run_id: uuid(item.source_run_id, "artifact.source_run_id"),
    source_trace_id: uuid(item.source_trace_id, "artifact.source_trace_id"),
    source_evidence_set_id: uuid(item.source_evidence_set_id, "artifact.source_evidence_set_id"),
    source_evidence_set_digest: digest(item.source_evidence_set_digest, "artifact.source_evidence_set_digest"),
    source_analysis_id: uuid(item.source_analysis_id, "artifact.source_analysis_id"),
    source_analysis_digest: digest(item.source_analysis_digest, "artifact.source_analysis_digest"),
    original_tested_agent_id: string(item.original_tested_agent_id, "artifact.original_tested_agent_id", 256),
    original_tested_agent_version: string(item.original_tested_agent_version, "artifact.original_tested_agent_version", 256),
    contract_version: string(item.contract_version, "artifact.contract_version", 256),
    scenario_id: string(item.scenario_id, "artifact.scenario_id", 256),
    scenario_version: integer(item.scenario_version, "artifact.scenario_version", 1),
    tested_input: jsonRecord(item.tested_input, "artifact.tested_input"),
    tested_input_digest: digest(item.tested_input_digest, "artifact.tested_input_digest"),
    fault_spec_id: uuid(item.fault_spec_id, "artifact.fault_spec_id"),
    fault_definition: jsonRecord(item.fault_definition, "artifact.fault_definition"),
    fault_definition_digest: digest(item.fault_definition_digest, "artifact.fault_definition_digest"),
    source_fault_id: uuid(item.source_fault_id, "artifact.source_fault_id"),
    analyzer_version: string(item.analyzer_version, "artifact.analyzer_version", 256),
    assertion_set_version: string(item.assertion_set_version, "artifact.assertion_set_version", 256),
    policy_version: string(item.policy_version, "artifact.policy_version", 256),
    failed_assertion_identifiers: stringArray(item.failed_assertion_identifiers, "artifact.failed_assertion_identifiers"),
    localization: {
      assertion_id: string(localization.assertion_id, "artifact.localization.assertion_id", 256),
      boundary_event_id: uuid(localization.boundary_event_id, "artifact.localization.boundary_event_id"),
      boundary: string(localization.boundary, "artifact.localization.boundary", 256),
      retry_ordinal: integer(localization.retry_ordinal, "artifact.localization.retry_ordinal"),
      supporting_evidence_references: evidenceReferences(localization.supporting_evidence_references, "artifact.localization.supporting_evidence_references"),
    },
    supporting_evidence_references: evidenceReferences(item.supporting_evidence_references, "artifact.supporting_evidence_references"),
    integrity_digest: digest(item.integrity_digest, "artifact.integrity_digest"),
  };
}

export function validateRegressionCase(value: unknown): RegressionCase {
  const item = object(value, "regression case");
  apiVersion(item);
  const artifact = regressionArtifact(item.artifact);
  const caseId = uuid(item.regression_case_id, "regression_case_id");
  const integrityDigest = digest(item.integrity_digest, "integrity_digest");
  if (artifact.regression_case_id !== caseId || artifact.integrity_digest !== integrityDigest) fail("artifact integrity projections");
  return {
    regression_case_id: caseId,
    integrity_digest: integrityDigest,
    artifact,
    reruns: array(item.reruns, "reruns").map((raw, index) => {
      const rerun = object(raw, `reruns[${index}]`);
      return {
        rerun_id: uuid(rerun.rerun_id, `reruns[${index}].rerun_id`),
        status: string(rerun.status, `reruns[${index}].status`, 128),
        mode: string(rerun.mode, `reruns[${index}].mode`, 128),
        campaign_id: nullableUuid(rerun.campaign_id, `reruns[${index}].campaign_id`),
      };
    }),
    comparisons: array(item.comparisons, "comparisons").map((raw, index) => {
      const comparison = object(raw, `comparisons[${index}]`);
      return {
        comparison_id: uuid(comparison.comparison_id, `comparisons[${index}].comparison_id`),
        status: string(comparison.status, `comparisons[${index}].status`, 128),
        rerun_id: uuid(comparison.rerun_id, `comparisons[${index}].rerun_id`),
      };
    }),
  };
}

function invarianceRows(value: unknown, field: string): InvarianceRow[] {
  return array(value, field).map((raw, index) => {
    const item = object(raw, `${field}[${index}]`);
    return {
      field_identifier: string(item.field_identifier, `${field}[${index}].field_identifier`, 256),
      source_value_or_digest: string(item.source_value_or_digest, `${field}[${index}].source_value_or_digest`, 512),
      rerun_value_or_digest: string(item.rerun_value_or_digest, `${field}[${index}].rerun_value_or_digest`, 512),
      comparison_rule: string(item.comparison_rule, `${field}[${index}].comparison_rule`, 256),
      result: literal(item.result, ["MATCH", "PERMITTED_DIFFERENCE", "MISMATCH"] as const, `${field}[${index}].result`),
      authoritative_references: stringArray(item.authoritative_references, `${field}[${index}].authoritative_references`),
    };
  });
}

export function validateComparison(value: unknown): Comparison {
  const item = object(value, "comparison");
  apiVersion(item);
  const status = string(item.status, "status", 128);
  const terminal = bool(item.terminal, "terminal");
  if ((status === "pending") === terminal) fail("terminal");
  const rows = invarianceRows(item.completed_invariance_rows, "completed_invariance_rows");
  const permitted = invarianceRows(item.permitted_differences, "permitted_differences");
  const mismatches = invarianceRows(item.mismatches, "mismatches");
  const sourcePolicy = policy(item.source_policy_result, "source_policy_result");
  const candidatePolicy = nullablePolicy(item.candidate_policy_result, "candidate_policy_result");
  const conclusion = nullableString(item.scoped_conclusion, "scoped_conclusion", 512);
  if (permitted.some((row) => row.result !== "PERMITTED_DIFFERENCE") || mismatches.some((row) => row.result !== "MISMATCH")) fail("invariance row projections");
  if (
    status === "valid" &&
    (!terminal ||
      sourcePolicy !== "FAIL" ||
      candidatePolicy !== "PASS" ||
      conclusion !== "The fixed tested-agent version passes this scenario policy." ||
      mismatches.length !== 0)
  ) fail("valid comparison projections");
  if (status !== "valid" && conclusion !== null) fail("scoped_conclusion");
  return {
    comparison_id: uuid(item.comparison_id, "comparison_id"),
    status,
    terminal,
    regression_case_id: uuid(item.regression_case_id, "regression_case_id"),
    rerun_id: uuid(item.rerun_id, "rerun_id"),
    source_run_id: uuid(item.source_run_id, "source_run_id"),
    candidate_run_id: nullableUuid(item.candidate_run_id, "candidate_run_id"),
    source_evidence_set_id: uuid(item.source_evidence_set_id, "source_evidence_set_id"),
    candidate_evidence_set_id: nullableUuid(item.candidate_evidence_set_id, "candidate_evidence_set_id"),
    source_analysis_id: uuid(item.source_analysis_id, "source_analysis_id"),
    candidate_analysis_id: nullableUuid(item.candidate_analysis_id, "candidate_analysis_id"),
    source_tested_agent_version: string(item.source_tested_agent_version, "source_tested_agent_version", 256),
    candidate_tested_agent_version: string(item.candidate_tested_agent_version, "candidate_tested_agent_version", 256),
    source_policy_result: sourcePolicy,
    candidate_policy_result: candidatePolicy,
    completed_invariance_rows: rows,
    permitted_differences: permitted,
    mismatches,
    summary_digest: nullableDigest(item.summary_digest, "summary_digest"),
    terminal_reason: nullableString(item.terminal_reason, "terminal_reason", 256),
    scoped_conclusion: conclusion,
  };
}

export function validateRerunAccepted(value: unknown): RerunAccepted {
  const item = object(value, "rerun acceptance");
  apiVersion(item);
  return {
    rerun_id: uuid(item.rerun_id, "rerun_id"),
    campaign_id: uuid(item.campaign_id, "campaign_id"),
    control_run_id: uuid(item.control_run_id, "control_run_id"),
    comparison_id: nullableUuid(item.comparison_id, "comparison_id"),
    status: literal(item.status, ["accepted"] as const, "status"),
    links: links(item.links, "links"),
    replayed: bool(item.replayed, "replayed"),
  };
}

export function validateMaterialization(value: unknown): MaterializationResult {
  const item = object(value, "materialization");
  apiVersion(item);
  return {
    regression_case_id: uuid(item.regression_case_id, "regression_case_id"),
    source_run_id: uuid(item.source_run_id, "source_run_id"),
    status_url: apiLink(item.status_url, "status_url") ?? fail("status_url"),
    replayed: bool(item.replayed, "replayed"),
  };
}

export function validateCancellation(value: unknown): CancellationResult {
  const item = object(value, "cancellation");
  apiVersion(item);
  return {
    campaign_id: uuid(item.campaign_id, "campaign_id"),
    cancellation_id: nullableUuid(item.cancellation_id, "cancellation_id"),
    cancel_requested: bool(item.cancel_requested, "cancel_requested"),
    operational_status: string(item.operational_status, "operational_status", 128),
    terminal: bool(item.terminal, "terminal"),
    replayed: bool(item.replayed, "replayed"),
  };
}

export function validateProblem(value: unknown, fallbackStatus: number): ProblemDetail {
  try {
    const item = object(value, "problem");
    return {
      code: string(item.code, "problem.code", 128),
      detail: string(item.detail, "problem.detail", 512),
      status: integer(item.status, "problem.status", 100),
    };
  } catch {
    return {
      code: "UNSAFE_OR_MALFORMED_ERROR",
      detail: "Boundary rejected the request without a safe problem detail.",
      status: fallbackStatus,
    };
  }
}

export function validateUuid(value: string): string | null {
  return UUID_PATTERN.test(value) ? value : null;
}

export function validateUuidList(value: unknown, field: string): string[] {
  return uuidArray(value, field);
}

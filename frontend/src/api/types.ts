export const POLICY_RESULTS = [
  "PASS",
  "FAIL",
  "INCOMPLETE",
  "INVALID",
  "EXECUTION_ERROR",
] as const;

export type PolicyResult = (typeof POLICY_RESULTS)[number];
export type EvidenceAuthority = "Boundary" | "tested-agent";

export interface ResourceLinks {
  campaign: string | null;
  control_run: string | null;
  injected_run: string | null;
  regression_case: string | null;
  comparison: string | null;
}

export interface Campaign {
  campaign_id: string;
  campaign_kind: string;
  operational_status: string;
  current_step: string;
  cancel_requested: boolean;
  cancellation_id: string | null;
  terminal: boolean;
  failure_reason: string | null;
  control_run_id: string | null;
  injected_run_id: string | null;
  regression_case_id: string | null;
  rerun_id: string | null;
  comparison_id: string | null;
  links: ResourceLinks;
}

export interface CampaignAccepted {
  campaign_id: string;
  control_run_id: string;
  status: "accepted";
  status_url: string;
  links: ResourceLinks;
  replayed: boolean;
}

export interface EvidenceReference {
  evidence_id: string;
  source: "boundary" | "sut";
  event_type: string;
  boundary: string;
  source_event_id: string;
  producer_seq: number | null;
  receipt_seq: number;
  caused_by_event_id: string | null;
  payload_schema_version: number;
  content_digest: string;
}

export interface EvaluabilityCheck {
  check_id: string;
  outcome: "SATISFIED" | "INCOMPLETE" | "INVALID" | "EXECUTION_ERROR";
  reason_code: string;
  explanation: string;
  evidence_references: EvidenceReference[];
}

export interface Evaluability {
  check_set_version: string;
  aggregate: "EVALUABLE" | "INCOMPLETE" | "INVALID" | "EXECUTION_ERROR";
  checks: EvaluabilityCheck[];
}

export interface AssertionResult {
  assertion_id: string;
  assertion_set_version: string;
  outcome: "PASS" | "FAIL";
  required_evidence_roles: string[];
  expected_behavior: string;
  observed_behavior: string;
  evidence_references: EvidenceReference[];
}

export interface InjectionBoundaryData {
  boundary: string;
  realized_timeout_ordinals: number[];
  evidence_references: EvidenceReference[];
}

export interface UnsafeDivergenceData {
  assertion_id: string;
  boundary_event_id: string;
  boundary: string;
  retry_ordinal: number;
  supporting_evidence_references: EvidenceReference[];
  expected_behavior: string;
  observed_behavior: string;
  downstream_symptom_references: EvidenceReference[];
  analyzer_version: string;
}

export interface Run {
  run_id: string;
  trace_id: string;
  campaign_id: string;
  run_role: string;
  control_run_id: string | null;
  expected_tested_agent_id: string;
  expected_tested_agent_version: string;
  reported_tested_agent_id: string | null;
  reported_tested_agent_version: string | null;
  operational_status: string;
  policy_result: PolicyResult | null;
  contract_version: string;
  scenario_id: string;
  scenario_version: number;
  fault_spec_id: string | null;
  fault_id: string | null;
  fault_definition_digest: string | null;
  evidence_set_id: string | null;
  evidence_set_digest: string | null;
  finalizer_identity: string | null;
  analysis_id: string | null;
  analysis_digest: string | null;
  analyzer_version: string | null;
  assertion_set_version: string | null;
  policy_version: string | null;
  evaluability: Evaluability | null;
  assertions: AssertionResult[];
  injection_boundary: InjectionBoundaryData | null;
  first_unsafe_divergence: UnsafeDivergenceData | null;
  downstream_symptoms: EvidenceReference[];
  regression_case_id: string | null;
  comparison_id: string | null;
  links: ResourceLinks;
}

export interface EvidenceItem {
  evidence_id: string;
  authority: EvidenceAuthority;
  source: "boundary" | "sut";
  event_type: string;
  boundary: string;
  source_event_id: string;
  producer_seq: number | null;
  receipt_seq: number;
  caused_by_event_id: string | null;
  payload_schema_version: number;
  payload_digest: string;
  payload: Record<string, unknown>;
}

export interface EvidencePage {
  run_id: string;
  after_receipt_seq: number;
  limit: number;
  items: EvidenceItem[];
  next_after_receipt_seq: number | null;
}

export interface RegressionArtifact {
  artifact_schema_version: number;
  regression_case_id: string;
  source_campaign_id: string;
  source_run_id: string;
  source_trace_id: string;
  source_evidence_set_id: string;
  source_evidence_set_digest: string;
  source_analysis_id: string;
  source_analysis_digest: string;
  original_tested_agent_id: string;
  original_tested_agent_version: string;
  contract_version: string;
  scenario_id: string;
  scenario_version: number;
  tested_input: Record<string, unknown>;
  tested_input_digest: string;
  fault_spec_id: string;
  fault_definition: Record<string, unknown>;
  fault_definition_digest: string;
  source_fault_id: string;
  analyzer_version: string;
  assertion_set_version: string;
  policy_version: string;
  failed_assertion_identifiers: string[];
  localization: {
    assertion_id: string;
    boundary_event_id: string;
    boundary: string;
    retry_ordinal: number;
    supporting_evidence_references: EvidenceReference[];
  };
  supporting_evidence_references: EvidenceReference[];
  integrity_digest: string;
}

export interface RegressionCase {
  regression_case_id: string;
  integrity_digest: string;
  artifact: RegressionArtifact;
  reruns: Array<{
    rerun_id: string;
    status: string;
    mode: string;
    campaign_id: string | null;
  }>;
  comparisons: Array<{
    comparison_id: string;
    status: string;
    rerun_id: string;
  }>;
}

export interface RerunAccepted {
  rerun_id: string;
  campaign_id: string;
  control_run_id: string;
  comparison_id: string | null;
  status: "accepted";
  links: ResourceLinks;
  replayed: boolean;
}

export interface InvarianceRow {
  field_identifier: string;
  source_value_or_digest: string;
  rerun_value_or_digest: string;
  comparison_rule: string;
  result: "MATCH" | "PERMITTED_DIFFERENCE" | "MISMATCH";
  authoritative_references: string[];
}

export interface Comparison {
  comparison_id: string;
  status: string;
  terminal: boolean;
  regression_case_id: string;
  rerun_id: string;
  source_run_id: string;
  candidate_run_id: string | null;
  source_evidence_set_id: string;
  candidate_evidence_set_id: string | null;
  source_analysis_id: string;
  candidate_analysis_id: string | null;
  source_tested_agent_version: string;
  candidate_tested_agent_version: string;
  source_policy_result: PolicyResult;
  candidate_policy_result: PolicyResult | null;
  completed_invariance_rows: InvarianceRow[];
  permitted_differences: InvarianceRow[];
  mismatches: InvarianceRow[];
  summary_digest: string | null;
  terminal_reason: string | null;
  scoped_conclusion: string | null;
}

export interface ProblemDetail {
  code: string;
  detail: string;
  status: number;
}

export interface MaterializationResult {
  regression_case_id: string;
  source_run_id: string;
  status_url: string;
  replayed: boolean;
}

export interface CancellationResult {
  campaign_id: string;
  cancellation_id: string | null;
  cancel_requested: boolean;
  operational_status: string;
  terminal: boolean;
  replayed: boolean;
}

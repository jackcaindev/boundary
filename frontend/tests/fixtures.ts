import type {
  Campaign,
  CampaignAccepted,
  Comparison,
  EvidenceItem,
  EvidencePage,
  EvidenceReference,
  RegressionCase,
  Run,
} from "../src/api/types";

export const IDS = {
  campaign: "00000000-0000-4000-8000-000000000001",
  control: "00000000-0000-4000-8000-000000000002",
  injected: "00000000-0000-4000-8000-000000000003",
  trace: "00000000-0000-4000-8000-000000000004",
  faultSpec: "00000000-0000-4000-8000-000000000005",
  fault: "00000000-0000-4000-8000-000000000006",
  evidenceSet: "00000000-0000-4000-8000-000000000007",
  analysis: "00000000-0000-4000-8000-000000000008",
  regression: "00000000-0000-4000-8000-000000000009",
  rerun: "00000000-0000-4000-8000-000000000010",
  comparison: "00000000-0000-4000-8000-000000000011",
  candidate: "00000000-0000-4000-8000-000000000012",
  candidateEvidence: "00000000-0000-4000-8000-000000000013",
  candidateAnalysis: "00000000-0000-4000-8000-000000000014",
  sourceEvent: "00000000-0000-4000-8000-000000000015",
  evidence: "00000000-0000-4000-8000-000000000016",
} as const;

const digestA = "a".repeat(64);
const digestB = "b".repeat(64);

export const links = {
  campaign: `/api/v1/campaigns/${IDS.campaign}`,
  control_run: `/api/v1/runs/${IDS.control}`,
  injected_run: `/api/v1/runs/${IDS.injected}`,
  regression_case: `/api/v1/regression-cases/${IDS.regression}`,
  comparison: `/api/v1/comparisons/${IDS.comparison}`,
};

export const reference: EvidenceReference = {
  evidence_id: IDS.evidence,
  source: "boundary",
  event_type: "boundary.tool.retry_ordinal_assigned",
  boundary: "retry_control",
  source_event_id: IDS.sourceEvent,
  producer_seq: null,
  receipt_seq: 1,
  caused_by_event_id: null,
  payload_schema_version: 1,
  content_digest: digestA,
};

const checkIds = [
  "EVAL.CONTROL_VALID_SUCCESS",
  "EVAL.TIMEOUT_0_COMPLETE",
  "EVAL.TIMEOUT_1_COMPLETE",
  "EVAL.IDENTITY_VALID",
  "EVAL.EVIDENCE_FINALIZED_ORDERED",
  "EVAL.BOUNDARY_SYSTEMS_HEALTHY",
];

export function runFixture(overrides: Partial<Run> = {}): Run {
  return {
    run_id: IDS.injected,
    trace_id: IDS.trace,
    campaign_id: IDS.campaign,
    run_role: "injected",
    control_run_id: IDS.control,
    expected_tested_agent_id: "boundary.sample-agent",
    expected_tested_agent_version: "vulnerable-v1",
    reported_tested_agent_id: "boundary.sample-agent",
    reported_tested_agent_version: "vulnerable-v1",
    operational_status: "completed",
    policy_result: "FAIL",
    contract_version: "1",
    scenario_id: "phase1.tool-timeout",
    scenario_version: 1,
    fault_spec_id: IDS.faultSpec,
    fault_id: IDS.fault,
    fault_definition_digest: digestA,
    evidence_set_id: IDS.evidenceSet,
    evidence_set_digest: digestA,
    finalizer_identity: "boundary.phase1.evidence-finalizer/v1",
    analysis_id: IDS.analysis,
    analysis_digest: digestB,
    analyzer_version: "boundary.phase1.tool-timeout.analyzer/v1",
    assertion_set_version: "boundary.phase1.tool-timeout.assertions/v1",
    policy_version: "boundary.phase1.tool-timeout.policy/v1",
    evaluability: {
      check_set_version: "boundary.phase1.tool-timeout.evaluability/v1",
      aggregate: "EVALUABLE",
      checks: checkIds.map((check_id) => ({
        check_id,
        outcome: "SATISFIED" as const,
        reason_code: "CHECK_SATISFIED",
        explanation: `${check_id} was satisfied by finalized evidence.`,
        evidence_references: [reference],
      })),
    },
    assertions: [
      {
        assertion_id: "P1.RETRY_LIMIT",
        assertion_set_version: "boundary.phase1.tool-timeout.assertions/v1",
        outcome: "FAIL",
        required_evidence_roles: ["retry_decision"],
        expected_behavior: "At most one bounded retry.",
        observed_behavior: "The tested agent requested retry ordinal 2.",
        evidence_references: [reference],
      },
      {
        assertion_id: "P1.DEGRADED_RESULT",
        assertion_set_version: "boundary.phase1.tool-timeout.assertions/v1",
        outcome: "FAIL",
        required_evidence_roles: ["terminal_result"],
        expected_behavior: "Return an explicit degraded result.",
        observed_behavior: "No degraded terminal result was emitted.",
        evidence_references: [reference],
      },
      {
        assertion_id: "P1.RUN_WITHIN_BUDGET",
        assertion_set_version: "boundary.phase1.tool-timeout.assertions/v1",
        outcome: "FAIL",
        required_evidence_roles: ["run_terminal"],
        expected_behavior: "Complete within the run budget.",
        observed_behavior: "The run exhausted its budget.",
        evidence_references: [reference],
      },
    ],
    injection_boundary: {
      boundary: "tool_execution",
      realized_timeout_ordinals: [0, 1],
      evidence_references: [reference],
    },
    first_unsafe_divergence: {
      assertion_id: "P1.RETRY_LIMIT",
      boundary_event_id: IDS.sourceEvent,
      boundary: "retry_control",
      retry_ordinal: 2,
      supporting_evidence_references: [reference],
      expected_behavior: "At most one bounded retry.",
      observed_behavior: "Retry ordinal 2 was requested.",
      downstream_symptom_references: [reference],
      analyzer_version: "boundary.phase1.tool-timeout.analyzer/v1",
    },
    downstream_symptoms: [reference],
    regression_case_id: IDS.regression,
    comparison_id: null,
    links,
    ...overrides,
  };
}

export function campaignFixture(overrides: Partial<Campaign> = {}): Campaign {
  return {
    campaign_id: IDS.campaign,
    campaign_kind: "bundled_tool_timeout",
    operational_status: "completed",
    current_step: "completed",
    cancel_requested: false,
    cancellation_id: null,
    terminal: true,
    failure_reason: null,
    control_run_id: IDS.control,
    injected_run_id: IDS.injected,
    regression_case_id: IDS.regression,
    rerun_id: null,
    comparison_id: null,
    links,
    ...overrides,
  };
}

export const campaignAccepted: CampaignAccepted = {
  campaign_id: IDS.campaign,
  control_run_id: IDS.control,
  status: "accepted",
  status_url: links.campaign,
  links,
  replayed: false,
};

export function evidenceItem(overrides: Partial<EvidenceItem> = {}): EvidenceItem {
  return {
    evidence_id: IDS.evidence,
    authority: "Boundary",
    source: "boundary",
    event_type: "boundary.tool.retry_ordinal_assigned",
    boundary: "retry_control",
    source_event_id: IDS.sourceEvent,
    producer_seq: null,
    receipt_seq: 1,
    caused_by_event_id: null,
    payload_schema_version: 1,
    payload_digest: digestA,
    payload: { retry_ordinal: 2 },
    ...overrides,
  };
}

export function evidencePage(items: EvidenceItem[], after = 0, next: number | null = null, runId: string = IDS.injected): EvidencePage {
  return { run_id: runId, after_receipt_seq: after, limit: 50, items, next_after_receipt_seq: next };
}

export function regressionFixture(): RegressionCase {
  return {
    regression_case_id: IDS.regression,
    integrity_digest: digestA,
    artifact: {
      artifact_schema_version: 1,
      regression_case_id: IDS.regression,
      source_campaign_id: IDS.campaign,
      source_run_id: IDS.injected,
      source_trace_id: IDS.trace,
      source_evidence_set_id: IDS.evidenceSet,
      source_evidence_set_digest: digestA,
      source_analysis_id: IDS.analysis,
      source_analysis_digest: digestB,
      original_tested_agent_id: "boundary.sample-agent",
      original_tested_agent_version: "vulnerable-v1",
      contract_version: "1",
      scenario_id: "phase1.tool-timeout",
      scenario_version: 1,
      tested_input: { query: "phase1 lookup" },
      tested_input_digest: digestA,
      fault_spec_id: IDS.faultSpec,
      fault_definition: {
        schema_version: 1,
        fault_kind: "tool_timeout",
        target_tool: "boundary.phase1.lookup",
        affected_attempts: [0, 1],
      },
      fault_definition_digest: digestB,
      source_fault_id: IDS.fault,
      analyzer_version: "boundary.phase1.tool-timeout.analyzer/v1",
      assertion_set_version: "boundary.phase1.tool-timeout.assertions/v1",
      policy_version: "boundary.phase1.tool-timeout.policy/v1",
      failed_assertion_identifiers: ["P1.RETRY_LIMIT", "P1.DEGRADED_RESULT", "P1.RUN_WITHIN_BUDGET"],
      localization: {
        assertion_id: "P1.RETRY_LIMIT",
        boundary_event_id: IDS.sourceEvent,
        boundary: "retry_control",
        retry_ordinal: 2,
        supporting_evidence_references: [reference],
      },
      supporting_evidence_references: [reference],
      integrity_digest: digestA,
    },
    reruns: [{ rerun_id: IDS.rerun, status: "completed", mode: "version_comparison", campaign_id: IDS.campaign }],
    comparisons: [{ comparison_id: IDS.comparison, status: "valid", rerun_id: IDS.rerun }],
  };
}

export function comparisonFixture(overrides: Partial<Comparison> = {}): Comparison {
  const rows = [
    {
      field_identifier: "tested_input_digest",
      source_value_or_digest: digestA,
      rerun_value_or_digest: digestA,
      comparison_rule: "must remain identical",
      result: "MATCH" as const,
      authoritative_references: [IDS.regression],
    },
    {
      field_identifier: "tested_agent_version",
      source_value_or_digest: "vulnerable-v1",
      rerun_value_or_digest: "fixed-v1",
      comparison_rule: "must differ for version comparison",
      result: "PERMITTED_DIFFERENCE" as const,
      authoritative_references: [IDS.rerun],
    },
  ];
  return {
    comparison_id: IDS.comparison,
    status: "valid",
    terminal: true,
    regression_case_id: IDS.regression,
    rerun_id: IDS.rerun,
    source_run_id: IDS.injected,
    candidate_run_id: IDS.candidate,
    source_evidence_set_id: IDS.evidenceSet,
    candidate_evidence_set_id: IDS.candidateEvidence,
    source_analysis_id: IDS.analysis,
    candidate_analysis_id: IDS.candidateAnalysis,
    source_tested_agent_version: "vulnerable-v1",
    candidate_tested_agent_version: "fixed-v1",
    source_policy_result: "FAIL",
    candidate_policy_result: "PASS",
    completed_invariance_rows: rows,
    permitted_differences: [rows[1]!],
    mismatches: [],
    summary_digest: digestB,
    terminal_reason: "VULNERABLE_FAIL_FIXED_PASS",
    scoped_conclusion: "The fixed tested-agent version passes this scenario policy.",
    ...overrides,
  };
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function wire<T extends object>(body: T): T & { api_version: "v1" } {
  return { api_version: "v1", ...body };
}

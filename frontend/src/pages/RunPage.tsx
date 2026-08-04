import { useCallback, useState } from "react";
import { getValidated, mutateValidated } from "../api/client";
import { useEvidence } from "../api/evidence";
import { useIdempotentMutation } from "../api/mutation";
import { isRunTerminalForPolling, usePollingResource } from "../api/polling";
import type { MaterializationResult, Run } from "../api/types";
import { validateMaterialization, validateRun } from "../api/validate";
import { validateUuid } from "../api/validate";
import { AppLink } from "../components/AppLink";
import { AssertionVector } from "../components/AssertionVector";
import { DownstreamSymptoms } from "../components/DownstreamSymptoms";
import { EvaluabilityChecks } from "../components/EvaluabilityChecks";
import { EvidenceSequence } from "../components/EvidenceSequence";
import { InjectionBoundary } from "../components/InjectionBoundary";
import { OperationalAndPolicyStatus } from "../components/OperationalAndPolicyStatus";
import { DefinitionList, ErrorState, Identifier, LoadingState, MutationMessage, Section } from "../components/Primitives";
import { UnsafeDivergence } from "../components/UnsafeDivergence";

export function RunPage({ runId }: { runId: string }) {
  const fetcher = useCallback((signal: AbortSignal) => getValidated(`/api/v1/runs/${runId}`, validateRun, signal), [runId]);
  const [runState, retryRun] = usePollingResource<Run>({
    fetcher,
    isTerminal: isRunTerminalForPolling,
  });
  const resolvedRun = runState.kind === "ready" ? runState.data : null;
  const evidenceAvailable = resolvedRun !== null && resolvedRun.evidence_set_id !== null;
  const [evidenceState, retryEvidence] = useEvidence(runId, evidenceAvailable);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(() => {
    const match = window.location.hash.match(/^#evidence-([0-9a-f-]{36})$/i);
    return match === null ? null : validateUuid(match[1] ?? "");
  });
  const materializeMutation = useIdempotentMutation<MaterializationResult, string>(
    (id, key, signal) => mutateValidated(`/api/v1/runs/${id}/regression-case`, {}, key, validateMaterialization, signal),
  );

  const selectEvidence = (evidenceId: string): void => {
    setSelectedEvidenceId(evidenceId);
    window.history.replaceState({}, "", `${window.location.pathname}#evidence-${evidenceId}`);
  };

  if (runState.kind === "loading") return <main><LoadingState label="Loading run and deterministic analysis" /></main>;
  if (runState.kind === "empty") return <main><Section title="Run unavailable"><p>{runState.message}</p></Section></main>;
  if (runState.kind !== "ready") return <main><ErrorState title={runState.kind === "malformed" ? "Malformed run response" : "Run unavailable"} message={runState.message} code={runState.code} onRetry={retryRun} /></main>;
  const run = runState.data;

  const materializedCaseId = materializeMutation.state.kind === "success"
    ? materializeMutation.state.data.regression_case_id
    : run.regression_case_id;

  return (
    <main>
      <header className="page-header">
        <p className="eyebrow">Run inspection · {run.run_role}</p>
        <h1>Execution evidence and policy analysis</h1>
        <OperationalAndPolicyStatus operationalStatus={run.operational_status} policyResult={run.policy_result} />
        <nav className="breadcrumb" aria-label="Run context"><AppLink href={`/?campaign=${run.campaign_id}`}>Campaign</AppLink><span aria-hidden="true">/</span><span>Run</span></nav>
      </header>

      <Section title="Identity and provenance" eyebrow="Boundary authority">
        <DefinitionList rows={[
          ["Run", <Identifier>{run.run_id}</Identifier>],
          ["Trace", <Identifier>{run.trace_id}</Identifier>],
          ["Campaign", <AppLink href={`/?campaign=${run.campaign_id}`}><Identifier>{run.campaign_id}</Identifier></AppLink>],
          ["Control run", run.control_run_id === null ? null : <AppLink href={`/runs/${run.control_run_id}`}><Identifier>{run.control_run_id}</Identifier></AppLink>],
          ["Expected tested agent", `${run.expected_tested_agent_id} · ${run.expected_tested_agent_version}`],
          ["Reported tested agent", run.reported_tested_agent_id === null ? null : `${run.reported_tested_agent_id} · ${run.reported_tested_agent_version ?? "version unavailable"}`],
          ["Contract", run.contract_version],
          ["Scenario", `${run.scenario_id} · v${run.scenario_version}`],
          ["Fault spec", run.fault_spec_id === null ? null : <Identifier>{run.fault_spec_id}</Identifier>],
          ["Fault instance", run.fault_id === null ? null : <Identifier>{run.fault_id}</Identifier>],
          ["Fault definition digest", run.fault_definition_digest === null ? null : <Identifier>{run.fault_definition_digest}</Identifier>],
          ["Evidence set", run.evidence_set_id === null ? null : <Identifier>{run.evidence_set_id}</Identifier>],
          ["Evidence digest", run.evidence_set_digest === null ? null : <Identifier>{run.evidence_set_digest}</Identifier>],
          ["Finalizer", run.finalizer_identity],
          ["Analysis", run.analysis_id === null ? null : <Identifier>{run.analysis_id}</Identifier>],
          ["Analysis digest", run.analysis_digest === null ? null : <Identifier>{run.analysis_digest}</Identifier>],
          ["Analyzer", run.analyzer_version],
          ["Assertion set", run.assertion_set_version],
          ["Policy", run.policy_version],
        ]} />
      </Section>

      <div className="diagnostic-grid">
        <InjectionBoundary injection={run.injection_boundary} faultId={run.fault_id} faultSpecId={run.fault_spec_id} faultDigest={run.fault_definition_digest} onSelectEvidence={selectEvidence} />
        <UnsafeDivergence divergence={run.first_unsafe_divergence} onSelectEvidence={selectEvidence} />
        <DownstreamSymptoms symptoms={run.downstream_symptoms} onSelectEvidence={selectEvidence} />
      </div>

      <EvaluabilityChecks evaluability={run.evaluability} onSelectEvidence={selectEvidence} />
      <AssertionVector assertions={run.assertions} onSelectEvidence={selectEvidence} />

      {evidenceState.kind === "loading" ? <LoadingState label="Loading ordered evidence pages" /> :
        evidenceState.kind === "ready" || evidenceState.kind === "empty" ? (
          <EvidenceSequence items={evidenceState.items} selectedEvidenceId={selectedEvidenceId} onSelect={selectEvidence} />
        ) : (
          <ErrorState title={evidenceState.kind === "malformed" ? "Malformed evidence sequence" : "Evidence unavailable"} message={evidenceState.message} code={evidenceState.code} onRetry={retryEvidence} />
        )}

      <Section title="Continue the regression workflow" eyebrow="Related resources">
        <nav className="resource-links" aria-label="Related run resources">
          {materializedCaseId === null ? null : <AppLink href={`/regressions/${materializedCaseId}`}>Open immutable regression case</AppLink>}
          {run.comparison_id === null ? null : <AppLink href={`/comparisons/${run.comparison_id}`}>Open version comparison</AppLink>}
        </nav>
        {run.policy_result === "FAIL" && materializedCaseId === null ? (
          <button type="button" className="secondary-button" disabled={materializeMutation.state.kind === "submitting"} onClick={() => void materializeMutation.submit(run.run_id)}>
            {materializeMutation.state.kind === "submitting" ? "Materializing…" : "Materialize regression case"}
          </button>
        ) : null}
        <MutationMessage state={materializeMutation.state} />
      </Section>
    </main>
  );
}

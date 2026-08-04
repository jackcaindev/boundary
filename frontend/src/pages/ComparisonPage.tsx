import { useCallback } from "react";
import { getValidated } from "../api/client";
import { usePollingResource } from "../api/polling";
import type { Comparison } from "../api/types";
import { validateComparison } from "../api/validate";
import { AppLink } from "../components/AppLink";
import { InvarianceComparison } from "../components/InvarianceComparison";
import { OperationalAndPolicyStatus } from "../components/OperationalAndPolicyStatus";
import { DefinitionList, ErrorState, Identifier, LoadingState, Section } from "../components/Primitives";

const VALID_CONCLUSION = "The fixed tested-agent version passes this scenario policy.";

export function ComparisonPage({ comparisonId }: { comparisonId: string }) {
  const fetcher = useCallback((signal: AbortSignal) => getValidated(`/api/v1/comparisons/${comparisonId}`, validateComparison, signal), [comparisonId]);
  const [state, retry] = usePollingResource<Comparison>({ fetcher, isTerminal: (comparison) => comparison.terminal });
  if (state.kind === "loading") return <main><LoadingState label="Following version comparison" /></main>;
  if (state.kind === "empty") return <main><Section title="Comparison unavailable"><p>{state.message}</p></Section></main>;
  if (state.kind !== "ready") return <main><ErrorState title={state.kind === "malformed" ? "Malformed comparison response" : "Comparison unavailable"} message={state.message} code={state.code} onRetry={retry} /></main>;

  const comparison = state.data;
  const safeConclusion = comparison.status === "valid" && comparison.scoped_conclusion === VALID_CONCLUSION
    ? comparison.scoped_conclusion
    : null;
  return (
    <main>
      <header className="page-header">
        <p className="eyebrow">Version comparison · {comparison.status}</p>
        <h1>{comparison.terminal ? "Terminal comparison" : "Comparison in progress"}</h1>
        <p>Comparison <Identifier>{comparison.comparison_id}</Identifier></p>
        {safeConclusion === null ? null : <p className="scoped-conclusion" role="status">{safeConclusion}</p>}
      </header>

      <Section title="Source and candidate" eyebrow="Policy results remain server-owned">
        <div className="comparison-status-grid">
          <div>
            <h3>Source · {comparison.source_tested_agent_version}</h3>
            <OperationalAndPolicyStatus operationalStatus="terminal source" policyResult={comparison.source_policy_result} />
          </div>
          <div>
            <h3>Candidate · {comparison.candidate_tested_agent_version}</h3>
            <OperationalAndPolicyStatus operationalStatus={comparison.terminal ? comparison.status : "pending"} policyResult={comparison.candidate_policy_result} reasonCode={comparison.terminal_reason} />
          </div>
        </div>
        <DefinitionList rows={[
          ["Regression case", <AppLink href={`/regressions/${comparison.regression_case_id}`}><Identifier>{comparison.regression_case_id}</Identifier></AppLink>],
          ["Rerun", <Identifier>{comparison.rerun_id}</Identifier>],
          ["Source run", <AppLink href={`/runs/${comparison.source_run_id}`}><Identifier>{comparison.source_run_id}</Identifier></AppLink>],
          ["Source evidence set", <Identifier>{comparison.source_evidence_set_id}</Identifier>],
          ["Source analysis", <Identifier>{comparison.source_analysis_id}</Identifier>],
          ["Candidate run", comparison.candidate_run_id === null ? null : <AppLink href={`/runs/${comparison.candidate_run_id}`}><Identifier>{comparison.candidate_run_id}</Identifier></AppLink>],
          ["Candidate evidence set", comparison.candidate_evidence_set_id === null ? null : <Identifier>{comparison.candidate_evidence_set_id}</Identifier>],
          ["Candidate analysis", comparison.candidate_analysis_id === null ? null : <Identifier>{comparison.candidate_analysis_id}</Identifier>],
          ["Summary digest", comparison.summary_digest === null ? null : <Identifier>{comparison.summary_digest}</Identifier>],
          ["Terminal reason", comparison.terminal_reason],
        ]} />
      </Section>

      <InvarianceComparison rows={comparison.completed_invariance_rows} permittedDifferences={comparison.permitted_differences} mismatches={comparison.mismatches} />

      {!comparison.terminal ? <Section title="Waiting for fixed execution"><p>Boundary is running a fresh fixed-v1 control and injected execution. This page will stop polling when the comparison becomes terminal.</p></Section> : null}
    </main>
  );
}

import { useCallback } from "react";
import { getValidated, mutateValidated } from "../api/client";
import { useIdempotentMutation } from "../api/mutation";
import { usePollingResource } from "../api/polling";
import type { RegressionCase, RerunAccepted } from "../api/types";
import { validateRegressionCase, validateRerunAccepted } from "../api/validate";
import { AppLink } from "../components/AppLink";
import { ErrorState, Identifier, LoadingState, MutationMessage, Section } from "../components/Primitives";
import { RegressionProvenance } from "../components/RegressionProvenance";
import { navigate } from "../routes/router";

export function RegressionPage({ regressionCaseId }: { regressionCaseId: string }) {
  const fetcher = useCallback((signal: AbortSignal) => getValidated(`/api/v1/regression-cases/${regressionCaseId}`, validateRegressionCase, signal), [regressionCaseId]);
  const [state, retry] = usePollingResource<RegressionCase>({ fetcher, isTerminal: () => true });
  const rerunMutation = useIdempotentMutation<RerunAccepted, { mode: "version_comparison"; tested_agent_version: "fixed-v1" }>(
    (body, key, signal) => mutateValidated(`/api/v1/regression-cases/${regressionCaseId}/reruns`, body, key, validateRerunAccepted, signal),
  );

  if (state.kind === "loading") return <main><LoadingState label="Loading immutable regression case" /></main>;
  if (state.kind === "empty") return <main><Section title="Regression case unavailable"><p>{state.message}</p></Section></main>;
  if (state.kind !== "ready") return <main><ErrorState title={state.kind === "malformed" ? "Malformed regression response" : "Regression case unavailable"} message={state.message} code={state.code} onRetry={retry} /></main>;

  const regression = state.data;
  const accepted = rerunMutation.state.kind === "success" ? rerunMutation.state.data : null;
  return (
    <main>
      <header className="page-header">
        <p className="eyebrow">Immutable regression artifact</p>
        <h1>Reproduce the failed boundary without redefining it.</h1>
        <p>Case <Identifier>{regression.regression_case_id}</Identifier></p>
      </header>

      <RegressionProvenance
        regression={regression}
        onSelectEvidence={(evidenceId) => navigate(`/runs/${regression.artifact.source_run_id}#evidence-${evidenceId}`)}
      />

      <Section title="Existing reruns and comparisons" eyebrow="Recorded history">
        {regression.reruns.length === 0 ? <p className="empty-copy">No reruns have been accepted.</p> : (
          <ul className="record-list">{regression.reruns.map((rerun) => (
            <li key={rerun.rerun_id}>
              <div><span className="label">Rerun</span><Identifier>{rerun.rerun_id}</Identifier></div>
              <span>{rerun.mode} · {rerun.status}</span>
              {rerun.campaign_id === null ? null : <AppLink href={`/?campaign=${rerun.campaign_id}`}>Follow campaign</AppLink>}
            </li>
          ))}</ul>
        )}
        {regression.comparisons.length === 0 ? <p className="empty-copy">No comparisons have been created.</p> : (
          <ul className="record-list">{regression.comparisons.map((comparison) => (
            <li key={comparison.comparison_id}>
              <div><span className="label">Comparison</span><Identifier>{comparison.comparison_id}</Identifier></div>
              <span>{comparison.status}</span>
              <AppLink href={`/comparisons/${comparison.comparison_id}`}>Inspect comparison</AppLink>
            </li>
          ))}</ul>
        )}
      </Section>

      <Section title="Start fixed-version comparison" eyebrow="Narrow rerun">
        <form onSubmit={(event) => {
          event.preventDefault();
          void rerunMutation.submit({ mode: "version_comparison", tested_agent_version: "fixed-v1" });
        }}>
          <div className="form-grid">
            <label>Mode<input value="version_comparison" readOnly /></label>
            <label>Tested-agent version<input value="fixed-v1" readOnly /></label>
          </div>
          <p className="muted">Boundary will reuse the immutable input, fault, analyzer, assertions, and policy while changing the tested-agent version.</p>
          <button type="submit" className="primary-button" disabled={rerunMutation.state.kind === "submitting"}>
            {rerunMutation.state.kind === "submitting" ? "Accepting rerun…" : "Start fixed-v1 comparison"}
          </button>
        </form>
        <MutationMessage state={rerunMutation.state} />
        {accepted === null ? null : (
          <div className="accepted-callout" role="status">
            <h3>Rerun accepted</h3>
            <p>Rerun <Identifier>{accepted.rerun_id}</Identifier></p>
            <nav className="resource-links" aria-label="Accepted rerun resources">
              <AppLink href={`/?campaign=${accepted.campaign_id}`}>Follow rerun campaign</AppLink>
              {accepted.comparison_id === null ? null : <AppLink href={`/comparisons/${accepted.comparison_id}`}>Follow comparison</AppLink>}
            </nav>
          </div>
        )}
      </Section>
    </main>
  );
}

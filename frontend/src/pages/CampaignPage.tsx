import { useCallback, useMemo } from "react";
import { getValidated, mutateValidated } from "../api/client";
import { useIdempotentMutation } from "../api/mutation";
import { isRunTerminalForPolling, usePollingResource, type ResourceState } from "../api/polling";
import type { CampaignAccepted, CancellationResult, Run } from "../api/types";
import {
  validateCampaign,
  validateCampaignAccepted,
  validateCancellation,
  validateRun,
  validateUuid,
} from "../api/validate";
import { AppLink } from "../components/AppLink";
import { CampaignProgress } from "../components/CampaignProgress";
import { OperationalAndPolicyStatus } from "../components/OperationalAndPolicyStatus";
import { ErrorState, Identifier, LoadingState, MutationMessage, Section } from "../components/Primitives";
import { navigate } from "../routes/router";

function useOptionalRun(runId: string | null): [ResourceState<Run>, () => void] {
  const fetcher = useCallback(
    (signal: AbortSignal) => getValidated(`/api/v1/runs/${runId ?? "unavailable"}`, validateRun, signal),
    [runId],
  );
  return usePollingResource({
    enabled: runId !== null,
    fetcher,
    isTerminal: isRunTerminalForPolling,
    emptyMessage: "Run not created yet.",
  });
}

function readyData<T>(state: ResourceState<T>): T | null {
  return state.kind === "ready" ? state.data : null;
}

export function CampaignPage() {
  const rawCampaignId = useMemo(() => new URLSearchParams(window.location.search).get("campaign"), []);
  const campaignId = rawCampaignId === null ? null : validateUuid(rawCampaignId);
  const invalidCampaignId = rawCampaignId !== null && campaignId === null;
  const campaignFetcher = useCallback(
    (signal: AbortSignal) => getValidated(`/api/v1/campaigns/${campaignId ?? "unavailable"}`, validateCampaign, signal),
    [campaignId],
  );
  const [campaignState, retryCampaign] = usePollingResource({
    enabled: campaignId !== null,
    fetcher: campaignFetcher,
    isTerminal: (campaign) => campaign.terminal,
    emptyMessage: "No campaign is active.",
  });
  const campaign = readyData(campaignState);
  const [controlState] = useOptionalRun(campaign?.control_run_id ?? null);
  const [injectedState] = useOptionalRun(campaign?.injected_run_id ?? null);
  const controlRun = readyData(controlState);
  const injectedRun = readyData(injectedState);

  const startMutation = useIdempotentMutation<CampaignAccepted, undefined>(
    (_argument, key, signal) => mutateValidated(
      "/api/v1/campaigns/bundled-tool-timeout",
      {},
      key,
      validateCampaignAccepted,
      signal,
    ),
  );
  const cancelMutation = useIdempotentMutation<CancellationResult, string>(
    (id, key, signal) => mutateValidated(
      `/api/v1/campaigns/${id}/cancel`,
      {},
      key,
      validateCancellation,
      signal,
    ),
  );

  const start = async (): Promise<void> => {
    const accepted = await startMutation.submit(undefined);
    if (accepted !== null) navigate(`/?campaign=${accepted.campaign_id}`, true);
  };
  const cancel = async (): Promise<void> => {
    if (campaign === null) return;
    const cancelled = await cancelMutation.submit(campaign.campaign_id);
    if (cancelled !== null) retryCampaign();
  };

  return (
    <main>
      <header className="hero">
        <p className="eyebrow">Active reliability testing for AI agents</p>
        <h1>Find the first unsafe behavior after a controlled failure.</h1>
        <p className="hero-copy">Boundary injects a production-shaped tool timeout, preserves ordered evidence, and applies one explicit scenario policy.</p>
        <div className="fixture-summary" aria-label="Bundled fixture">
          <span><span className="label">Scenario</span><code>phase1.tool-timeout · v1</code></span>
          <span><span className="label">Tested version</span><code>vulnerable-v1</code></span>
        </div>
        <button
          type="button"
          className="primary-button"
          disabled={startMutation.state.kind === "submitting" || (campaign !== null && !campaign.terminal)}
          onClick={() => void start()}
        >
          {startMutation.state.kind === "submitting" ? "Starting…" : campaign?.terminal ? "Start a new vulnerable campaign" : "Start bundled vulnerable campaign"}
        </button>
        <MutationMessage state={startMutation.state} />
      </header>

      {invalidCampaignId ? (
        <ErrorState title="Invalid campaign link" message="The campaign query parameter is not a Boundary UUID." />
      ) : campaignId === null ? (
        <Section title="No active campaign" eyebrow="Ready"><p>Start the bundled campaign to follow its control and injected executions.</p></Section>
      ) : campaignState.kind === "loading" ? (
        <LoadingState label="Loading campaign" />
      ) : campaignState.kind === "empty" ? (
        <Section title="Campaign unavailable"><p>{campaignState.message}</p></Section>
      ) : campaignState.kind !== "ready" ? (
        <ErrorState title={campaignState.kind === "malformed" ? "Malformed campaign response" : "Campaign unavailable"} message={campaignState.message} code={campaignState.code} onRetry={retryCampaign} />
      ) : (
        <>
          <Section title="Campaign execution" eyebrow={campaignState.data.terminal ? "Terminal" : "Following live execution"}>
            <OperationalAndPolicyStatus operationalStatus={campaignState.data.operational_status} policyResult={injectedRun?.policy_result ?? null} />
            <CampaignProgress campaign={campaignState.data} controlRun={controlRun} injectedRun={injectedRun} />
            {campaignState.data.failure_reason === null ? null : <div className="safe-failure" role="alert"><span className="label">Safe failure reason</span>{campaignState.data.failure_reason}</div>}
            {!campaignState.data.terminal && !campaignState.data.cancel_requested ? (
              <button type="button" className="danger-button" disabled={cancelMutation.state.kind === "submitting"} onClick={() => void cancel()}>
                {cancelMutation.state.kind === "submitting" ? "Requesting cancellation…" : "Cancel active campaign"}
              </button>
            ) : campaignState.data.cancel_requested ? <p className="status-note">Cancellation requested.</p> : null}
            <MutationMessage state={cancelMutation.state} />
          </Section>
          <Section title="Available records" eyebrow="Boundary-owned navigation">
            <nav className="resource-links" aria-label="Available campaign resources">
              {campaignState.data.control_run_id === null ? null : <AppLink href={`/runs/${campaignState.data.control_run_id}`}>Open control run</AppLink>}
              {campaignState.data.injected_run_id === null ? null : <AppLink href={`/runs/${campaignState.data.injected_run_id}`}>Inspect injected run</AppLink>}
              {campaignState.data.regression_case_id === null ? null : <AppLink href={`/regressions/${campaignState.data.regression_case_id}`}>Open immutable regression case</AppLink>}
              {campaignState.data.comparison_id === null ? null : <AppLink href={`/comparisons/${campaignState.data.comparison_id}`}>Open version comparison</AppLink>}
            </nav>
            <p className="muted">Campaign ID <Identifier>{campaignState.data.campaign_id}</Identifier></p>
          </Section>
        </>
      )}
    </main>
  );
}

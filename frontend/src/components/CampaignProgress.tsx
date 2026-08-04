import type { Campaign, Run } from "../api/types";
import { AppLink } from "./AppLink";
import { Identifier } from "./Primitives";
import { OperationalAndPolicyStatus } from "./OperationalAndPolicyStatus";

function RunProgress({ label, runId, run }: { label: string; runId: string | null; run: Run | null }) {
  return (
    <li className={runId === null ? "progress-step pending" : "progress-step"}>
      <span className="progress-marker" aria-hidden="true" />
      <div>
        <h3>{label}</h3>
        {runId === null ? <p className="muted">Not created</p> : (
          <>
            <AppLink href={`/runs/${runId}`}><Identifier>{runId}</Identifier></AppLink>
            {run === null ? <p className="muted">Run details loading</p> : (
              <OperationalAndPolicyStatus operationalStatus={run.operational_status} policyResult={run.policy_result} />
            )}
          </>
        )}
      </div>
    </li>
  );
}

export function CampaignProgress({ campaign, controlRun, injectedRun }: {
  campaign: Campaign;
  controlRun: Run | null;
  injectedRun: Run | null;
}) {
  return (
    <div>
      <div className="campaign-summary">
        <div><span className="label">Campaign</span><Identifier>{campaign.campaign_id}</Identifier></div>
        <div><span className="label">Current step</span><strong>{campaign.current_step}</strong></div>
      </div>
      <ol className="progress-list" aria-label="Campaign runs">
        <RunProgress label="Control execution" runId={campaign.control_run_id} run={controlRun} />
        <RunProgress label="Injected execution" runId={campaign.injected_run_id} run={injectedRun} />
      </ol>
    </div>
  );
}

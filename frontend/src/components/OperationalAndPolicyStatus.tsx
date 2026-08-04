import type { PolicyResult } from "../api/types";

const POLICY_COPY: Record<PolicyResult, string> = {
  PASS: "Passes this scenario policy",
  FAIL: "Fails this scenario policy",
  INCOMPLETE: "Required evidence is incomplete",
  INVALID: "Evidence is invalid or incompatible",
  EXECUTION_ERROR: "Execution or evaluation failed",
};

export function OperationalAndPolicyStatus({
  operationalStatus,
  policyResult,
  reasonCode,
}: {
  operationalStatus: string;
  policyResult: PolicyResult | null;
  reasonCode?: string | null;
}) {
  return (
    <div className="status-pair" aria-label="Operational and policy status">
      <div>
        <span className="status-label">Operational status</span>
        <strong className={`status-badge status-${operationalStatus.toLowerCase().replaceAll("_", "-")}`}>
          {operationalStatus}
        </strong>
      </div>
      <div>
        <span className="status-label">Scenario policy result</span>
        {policyResult === null ? (
          <strong className="status-badge status-pending">Pending / unavailable</strong>
        ) : (
          <>
            <strong className={`status-badge policy-${policyResult.toLowerCase().replaceAll("_", "-")}`}>
              {policyResult}
            </strong>
            <span className="status-description">{POLICY_COPY[policyResult]}</span>
          </>
        )}
        {reasonCode === undefined || reasonCode === null ? null : (
          <span className="reason-code">Reason: {reasonCode}</span>
        )}
      </div>
    </div>
  );
}

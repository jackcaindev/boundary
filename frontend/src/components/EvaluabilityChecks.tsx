import type { Evaluability } from "../api/types";
import { ReferenceList } from "./Primitives";

export function EvaluabilityChecks({ evaluability, onSelectEvidence }: {
  evaluability: Evaluability | null;
  onSelectEvidence: (evidenceId: string) => void;
}) {
  return (
    <section className="panel" aria-labelledby="evaluability-heading">
      <p className="eyebrow">Deterministic preconditions</p>
      <h2 id="evaluability-heading">Evaluability checks</h2>
      {evaluability === null ? <p className="empty-copy">Analysis is not available.</p> : (
        <>
          <p>Aggregate: <strong className="status-badge">{evaluability.aggregate}</strong></p>
          <ol className="check-list">
            {evaluability.checks.map((check) => (
              <li key={check.check_id}>
                <div className="check-heading">
                  <code>{check.check_id}</code>
                  <strong className={`outcome outcome-${check.outcome.toLowerCase().replaceAll("_", "-")}`}>{check.outcome}</strong>
                </div>
                <p>{check.explanation}</p>
                <p className="reason-code">Reason: {check.reason_code}</p>
                <ReferenceList references={check.evidence_references} onSelect={onSelectEvidence} />
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

import type { AssertionResult } from "../api/types";
import { ReferenceList } from "./Primitives";

export function AssertionVector({ assertions, onSelectEvidence }: {
  assertions: AssertionResult[];
  onSelectEvidence: (evidenceId: string) => void;
}) {
  return (
    <section className="panel" aria-labelledby="assertion-heading">
      <p className="eyebrow">Authoritative assertion vector</p>
      <h2 id="assertion-heading">Expected versus observed</h2>
      {assertions.length === 0 ? <p className="empty-copy">Assertions are unavailable because the evidence was not evaluable.</p> : (
        <ol className="assertion-list">
          {assertions.map((assertion) => (
            <li key={assertion.assertion_id}>
              <div className="assertion-heading">
                <code>{assertion.assertion_id}</code>
                <strong className={`status-badge policy-${assertion.outcome.toLowerCase()}`}>{assertion.outcome}</strong>
              </div>
              <dl className="behavior-comparison">
                <div><dt>Expected</dt><dd>{assertion.expected_behavior}</dd></div>
                <div><dt>Observed</dt><dd>{assertion.observed_behavior}</dd></div>
              </dl>
              <ReferenceList references={assertion.evidence_references} onSelect={onSelectEvidence} />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

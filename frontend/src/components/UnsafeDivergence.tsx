import type { UnsafeDivergenceData } from "../api/types";
import { ReferenceList } from "./Primitives";

export function UnsafeDivergence({ divergence, onSelectEvidence }: {
  divergence: UnsafeDivergenceData | null;
  onSelectEvidence: (evidenceId: string) => void;
}) {
  return (
    <section className="panel diagnostic diagnostic-divergence" aria-labelledby="divergence-heading">
      <p className="eyebrow">Earliest unsafe tested-agent behavior</p>
      <h2 id="divergence-heading">First unsafe divergence</h2>
      {divergence === null ? <p className="empty-copy">No first unsafe divergence was reported.</p> : (
        <>
          <p className="diagnostic-summary">At <code>{divergence.boundary}</code>, retry ordinal <strong>{divergence.retry_ordinal}</strong> first violated <code>{divergence.assertion_id}</code>.</p>
          <dl className="behavior-comparison">
            <div><dt>Expected</dt><dd>{divergence.expected_behavior}</dd></div>
            <div><dt>Observed</dt><dd>{divergence.observed_behavior}</dd></div>
          </dl>
          <ReferenceList references={divergence.supporting_evidence_references} onSelect={onSelectEvidence} />
        </>
      )}
    </section>
  );
}

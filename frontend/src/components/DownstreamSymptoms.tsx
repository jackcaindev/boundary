import type { EvidenceReference } from "../api/types";
import { ReferenceList } from "./Primitives";

export function DownstreamSymptoms({ symptoms, onSelectEvidence }: {
  symptoms: EvidenceReference[];
  onSelectEvidence: (evidenceId: string) => void;
}) {
  return (
    <section className="panel diagnostic diagnostic-symptoms" aria-labelledby="symptoms-heading">
      <p className="eyebrow">Later consequences</p>
      <h2 id="symptoms-heading">Downstream symptoms</h2>
      <p className="diagnostic-summary">These events occurred after the first unsafe divergence and are not labeled as the primary defect.</p>
      <ReferenceList references={symptoms} onSelect={onSelectEvidence} empty="No downstream symptoms were reported." />
    </section>
  );
}

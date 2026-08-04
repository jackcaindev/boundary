import type { InjectionBoundaryData } from "../api/types";
import { ReferenceList } from "./Primitives";

export function InjectionBoundary({ injection, faultId, faultSpecId, faultDigest, onSelectEvidence }: {
  injection: InjectionBoundaryData | null;
  faultId: string | null;
  faultSpecId: string | null;
  faultDigest: string | null;
  onSelectEvidence: (evidenceId: string) => void;
}) {
  return (
    <section className="panel diagnostic diagnostic-injection" aria-labelledby="injection-heading">
      <p className="eyebrow">Deliberate test condition</p>
      <h2 id="injection-heading">Injection boundary</h2>
      <p className="diagnostic-summary">Boundary injected a controlled tool timeout. This is the adverse condition, not the diagnosed agent defect.</p>
      {injection === null ? <p className="empty-copy">Injection proof is unavailable.</p> : (
        <>
          <dl className="definition-list compact">
            <div><dt>Boundary</dt><dd><code>{injection.boundary}</code></dd></div>
            <div><dt>Realized timeout ordinals</dt><dd>{injection.realized_timeout_ordinals.join(", ")}</dd></div>
            <div><dt>Fault ID</dt><dd><code className="identifier">{faultId}</code></dd></div>
            <div><dt>Fault spec</dt><dd><code className="identifier">{faultSpecId}</code></dd></div>
            <div><dt>Definition digest</dt><dd><code className="identifier">{faultDigest}</code></dd></div>
          </dl>
          <ReferenceList references={injection.evidence_references} onSelect={onSelectEvidence} />
        </>
      )}
    </section>
  );
}

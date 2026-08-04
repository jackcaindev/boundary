import type { ReactNode } from "react";
import type { EvidenceReference } from "../api/types";

export function Section({ title, eyebrow, children, className = "" }: {
  title: string;
  eyebrow?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`.trim()}>
      {eyebrow === undefined ? null : <p className="eyebrow">{eyebrow}</p>}
      <h2>{title}</h2>
      {children}
    </section>
  );
}

export function Identifier({ children }: { children: ReactNode }) {
  return <code className="identifier">{children}</code>;
}

export function DefinitionList({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="definition-list">
      {rows.map(([label, value], index) => (
        <div key={`${label}-${index}`}>
          <dt>{label}</dt>
          <dd>{value ?? <span className="muted">Unavailable</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

export function EvidenceReferenceButton({ reference, onSelect }: {
  reference: EvidenceReference;
  onSelect: (evidenceId: string) => void;
}) {
  return (
    <button
      type="button"
      className="reference-button"
      onClick={() => onSelect(reference.evidence_id)}
      aria-label={`Select evidence receipt ${reference.receipt_seq}`}
    >
      #{reference.receipt_seq} · {reference.event_type}
    </button>
  );
}

export function ReferenceList({ references, onSelect, empty = "No evidence references." }: {
  references: EvidenceReference[];
  onSelect: (evidenceId: string) => void;
  empty?: string;
}) {
  if (references.length === 0) return <p className="empty-copy">{empty}</p>;
  return (
    <div className="reference-list">
      {references.map((reference) => (
        <EvidenceReferenceButton key={reference.evidence_id} reference={reference} onSelect={onSelect} />
      ))}
    </div>
  );
}

export function ErrorState({ title, message, code, onRetry }: {
  title: string;
  message: string;
  code?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state-message error-state" role="alert">
      <h2>{title}</h2>
      {code === undefined ? null : <p><span className="label">Code</span> <Identifier>{code}</Identifier></p>}
      <p>{message}</p>
      {onRetry === undefined ? null : <button type="button" className="secondary-button" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function LoadingState({ label }: { label: string }) {
  return <div className="state-message" role="status"><span className="loading-dot" /> {label}</div>;
}

export function MutationMessage({ state }: {
  state: { kind: string; message?: string; code?: string };
}) {
  if (state.kind === "idle" || state.kind === "submitting" || state.kind === "success") return null;
  return <ErrorState title="Action failed safely" message={state.message ?? "The action failed."} code={state.code} />;
}

export function PlainData({ value }: { value: unknown }) {
  return <pre className="plain-data">{JSON.stringify(value, null, 2)}</pre>;
}

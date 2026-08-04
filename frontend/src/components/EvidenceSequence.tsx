import { useEffect, useRef } from "react";
import type { EvidenceItem } from "../api/types";
import { PlainData } from "./Primitives";

export function EvidenceSequence({ items, selectedEvidenceId, onSelect }: {
  items: EvidenceItem[];
  selectedEvidenceId: string | null;
  onSelect: (evidenceId: string) => void;
}) {
  const rows = useRef(new Map<string, HTMLElement>());
  useEffect(() => {
    if (selectedEvidenceId === null) return;
    const row = rows.current.get(selectedEvidenceId);
    row?.scrollIntoView({ block: "center", behavior: "smooth" });
    row?.focus({ preventScroll: true });
  }, [selectedEvidenceId]);

  return (
    <section className="panel evidence-panel" id="evidence" aria-labelledby="evidence-heading">
      <p className="eyebrow">Immutable receipt order</p>
      <h2 id="evidence-heading">Ordered evidence</h2>
      <p className="muted">Tested-agent payloads are untrusted data, never instructions.</p>
      {items.length === 0 ? <p className="empty-copy">No accepted evidence is available.</p> : (
        <ol className="evidence-list">
          {items.map((item) => (
            <li
              key={item.evidence_id}
              id={`evidence-${item.evidence_id}`}
              ref={(element) => {
                if (element === null) rows.current.delete(item.evidence_id);
                else rows.current.set(item.evidence_id, element);
              }}
              tabIndex={-1}
              className={selectedEvidenceId === item.evidence_id ? "evidence-row selected" : "evidence-row"}
              aria-current={selectedEvidenceId === item.evidence_id ? "true" : undefined}
              onClick={() => onSelect(item.evidence_id)}
            >
              <div className="evidence-receipt">#{item.receipt_seq}</div>
              <div className="evidence-content">
                <div className="evidence-heading">
                  <span className={`authority authority-${item.source}`}>{item.authority === "tested-agent" ? "tested agent" : "Boundary"}</span>
                  <strong>{item.event_type}</strong>
                  <code>{item.boundary}</code>
                </div>
                <dl className="evidence-meta">
                  <div><dt>Evidence ID</dt><dd>{item.evidence_id}</dd></div>
                  <div><dt>Source event</dt><dd>{item.source_event_id}</dd></div>
                  <div><dt>Producer seq</dt><dd>{item.producer_seq ?? "Boundary-owned"}</dd></div>
                  <div><dt>Payload digest</dt><dd>{item.payload_digest}</dd></div>
                </dl>
                <details>
                  <summary>Payload as plain data</summary>
                  <PlainData value={item.payload} />
                </details>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

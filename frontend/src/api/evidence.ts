import { useEffect, useState } from "react";
import { getValidated, safeError } from "./client";
import type { EvidenceItem } from "./types";
import { MalformedResponseError, validateEvidencePage } from "./validate";

const EVIDENCE_PAGE_LIMIT = 50;

export type EvidenceState =
  | { kind: "loading" }
  | { kind: "empty"; items: EvidenceItem[] }
  | { kind: "ready"; items: EvidenceItem[] }
  | { kind: "malformed" | "transport-error" | "problem"; message: string; code?: string };

export function validateAndAppendEvidencePage(
  runId: string,
  cursor: number,
  existing: EvidenceItem[],
  raw: unknown,
): { items: EvidenceItem[]; next: number | null } {
  const page = validateEvidencePage(raw);
  if (page.run_id !== runId || page.after_receipt_seq !== cursor || page.limit !== EVIDENCE_PAGE_LIMIT) {
    throw new MalformedResponseError("Evidence page identity or cursor conflicts with the requested run.");
  }
  const evidenceIds = new Set(existing.map((item) => item.evidence_id));
  const sourceEventIds = new Set(existing.map((item) => item.source_event_id));
  let expected = cursor + 1;
  for (const item of page.items) {
    if (
      item.receipt_seq !== expected ||
      evidenceIds.has(item.evidence_id) ||
      sourceEventIds.has(item.source_event_id)
    ) {
      throw new MalformedResponseError("Evidence contains a duplicate, gap, or out-of-order receipt.");
    }
    evidenceIds.add(item.evidence_id);
    sourceEventIds.add(item.source_event_id);
    expected += 1;
  }
  if (
    (page.next_after_receipt_seq !== null && page.items.length === 0) ||
    (page.next_after_receipt_seq !== null &&
      page.next_after_receipt_seq !== page.items.at(-1)?.receipt_seq)
  ) {
    throw new MalformedResponseError("Evidence pagination cursor is malformed.");
  }
  return { items: [...existing, ...page.items], next: page.next_after_receipt_seq };
}

export function useEvidence(runId: string, enabled: boolean): [EvidenceState, () => void] {
  const [state, setState] = useState<EvidenceState>({ kind: "loading" });
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setState({ kind: "empty", items: [] });
      return;
    }
    const controller = new AbortController();
    let current = true;
    const load = async (): Promise<void> => {
      let items: EvidenceItem[] = [];
      let cursor = 0;
      try {
        do {
          const path = `/api/v1/runs/${runId}/evidence?after_receipt_seq=${cursor}&limit=${EVIDENCE_PAGE_LIMIT}`;
          const rawPage = await getValidated(path, (value) => value, controller.signal);
          const appended = validateAndAppendEvidencePage(runId, cursor, items, rawPage);
          items = appended.items;
          if (appended.next === null) break;
          cursor = appended.next;
        } while (!controller.signal.aborted);
        if (!current || controller.signal.aborted) return;
        setState(items.length === 0 ? { kind: "empty", items } : { kind: "ready", items });
      } catch (error) {
        if (!current || controller.signal.aborted) return;
        const safe = safeError(error);
        setState({
          kind: safe.kind === "transport" ? "transport-error" : safe.kind,
          message: safe.message,
          ...(safe.code === undefined ? {} : { code: safe.code }),
        });
      }
    };
    setState({ kind: "loading" });
    void load();
    return () => {
      current = false;
      controller.abort();
    };
  }, [enabled, generation, runId]);

  return [state, () => setGeneration((value) => value + 1)];
}

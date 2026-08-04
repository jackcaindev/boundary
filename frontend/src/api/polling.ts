import { useCallback, useEffect, useRef, useState } from "react";
import { safeError } from "./client";
import type { Run } from "./types";

export const POLL_INTERVAL_MS = 750;
const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "cancelled", "timed_out", "invalid"]);

export function isRunTerminalForPolling(run: Run): boolean {
  return TERMINAL_RUN_STATUSES.has(run.operational_status) &&
    (run.run_role === "control" || run.policy_result !== null);
}

export type ResourceState<T> =
  | { kind: "loading" }
  | { kind: "empty"; message: string }
  | { kind: "ready"; data: T; terminal: boolean }
  | { kind: "malformed" | "transport-error" | "problem"; message: string; code?: string };

interface PollOptions<T> {
  enabled?: boolean;
  fetcher: (signal: AbortSignal) => Promise<T>;
  isTerminal: (value: T) => boolean;
  isEmpty?: (value: T) => boolean;
  emptyMessage?: string;
  intervalMs?: number;
}

export function usePollingResource<T>({
  enabled = true,
  fetcher,
  isTerminal,
  isEmpty,
  emptyMessage = "The resource is empty.",
  intervalMs = POLL_INTERVAL_MS,
}: PollOptions<T>): [ResourceState<T>, () => void] {
  const [state, setState] = useState<ResourceState<T>>({ kind: "loading" });
  const [generation, setGeneration] = useState(0);
  const fetcherRef = useRef(fetcher);
  const terminalRef = useRef(isTerminal);
  const emptyRef = useRef(isEmpty);
  fetcherRef.current = fetcher;
  terminalRef.current = isTerminal;
  emptyRef.current = isEmpty;

  const retry = useCallback(() => {
    setState({ kind: "loading" });
    setGeneration((value) => value + 1);
  }, []);

  useEffect(() => {
    if (!enabled) {
      setState({ kind: "empty", message: emptyMessage });
      return;
    }
    const controller = new AbortController();
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async (): Promise<void> => {
      try {
        const value = await fetcherRef.current(controller.signal);
        if (!current || controller.signal.aborted) return;
        if (emptyRef.current?.(value)) {
          setState({ kind: "empty", message: emptyMessage });
          return;
        }
        const terminal = terminalRef.current(value);
        setState({ kind: "ready", data: value, terminal });
        if (!terminal) timer = setTimeout(() => void poll(), intervalMs);
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
    void poll();
    return () => {
      current = false;
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [enabled, emptyMessage, generation, intervalMs]);

  return [state, retry];
}

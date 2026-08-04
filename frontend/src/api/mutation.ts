import { useCallback, useEffect, useRef, useState } from "react";
import { safeError } from "./client";

export type MutationState<T> =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success"; data: T }
  | { kind: "malformed" | "transport-error" | "problem"; message: string; code?: string };

export function useIdempotentMutation<T, A>(
  mutate: (argument: A, key: string, signal: AbortSignal) => Promise<T>,
): {
  state: MutationState<T>;
  submit: (argument: A) => Promise<T | null>;
  reset: () => void;
} {
  const [state, setState] = useState<MutationState<T>>({ kind: "idle" });
  const keyRef = useRef<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);
  const mutateRef = useRef(mutate);
  mutateRef.current = mutate;

  useEffect(() => () => controllerRef.current?.abort(), []);

  const submit = useCallback(async (argument: A): Promise<T | null> => {
    if (inFlightRef.current) return null;
    inFlightRef.current = true;
    const key = keyRef.current ?? crypto.randomUUID();
    keyRef.current = key;
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ kind: "submitting" });
    try {
      const data = await mutateRef.current(argument, key, controller.signal);
      if (controller.signal.aborted) return null;
      keyRef.current = null;
      setState({ kind: "success", data });
      return data;
    } catch (error) {
      if (controller.signal.aborted) return null;
      const safe = safeError(error);
      setState({
        kind: safe.kind === "transport" ? "transport-error" : safe.kind,
        message: safe.message,
        ...(safe.code === undefined ? {} : { code: safe.code }),
      });
      return null;
    } finally {
      inFlightRef.current = false;
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }, []);

  const reset = useCallback(() => {
    controllerRef.current?.abort();
    keyRef.current = null;
    inFlightRef.current = false;
    setState({ kind: "idle" });
  }, []);

  return { state, submit, reset };
}

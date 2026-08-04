import type { ProblemDetail } from "./types";
import { MalformedResponseError, validateProblem } from "./validate";

export class ApiProblemError extends Error {
  readonly problem: ProblemDetail;

  constructor(problem: ProblemDetail) {
    super(problem.detail);
    this.name = "ApiProblemError";
    this.problem = problem;
  }
}

export class TransportError extends Error {
  readonly kind = "transport";

  constructor(message = "Boundary could not be reached.") {
    super(message);
    this.name = "TransportError";
  }
}

async function receiveUnknown(response: Response): Promise<unknown> {
  try {
    return await response.json() as unknown;
  } catch {
    throw new MalformedResponseError();
  }
}

export async function getValidated<T>(
  path: string,
  validate: (value: unknown) => T,
  signal: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal,
      credentials: "same-origin",
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new TransportError();
  }
  const body = await receiveUnknown(response);
  if (!response.ok) throw new ApiProblemError(validateProblem(body, response.status));
  return validate(body);
}

export async function mutateValidated<T>(
  path: string,
  body: Record<string, unknown>,
  idempotencyKey: string,
  validate: (value: unknown) => T,
  signal: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify(body),
      signal,
      credentials: "same-origin",
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new TransportError();
  }
  const responseBody = await receiveUnknown(response);
  if (!response.ok) throw new ApiProblemError(validateProblem(responseBody, response.status));
  return validate(responseBody);
}

export function safeError(error: unknown): {
  kind: "malformed" | "transport" | "problem";
  message: string;
  code?: string;
} {
  if (error instanceof ApiProblemError) {
    return { kind: "problem", code: error.problem.code, message: error.problem.detail };
  }
  if (error instanceof MalformedResponseError) {
    return { kind: "malformed", message: error.message };
  }
  return { kind: "transport", message: error instanceof TransportError ? error.message : "Boundary could not be reached." };
}

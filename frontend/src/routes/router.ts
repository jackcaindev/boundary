import { validateUuid } from "../api/validate";

export type Route =
  | { kind: "campaign" }
  | { kind: "run"; runId: string }
  | { kind: "regression"; regressionCaseId: string }
  | { kind: "comparison"; comparisonId: string }
  | { kind: "not-found" };

export function resolveRoute(pathname: string): Route {
  if (pathname === "/") return { kind: "campaign" };
  const match = pathname.match(/^\/(runs|regressions|comparisons)\/([^/]+)\/?$/);
  if (match === null) return { kind: "not-found" };
  const id = validateUuid(match[2] ?? "");
  if (id === null) return { kind: "not-found" };
  if (match[1] === "runs") return { kind: "run", runId: id };
  if (match[1] === "regressions") return { kind: "regression", regressionCaseId: id };
  return { kind: "comparison", comparisonId: id };
}

export function apiLinkToApplicationPath(link: string | null): string | null {
  if (link === null) return null;
  const match = link.match(
    /^\/api\/v1\/(campaigns|runs|regression-cases|comparisons)\/([0-9a-f-]{36})$/i,
  );
  if (match === null || validateUuid(match[2] ?? "") === null) return null;
  switch (match[1]) {
    case "campaigns":
      return `/?campaign=${match[2]}`;
    case "runs":
      return `/runs/${match[2]}`;
    case "regression-cases":
      return `/regressions/${match[2]}`;
    case "comparisons":
      return `/comparisons/${match[2]}`;
    default:
      return null;
  }
}

export function navigate(path: string, replace = false): void {
  if (!path.startsWith("/") || path.startsWith("//")) return;
  window.history[replace ? "replaceState" : "pushState"]({}, "", path);
  window.dispatchEvent(new Event("boundary:navigate"));
}

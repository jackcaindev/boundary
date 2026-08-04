import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { resolveRoute } from "../src/routes/router";
import {
  campaignFixture,
  comparisonFixture,
  evidencePage,
  IDS,
  jsonResponse,
  links,
  regressionFixture,
  runFixture,
  wire,
} from "./fixtures";

function urlOf(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
}

describe("direct application routes", () => {
  beforeEach(() => vi.useRealTimers());

  it.each([
    ["campaign", `/?campaign=${IDS.campaign}`, "Campaign execution"],
    ["run", `/runs/${IDS.injected}`, "Execution evidence and policy analysis"],
    ["regression", `/regressions/${IDS.regression}`, "Reproduce the failed boundary without redefining it."],
    ["comparison", `/comparisons/${IDS.comparison}`, "Terminal comparison"],
  ])("loads the %s route directly on refresh", async (_label, path, heading) => {
    window.history.replaceState({}, "", path);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = urlOf(input);
      if (url.includes("/evidence?")) return Promise.resolve(jsonResponse(wire(evidencePage([]))));
      if (url.includes("/regression-cases/")) return Promise.resolve(jsonResponse(wire(regressionFixture())));
      if (url.includes("/comparisons/")) return Promise.resolve(jsonResponse(wire(comparisonFixture())));
      if (url.includes("/runs/")) return Promise.resolve(jsonResponse(wire(runFixture())));
      if (url.includes("/campaigns/")) return Promise.resolve(jsonResponse(wire(campaignFixture({ control_run_id: null, injected_run_id: null }))));
      throw new Error(`Unexpected fetch ${url}`);
    }));
    render(<App />);
    expect(await screen.findByRole("heading", { name: heading })).not.toBeNull();
  });

  it("resolves only the four owned route shapes and responds to history navigation", async () => {
    expect(resolveRoute("/").kind).toBe("campaign");
    expect(resolveRoute(`/runs/${IDS.injected}`).kind).toBe("run");
    expect(resolveRoute(`/regressions/${IDS.regression}`).kind).toBe("regression");
    expect(resolveRoute(`/comparisons/${IDS.comparison}`).kind).toBe("comparison");
    expect(resolveRoute("/dashboard").kind).toBe("not-found");
    expect(resolveRoute("/runs/javascript:alert(1)").kind).toBe("not-found");

    window.history.replaceState({}, "", "/dashboard");
    render(<App />);
    expect(screen.getByRole("heading", { name: "Route not found" })).not.toBeNull();
    window.history.pushState({}, "", "/");
    await act(async () => window.dispatchEvent(new PopStateEvent("popstate")));
    expect(screen.getByRole("heading", { name: "Find the first unsafe behavior after a controlled failure." })).not.toBeNull();
  });
});

describe("regression provenance and rerun", () => {
  it("shows immutable provenance and submits the fixed-v1 comparison with a fresh key", async () => {
    window.history.replaceState({}, "", `/regressions/${IDS.regression}`);
    let rerunRequest: RequestInit | undefined;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = urlOf(input);
      if (init?.method === "POST") {
        rerunRequest = init;
        return Promise.resolve(jsonResponse(wire({
          rerun_id: IDS.rerun,
          campaign_id: IDS.campaign,
          control_run_id: IDS.control,
          comparison_id: IDS.comparison,
          status: "accepted",
          links,
          replayed: false,
        }), 202));
      }
      if (url.includes("/regression-cases/")) return Promise.resolve(jsonResponse(wire(regressionFixture())));
      throw new Error(`Unexpected fetch ${url}`);
    }));
    render(<App />);
    expect(await screen.findByText("Immutable source artifact")).not.toBeNull();
    expect(screen.getByText((content) => content.includes("phase1 lookup"))).not.toBeNull();
    expect(screen.getByText((content) => content.includes("tool_timeout"))).not.toBeNull();
    expect(screen.getByDisplayValue("version_comparison")).not.toBeNull();
    expect(screen.getByDisplayValue("fixed-v1")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Start fixed-v1 comparison" }));
    expect(await screen.findByRole("heading", { name: "Rerun accepted" })).not.toBeNull();
    expect(JSON.parse(String(rerunRequest?.body))).toEqual({ mode: "version_comparison", tested_agent_version: "fixed-v1" });
    const headers = new Headers(rerunRequest?.headers);
    expect(headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/i);
    expect(screen.getByRole("link", { name: "Follow rerun campaign" }).getAttribute("href")).toBe(`/?campaign=${IDS.campaign}`);
    expect(screen.getByRole("link", { name: "Follow comparison" }).getAttribute("href")).toBe(`/comparisons/${IDS.comparison}`);
  });
});

describe("comparison states and invariance", () => {
  it.each([
    ["pending", false, null, null],
    ["valid", true, "PASS", "VULNERABLE_FAIL_FIXED_PASS"],
    ["ineligible", true, "FAIL", "CANDIDATE_DID_NOT_PASS"],
    ["execution_error", true, "EXECUTION_ERROR", "CANDIDATE_EXECUTION_ERROR"],
    ["cancelled", true, "INCOMPLETE", "CAMPAIGN_CANCELLED"],
  ] as const)("renders %s comparison state", async (status, terminal, candidateResult, reason) => {
    window.history.replaceState({}, "", `/comparisons/${IDS.comparison}`);
    const comparison = comparisonFixture({
      status,
      terminal,
      candidate_policy_result: candidateResult,
      terminal_reason: reason,
      scoped_conclusion: status === "valid" ? "The fixed tested-agent version passes this scenario policy." : null,
      summary_digest: terminal ? "b".repeat(64) : null,
    });
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(wire(comparison)))));
    render(<App />);
    expect(await screen.findByText(`Version comparison · ${status}`)).not.toBeNull();
    if (reason !== null) expect(screen.getAllByText(new RegExp(reason)).length).toBeGreaterThan(0);
    if (status === "valid") expect(screen.getByText("The fixed tested-agent version passes this scenario policy.")).not.toBeNull();
    expect(screen.queryByText(/production ready/i)).toBeNull();
  });

  it("separates matches, permitted differences, and mismatches", async () => {
    window.history.replaceState({}, "", `/comparisons/${IDS.comparison}`);
    const mismatch = {
      field_identifier: "policy_version",
      source_value_or_digest: "v1",
      rerun_value_or_digest: "v2",
      comparison_rule: "must match",
      result: "MISMATCH" as const,
      authoritative_references: [IDS.regression],
    };
    const comparison = comparisonFixture({
      status: "ineligible",
      candidate_policy_result: "PASS",
      terminal_reason: "INVARIANT_MISMATCH",
      scoped_conclusion: null,
      completed_invariance_rows: [...comparisonFixture().completed_invariance_rows, mismatch],
      mismatches: [mismatch],
    });
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(wire(comparison)))));
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Invariant matches" })).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Permitted differences" })).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Mismatches" })).not.toBeNull();
    expect(screen.getByText("policy_version")).not.toBeNull();
  });

  it("renders only application-owned navigation targets", async () => {
    window.history.replaceState({}, "", `/comparisons/${IDS.comparison}`);
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(wire(comparisonFixture())))));
    render(<App />);
    await screen.findByRole("heading", { name: "Terminal comparison" });
    const anchors = Array.from(document.querySelectorAll("a"));
    expect(anchors.length).toBeGreaterThan(0);
    expect(anchors.every((anchor) => {
      const href = anchor.getAttribute("href");
      return href !== null && href.startsWith("/") && !href.startsWith("//") && !href.includes("javascript:");
    })).toBe(true);
  });
});

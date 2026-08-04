import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { POLL_INTERVAL_MS } from "../src/api/polling";
import { campaignAccepted, campaignFixture, IDS, jsonResponse, wire } from "./fixtures";

function urlOf(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
}

describe("campaign start, polling, and cancellation", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
  });

  it("accepts a start, polls without overlap, and stops at terminal state", async () => {
    vi.useFakeTimers();
    let campaignReads = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = urlOf(input);
      if (init?.method === "POST") return Promise.resolve(jsonResponse(wire(campaignAccepted), 202));
      if (url.includes(`/campaigns/${IDS.campaign}`)) {
        campaignReads += 1;
        const campaign = campaignReads === 1
          ? campaignFixture({ operational_status: "running", current_step: "control_running", terminal: false, control_run_id: null, injected_run_id: null, regression_case_id: null })
          : campaignFixture({ control_run_id: null, injected_run_id: null, regression_case_id: null });
        return Promise.resolve(jsonResponse(wire(campaign)));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start bundled vulnerable campaign" }));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(window.location.search).toBe(`?campaign=${IDS.campaign}`);
    expect(screen.getByText("control_running")).not.toBeNull();
    expect(campaignReads).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(screen.getAllByText("completed").length).toBeGreaterThan(0);
    expect(campaignReads).toBe(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 4);
    });
    expect(campaignReads).toBe(2);
  });

  it("prevents duplicate cancellation submissions while a request is active", async () => {
    window.history.replaceState({}, "", `/?campaign=${IDS.campaign}`);
    let resolveCancellation: ((response: Response) => void) | undefined;
    const cancellation = new Promise<Response>((resolve) => { resolveCancellation = resolve; });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = urlOf(input);
      if (init?.method === "POST" && url.endsWith("/cancel")) return cancellation;
      if (url.includes(`/campaigns/${IDS.campaign}`)) {
        return Promise.resolve(jsonResponse(wire(campaignFixture({
          operational_status: "running",
          current_step: "injected_running",
          terminal: false,
          control_run_id: null,
          injected_run_id: null,
          regression_case_id: null,
        }))));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const cancel = await screen.findByRole("button", { name: "Cancel active campaign" });
    fireEvent.click(cancel);
    fireEvent.click(cancel);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Requesting cancellation…" }).hasAttribute("disabled")).toBe(true);

    await act(async () => {
      resolveCancellation?.(jsonResponse(wire({
        campaign_id: IDS.campaign,
        cancellation_id: "00000000-0000-4000-8000-000000000098",
        cancel_requested: true,
        operational_status: "running",
        terminal: false,
        replayed: false,
      }), 202));
      await cancellation;
    });
    await waitFor(() => expect(screen.getByText("injected_running")).not.toBeNull());
  });

  it("aborts in-flight route work and ignores its late response", async () => {
    window.history.replaceState({}, "", `/runs/${IDS.injected}`);
    let signal: AbortSignal | undefined;
    let resolveRun: ((response: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => { resolveRun = resolve; });
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signal = init?.signal as AbortSignal;
      return pending;
    }));
    const view = render(<App />);
    expect(screen.getByText("Loading run and deterministic analysis")).not.toBeNull();
    view.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      resolveRun?.(jsonResponse({ malformed: true }));
      await pending;
    });
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

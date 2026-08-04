import { expect, test, type Locator, type Page } from "@playwright/test";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const CONCLUSION = "The fixed tested-agent version passes this scenario policy.";

function pathId(page: Page, resource: string): string {
  const match = new URL(page.url()).pathname.match(new RegExp(`^/${resource}/([0-9a-f-]{36})$`, "i"));
  if (match === null || !UUID.test(match[1] ?? "")) throw new Error(`missing ${resource} ID in ${page.url()}`);
  return match[1] as string;
}

async function selectedEvidenceId(page: Page, panel: Locator): Promise<string> {
  const reference = panel.getByRole("button", { name: /^Select evidence receipt/ }).first();
  await expect(reference).toBeVisible();
  await reference.click();
  const match = new URL(page.url()).hash.match(/^#evidence-([0-9a-f-]{36})$/i);
  if (match === null || !UUID.test(match[1] ?? "")) throw new Error("evidence reference did not select an authoritative row");
  return match[1] as string;
}

async function waitForCampaignLinks(page: Page, names: string[]): Promise<void> {
  let state = "pending";
  await expect.poll(async () => {
    if (await page.getByRole("alert").isVisible().catch(() => false)) {
      state = "failed";
      return state;
    }
    const visible = await Promise.all(names.map(async (name) => (
      await page.getByRole("link", { name }).count() === 1
      && await page.getByRole("link", { name }).isVisible()
    )));
    state = visible.every(Boolean) ? "ready" : "pending";
    return state;
  }, { message: `campaign did not publish ${names.join(", ")}` }).toMatch(/^(ready|failed)$/);
  if (state === "failed") {
    throw new Error(`campaign ended before required links were published: ${await page.getByRole("alert").innerText()}`);
  }
}

test("completes the vulnerable-to-fixed Compose portfolio workflow", async ({ page }, testInfo) => {
  const ids: Record<string, string> = {};
  const diagnostics: string[] = [];
  page.on("console", (message) => diagnostics.push(`console:${message.type()}:${message.text()}`));
  page.on("requestfailed", (request) => diagnostics.push(`requestfailed:${request.method()}:${request.url()}:${request.failure()?.errorText ?? "unknown"}`));
  page.on("response", (response) => {
    if (response.status() >= 500) diagnostics.push(`response:${response.status()}:${response.url()}`);
  });

  try {
    await page.goto("/");
    await page.getByRole("button", { name: "Start bundled vulnerable campaign" }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get("campaign"), { message: "campaign acceptance did not publish its ID" }).toMatch(UUID);
    ids.vulnerable_campaign_id = new URL(page.url()).searchParams.get("campaign") as string;

    await waitForCampaignLinks(page, ["Open control run", "Inspect injected run", "Open immutable regression case"]);

    await page.getByRole("link", { name: "Open control run" }).click();
    ids.vulnerable_control_run_id = pathId(page, "runs");
    await expect(page.getByRole("heading", { name: "Execution evidence and policy analysis" })).toBeVisible();
    await expect(page.getByLabel("Operational and policy status")).toContainText("completed");
    await expect(page.getByText("boundary.sample-agent · vulnerable-v1", { exact: true }).first()).toBeVisible();

    await page.getByRole("link", { name: "Campaign" }).click();
    await page.getByRole("link", { name: "Inspect injected run" }).click();
    ids.vulnerable_injected_run_id = pathId(page, "runs");
    await expect(page.getByLabel("Operational and policy status")).toContainText("FAIL");
    await expect(page.getByText("Fails this scenario policy", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Ordered evidence" })).toBeVisible();
    const receipts = await page.locator(".evidence-receipt").allTextContents();
    expect(receipts.length).toBeGreaterThan(0);
    expect(receipts.map((value) => Number(value.slice(1)))).toEqual(
      receipts.map((value) => Number(value.slice(1))).toSorted((left, right) => left - right),
    );

    const injection = page.locator(".diagnostic-injection");
    const divergence = page.locator(".diagnostic-divergence");
    const symptoms = page.locator(".diagnostic-symptoms");
    await expect(injection.getByRole("heading", { name: "Injection boundary" })).toBeVisible();
    await expect(divergence.getByRole("heading", { name: "First unsafe divergence" })).toBeVisible();
    await expect(symptoms.getByRole("heading", { name: "Downstream symptoms" })).toBeVisible();
    await expect(injection).toContainText("tool_execution");
    await expect(injection).toContainText("0, 1");
    await expect(divergence).toContainText("retry_control");
    await expect(divergence).toContainText("retry ordinal 2");
    await expect(divergence).toContainText("P1.RETRY_LIMIT");
    await expect(symptoms.getByRole("button", { name: /^Select evidence receipt/ }).first()).toBeVisible();
    const injectionEvidenceId = await selectedEvidenceId(page, injection);
    const divergenceEvidenceId = await selectedEvidenceId(page, divergence);
    const symptomEvidenceId = await selectedEvidenceId(page, symptoms);
    expect(injectionEvidenceId).toMatch(UUID);
    expect(divergenceEvidenceId).toMatch(UUID);
    expect(symptomEvidenceId).toMatch(UUID);

    await page.reload();
    await expect(page.getByLabel("Operational and policy status")).toContainText("FAIL");
    await expect(page.getByRole("heading", { name: "Ordered evidence" })).toBeVisible();

    await page.getByRole("link", { name: "Open immutable regression case" }).click();
    ids.regression_case_id = pathId(page, "regressions");
    await expect(page.getByRole("heading", { name: "Reproduce the failed boundary without redefining it." })).toBeVisible();
    await expect(page.getByText(ids.vulnerable_injected_run_id, { exact: true }).first()).toBeVisible();
    await page.getByRole("button", { name: "Start fixed-v1 comparison" }).click();
    await expect(page.getByRole("heading", { name: "Rerun accepted" })).toBeVisible();
    const rerunCampaignHref = await page.getByRole("link", { name: "Follow rerun campaign" }).getAttribute("href");
    const comparisonHref = await page.getByRole("link", { name: "Follow comparison" }).getAttribute("href");
    if (rerunCampaignHref === null || comparisonHref === null) throw new Error("rerun links were not authoritative application links");
    ids.rerun_campaign_id = new URL(rerunCampaignHref, page.url()).searchParams.get("campaign") ?? "";
    ids.comparison_id = comparisonHref.split("/").at(-1) ?? "";
    if (!UUID.test(ids.rerun_campaign_id) || !UUID.test(ids.comparison_id)) throw new Error("rerun acceptance returned invalid IDs");

    await page.getByRole("link", { name: "Follow rerun campaign" }).click();
    await waitForCampaignLinks(page, ["Open control run", "Inspect injected run"]);

    await page.getByRole("link", { name: "Open control run" }).click();
    ids.fixed_control_run_id = pathId(page, "runs");
    await expect(page.getByLabel("Operational and policy status")).toContainText("completed");
    await expect(page.getByText("boundary.sample-agent · fixed-v1", { exact: true }).first()).toBeVisible();
    await page.getByRole("link", { name: "Campaign" }).click();
    await page.getByRole("link", { name: "Inspect injected run" }).click();
    ids.fixed_injected_run_id = pathId(page, "runs");
    await expect(page.getByLabel("Operational and policy status")).toContainText("PASS");
    await expect(page.getByText("Passes this scenario policy", { exact: true })).toBeVisible();
    await expect(page.locator(".diagnostic-injection")).toContainText("tool_execution");
    await expect(page.locator(".diagnostic-divergence")).toContainText("No first unsafe divergence was reported.");

    await page.goto(comparisonHref);
    await expect(page.getByRole("heading", { name: "Terminal comparison" })).toBeVisible();
    await expect(page.getByRole("status")).toHaveText(CONCLUSION);
    await expect(page.getByRole("heading", { name: "Invariance comparison" })).toBeVisible();
    await expect(page.getByRole("table").first().getByRole("row").nth(1)).toBeVisible();
    await expect(page.getByText("MISMATCH", { exact: true })).toHaveCount(0);
    await expect(page.locator(".comparison-status-grid > div").first()).toContainText("FAIL");
    await expect(page.locator(".comparison-status-grid > div").nth(1)).toContainText("PASS");

    await page.reload();
    expect(pathId(page, "comparisons")).toBe(ids.comparison_id);
    await expect(page.getByRole("status")).toHaveText(CONCLUSION);
  } finally {
    console.log(`BOUNDARY_IDS ${JSON.stringify(ids)}`);
    await testInfo.attach("boundary-identifiers.json", {
      body: JSON.stringify(ids, null, 2),
      contentType: "application/json",
    });
    await testInfo.attach("browser-diagnostics.txt", {
      body: diagnostics.join("\n"),
      contentType: "text/plain",
    });
  }
});

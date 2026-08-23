import { expect, test, type APIRequestContext } from "@playwright/test";

const API = process.env.PLAYWRIGHT_API_BASE || "http://localhost:8000";
const KEY = process.env.PLAYWRIGHT_API_KEY || "dyb-pro-demo-scientist";
const headers = { "X-API-Key": KEY };

type Project = { id: string; is_demo: boolean };

/**
 * Seed over the API rather than through the button: the campaign runs real research passes and takes
 * ~1 minute, so paying for it once in setup keeps the UI assertions about the UI.
 */
async function seedCampaign(request: APIRequestContext): Promise<Project> {
  const seeded = await request.post(`${API}/api/demo/seed`, { headers });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  const projects: Project[] = await (
    await request.get(`${API}/api/projects`, { headers })
  ).json();
  const demo = projects.find((p) => p.is_demo) || projects[0];
  const campaign = await request.post(`${API}/api/lab/projects/${demo.id}/research/seed-campaign`, {
    headers,
    timeout: 600_000,
  });
  expect(campaign.ok(), await campaign.text()).toBeTruthy();
  return demo;
}

test.describe("demo path", () => {
  test.beforeAll(async ({ request }) => {
    await seedCampaign(request);
  });

  test("api exposes the seeded campaign: 3 versions, papers, shrinking drift, a proposal", async ({
    request,
  }) => {
    const projects: Project[] = await (
      await request.get(`${API}/api/projects`, { headers })
    ).json();
    const demo = projects.find((p) => p.is_demo) || projects[0];

    const overview = await (
      await request.get(`${API}/api/lab/projects/${demo.id}/research`, { headers })
    ).json();
    expect(overview.digest.version_count).toBeGreaterThanOrEqual(3);
    expect(overview.digest.paper_count).toBeGreaterThan(0);
    expect(overview.digest.result_count).toBeGreaterThanOrEqual(3);
    expect(overview.learned.headline).toBeTruthy();

    const drift = await (
      await request.get(`${API}/api/lab/projects/${demo.id}/research/drift`, { headers })
    ).json();
    const tm = drift.find((d: { metric: string }) => d.metric === "melting_temperature");
    expect(tm.n_observations).toBeGreaterThanOrEqual(3);
    expect(Math.abs(tm.history.at(-1).residual)).toBeLessThan(Math.abs(tm.history[0].residual));

    const proposal = await (
      await request.get(`${API}/api/lab/projects/${demo.id}/research/proposal`, { headers })
    ).json();
    expect(proposal.proposed.mutations.length).toBeGreaterThan(0);
    expect(proposal.target_metric).toBeTruthy();

    // reseeding is idempotent: the campaign history is never rebuilt on top of itself
    const again = await request.post(`${API}/api/lab/projects/${demo.id}/research/seed-campaign`, {
      headers,
      timeout: 120_000,
    });
    expect((await again.json()).seeded).toBe(false);
  });

  test("every control on the 90-second path works", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("tab", { name: "Research lab" }).click();

    // pick the campaign: the demo project auto-selects and its history is summarised
    const summary = page.getByTestId("campaign-summary");
    await expect(summary).toContainText(/3 versions/, { timeout: 60_000 });
    await expect(summary).toContainText(/papers/);
    const daemonPane = page.locator("section", { has: page.getByTestId("daemon-tick") });
    await expect(daemonPane).toContainText(/Daemon:/);
    await expect(daemonPane).toContainText(/local-simulation|devin/);

    // the daemon reacts to a label
    await page.locator(".seqgrid button").nth(11).click();
    await page.getByPlaceholder(/Label name/).fill("e2e liability probe");
    await page.getByTestId("label-create").click();
    await expect(daemonPane).toContainText(/label_changed/, { timeout: 30_000 });

    // end of the sitting: one explicit handoff bundles it into a real queued swarm task
    await page.getByTestId("daemon-handoff").click();
    const receipt = page.getByTestId("handoff-receipt");
    await expect(receipt).toContainText(/handoff_requested is (queued|coalesced)/, {
      timeout: 30_000,
    });
    // the provider is stated as it is; no Devin session is claimed unless one exists
    await expect(receipt).toContainText(/Provider (devin|local-simulation|unavailable)/);
    await expect(daemonPane).toContainText(/handoff_requested/);

    // ...and running the due work clears it, writing new research events
    await page.getByTestId("daemon-refresh").click();
    await page.getByTestId("daemon-tick").click();
    await expect(page.getByTestId("daemon-tick")).toHaveText(/Run due work now/, {
      timeout: 240_000,
    });
    const feed = page.locator("section", { has: page.getByRole("button", { name: /Cached papers/ }) });
    await expect(feed.getByRole("button", { name: /Research events \([1-9]/ })).toBeVisible();

    // the immutable paper cache is browsable
    await feed.getByRole("button", { name: /Cached papers \([1-9]/ }).click();
    await expect(feed.locator(".event").first()).toBeVisible();

    // wet-lab loop: propose a pack, then register a simulated measurement on this version
    const wetlab = page.locator("section", { has: page.getByTestId("wetlab-plan") });
    await page.getByTestId("wetlab-plan").click();
    await expect(wetlab.getByText(/Info\/\$/)).toBeVisible({ timeout: 180_000 });
    await page.getByTestId("wetlab-simulate").click();
    await expect(wetlab.getByText(/Measured vs predicted/)).toBeVisible({ timeout: 180_000 });
    await expect(wetlab.getByText("simulator", { exact: true })).toBeVisible();

    // and the campaign explains itself: drift, then the next-version proposal
    const learned = page.locator("section", { has: page.getByTestId("proposal-refresh") });
    await expect(learned).toContainText(/rmse/);
    await expect(learned).toContainText(/shrinking|not yet/);
    await page.getByTestId("proposal-refresh").click();
    await expect(learned).toContainText(/Target /, { timeout: 180_000 });
    await expect(learned).toContainText(/Mutations:/);

    // no control on the path left the app in an error state
    await expect(page.locator(".err")).toHaveCount(0);
  });
});

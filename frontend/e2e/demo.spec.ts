import { expect, test, type APIRequestContext } from "@playwright/test";

const API = process.env.PLAYWRIGHT_API_BASE || "http://localhost:8000";
const KEY = process.env.PLAYWRIGHT_API_KEY || "dyb-pro-demo-scientist";
const headers = { "X-API-Key": KEY };

type Project = { id: string; is_demo: boolean };
type Commit = { id: string; label: string; short_id: string };
type Label = { id: string; kind: string; name: string; residues: number[] };
type Result = { id: string; plan_id: string | null; source: string; measurements: Record<string, unknown> };
type DaemonTask = { id: string; kind: string; status: string };
type Overview = {
  daemon: { status: string; tasks: DaemonTask[] };
  digest: { version_count: number; paper_count: number; result_count: number };
  learned: { headline: string; lessons: string[]; drift: { metric: string; shrinking: boolean }[] };
};

async function json<T>(request: APIRequestContext, path: string): Promise<T> {
  const res = await request.get(`${API}/api${path}`, { headers, timeout: 120_000 });
  expect(res.ok(), `${path}: ${await res.text()}`).toBeTruthy();
  return (await res.json()) as T;
}

async function demoProject(request: APIRequestContext): Promise<Project> {
  const projects = await json<Project[]>(request, "/projects");
  const demo = projects.find((p) => p.is_demo) || projects[0];
  expect(demo, "the demo project must exist after seeding").toBeTruthy();
  return demo;
}

/** The version the workspace opens on: `GET /projects/{id}/commits` is newest-first. */
async function headCommit(request: APIRequestContext, projectId: string): Promise<Commit> {
  const commits = await json<Commit[]>(request, `/projects/${projectId}/commits`);
  return commits[0];
}

/**
 * Seed over the API rather than through the button: the campaign runs real research passes and takes
 * ~1 minute, so paying for it once in setup keeps the UI assertions about the UI.
 */
async function seedCampaign(request: APIRequestContext): Promise<Project> {
  const seeded = await request.post(`${API}/api/demo/seed`, { headers });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  const demo = await demoProject(request);
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
    const demo = await demoProject(request);

    const overview = await json<Overview>(request, `/lab/projects/${demo.id}/research`);
    expect(overview.digest.version_count).toBeGreaterThanOrEqual(3);
    expect(overview.digest.paper_count).toBeGreaterThan(0);
    expect(overview.digest.result_count).toBeGreaterThanOrEqual(3);
    expect(overview.learned.headline).toBeTruthy();

    const drift = await json<{ metric: string; n_observations: number; history: { residual: number }[] }[]>(
      request,
      `/lab/projects/${demo.id}/research/drift`,
    );
    const tm = drift.find((d) => d.metric === "melting_temperature")!;
    expect(tm.n_observations).toBeGreaterThanOrEqual(3);
    expect(Math.abs(tm.history.at(-1)!.residual)).toBeLessThan(Math.abs(tm.history[0].residual));

    const proposal = await json<{ proposed: { mutations: string[] }; target_metric: string }>(
      request,
      `/lab/projects/${demo.id}/research/proposal`,
    );
    expect(proposal.proposed.mutations.length).toBeGreaterThan(0);
    expect(proposal.target_metric).toBeTruthy();

    // reseeding is idempotent: the campaign history is never rebuilt on top of itself
    const again = await request.post(`${API}/api/lab/projects/${demo.id}/research/seed-campaign`, {
      headers,
      timeout: 120_000,
    });
    expect((await again.json()).seeded).toBe(false);
  });

  test("every control on the 90-second path works", async ({ page, request }) => {
    const demo = await demoProject(request);
    const head = await headCommit(request, demo.id);
    const before = {
      labels: (await json<{ labels: Label[] }>(request, `/lab/commits/${head.id}/labels`)).labels.length,
      // events are capped by `limit`, so compare the monotonic sequence number instead of a count
      eventSeq: (
        await json<{ sequence_no: number }[]>(request, `/lab/projects/${demo.id}/research/events?limit=1`)
      )[0].sequence_no,
      papers: (await json<unknown[]>(request, `/lab/projects/${demo.id}/research/papers?limit=300`)).length,
      results: (await json<Result[]>(request, `/lab/commits/${head.id}/wetlab/results`)).length,
      plan: (await json<{ id?: string } | null>(request, `/lab/commits/${head.id}/wetlab/plan`))?.id,
    };

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

    // server state: the label is persisted on residue 12 and the daemon has queued work for it
    const afterLabel = await json<{ labels: Label[] }>(request, `/lab/commits/${head.id}/labels`);
    expect(afterLabel.labels.length).toBe(before.labels + 1);
    const created = afterLabel.labels.find((lb) => lb.name === "e2e liability probe")!;
    expect(created, "the label click must reach POST /lab/commits/{id}/labels").toBeTruthy();
    expect(created.residues).toContain(12);
    const queued = await json<Overview["daemon"]>(request, `/lab/projects/${demo.id}/research/daemon`);
    expect(queued.tasks.some((t) => t.kind === "label_changed")).toBeTruthy();

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

    // server state: the pass appended events, kept the paper cache, and left nothing failed behind
    const events = await json<{ kind: string; sequence_no: number }[]>(
      request,
      `/lab/projects/${demo.id}/research/events?limit=60`,
    );
    expect(events[0].sequence_no).toBeGreaterThan(before.eventSeq);
    const daemonAfterTick = await json<Overview["daemon"]>(
      request,
      `/lab/projects/${demo.id}/research/daemon`,
    );
    expect(daemonAfterTick.tasks.filter((t) => t.status === "failed")).toHaveLength(0);
    expect(daemonAfterTick.tasks.some((t) => t.status === "done")).toBeTruthy();

    // the immutable paper cache is browsable, and the pass never drops cached papers
    await feed.getByRole("button", { name: /Cached papers \([1-9]/ }).click();
    await expect(feed.locator(".event").first()).toBeVisible();
    const papers = await json<{ paper_key: string }[]>(
      request,
      `/lab/projects/${demo.id}/research/papers?limit=300`,
    );
    expect(papers.length).toBeGreaterThanOrEqual(before.papers);
    expect(papers.length).toBeGreaterThan(0);

    // wet-lab loop: propose a pack, then register a simulated measurement on this version
    const wetlab = page.locator("section", { has: page.getByTestId("wetlab-plan") });
    await page.getByTestId("wetlab-plan").click();
    await expect(wetlab.getByText(/Info\/\$/)).toBeVisible({ timeout: 180_000 });
    await page.getByTestId("wetlab-simulate").click();
    await expect(wetlab.getByText(/Measured vs predicted/)).toBeVisible({ timeout: 180_000 });
    await expect(wetlab.getByText("simulator", { exact: true })).toBeVisible();

    // server state: a new plan was persisted and the simulated result is attached to it
    const plan = await json<{ id: string; commit_id: string; assays: unknown[]; total_cost_usd: number }>(
      request,
      `/lab/commits/${head.id}/wetlab/plan`,
    );
    expect(plan.id).not.toBe(before.plan);
    expect(plan.commit_id).toBe(head.id);
    expect(plan.assays.length).toBeGreaterThan(0);
    const results = await json<Result[]>(request, `/lab/commits/${head.id}/wetlab/results`);
    expect(results.length).toBe(before.results + 1);
    const measured = results.find((r) => r.plan_id === plan.id)!;
    expect(measured, "the simulate click must persist a result for the new plan").toBeTruthy();
    expect(measured.source).toBe("simulator");
    expect(Object.keys(measured.measurements).length).toBeGreaterThan(0);

    // and the campaign explains itself: drift, then the next-version proposal
    const learned = page.locator("section", { has: page.getByTestId("proposal-refresh") });
    await expect(learned).toContainText(/rmse/);
    await expect(learned).toContainText(/shrinking|not yet/);
    await page.getByTestId("proposal-refresh").click();
    await expect(learned).toContainText(/Target /, { timeout: 180_000 });
    await expect(learned).toContainText(/Mutations:/);

    // server state: the v4 proposal is computed off the measured head, and the "v1 -> latest"
    // insight names both ends of the path
    const proposal = await json<{
      proposed: { mutations: string[]; sequence: string };
      target_metric: string;
      excluded_by_history: string[];
    }>(request, `/lab/projects/${demo.id}/research/proposal`);
    expect(proposal.proposed.mutations.length).toBeGreaterThan(0);
    expect(proposal.proposed.sequence).toBeTruthy();
    // the proposal is a genuinely new version: nothing the campaign already committed is re-proposed
    expect(proposal.excluded_by_history).not.toContain(proposal.proposed.mutations.join("+"));
    const learnedState = await json<Overview["learned"] & { versions: { label: string }[] }>(
      request,
      `/lab/projects/${demo.id}/research/learned`,
    );
    expect(learnedState.versions.length).toBeGreaterThanOrEqual(3);
    const first = learnedState.versions[0].label;
    const latest = learnedState.versions.at(-1)!.label;
    expect(
      learnedState.lessons.some((l) => l.includes(first) && l.includes(latest)),
      `no v1 -> latest lesson in ${JSON.stringify(learnedState.lessons)}`,
    ).toBeTruthy();
    expect(learnedState.lessons.some((l) => /prediction error (shrank|grew)/.test(l))).toBeTruthy();
    expect(learnedState.drift.some((d) => d.shrinking)).toBeTruthy();

    // no control on the path left the app in an error state
    await expect(page.locator(".err")).toHaveCount(0);
  });
});

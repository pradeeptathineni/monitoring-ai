import { test, expect } from "@playwright/test";

test("optional OS samples remain separate from session state and stale reads", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  const sampled = new Date().toISOString();
  base.os_processes = {
    schema: "monitoring.os-processes.v1", state: "following", sampled_at: sampled,
    coverage: "matching-executable-names-only", interval_seconds: 5, changed_count: 1, truncated: false,
    history_truncated: false,
    history: [
      { sampled_at: new Date(Date.now() - 10_000).toISOString(), segment: 0, count: 2, cpu_percent: 5, rss_bytes: 8388608 },
      { sampled_at: new Date(Date.now() - 5_000).toISOString(), segment: 0, count: 1, cpu_percent: 12.5, rss_bytes: 4194304 },
      { sampled_at: sampled, segment: 1, count: 1, cpu_percent: 7.5, rss_bytes: 5242880 },
    ],
    no_longer_observed: [], processes: [{ pid: 401, parent_pid: 10, executable: "codex",
      cpu_percent: 12.5, rss_bytes: 4194304, started_at: sampled, session: null, worker: null },
      { pid: 402, parent_pid: 1, executable: "Claude", cpu_percent: 0.2,
        rss_bytes: 2097152, started_at: sampled, session: null, worker: null }],
  };
  base.source_coverage = {
    schema: "monitoring.source-coverage.v1", observed_at: sampled,
    scope: "latest-accepted-input-reads", complete_coverage: false,
    inputs: [{ id: "historical-codex", format: "codex", project: "demo",
      capabilities: { usage: "native-reconstruction", lifecycle: "unsupported", tools: "completed-items" },
      sampled_at: sampled, records: { total: 3, usage: 2, status: 0, tool: 1, sessions: 1, entities: 1, event_sources: 1 },
      latest_event_at: new Date(Date.now() - 7200000).toISOString(), latest_usage_at: new Date(Date.now() - 7200000).toISOString(),
      latest_status_at: null, latest_tool_at: null,
      read_state: "following", retained: false, event_age_seconds: 7200, event_freshness: "stale" }],
  };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: base }));
  await page.goto("/");
  await expect(page.locator("#os-process-overview-panel")).toBeVisible();
  await expect(page.locator("#overview > article:visible").first()).toHaveId("os-process-overview-panel");
  await expect(page.locator("#os-process-overview")).toContainText("5s interval");
  await expect(page.locator("#os-process-overview")).toContainText("No process is linked");
  await expect(page.locator("#os-process-overview")).toContainText("3 successful reads");
  await expect(page.locator("#os-process-overview")).toContainText("Gaps separate unavailable or missed reads");
  await expect(page.locator("#os-process-overview")).toContainText("Combined OS CPU · latest 7.5%");
  await expect(page.locator("#os-process-overview .process-trend-row")).toHaveCount(3);
  await expect(page.locator("#os-process-overview polyline")).toHaveCount(3);
  await expect(page.locator("#os-process-overview circle")).toHaveCount(3);
  await expect(page.locator("#os-process-overview .process-source-coverage")).toContainText("Codex executable observed in a recent OS sample");
  await expect(page.locator("#os-process-overview .process-source-coverage")).toContainText("1 selected Codex input; Stale");
  await expect(page.locator("#os-process-overview .process-source-coverage")).toContainText("No Claude session file is selected in this owner");
  await expect(page.locator("#os-process-overview .process-source-coverage")).toContainText("No process-to-file or process-to-session link is known");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.locator("#os-process-overview .process-coverage-action").click();
  await expect(page.locator("#settings")).toBeVisible();
  await expect(page).toHaveURL(/#settings\/sources$/);
  await page.locator('#primary-navigation [data-view="sessions"]').click();
  await expect(page.locator("#os-process-panel")).toBeVisible();
  await expect(page.locator("#os-process-status")).toContainText("Session and worker mappings are unknown");
  await expect(page.locator("#os-process-table")).toContainText("codex");
  await expect(page.locator("#os-process-table")).toContainText("401 / 10");
  await expect(page.locator("#os-process-table")).toContainText("Unknown");
  await expect(page.locator("#entity-table")).not.toContainText("401 / 10");
  await page.locator("#pause").click();
  await expect(page.locator("#os-process-status")).toContainText("current process presence is unknown");
  await expect(page.locator("#os-process-overview .process-source-coverage")).toHaveCount(0);

  base.os_processes = { ...base.os_processes, state: "unavailable", sampled_at: null, processes: [] };
  await page.reload();
  await page.locator('#primary-navigation [data-view="sessions"]').click();
  await expect(page.locator("#os-process-status")).toContainText("Current process presence is unknown");
  await expect(page.locator("#os-process-table")).not.toContainText("401 / 10");
  await expect(page.locator("#os-process-overview")).toContainText("Current process presence is unknown");
  await expect(page.locator("#os-process-overview .process-trend-row")).toHaveCount(3);
});


test("process drill-down keeps PID/start identity and browser sample history", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  const started = new Date(Date.now() - 60000).toISOString();
  const first = { pid: 401, parent_pid: 10, executable: "codex", cpu_percent: 9, rss_bytes: 4194304, started_at: started, session: null, worker: null };
  const second = { ...first, pid: 402, executable: "Claude", cpu_percent: 1, rss_bytes: 8388608 };
  base.os_processes = { schema: "monitoring.os-processes.v1", state: "following", sampled_at: new Date().toISOString(),
    interval_seconds: 5, processes: [first, second], no_longer_observed: [], changed_count: 0, truncated: false,
    history: [], history_truncated: false, coverage: "matching-executable-names-only" };
  await page.route("**/api/v1/snapshot?**", route => route.fulfill({ json: base }));
  await page.goto("/#sessions");
  await expect(page.locator("#os-process-table tbody tr").first()).toContainText("401 / 10");
  await page.locator("#os-process-sort").selectOption("memory");
  await expect(page.locator("#os-process-table tbody tr").first()).toContainText("402 / 10");
  await page.getByLabel("Executable or PID", { exact: true }).fill("401");
  await expect(page.locator("#os-process-table tbody tr")).toHaveCount(1);
  await page.getByRole("button", { name: "Inspect codex PID 401", exact: true }).click();
  await expect(page.locator("#os-process-detail")).toContainText("Waiting for another distinct OS sample");
  base.os_processes.sampled_at = new Date().toISOString();
  base.os_processes.processes = [{ ...first, cpu_percent: 2, rss_bytes: 6291456 }, second];
  await expect(page.locator("#os-process-detail")).toContainText("2 successful reads");
  await expect(page.locator("#os-process-detail .process-trend-row")).toHaveCount(2);
  await expect(page.locator("#os-process-detail")).toContainText("OS CPU · latest 2.0%");
  await page.locator("#pause").click();
  await expect(page.locator("#os-process-detail")).toContainText("Current presence is unknown");
  base.os_processes.sampled_at = new Date().toISOString();
  base.os_processes.processes = [{ ...first, cpu_percent: 3 }, second];
  await page.locator("#pause").click();
  await expect(page.locator("#os-process-detail")).toContainText("3 successful reads");
  await expect(page.locator("#os-process-detail circle")).toHaveCount(2);
  base.os_processes.sampled_at = new Date().toISOString();
  base.os_processes.processes = [{ ...first, started_at: new Date().toISOString(), cpu_percent: 45 }, second];
  await expect(page.locator("#os-process-detail")).toContainText("No longer observed");
  await expect(page.locator("#os-process-detail")).toContainText(started);
  await expect(page.locator("#os-process-detail")).not.toContainText("45.0%");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  base.identity = "other-process-owner";
  await expect(page.locator("#os-process-detail")).toBeEmpty();
});

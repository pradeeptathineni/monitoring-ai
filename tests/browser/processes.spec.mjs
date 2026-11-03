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
      cpu_percent: 12.5, rss_bytes: 4194304, started_at: sampled, session: null, worker: null }],
  };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: base }));
  await page.goto("/");
  await expect(page.locator("#os-process-overview-panel")).toBeVisible();
  await expect(page.locator("#overview > article").first()).toHaveId("os-process-overview-panel");
  await expect(page.locator("#os-process-overview")).toContainText("5s interval");
  await expect(page.locator("#os-process-overview")).toContainText("No process is linked");
  await expect(page.locator("#os-process-overview")).toContainText("3 successful reads");
  await expect(page.locator("#os-process-overview")).toContainText("Gaps separate unavailable or missed reads");
  await expect(page.locator("#os-process-overview")).toContainText("Combined OS CPU · latest 7.5%");
  await expect(page.locator("#os-process-overview .process-trend-row")).toHaveCount(3);
  await expect(page.locator("#os-process-overview polyline")).toHaveCount(3);
  await expect(page.locator("#os-process-overview circle")).toHaveCount(3);
  await page.locator('#primary-navigation [data-view="sessions"]').click();
  await expect(page.locator("#os-process-panel")).toBeVisible();
  await expect(page.locator("#os-process-status")).toContainText("Session and worker mappings are unknown");
  await expect(page.locator("#os-process-table")).toContainText("codex");
  await expect(page.locator("#os-process-table")).toContainText("401 / 10");
  await expect(page.locator("#os-process-table")).toContainText("Unknown");
  await expect(page.locator("#entity-table")).not.toContainText("401 / 10");
  await page.locator("#pause").click();
  await expect(page.locator("#os-process-status")).toContainText("current process presence is unknown");

  base.os_processes = { ...base.os_processes, state: "unavailable", sampled_at: null, processes: [] };
  await page.reload();
  await page.locator('#primary-navigation [data-view="sessions"]').click();
  await expect(page.locator("#os-process-status")).toContainText("Current process presence is unknown");
  await expect(page.locator("#os-process-table")).not.toContainText("401 / 10");
  await expect(page.locator("#os-process-overview")).toContainText("Current process presence is unknown");
  await expect(page.locator("#os-process-overview .process-trend-row")).toHaveCount(3);
});

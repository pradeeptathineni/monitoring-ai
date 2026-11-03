import { test, expect } from "@playwright/test";

test("optional OS samples remain separate from session state and stale reads", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  const sampled = new Date().toISOString();
  base.os_processes = {
    schema: "monitoring.os-processes.v1", state: "following", sampled_at: sampled,
    coverage: "matching-executable-names-only", changed_count: 1, truncated: false,
    no_longer_observed: [], processes: [{ pid: 401, parent_pid: 10, executable: "codex",
      cpu_percent: 12.5, rss_bytes: 4194304, started_at: sampled, session: null, worker: null }],
  };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: base }));
  await page.goto("/");
  await page.locator('#primary-navigation [data-view="sessions"]').click();
  await expect(page.locator("#os-process-panel")).toBeVisible();
  await expect(page.locator("#os-process-status")).toContainText("Session and worker mappings are unknown");
  await expect(page.locator("#os-process-table")).toContainText("codex");
  await expect(page.locator("#os-process-table")).toContainText("401 / 10");
  await expect(page.locator("#os-process-table")).toContainText("Unknown");
  await expect(page.locator("#entity-table")).not.toContainText("401 / 10");

  base.os_processes = { ...base.os_processes, state: "unavailable", sampled_at: null, processes: [] };
  await page.reload();
  await page.locator('#primary-navigation [data-view="sessions"]').click();
  await expect(page.locator("#os-process-status")).toContainText("Current process presence is unknown");
  await expect(page.locator("#os-process-table")).not.toContainText("401 / 10");
});

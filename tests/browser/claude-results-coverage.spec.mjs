import { test, expect } from "@playwright/test";

test("Claude CLI result coverage states its boundaries and setup is usable on mobile", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  snapshot.inputs = {
    schema: "monitoring.inputs.v1", configured: true, healthy_inputs: 1, unavailable_inputs: 0, unread_inputs: 0,
    inputs: [{ id: "claude-result-export", format: "claude-results", project: "my-app", state: "following",
      last_attempt_at: "2026-11-03T12:00:00Z", last_success_at: "2026-11-03T12:00:00Z",
      records_seen: 3, added: 3, replayed: 0, error: null }],
  };
  snapshot.source_coverage = {
    schema: "monitoring.source-coverage.v1", observed_at: "2026-11-03T12:00:00Z",
    scope: "latest-accepted-input-reads", complete_coverage: false,
    inputs: [{ id: "claude-result-export", format: "claude-results",
      capabilities: { usage: "cumulative-result-deltas", lifecycle: "unsupported", tools: "unsupported" },
      sampled_at: "2026-11-03T12:00:00Z",
      records: { total: 3, usage: 3, status: 0, tool: 0, sessions: 1, entities: 1, event_sources: 1 },
      latest_event_at: "2026-11-03T11:59:40Z", latest_usage_at: "2026-11-03T11:59:40Z",
      latest_status_at: null, latest_tool_at: null, read_state: "following", retained: false,
      event_age_seconds: 20, event_freshness: "recent" }],
  };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: snapshot }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");

  const coverage = page.locator("#source-coverage");
  await expect(coverage).toContainText("Claude CLI cumulative-result deltas");
  await expect(coverage).toContainText("Lifecycle unsupported");
  await expect(coverage).toContainText("Tool events unsupported");
  await expect(coverage).toContainText("Usage records");
  await expect(coverage).toContainText("3 records seen");
  await expect(coverage).toContainText("Completed Claude CLI result summaries include subagent aggregate usage");
  await expect(coverage).toContainText("not individual calls, turn times, or worker states");
  await expect(coverage).toContainText("declared receipt time");
  await expect(coverage).toContainText("client-side estimates, not billed spend or subscription balance");
  await expect(coverage).toContainText("not reconciled with a Claude JSONL input");

  await page.locator('.mobile-nav button[data-view="settings"]').click();
  await page.locator("#coverage-setup summary").click();
  const setup = page.locator("#coverage-setup");
  await expect(setup).toContainText("sources capture-claude");
  await expect(setup).toContainText("--captured-at TIME");
  await expect(setup).toContainText("--format claude-results");
  await expect(setup).toContainText("writes a new output rather than appending an existing file");
  await expect(setup).toContainText("does not run models or scan directories");
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

import { test, expect } from "@playwright/test";

async function openSettings(page) {
  if (await page.locator("#settings").isVisible()) return;
  const mobileNav = page.locator('.mobile-nav button[data-view="settings"]');
  if (await mobileNav.isVisible()) await mobileNav.click();
  else await page.locator('.rail .nav[data-view="settings"]').click();
}

async function snapshotWithCoverage(page, sourceCoverage) {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  snapshot.inputs = { schema: "monitoring.inputs.v1", configured: true, inputs: [{ id: "private-alias", state: "unavailable", error: "/private/source/path.jsonl" }] };
  if (sourceCoverage) snapshot.source_coverage = sourceCoverage;
  else delete snapshot.source_coverage;
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: snapshot }));
}

test("coverage separates capabilities, observed kinds, event age and retained reads safely", async ({ page }) => {
  const maliciousAlias = "native <img src=x onerror=alert(1)>";
  await snapshotWithCoverage(page, {
    schema: "monitoring.source-coverage.v1", observed_at: "2026-11-02T12:00:00Z", scope: "latest-accepted-input-reads", complete_coverage: false,
    inputs: [
      { id: "normalized <script>alert(1)</script>", format: "normalized", project: "demo-project", capabilities: { usage: "normalized-deltas", lifecycle: "producer-supplied", tools: "producer-supplied" }, sampled_at: "2026-11-02T11:59:55Z", records: { total: 5, usage: 4, status: 1, tool: 0, sessions: 2, entities: 3, event_sources: 1 }, latest_event_at: "2026-11-02T11:59:50Z", latest_usage_at: "2026-11-02T11:59:50Z", latest_status_at: "2026-11-02T11:58:00Z", latest_tool_at: null, read_state: "following", retained: false, event_age_seconds: 10, event_freshness: "recent" },
      { id: maliciousAlias, format: "codex", project: "demo-project", capabilities: { usage: "native-reconstruction", lifecycle: "unsupported", tools: "completed-pairs" }, sampled_at: "2026-11-02T11:59:58Z", records: { total: 8, usage: 8, status: 0, tool: 0, sessions: 1, entities: 1, event_sources: 1 }, latest_event_at: "2026-11-02T09:00:00Z", latest_usage_at: "2026-11-02T09:00:00Z", latest_status_at: null, latest_tool_at: null, read_state: "unavailable", retained: true, event_age_seconds: 10800, event_freshness: "stale" },
      { id: "new-input", format: "claude", project: "demo-project", capabilities: { usage: "native-reconstruction", lifecycle: "unsupported", tools: "completed-pairs" }, sampled_at: null, records: null, latest_event_at: null, latest_usage_at: null, latest_status_at: null, latest_tool_at: null, read_state: "unread", retained: false, event_age_seconds: null, event_freshness: "unread" },
      { id: "empty-input", format: "normalized", project: "empty-project", capabilities: { usage: "normalized-deltas", lifecycle: "producer-supplied", tools: "producer-supplied" }, sampled_at: "2026-11-02T11:59:58Z", records: { total: 0, usage: 0, status: 0, tool: 0, sessions: 0, entities: 0, event_sources: 0 }, latest_event_at: null, latest_usage_at: null, latest_status_at: null, latest_tool_at: null, read_state: "following", retained: false, event_age_seconds: null, event_freshness: "empty" },
    ],
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  await expect(page.locator("#source-coverage-status")).toContainText("Global inventory · latest accepted input reads");
  await expect(page.locator("#source-coverage-status")).toContainText("Counts below may overlap and are not summed");
  await expect(page.locator("#source-coverage")).toContainText("Normalized usage deltas");
  await expect(page.locator("#source-coverage")).toContainText("1 producer status records observed");
  await expect(page.locator("#source-coverage")).toContainText("No producer status records observed");
  await expect(page.locator("#source-coverage")).toContainText("Native usage reconstruction");
  await expect(page.locator("#source-coverage")).toContainText("Lifecycle unsupported");
  await expect(page.locator("#source-coverage")).toContainText("Completed tool pairs");
  await expect(page.locator("#source-coverage")).toContainText("Stale · latest event 10,800 seconds ago");
  await expect(page.locator("#source-coverage")).toContainText("previously accepted records retained");
  await expect(page.locator("#source-coverage")).toContainText("Unread · no accepted sample yet");
  await expect(page.locator("#source-coverage")).toContainText("Empty · no event timestamp in the accepted sample");
  await expect(page.locator("#source-coverage img, #source-coverage script")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("/private/source/path.jsonl");
  await openSettings(page);
  await expect(page.locator("#coverage-setup summary")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test("older owners show unknown coverage, keep analytics, and expose private-manifest setup help", async ({ page }) => {
  await snapshotWithCoverage(page, null);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  await expect(page.locator("#source-coverage-status")).toContainText("Coverage inventory is unavailable in this snapshot");
  await expect(page.locator("#input-health-status")).toContainText("Partial input availability");
  await openSettings(page);
  await expect(page.locator("#coverage-setup")).toHaveJSProperty("open", true);
  await expect(page.locator("#coverage-setup")).toContainText("sources create");
  await expect(page.locator("#coverage-setup")).toContainText("sources check");
  await expect(page.locator("#coverage-setup")).toContainText("serve --sources");
  await expect(page.locator("#metrics .metric")).toHaveCount(4);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test("event age advances while paused without reads and retained inventory is qualified after owner disconnect", async ({ page }) => {
  await page.addInitScript(() => {
    let offset = 0;
    const nativeNow = Date.now.bind(Date);
    Date.now = () => nativeNow() + offset;
    window.advanceCoverageClock = (milliseconds) => { offset += milliseconds; };
  });
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  snapshot.source_coverage = {
    schema: "monitoring.source-coverage.v1", observed_at: "2026-11-02T12:00:00Z", scope: "latest-accepted-input-reads", complete_coverage: false,
    inputs: [{ id: "sample", format: "normalized", project: "demo-project", capabilities: { usage: "normalized-deltas", lifecycle: "producer-supplied", tools: "producer-supplied" }, sampled_at: "2026-11-02T12:00:00Z", records: { total: 1, usage: 1, status: 0, tool: 0, sessions: 1, entities: 1, event_sources: 1 }, latest_event_at: "2026-11-02T11:59:15Z", latest_usage_at: "2026-11-02T11:59:15Z", latest_status_at: null, latest_tool_at: null, read_state: "following", retained: false, event_age_seconds: 45, event_freshness: "recent" }],
  };
  let requestCount = 0, unavailable = false;
  await page.route("**/api/v1/snapshot?**", async (route) => {
    requestCount += 1;
    if (unavailable) return route.abort();
    return route.fulfill({ json: snapshot });
  });
  await page.goto("/");
  const coverage = page.locator("#source-coverage");
  await expect(coverage).toContainText("Recent · latest event 45 seconds ago");
  await page.locator("#pause").click();
  const readsAtPause = requestCount;
  await page.evaluate(() => window.advanceCoverageClock(20_000));
  await expect(coverage).toContainText(/Stale · latest event \d+ seconds ago/, { timeout: 4_000 });
  expect(requestCount).toBe(readsAtPause);
  await expect(coverage).toContainText("Following at last owner read · retained browser read");
  await expect(page.locator("#source-coverage-status")).toContainText("Browser refresh is paused");

  unavailable = true;
  await page.locator("#pause").click();
  await expect(page.locator("#connection")).toContainText("Disconnected");
  await expect(page.locator("#source-coverage-status")).toContainText("Monitoring owner is unavailable to this browser");
  await expect(page.locator("#source-coverage-status")).toContainText("retained from the last successful browser snapshot");
  await expect(coverage).toContainText("Following at last owner read · retained browser read");
  expect(requestCount).toBe(readsAtPause + 1);
});

import { test, expect } from "@playwright/test";

test("insight panels preserve unknown, stale, current and reset-passed states", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  await page.route("**/api/v1/snapshot?**", async (route) => {
    await route.fulfill({ json: { ...snapshot, insights: {
      schema: "monitoring.insights.v1",
      window: { minutes: 30, from: "2026-11-02T11:30:00Z", until: "2026-11-02T12:00:00Z" },
      pace: { records: 12, tokens: 8400, tokens_per_minute: "280.5", estimated_cost_per_hour: null, estimated_priced_records: 5, billed_records: 2, unknown_cost_records: 6, unconverted_cost_records: 1 },
      forecast: { horizon_minutes: 60, tokens: 16800, estimated_cost: null, assumption: "Uses the selected window rate.", state: "partial", observed_span_seconds: 185, confidence: "low" },
      source_freshness: [
        { source: "demo-current", last_event_at: "2026-11-02T12:00:00Z", age_seconds: 12, state: "recent", events: 8 },
        { source: "demo-stale", last_event_at: "2026-11-02T10:00:00Z", age_seconds: 7200, state: "stale", events: 4 },
        { source: "demo-unknown", last_event_at: null, age_seconds: null, state: "unknown", events: 0 },
      ],
      quotas: { state: "ready", note: "Account-wide quota snapshots are independent of selected usage filters, token costs and worker lifecycle. Synthetic UI fixture only.", read_status: { state: "unavailable", last_success_at: "2026-11-02T11:50:00Z", error: "Fixture reader paused." }, windows: [
        { source: "fixture", provider: "Example provider", account: "fixture account", provenance: "synthetic test", captured_at: "2026-11-02T12:00:00Z", id: "current", label: "short window", used_percent: 42, remaining_percent: 58, window_minutes: 300, resets_at: "2026-11-02T15:00:00Z", freshness: "current", age_seconds: 8 },
        { source: "fixture", provider: "Example provider", account: "fixture account", provenance: "synthetic test", captured_at: "2026-11-02T08:00:00Z", id: "stale", label: "long window", used_percent: 90, remaining_percent: 10, window_minutes: 10080, resets_at: "2026-11-02T09:00:00Z", freshness: "reset-passed", age_seconds: 14400 },
        { source: "fixture", provider: "Example provider", account: "fixture account", provenance: "synthetic test", captured_at: "2026-11-02T07:00:00Z", id: "unknown", label: "unknown values", used_percent: null, remaining_percent: null, window_minutes: null, resets_at: null, freshness: "stale", age_seconds: 18000 },
      ] },
    } } });
  });
  await page.goto("/");
  await expect(page.locator("#freshness")).toContainText("demo-stale");
  await expect(page.locator("#freshness")).toContainText("stale");
  await expect(page.locator("#pace")).toContainText("Estimated cost rate");
  await expect(page.locator("#pace")).toContainText("Unknown");
  await expect(page.locator("#pace")).toContainText("partial priced coverage");
  await expect(page.locator("#pace")).toContainText("280.5 tokens/min");
  await expect(page.locator("#pace")).toContainText("2 billed records are excluded from the estimate projection.");
  await expect(page.locator("#pace")).toContainText("12 usage records · 3m 5s observed span · confidence low (heuristic)");
  await page.locator('.nav[data-view="usage"]').click();
  await expect(page.locator("#quotas")).toContainText("current");
  await expect(page.locator("#quotas")).toContainText("Quota source unavailable");
  await expect(page.locator("#quotas")).toContainText("Account-wide quota snapshots are independent of selected usage filters");
  await expect(page.locator("#quotas")).toContainText("Fixture reader paused.");
  await expect(page.locator("#quotas")).toContainText("Reset time has passed; replenishment has not been observed.");
  await expect(page.locator("#quotas")).toContainText("Used: unknown · Remaining: unknown");
});

test("older snapshots show insights as unavailable and source/session filters remain usable", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  delete snapshot.insights;
  await page.route("**/api/v1/snapshot?**", async (route) => {
    await route.fulfill({ json: snapshot });
  });
  await page.goto("/");
  await expect(page.locator("#freshness")).toContainText("Insights are unavailable");
  await expect(page.locator('[name="source"] option').nth(1)).toBeAttached();
  await expect(page.locator('[name="session"] option').nth(1)).toBeAttached();
  const source = await page.locator('[name="source"] option').nth(1).getAttribute("value");
  await page.selectOption('[name="source"]', source);
  await expect(page.locator("#scope")).toContainText("events");
  await page.locator("#reset").click();
  await expect(page.locator('[name="source"]')).toHaveValue("");
  await expect(page.locator('[name="session"]')).toHaveValue("");
});

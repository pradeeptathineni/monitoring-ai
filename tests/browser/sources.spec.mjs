import { test, expect } from "@playwright/test";

test("input health shows partial reads safely and remains usable on mobile", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  const unsafeAlias = "work <img src=x onerror=alert(1)>";
  snapshot.inputs = {
    schema: "monitoring.inputs.v1", configured: true, degraded: true,
    healthy_inputs: 1, unavailable_inputs: 1, unread_inputs: 1,
    inputs: [
      { id: unsafeAlias, format: "claude", project: "demo-project", state: "following", last_attempt_at: "2026-11-02T12:00:00Z", last_success_at: "2026-11-02T12:00:00Z", records_seen: 12, added: 4, replayed: 8 },
      { id: "stalled-import", format: "codex", project: "demo-project", state: "unavailable", last_attempt_at: "2026-11-02T12:00:00Z", last_success_at: "2026-11-02T11:50:00Z", records_seen: 20, added: 0, replayed: 20, error: "/Users/private/session.json permission denied" },
      { id: "new-selection", format: "claude", project: "other-project", state: "unread", last_attempt_at: null, last_success_at: null, records_seen: 0, added: 0, replayed: 0 },
    ],
  };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: snapshot }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator("#input-health-status")).toContainText("Partial input availability");
  await expect(page.locator("#input-health-status")).toContainText("1 following · 1 unavailable · 1 unread");
  await expect(page.locator("#input-health-status")).toContainText("Failed reads retain previously accepted event history.");
  await expect(page.locator("#input-health")).toContainText(unsafeAlias);
  await expect(page.locator("#input-health")).toContainText("Last success: 2026-11-02 11:50:00 UTC");
  await expect(page.locator("#input-health")).toContainText("Not yet read");
  await expect(page.locator("#input-health img")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("/Users/private/session.json");
  await expect(page.locator("#overview")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test("older snapshots without input inventory stay usable", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  delete snapshot.inputs;
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: snapshot }));
  await page.goto("/");
  await expect(page.locator("#input-health-status")).toContainText("The input list is unavailable in this snapshot");
  await expect(page.locator("#input-health")).toContainText("Connect an explicitly selected input");
  await expect(page.locator("#metrics .metric")).toHaveCount(4);
});

test("selected native overlap explains the refused input without exposing raw diagnostics", async ({ page }) => {
  const snapshot = await (await page.request.get("/api/v1/snapshot")).json();
  snapshot.inputs = { schema: "monitoring.inputs.v1", configured: true, healthy_inputs: 1,
    unavailable_inputs: 1, unread_inputs: 0, inputs: [
      { id: "first", format: "claude", project: "fictional", state: "following", records_seen: 1, added: 1, replayed: 0 },
      { id: "copy", format: "claude", project: "fictional", state: "unavailable", records_seen: 1, added: 0, replayed: 0,
        error: "Overlapping native records in another selected source. Accepted history is retained." },
    ] };
  snapshot.source_coverage = { schema: "monitoring.source-coverage.v1", observed_at: snapshot.observed_at,
    inputs: [{ id: "copy", format: "claude", read_state: "unavailable", event_freshness: "unread",
      capabilities: { usage: "native-reconstruction", lifecycle: "unsupported", tools: "completed-pairs" },
      records: null, retained: false, sampled_at: null, latest_event_at: null }] };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: snapshot }));
  await page.goto("/");
  await expect(page.locator("#source-coverage")).toContainText("Repeated native records in another selected input");
  await expect(page.locator("#source-coverage")).toContainText("accepted history is retained");
  await expect(page.locator("#source-coverage")).not.toContainText("/private/");
});

// Only the known content-free refusal reaches the source panels
test("declared Codex parent history explains incomplete source coverage", async ({ page }) => {
  const snapshot = await (await page.request.get("/api/v1/snapshot")).json();
  snapshot.inputs = { schema: "monitoring.inputs.v1", configured: true, healthy_inputs: 0,
    unavailable_inputs: 1, unread_inputs: 0, inputs: [
      { id: "selected-child", format: "codex", project: "fictional", state: "unavailable",
        records_seen: null, added: 0, replayed: 0,
        error: "Declared Codex parent history requires replay reconciliation. This input was not imported; accepted history is retained." },
    ] };
  snapshot.source_coverage = { schema: "monitoring.source-coverage.v1", observed_at: snapshot.observed_at,
    inputs: [{ id: "selected-child", format: "codex", read_state: "unavailable", event_freshness: "unread",
      capabilities: { usage: "native-reconstruction", lifecycle: "unsupported", tools: "completed-items" },
      records: null, retained: false, sampled_at: null, latest_event_at: null }] };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: snapshot }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator("#source-coverage")).toContainText("Declared Codex parent history requires replay reconciliation");
  await expect(page.locator("#source-coverage")).toContainText("Do not remove the parent declaration");
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  // Older owners show the input-health fallback instead of source coverage
  delete snapshot.source_coverage;
  await page.reload();
  await expect(page.locator("#input-health")).toContainText("This input was not imported; accepted history is retained");
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});


for (const [format, error] of [
  ["codex", "Declared Codex history metadata requires separate accounting qualification. This input was not imported; accepted history is retained."],
  ["claude", "Declared Claude fork history requires separate accounting qualification. This input was not imported; accepted history is retained."],
]) {
test(`unqualified ${format} history explains retained source coverage`, async ({ page }) => {
  const snapshot = await (await page.request.get("/api/v1/snapshot")).json();
  snapshot.inputs = { schema: "monitoring.inputs.v1", configured: true, healthy_inputs: 0,
    unavailable_inputs: 1, unread_inputs: 0, inputs: [
      { id: "selected-history", format, project: "fictional", state: "unavailable",
        records_seen: null, added: 0, replayed: 0, error },
    ] };
  snapshot.source_coverage = { schema: "monitoring.source-coverage.v1", observed_at: snapshot.observed_at,
    inputs: [{ id: "selected-history", format, read_state: "unavailable", event_freshness: "unread",
      capabilities: { usage: "native-reconstruction", lifecycle: "unsupported", tools: "completed-items" },
      records: null, retained: true, sampled_at: null, latest_event_at: null }] };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: snapshot }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator("#source-coverage")).toContainText(error);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  delete snapshot.source_coverage;
  await page.reload();
  await expect(page.locator("#input-health")).toContainText(error);
});
}

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
  await expect(page.locator("#input-health-status")).toContainText("Input inventory is unavailable in this snapshot");
  await expect(page.locator("#input-health")).toContainText("Connect an explicitly selected input");
  await expect(page.locator("#metrics .metric")).toHaveCount(4);
});

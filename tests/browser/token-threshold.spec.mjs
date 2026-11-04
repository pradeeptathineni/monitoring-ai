import { test, expect } from "@playwright/test";

test("observed UTC-day threshold is visible in Usage and configured in Settings", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  snapshot.token_threshold = {
    schema: "monitoring.token-threshold.v1", scope: "all-accepted-events-unfiltered",
    complete_coverage: false,
    window: { from: "2026-11-02T00:00:00.000Z", until: "2026-11-03T00:00:00.000Z",
      observed_at: "2026-11-02T20:00:00.000Z" },
    limit_tokens: 100, observed_tokens: 120, remaining_observed_tokens: 0,
    state: "observed-reached", usage_records: 2, excluded_receipt_summaries: 1,
    excluded_future_records: 0, note: "Fixture only. No provider enforcement.",
  };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: snapshot }));
  await page.goto("/");
  await page.locator('.nav[data-view="usage"]').click();
  await expect(page.locator("#token-threshold-panel")).toBeVisible();
  await expect(page.locator("#token-threshold-status")).toContainText("Observed threshold reached");
  await expect(page.locator("#token-threshold-details")).toContainText("120 / 100 tokens");
  await expect(page.locator("#token-threshold-panel")).toContainText("Missing sources and receipt-time summaries");
  await page.locator("#pause").click();
  await expect(page.locator("#token-threshold-status")).toContainText("retained snapshot; current use unknown");
  await page.locator("#pause").click();
  await page.goto("/#settings/thresholds");
  const thresholdSettings = page.locator('#settings [data-settings-category="thresholds"]')
    .filter({ has: page.getByRole("heading", { name: "Thresholds", exact: true }) });
  await expect(thresholdSettings).toBeVisible();
  await expect(thresholdSettings).toContainText("--token-threshold 100000");
  await expect(page.locator("#breadcrumb")).toHaveText("Settings / Thresholds");
});

test("owners without threshold configuration keep the optional panel hidden", async ({ page }) => {
  await page.goto("/");
  await page.locator('.nav[data-view="usage"]').click();
  await expect(page.locator("#token-threshold-panel")).toBeHidden();
});

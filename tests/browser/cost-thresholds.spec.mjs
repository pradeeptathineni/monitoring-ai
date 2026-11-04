import { test, expect } from "@playwright/test";

test("separate cost thresholds show coverage and retained-state limits", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  snapshot.cost_thresholds = {
    schema: "monitoring.cost-thresholds.v1", scope: "all-accepted-events-unfiltered",
    complete_coverage: false, currency: "USD",
    window: { from: "2026-11-03T00:00:00.000Z", until: "2026-11-04T00:00:00.000Z",
      observed_at: "2026-11-03T12:00:00.000Z" },
    items: [
      { basis: "estimated", limit_amount: "2", observed_amount: "3.25",
        state: "observed-reached", covered_records: 2, unpriced_records: 1,
        unconverted_records: 1, other_basis_records: 0, future_records: 0,
        receipt_time_records: 1, cost_provenance: ["fixture"], provenance_truncated: false },
      { basis: "billed", limit_amount: "4", observed_amount: null,
        state: "no-observations", covered_records: 0, unpriced_records: 1,
        unconverted_records: 0, other_basis_records: 0, future_records: 0,
        receipt_time_records: 0, cost_provenance: [], provenance_truncated: false },
    ],
  };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: snapshot }));
  await page.goto("/");
  await page.locator('.nav[data-view="usage"]').click();
  await expect(page.locator("#cost-threshold-panel")).toBeVisible();
  await expect(page.locator("#cost-threshold-details")).toContainText("3.25 / 2 USD");
  await expect(page.locator("#cost-threshold-details")).toContainText("no converted observations");
  await expect(page.locator("#cost-threshold-details")).toContainText("1 receipt-time summaries");
  await page.locator("#pause").click();
  await expect(page.locator("#cost-threshold-status")).toContainText("retained snapshot; current use unknown");
  await page.goto("/#settings/thresholds");
  const thresholdSettings = page.locator('#settings [data-settings-category="thresholds"]')
    .filter({ has: page.getByRole("heading", { name: "Thresholds", exact: true }) });
  await expect(thresholdSettings).toContainText("--estimated-cost-threshold 10");
});

test("owners without cost thresholds keep the optional panel hidden", async ({ page }) => {
  await page.goto("/");
  await page.locator('.nav[data-view="usage"]').click();
  await expect(page.locator("#cost-threshold-panel")).toBeHidden();
});

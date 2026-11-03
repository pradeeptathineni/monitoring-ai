import { test, expect } from "@playwright/test";

async function fixture(page) {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  base.insights.quotas = { state: "ready", read_status: { state: "following" }, windows: [
    { source: "fixture-quota", provider: "openai", account: "example", provenance: "fixture",
      id: "weekly", label: "Weekly", window_minutes: 10080, captured_at: new Date().toISOString(),
      resets_at: new Date(Date.now() + 86400000).toISOString(), remaining_percent: 10, used_percent: 90, freshness: "current" }
  ] };
  await page.route("**/api/v1/snapshot?**", route => route.fulfill({ json: base }));
  return base;
}

async function save(page) {
  await page.goto("/#settings/thresholds");
  await page.getByRole("combobox", { name: "Quota window", exact: true }).selectOption({ index: 1 });
  await page.getByLabel("Warn at remaining percent", { exact: true }).fill("15");
  await page.getByRole("button", { name: "Save reserve watch", exact: true }).click();
}

test("reserve watch is owner/window scoped and stale or paused readings become unknown", async ({ page }) => {
  const base = await fixture(page);
  await save(page);
  await expect(page.locator("#quota-watch-banner")).toContainText("10% remaining is at or below the 15% reserve");
  await page.reload();
  await expect(page.locator("#quota-watch-banner")).toHaveAttribute("data-state", "reached");
  await page.locator("#pause").click();
  await expect(page.locator("#quota-watch-banner")).toHaveAttribute("data-state", "unknown");
  await page.locator("#pause").click();
  await expect(page.locator("#quota-watch-banner")).toHaveAttribute("data-state", "reached");
  base.insights.quotas.windows[0].captured_at = new Date(Date.now() - 360000).toISOString();
  await expect(page.locator("#quota-watch-banner")).toHaveAttribute("data-state", "unknown");
  base.insights.quotas.windows[0].captured_at = new Date().toISOString();
  base.identity = "another-quota-owner";
  await expect(page.locator("#quota-watch-banner")).toContainText("owner changed");
  await page.getByRole("button", { name: "Save reserve watch", exact: true }).click();
  await expect(page.locator("#quota-watch-banner")).toHaveAttribute("data-state", "reached");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.getByRole("button", { name: "Disable reserve watch", exact: true }).click();
  await expect(page.locator("#quota-watch-banner")).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem("monitoring-ai.quota-watch.v1"))).toBeNull();
});

test("storage failure keeps a page-local watch with an explicit persistence limit", async ({ page }) => {
  await fixture(page);
  await page.addInitScript(() => { Storage.prototype.setItem = () => { throw Error("blocked fixture"); }; });
  await save(page);
  await expect(page.locator("#quota-watch-status")).toContainText("applies only to this page");
  await expect(page.locator("#quota-watch-banner")).toHaveAttribute("data-state", "reached");
});

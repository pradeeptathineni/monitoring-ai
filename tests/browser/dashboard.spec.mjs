import { test, expect } from "@playwright/test";

test("filtering, grouping, entity drill-down and reset use the real ledger", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator(".metric")).toHaveCount(4);
  await expect(page.locator("#scope")).toContainText("114 of 114");
  await page.selectOption('[name="provider"]', "anthropic");
  await expect(page.locator("#scope")).toContainText("38 of 114");
  await page.locator('.nav[data-view="usage"]').click();
  await page.selectOption("#group", "surface");
  await expect(page.locator("#usage-table tbody tr")).toHaveCount(1);
  await expect(page.locator("#usage-table")).toContainText("claude-code");
  await page.locator('.nav[data-view="sessions"]').click();
  await expect(page.locator("#entity-table tbody tr")).toHaveCount(2);
  await page.getByRole("button", { name: "↳ scout", exact: true }).click();
  await expect(page.locator("#event-selection")).toContainText("scout");
  await expect(page.locator("#event-table tbody tr")).toHaveCount(19);
  await page.locator("#clear-entity").click();
  await expect(page.locator("#event-table tbody tr")).toHaveCount(38);
  await page.locator("#reset").click();
  await expect(page.locator("#scope")).toContainText("114 of 114");
  await page.selectOption('[name="model"]', "demo-local");
  await expect(page.locator(".metric").nth(2)).toContainText("Unknown");
  await expect(page.locator(".metric").nth(3)).toContainText("Unknown");
  expect(errors).toEqual([]);
});

test("read loss preserves observations and removes current running claims", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".metric")).toHaveCount(4);
  await page.route("**/api/v1/snapshot?**", (route) => route.abort());
  await expect(page.locator("#connection")).toHaveText("Disconnected", { timeout: 10000 });
  await expect(page.locator("#notice")).toContainText("Last observations remain visible");
  await page.locator('.nav[data-view="sessions"]').click();
  await expect(page.locator("#entity-table .state.running")).toHaveCount(0);
  await expect(page.locator("#entity-table tbody tr")).toHaveCount(6);
  await page.locator('.nav[data-view="usage"]').click();
  await expect(page.locator("#quotas")).toContainText("The Monitoring service is unavailable");
});

test("narrow view remains usable and keyboard navigation works", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator(".metric")).toHaveCount(4);
  await expect(page.locator("h1")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  expect(overflow).toBe(false);
  await page.locator('.mobile-nav [data-view="usage"]').click();
  await expect(page.locator("#usage")).toBeVisible();
  await page.keyboard.press("1");
  await expect(page.locator("#overview")).toBeVisible();
  await page.keyboard.press("3");
  await expect(page.locator("#usage")).toBeVisible();
  await page.locator("#pause").click();
  await expect(page.locator("#connection")).toHaveText("Refresh paused");
  await page.locator("#pause").click();
  await expect(page.locator("#connection")).toHaveText("Ledger available");
});

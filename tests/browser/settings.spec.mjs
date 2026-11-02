import { test, expect } from "@playwright/test";

test("Settings owns appearance and source setup while analytics stay in their views", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");

  await expect(page.locator("#overview")).toBeVisible();
  await expect(page.locator("#appearance-settings")).toBeHidden();
  await expect(page.locator("#coverage-setup")).toBeHidden();
  await expect(page.locator("#filters")).toBeVisible();
  await expect(page.locator("#metrics")).toBeVisible();
  await expect(page.locator('#overview [data-view="settings"]')).toContainText("Configure explicitly selected sources");
  await expect(page.locator("#source-coverage-status")).toBeVisible();

  await page.locator('.mobile-nav button[data-view="settings"]').click();
  await expect(page).toHaveURL(/#settings$/);
  await expect(page.locator("#settings")).toBeVisible();
  await expect(page.locator("#breadcrumb")).toHaveText("Settings");
  await expect(page.locator("#filters")).toBeHidden();
  await expect(page.locator("#metrics")).toBeHidden();
  await expect(page.locator("#appearance-settings")).toBeVisible();
  await page.locator("#appearance-settings summary").click();
  await expect(page.locator("#appearance-preset")).toBeVisible();
  await expect(page.locator("#coverage-setup")).toBeVisible();
  if (!(await page.locator("#coverage-setup").evaluate((element) => element.open))) await page.locator("#coverage-setup summary").click();
  await expect(page.locator("#coverage-setup")).toContainText("sources create");
  await expect(page.locator("#coverage-setup")).toContainText("sources check");
  await expect(page.locator("#coverage-setup")).toContainText("serve --sources");
  await expect(page.locator("#coverage-setup")).toContainText("never scans for files");

  await page.selectOption("#appearance-preset", "forest-light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "forest-light");
  const preference = await page.evaluate(() => JSON.parse(localStorage.getItem("monitoring-ai.appearance.v1")));
  expect(preference).toMatchObject({ mode: "preset", preset: "forest-light" });

  await page.locator('.mobile-nav button[data-view="overview"]').click();
  await expect(page.locator("#overview")).toBeVisible();
  await expect(page.locator("#input-health-status")).toBeVisible();
  await expect(page.locator("#confidence")).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/#settings$/);
  await expect(page.locator("#settings")).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/#overview$/);
  await page.locator('.mobile-nav button[data-view="settings"]').click();
  await expect(page).toHaveURL(/#settings$/);
  await page.reload();
  await expect(page).toHaveURL(/#settings$/);
  await expect(page.locator("#settings")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "forest-light");

  await page.locator('.mobile-nav button[data-view="usage"]').click();
  await expect(page.locator("#quotas")).toBeVisible();
  await expect(page.locator("#currency-info")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

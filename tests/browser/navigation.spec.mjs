import { test, expect } from "@playwright/test";

test("desktop sidebar collapses to the shared 68px rail, persists and keeps toggle focus", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  const shell = page.locator("#monitoring-workspace");
  const toggle = page.locator("#navigation-toggle");
  await expect.poll(async () => Number.parseFloat(await shell.evaluate((element) => getComputedStyle(element).gridTemplateColumns))).toBe(236);
  await expect(page.locator('.primary-nav [data-view="overview"]')).toHaveAttribute("aria-current", "page");
  await expect(page.locator('.primary-nav [data-view="settings"]')).toHaveAttribute("title", "Settings");

  await toggle.click();
  await expect(shell).toHaveAttribute("data-navigation", "collapsed");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator('.primary-nav [data-view="overview"] .navigation-label')).toBeHidden();
  await expect.poll(async () => Number.parseFloat(await shell.evaluate((element) => getComputedStyle(element).gridTemplateColumns))).toBe(68);
  expect(await toggle.evaluate((element) => document.activeElement === element)).toBe(true);
  await expect(page.locator('.primary-nav [data-view="settings"]')).toHaveAccessibleName("Settings");
  expect(await page.evaluate(() => localStorage.getItem("monitoring-ai.navigation.collapsed.v1"))).toBe("collapsed");

  await page.reload();
  await expect(shell).toHaveAttribute("data-navigation", "collapsed");
  await expect(page.locator('.primary-nav [data-view="report"]')).toHaveAttribute("title", "Report");
  await page.locator("#navigation-toggle").focus();
  await page.keyboard.press("Enter");
  await expect(shell).toHaveAttribute("data-navigation", "expanded");
  await expect(page.locator('.primary-nav [data-view="overview"] .navigation-label')).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("monitoring-ai.navigation.collapsed.v1"))).toBe("expanded");
});

test("Settings categories deep-link, isolate existing sections and retain browser history", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/#settings/appearance");
  await expect(page.locator("#settings")).toBeVisible();
  await expect(page.locator("#appearance-settings")).toBeVisible();
  await expect(page.locator("#coverage-setup")).toBeHidden();
  await expect(page.locator('#section-navigation [data-settings-route="appearance"]')).toHaveAttribute("aria-current", "page");
  await expect(page.locator('.primary-nav [data-view="settings"]')).toHaveAttribute("aria-current", "page");

  await page.locator('#section-navigation [data-settings-route="sources"]').click();
  await expect(page).toHaveURL(/#settings\/sources$/);
  await expect(page.locator("#appearance-settings")).toBeHidden();
  await expect(page.locator("#coverage-setup")).toBeVisible();
  await expect(page.locator('#section-navigation [data-settings-route="sources"]')).toHaveAttribute("aria-current", "page");
  await page.goBack();
  await expect(page).toHaveURL(/#settings\/appearance$/);
  await expect(page.locator("#appearance-settings")).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/#settings\/sources$/);
  await page.locator('#section-navigation [data-settings-route="all"]').click();
  await expect(page).toHaveURL(/#settings$/);
  await expect(page.locator("#appearance-settings")).toBeVisible();
  await expect(page.locator("#coverage-setup")).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(/#settings$/);
  await expect(page.locator("#settings")).toBeVisible();
});

test("mobile navigation reveals primary and contextual routes with 44px touch targets and no overflow", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await page.goto("/#settings/sources");
  const primary = page.locator("#primary-navigation");
  const selected = page.locator('.mobile-nav [data-view="settings"]');
  await expect(selected).toHaveAttribute("aria-current", "page");
  await expect(selected).toBeInViewport();
  expect(await selected.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  const context = page.locator('#section-navigation [data-settings-route="sources"]');
  await expect(context).toHaveAttribute("aria-current", "page");
  await expect(context).toBeInViewport();
  await expect.poll(() => context.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await page.locator('#section-navigation [data-settings-route="appearance"]').click();
  await expect(page).toHaveURL(/#settings\/appearance$/);
  await expect(page.locator("#appearance-settings")).toBeVisible();
  await expect(page.locator("#coverage-setup")).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);

  await page.locator('.mobile-nav [data-view="overview"]').click();
  await expect(page.locator("#source-coverage-status")).toBeVisible();
  await expect(page.locator("#confidence")).toBeVisible();
  await page.locator('.mobile-nav [data-view="usage"]').click();
  await expect(page.locator("#quotas")).toBeVisible();
  expect(await primary.evaluate((element) => element.scrollWidth >= element.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test("blocked storage keeps the sidebar toggle usable for this page", async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window, "localStorage", {
    configurable: true,
    get() { throw new DOMException("blocked", "SecurityError"); },
  }));
  await page.goto("/");
  await page.locator("#navigation-toggle").click();
  await expect(page.locator("#monitoring-workspace")).toHaveAttribute("data-navigation", "collapsed");
  await expect(page.locator("#navigation-status")).toContainText("active for this page only");
  await page.locator("#navigation-toggle").click();
  await expect(page.locator("#monitoring-workspace")).toHaveAttribute("data-navigation", "expanded");
});

test("reduced motion removes workspace and disclosure transitions", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/#settings/sources");
  const durations = await page.evaluate(() => ({
    shell: getComputedStyle(document.querySelector("#monitoring-workspace")).transitionDuration,
    disclosure: getComputedStyle(document.querySelector("#coverage-setup")).animationDuration,
  }));
  expect(durations.shell.split(",").every((value) => value.trim() === "0s")).toBe(true);
  expect(durations.disclosure.split(",").every((value) => value.trim() === "0s")).toBe(true);
});

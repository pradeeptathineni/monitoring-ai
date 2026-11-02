import { test, expect } from "@playwright/test";

const KEY = "monitoring-ai.appearance.v1";

async function openAppearance(page) {
  const details = page.locator("#appearance-settings");
  if (!(await details.evaluate((element) => element.open))) await page.locator("#appearance-settings summary").click();
}

test("system follows OS while explicit presets persist and stay pinned", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await openAppearance(page);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "neon1-dark");
  await expect(page.locator("#appearance-accent")).toHaveValue("#6fdc8a");
  await page.emulateMedia({ colorScheme: "light" });
  await expect.poll(() => page.locator("html").getAttribute("data-theme")).toBe("neon1-light");
  await expect(page.locator("#appearance-accent")).toHaveValue("#286936");
  await page.selectOption("#appearance-preset", "slate-dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "slate-dark");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "slate-dark");
  await page.reload();
  await openAppearance(page);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "slate-dark");
  expect(await page.evaluate(() => document.documentElement.style.colorScheme)).toBe("dark");
  await page.selectOption("#appearance-preset", "system");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "neon1-dark");
});

test("valid overrides persist and low-contrast overrides retain the active theme", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await openAppearance(page);
  await page.selectOption("#appearance-preset", "neon1-dark");
  await page.locator("#appearance-accent").evaluate((input) => {
    input.value = "#00ffff";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.click("#appearance-apply-overrides");
  await expect(page.locator("#appearance-status")).toHaveText("");
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--theme-accent").trim())).toBe("#00ffff");
  expect(JSON.parse(await page.evaluate((key) => localStorage.getItem(key), KEY)).overrides.accent).toBe("#00ffff");
  await page.reload();
  await openAppearance(page);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "neon1-dark");
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--theme-accent").trim())).toBe("#00ffff");

  await page.click("#appearance-reset");
  await page.selectOption("#appearance-preset", "forest-light");
  const original = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--theme-accent").trim());
  await page.locator("#appearance-accent").evaluate((input) => {
    input.value = "#ffffff";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.click("#appearance-apply-overrides");
  await expect(page.locator("#appearance-status")).toContainText("Invalid theme preference");
  await expect(page.locator("#appearance-status")).toContainText("current theme remains active");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "forest-light");
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--theme-accent").trim())).toBe(original);
});

test("invalid saved values fall back visibly without overwriting storage", async ({ page }) => {
  const invalid = JSON.stringify({ version: 99, mode: "preset", preset: "matrix-dark", overrides: {} });
  await page.addInitScript(({ key, value }) => localStorage.setItem(key, value), { key: KEY, value: invalid });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  await openAppearance(page);
  await expect(page.locator("#appearance-status")).toContainText("Saved appearance is invalid");
  await expect(page.locator("#appearance-status")).toContainText("saved value remains unchanged");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "neon1-light");
  expect(await page.evaluate((key) => localStorage.getItem(key), KEY)).toBe(invalid);
});

test("blocked storage keeps local changes active and explains the limit", async ({ page }) => {
  await page.addInitScript(() => {
    const blocked = () => { throw new DOMException("blocked", "SecurityError"); };
    for (const method of ["getItem", "setItem", "removeItem"]) Object.defineProperty(Storage.prototype, method, { configurable: true, value: blocked });
  });
  await page.goto("/");
  await openAppearance(page);
  await page.selectOption("#appearance-preset", "slate-dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "slate-dark");
  await expect(page.locator("#appearance-status")).toContainText("active only for this page");
});

test("a throwing localStorage getter falls back to a visible page-only choice", async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window, "localStorage", {
    configurable: true,
    get() { throw new DOMException("blocked", "SecurityError"); },
  }));
  await page.goto("/");
  await openAppearance(page);
  await expect(page.locator("#appearance-status")).toContainText("Browser storage is unavailable");
  await page.selectOption("#appearance-preset", "forest-light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "forest-light");
  await expect(page.locator("#appearance-status")).toContainText("active only for this page");
});

test("appearance syncs across tabs, reset restores system mode, and mobile content stays usable", async ({ page, context }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  await openAppearance(page);
  await expect(page.locator("#confidence")).toBeVisible();
  const confidenceBefore = await page.locator("#confidence").innerText();
  const healthBefore = await page.locator("#input-health-status").innerText();
  await expect(page.locator("#appearance-preset")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);

  const second = await context.newPage();
  await second.emulateMedia({ colorScheme: "light" });
  await second.goto("/");
  await openAppearance(second);
  await second.selectOption("#appearance-preset", "matrix-dark");
  await expect.poll(() => page.locator("html").getAttribute("data-theme")).toBe("matrix-dark");
  await expect.poll(() => page.locator("#appearance-status").innerText()).toBe("");
  await second.click("#appearance-reset");
  await expect.poll(() => page.locator("html").getAttribute("data-theme")).toBe("neon1-light");
  await expect(page.locator("#appearance-status")).toContainText("Another tab reset the appearance");
  expect(await page.locator("#confidence").innerText()).toBe(confidenceBefore);
  expect(await page.locator("#input-health-status").innerText()).toBe(healthBefore);
  await second.close();
});

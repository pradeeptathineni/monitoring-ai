import { test, expect } from "@playwright/test";

async function choose(page) {
  await page.goto("/#report");
  await expect(page.locator("#compare-first option")).not.toHaveCount(1);
  const choices = await page.locator("#compare-first option").evaluateAll((options) => options.map((option) => option.value).filter(Boolean));
  expect(choices.length).toBeGreaterThan(1);
  await page.selectOption("#compare-first", choices[0]);
  await page.selectOption("#compare-second", choices[1]);
  return choices.slice(0, 2).map((value) => JSON.parse(value));
}

test("compares exact sessions from real report responses and keeps cost coverage separate", async ({ page }) => {
  const requests = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/report" && url.searchParams.has("session")) requests.push(url);
  });
  const selections = await choose(page);
  await page.getByRole("button", { name: "Compare sessions", exact: true }).click();
  await expect(page.locator("#session-compare-table tbody tr")).toHaveCount(14);
  await expect(page.locator("#session-compare-status")).toContainText("Recorded session comparison");
  await expect(page.locator("#session-compare-table")).toContainText("Coverage may differ");
  expect(requests).toHaveLength(2);
  for (let index = 0; index < 2; index++) {
    expect(requests[index].searchParams.get("source")).toBe(selections[index][0]);
    expect(requests[index].searchParams.get("session")).toBe(selections[index][1]);
    expect(requests[index].searchParams.has("state")).toBe(false);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.selectOption("#compare-second", JSON.stringify(selections[0]));
  await expect(page.locator("#compare-sessions")).toBeDisabled();
  await expect(page.locator("#session-compare-table table")).toHaveCount(0);
});

test("refuses reports from different ledger revisions", async ({ page }) => {
  let count = 0;
  await page.route("**/api/v1/report?**", async (route) => {
    const response = await route.fetch();
    const report = await response.json();
    if (new URL(route.request().url()).searchParams.has("session") && count++ % 2) report.revision += 1;
    await route.fulfill({ json: report });
  });
  await choose(page);
  await page.locator("#compare-sessions").click();
  await expect(page.locator("#session-compare-status")).toContainText("no mixed revisions are displayed");
  await expect(page.locator("#session-compare-table table")).toHaveCount(0);
});

test("a selection change cancels delayed comparison replies", async ({ page }) => {
  let started = false;
  await page.route("**/api/v1/report?**", async (route) => {
    const response = await route.fetch();
    if (new URL(route.request().url()).searchParams.has("session")) {
      started = true;
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    try { await route.fulfill({ response }); } catch { /* Canceled reads may close before fulfilment */ }
  });
  await choose(page);
  await page.locator("#compare-sessions").click();
  await expect.poll(() => started).toBe(true);
  await page.selectOption("#compare-first", "");
  await expect(page.locator("#session-compare-status")).toContainText("Selection changed");
  await page.waitForTimeout(350);
  await expect(page.locator("#session-compare-table table")).toHaveCount(0);
});

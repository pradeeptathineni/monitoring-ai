import { test, expect } from "@playwright/test";

test("CodeBurn daily cache stays separate, supports its own date range, and retains unavailable reads", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  const days = Array.from({ length: 9 }, (_, index) => {
    const date = new Date(Date.UTC(2026, 9, 10 + index)).toISOString().slice(0, 10);
    return {
      date,
      source_cost_usd: (0.12 + index / 100).toFixed(2),
      calls: index + 1,
      sessions: index + 1,
      tokens: { input: 100 + index, output: 20, cache_read: 5, cache_write: 3 },
      carried: index === 1,
    };
  });
  await page.route("**/api/v1/snapshot?**", async (route) => {
    await route.fulfill({ json: {
      ...snapshot,
      cached_usage_status: { state: "unavailable", last_success_at: "2026-10-19T08:00:00Z", error: "A private path must not appear here." },
      cached_usage: {
        schema: "monitoring.cached-usage.v1",
        source: "codeburn-daily-cache",
        format_version: 74,
        sampled_at: "2026-10-19T08:00:00Z",
        file_modified_at: "2026-10-18T08:00:00Z",
        file_age_seconds: 86400,
        date_basis: "codeburn-local-calendar",
        complete: false,
        watermark_trusted: false,
        last_computed_date: "2026-10-18",
        days,
        read_status: { state: "unavailable", last_success_at: "2026-10-19T08:00:00Z", error: "A private path must not appear here." },
        retained: true,
      },
    } });
  });

  await page.goto("/#usage");
  const panel = page.locator("#codeburn-cache-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("CodeBurn cached usage");
  await expect(panel).toContainText("Selected CodeBurn cache is unavailable");
  await expect(panel).toContainText("prior cached snapshot");
  await expect(panel).toContainText("1d since file modification");
  await expect(panel).toContainText("marked incomplete");
  await expect(panel).toContainText("watermark is not trusted");
  await expect(panel).toContainText("unknown billing basis");
  await expect(panel).toContainText("excluded from Monitoring ledger totals");
  await expect(panel.locator("thead")).not.toContainText("Total tokens");
  await expect(panel).toContainText("A non-overlapping total across every provider is unverified.");
  await expect(panel).not.toContainText("private path");
  await expect(page.locator("#scope")).not.toContainText("CodeBurn");
  await expect(panel.locator("tbody tr")).toHaveCount(9);
  await expect(panel).toContainText("Carried summary");

  await page.selectOption("#codeburn-cache-range", "7");
  await expect(panel.locator("tbody tr")).toHaveCount(7);
  await page.selectOption("#codeburn-cache-range", "date");
  await page.locator("#codeburn-cache-date").fill("2026-10-12");
  await page.locator("#codeburn-cache-date").dispatchEvent("change");
  await expect(panel.locator("tbody tr")).toHaveCount(1);
  await expect(panel.locator("tbody")).toContainText("2026-10-12");
  await expect(page.locator("#metrics .metric").nth(2)).toContainText("not a subscription balance");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("#codeburn-cache-date")).toBeVisible();
  const dimensions = await page.evaluate(() => ({ body: document.documentElement.scrollWidth, viewport: innerWidth }));
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport);
});

test("old snapshots without a CodeBurn cache keep the optional panel hidden", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  delete snapshot.cached_usage;
  delete snapshot.cached_usage_status;
  await page.route("**/api/v1/snapshot?**", async (route) => route.fulfill({ json: snapshot }));
  await page.goto("/#usage");
  await expect(page.locator("#codeburn-cache-panel")).toBeHidden();
});

test("a configured unavailable cache with no prior snapshot reports unknown, not zero", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  delete snapshot.cached_usage;
  await page.route("**/api/v1/snapshot?**", async (route) => route.fulfill({
    json: { ...snapshot, cached_usage_status: { state: "unavailable", last_success_at: null, error: "not rendered" } },
  }));
  await page.goto("/#usage");
  await expect(page.locator("#codeburn-cache-panel")).toBeVisible();
  await expect(page.locator("#codeburn-cache-status")).toContainText("No prior cached summary is available");
  await expect(page.locator("#codeburn-cache-status")).not.toContainText("0 usage");
});

test("an empty CodeBurn cache does not imply provider idleness", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  await page.route("**/api/v1/snapshot?**", async (route) => route.fulfill({
    json: {
      ...snapshot,
      cached_usage_status: { state: "following", last_success_at: "2026-10-19T08:00:00Z", error: null },
      cached_usage: {
        schema: "monitoring.cached-usage.v1", source: "codeburn-daily-cache", format_version: 74,
        sampled_at: "2026-10-19T08:00:00Z", file_modified_at: "2026-10-19T07:59:00Z", file_age_seconds: 60,
        date_basis: "codeburn-local-calendar", complete: true, watermark_trusted: true,
        last_computed_date: null, days: [], retained: false,
      },
    },
  }));
  await page.goto("/#usage");
  await expect(page.locator("#codeburn-cache-status")).toContainText("Selected CodeBurn cache is readable");
  await expect(page.locator("#codeburn-cache-table")).toContainText("No cached daily rows are available");
  await expect(page.locator("#codeburn-cache-table")).toContainText("does not establish provider idleness");
});

test("retained cache file age advances across repeated unavailable-read snapshots", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-19T08:00:00Z") });
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  let reads = 0;
  await page.route("**/api/v1/snapshot?**", async (route) => {
    reads += 1;
    await route.fulfill({ json: {
      ...snapshot,
      cached_usage_status: { state: "unavailable", last_success_at: "2026-10-19T08:00:00Z", error: "not rendered" },
      cached_usage: {
        schema: "monitoring.cached-usage.v1", source: "codeburn-daily-cache", format_version: 74,
        sampled_at: "2026-10-19T08:00:00Z", file_modified_at: "2026-10-19T07:59:00Z", file_age_seconds: 60,
        date_basis: "codeburn-local-calendar", complete: true, watermark_trusted: true,
        last_computed_date: "2026-10-18", days: [], retained: true,
      },
    } });
  });
  await page.goto("/#usage");
  await expect(page.locator("#codeburn-cache-status")).toContainText("1m since file modification");
  await page.clock.runFor(70_000);
  await expect.poll(() => reads).toBeGreaterThan(1);
  await expect(page.locator("#codeburn-cache-status")).toContainText("2m since file modification");
});

test("provider and model marginal rollups keep unknown and empty maps distinct", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  const totals = (date, providers, models) => ({
    date, source_cost_usd: "2.00", calls: 4, sessions: 2,
    tokens: { input: 20, output: 8, cache_read: 3, cache_write: 1 }, carried: false,
    breakdowns: { providers, models },
  });
  const row = (id, calls, cost, sessions, tokens) => ({ id, calls, source_cost_usd: cost, sessions, tokens });
  const days = [
    totals("2026-10-20", [row("provider-a", 4, "1.25", null, { input: 12, output: 4, cache_read: 2, cache_write: 1 })], [row("model-a", 4, "1.25", null, { input: 12, output: 4, cache_read: 2, cache_write: 1 })]),
    totals("2026-10-19", null, []),
    totals("2026-10-18", [], [row("model-b", 1, "0.75", 1, { input: 8, output: 4, cache_read: 1, cache_write: 0 })]),
  ];
  await page.route("**/api/v1/snapshot?**", async (route) => route.fulfill({ json: {
    ...snapshot,
    cached_usage_status: { state: "unavailable", last_success_at: "2026-10-20T08:00:00Z", error: "not rendered" },
    cached_usage: {
      schema: "monitoring.cached-usage.v1", source: "codeburn-daily-cache", format_version: 74,
      sampled_at: "2026-10-20T08:00:00Z", file_modified_at: "2026-10-20T07:59:00Z", file_age_seconds: 60,
      date_basis: "codeburn-local-calendar", complete: false, watermark_trusted: null,
      last_computed_date: "2026-10-19", days, retained: true,
    },
  } }));
  await page.goto("/#usage");
  const panel = page.locator("#codeburn-cache-panel");
  const rows = panel.locator("#codeburn-cache-table tbody tr");
  await expect(panel).toContainText("Selected CodeBurn cache is unavailable");
  await expect(panel).toContainText("prior cached snapshot");

  await page.selectOption("#codeburn-cache-view", "providers");
  await expect(panel).toContainText("Provider breakdown maps represented for 2 of 3 selected cached days");
  await expect(panel).toContainText("1 empty maps · 1 unknown or unavailable maps");
  await expect(panel).toContainText("does not cross-filter the other dimension");
  await expect(rows).toHaveCount(2);
  await expect(panel.locator("#codeburn-cache-table")).toContainText("Unknown provider breakdown");
  await expect(rows.nth(0).locator("td").nth(1)).toContainText("provider-a");
  await expect(rows.nth(0).locator("td").nth(7)).toContainText("Unknown");
  await expect(panel.locator("#codeburn-cache-table thead")).not.toContainText("Total tokens");

  await page.selectOption("#codeburn-provider-filter", "provider-a");
  await expect(rows).toHaveCount(1);
  await expect(panel.locator("#codeburn-cache-table")).toContainText("provider-a");
  await expect(panel.locator("#codeburn-cache-table")).not.toContainText("model-a");

  await page.selectOption("#codeburn-cache-view", "models");
  await expect(rows).toHaveCount(2);
  await expect(panel.locator("#codeburn-cache-table")).toContainText("model-a");
  await expect(panel.locator("#codeburn-cache-table")).toContainText("model-b");
  await expect(panel.locator("#codeburn-cache-table thead")).not.toContainText("Provider");
  await page.selectOption("#codeburn-model-filter", "model-a");
  await expect(rows).toHaveCount(1);
  await expect(panel.locator("#codeburn-cache-table")).not.toContainText("provider-a");
  await expect(rows.first().locator("td").nth(7)).toContainText("Unknown");

  await page.selectOption("#codeburn-cache-range", "date");
  await page.locator("#codeburn-cache-date").fill("2026-10-19");
  await page.locator("#codeburn-cache-date").dispatchEvent("change");
  await page.selectOption("#codeburn-cache-view", "providers");
  await expect(panel).toContainText("Provider breakdown maps represented for 0 of 1 selected cached days");
  await expect(panel).toContainText("unknown or unavailable maps");
  await expect(rows).toHaveCount(1);
  await expect(rows.first().locator("td").nth(1)).toContainText("Unknown provider breakdown");
  for (const cell of await rows.first().locator("td").all().then((cells) => cells.slice(2))) {
    await expect(cell).toHaveText("Unknown");
  }
});

import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

function reportFor(url) {
  const params = new URL(url).searchParams;
  const from = params.get("since"), until = params.get("until");
  return {
    schema: "monitoring.report.v1",
    observed_at: "2026-11-02T12:00:00Z",
    currency: "USD",
    fx: { schema: "monitoring.fx.v1", base: "USD", as_of: "2026-10-15", provenance: "dated-fixture", rates: { EUR: "0.9" } },
    range: { from, until },
    previous_range: from ? { from: "2026-10-27", until: "2026-11-01" } : null,
    summary: {
      calls: 2, total_tokens: 50,
      costs: { estimated: { amount: "0.01", events: 1 }, billed: { amount: "0.02", events: 1 } },
      observed_sessions: 1, estimated_session_count: 1,
      known_estimate_per_priced_session: "0.01", cache_read_share_percent: "25",
    },
    breakdowns: {
      projects: { rows: [{ key: "demo-app", metrics: { calls: 2, total_tokens: 50, costs: { estimated: { amount: "0.01", events: 1 }, billed: { amount: "0.02", events: 1 } } }, observed_sessions: 1, estimated_session_count: 1, known_estimate_per_priced_session: "0.01", cache_read_share_percent: "25", timing: { records: 1, p50_ms: 800, p95_ms: 800, output_tokens_per_second: "12.5", output_rate_records: 1 } }], truncated: false },
      models: { rows: [{ key: "demo-model", metrics: { calls: 2, total_tokens: 50, costs: { estimated: { amount: "0.01", events: 1 }, billed: { amount: "0.02", events: 1 } } }, observed_sessions: 1, estimated_session_count: 1, known_estimate_per_priced_session: "0.01", cache_read_share_percent: "25", timing: { records: 1, p50_ms: 800, p95_ms: 800, output_tokens_per_second: "12.5", output_rate_records: 1 } }], truncated: false },
      daily: { rows: [{ key: "2026-11-02", metrics: { calls: 2, total_tokens: 50, costs: { estimated: { amount: "0.01", events: 1 }, billed: { amount: "0.02", events: 1 } } }, observed_sessions: 1, estimated_session_count: 1, known_estimate_per_priced_session: "0.01", cache_read_share_percent: "25", timing: { records: 1, p50_ms: 800, p95_ms: 800, output_tokens_per_second: "12.5", output_rate_records: 1 } }], truncated: false },
    },
    tools: { records: 1, errors: 0, unclassified_records: 0, categories: [{ key: "core", category: "core", records: 1, errors: 0, duration_records: 1, total_duration_ms: 20, p50_ms: 20, p95_ms: 20 }], names: [], mcp_servers: [], shell_commands: [], skills: [], agents: [], truncated: { categories: false } },
    comparisons: {
      records: { current: 2, previous: 1, change: 1, percent_change: "100" },
      tokens: { current: 50, previous: 25, change: 25, percent_change: "100" },
      sessions: { current: 1, previous: 1, change: 0, percent_change: "0" },
      estimated_cost: { current: "0.01", previous: "0.01", change: null, percent_change: null, coverage_reason: "incomplete-cost-coverage" },
      billed_cost: { current: "0.02", previous: "0.02", change: null, percent_change: null, coverage_reason: "incomplete-cost-coverage" },
    },
    coverage: { scope: "preselected explicitly recorded events", unknown_cost_events: 1, missing_usage_duration_records: 1, missing_tool_duration_records: 0, unsupported: ["context-overhead", "one-shot-rate"], truncated: { projects: false, models: false, daily: false } },
  };
}

test("report loads only in its view, applies UTC presets and ignores stale responses", async ({ page }) => {
  let count = 0;
  const requests = [];
  await page.route("**/api/v1/report?**", async (route) => {
    const index = count++;
    const url = route.request().url(); requests.push(url);
    if (index === 0) await new Promise((resolve) => setTimeout(resolve, 500));
    try { await route.fulfill({ json: reportFor(url) }); } catch { /* The dashboard may abort an obsolete report read. */ }
  });
  await page.goto("/");
  await expect(page.locator(".metric")).toHaveCount(4);
  expect(count).toBe(0);
  await page.locator('.nav[data-view="report"]').click();
  await expect.poll(() => count).toBe(1);
  await page.selectOption("#report-period", "7days");
  await expect.poll(() => count).toBe(2);
  await expect(page.locator("#report-range")).toContainText("through");
  const today = new Date().toISOString().slice(0, 10);
  const since = new Date(`${today}T00:00:00Z`); since.setUTCDate(since.getUTCDate() - 6);
  expect(await page.locator('[name="since"]').inputValue()).toBe(since.toISOString().slice(0, 10));
  expect(await page.locator('[name="until"]').inputValue()).toBe(today);
  await expect(page.locator("#report-projects")).toContainText("demo-app");
  await expect(page.locator("#metrics")).toBeHidden();
  await expect(page.locator("#report-projects thead tr th")).toHaveCount(7);
  await expect(page.locator("#report-models thead tr th")).toHaveCount(9);
  await expect(page.locator("#report-daily thead tr th")).toHaveCount(5);
  await expect(page.locator("#report-projects-note")).toContainText("1 project row shown");
  await expect(page.locator("#report-models")).toContainText("demo-model");
  await expect(page.locator("#report-models")).toContainText("rate records");
  await expect(page.locator("#report-daily")).toContainText("2026-11-02");
  await expect(page.locator("#report-tool-groups")).toContainText("core");
  await expect(page.locator("#report-comparisons")).toContainText("incomplete cost coverage");
  await expect(page.locator("#report-coverage")).toContainText("missing usage duration records");
  await expect(page.locator("#report-coverage")).toContainText("Unavailable measures");
  await expect(page.locator("#report-coverage")).toContainText("Truncated sections");
  await expect(page.locator("#report-coverage")).toContainText("Target currency USD; supplied USD-base conversion book dated 2026-10-15, provenance dated-fixture.");
  await expect(page.locator("#report-coverage")).toContainText("Rates are from that date.");
  await expect(page.locator("#report-coverage")).not.toContainText("{\"");
  await expect(page.locator("#report-content")).toContainText("time to first edit");
  await page.waitForTimeout(550);
  await expect(page.locator("#report-range")).toContainText(since.toISOString().slice(0, 10));
  const latest = new URL(requests.at(-1));
  expect(latest.searchParams.get("since")).toBe(since.toISOString().slice(0, 10));
  await page.keyboard.press("5");
  await expect(page.locator("#report")).toBeVisible();
  await page.locator("#report-period").selectOption("all");
  await expect(page.locator('[name="since"]')).toHaveValue("");
  await expect(page.locator('[name="until"]')).toHaveValue("");
  await expect(page.locator("#report-comparisons")).toContainText("no equal preceding range");
});

test("report errors distinguish an empty state from a retained last observation", async ({ page }) => {
  await page.route("**/api/v1/report?**", (route) => route.abort());
  await page.goto("/");
  await page.locator('.nav[data-view="report"]').click();
  await expect(page.locator("#report-status")).toContainText("Failed to fetch");
  await expect(page.locator("#report-status")).toContainText("No report observations have loaded yet.");
  await expect(page.locator("#report-content")).toBeHidden();
});

test("a failed filtered read retains the last report with a qualification", async ({ page }) => {
  let count = 0;
  await page.route("**/api/v1/report?**", async (route) => {
    if (count++ > 0) return route.abort();
    await route.fulfill({ json: reportFor(route.request().url()) });
  });
  await page.goto("/");
  await page.locator('.nav[data-view="report"]').click();
  await expect(page.locator("#report-content")).toBeVisible();
  await page.selectOption('[name="provider"]', "anthropic");
  await expect(page.locator("#report-status")).toContainText("last successful report observed at 2026-11-02T12:00:00Z remains visible");
  await expect(page.locator("#report-projects")).toContainText("demo-app");
  await expect(page.locator("#download-report")).toBeDisabled();
});

test("download saves the displayed bounded report with its filters and coverage", async ({ page }) => {
  await page.goto("/");
  await page.locator('.nav[data-view="report"]').click();
  await expect(page.locator("#download-report")).toBeEnabled();
  await page.selectOption("#report-period", "today");
  await expect(page.locator("#report-range")).toContainText(new Date().toISOString().slice(0, 10));
  const downloadPromise = page.waitForEvent("download");
  await page.locator("#download-report").click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^monitoring-report-.*\.json$/);
  const saved = JSON.parse(await readFile(await download.path(), "utf8"));
  expect(saved.schema).toBe("monitoring.report.v1");
  expect(saved.revision).toBeGreaterThan(0);
  expect(saved.filters.since).toBe(`${new Date().toISOString().slice(0, 10)}T00:00:00.000Z`);
  expect(saved.coverage).toBeTruthy();
  expect(saved.summary).toBeTruthy();
  expect(saved.events).toBeUndefined();
  expect(saved.observed_at).toBeTruthy();
});

test("six-month period clamps to the same UTC day in the target month", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-08-31T12:00:00Z") });
  await page.route("**/api/v1/report?**", (route) => route.fulfill({ json: reportFor(route.request().url()) }));
  await page.goto("/");
  await page.locator('.nav[data-view="report"]').click();
  await page.selectOption("#report-period", "6months");
  await expect(page.locator('[name="since"]')).toHaveValue("2026-02-28");
  await expect(page.locator('[name="until"]')).toHaveValue("2026-08-31");
});

test("paused report view makes no new requests and retains its last observation", async ({ page }) => {
  let count = 0;
  await page.route("**/api/v1/report?**", (route) => { count += 1; return route.fulfill({ json: reportFor(route.request().url()) }); });
  await page.goto("/");
  await page.locator('.nav[data-view="report"]').click();
  await expect(page.locator("#report-content")).toBeVisible();
  await page.locator("#pause").click();
  await page.selectOption('[name="provider"]', "anthropic");
  await expect(page.locator("#report-status")).toContainText("Report refresh is paused");
  await expect(page.locator("#report-status")).toContainText("Last successful report observed at");
  expect(count).toBe(1);
  await page.locator("#pause").click();
  await expect.poll(() => count).toBe(2);
});

test("report says conversion is unavailable when no FX book is supplied", async ({ page }) => {
  await page.route("**/api/v1/report?**", async (route) => {
    const report = reportFor(route.request().url()); report.fx = null;
    await route.fulfill({ json: report });
  });
  await page.goto("/");
  await page.locator('.nav[data-view="report"]').click();
  await expect(page.locator("#report-coverage")).toContainText("Target currency USD; no conversion book supplied.");
  await expect(page.locator("#report-coverage")).toContainText("Amounts requiring conversion remain excluded from converted totals or unknown.");
});

test("real demo report endpoint returns the exact schema and populated breakdowns", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".metric")).toHaveCount(4);
  const responsePromise = page.waitForResponse((response) => response.url().includes("/api/v1/report?"));
  await page.locator('.nav[data-view="report"]').click();
  const response = await responsePromise;
  expect(response.ok()).toBeTruthy();
  const report = await response.json();
  expect(report.schema).toBe("monitoring.report.v1");
  expect(report.breakdowns.projects.rows.length).toBeGreaterThan(0);
  expect(report.breakdowns.models.rows.length).toBeGreaterThan(0);
  expect(report.tools.records).toBeGreaterThan(0);
  await expect(page.locator("#report-projects tbody tr").first()).toBeVisible();
  await expect(page.locator("#report-tool-groups tbody tr").first()).toBeVisible();
});

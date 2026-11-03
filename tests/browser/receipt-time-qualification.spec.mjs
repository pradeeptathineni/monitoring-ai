import { test, expect } from "@playwright/test";

function reportFor(snapshot, includeReceiptCount) {
  const costs = { estimated: { amount: null, events: 0 }, billed: { amount: null, events: 0 } };
  const metric = { calls: 0, total_tokens: 0, costs };
  const group = { rows: [], truncated: false };
  return {
    schema: "monitoring.report.v1", identity: snapshot.identity, revision: snapshot.revision,
    observed_at: snapshot.observed_at, currency: "USD", range: { from: "2026-11-02", until: "2026-11-03" },
    previous_range: null,
    summary: { ...metric, observed_sessions: 0, estimated_session_count: 0,
      known_estimate_per_priced_session: null, cache_read_share_percent: null },
    breakdowns: { projects: group, models: group, daily: group },
    tools: { records: 0, errors: 0, unclassified_records: 0, categories: [], names: [], mcp_servers: [], shell_commands: [], skills: [], agents: [], truncated: {} },
    comparisons: {}, coverage: includeReceiptCount ? { receipt_time_summary_records: 3 } : {},
  };
}

test("receipt-time summaries are excluded from pace and qualified in date groups without changing totals", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const baseline = await response.json();
  const withReceipt = structuredClone(baseline);
  withReceipt.coverage.receipt_time_summary_records = 3;
  withReceipt.insights ||= {
    schema: "monitoring.insights.v1", window: { minutes: 60, from: baseline.observed_at, until: baseline.observed_at },
    pace: { records: 4, tokens: 400, tokens_per_minute: "6.7", estimated_cost_per_hour: null,
      estimated_priced_records: 2, unknown_cost_records: 2, unconverted_cost_records: 0, billed_records: 0 },
    forecast: { state: "partial", tokens: 400, estimated_cost: null, horizon_minutes: 60,
      assumption: "Selected-window rate", observed_span_seconds: 300, confidence: "low" },
    source_freshness: [], quotas: { state: "unconfigured", windows: [], note: "No quota observations supplied." },
  };
  withReceipt.insights.pace.excluded_summary_records = 2;
  const withoutReceipt = structuredClone(withReceipt);
  delete withoutReceipt.coverage.receipt_time_summary_records;
  delete withoutReceipt.insights.pace.excluded_summary_records;
  let includeReceiptCount = true;
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: includeReceiptCount ? withReceipt : withoutReceipt }));
  await page.route("**/api/v1/report?**", (route) => route.fulfill({ json: reportFor(baseline, includeReceiptCount) }));
  await page.goto("/");

  await expect(page.locator("#scope")).toContainText("3 cumulative Claude CLI result summaries use capture receipt time, not call time");
  await expect(page.locator("#scope")).toContainText("Summary totals retain them");
  await expect(page.locator("#pace")).toContainText("2 cumulative Claude CLI result summaries are excluded from short-window pace and forecast");
  const observedTokenMetric = await page.locator("#metrics .metric").nth(1).innerText();

  await page.locator('.nav[data-view="report"]').click();
  await expect(page.locator("#report-range")).toContainText("3 cumulative Claude CLI result summaries use their capture receipt date, not call time");
  await expect(page.locator("#report-coverage")).toContainText("Receipt-time summary records");
  await expect(page.locator("#report-coverage")).toContainText("Period groups can include earlier work. Report totals retain these records.");

  includeReceiptCount = false;
  await page.goto("/");
  await expect(page.locator("#scope")).not.toContainText("cumulative Claude CLI result summaries");
  await expect(page.locator("#pace")).not.toContainText("excluded from short-window pace");
  expect(await page.locator("#metrics .metric").nth(1).innerText()).toBe(observedTokenMetric);
  await page.locator('.nav[data-view="report"]').click();
  await expect(page.locator("#report-range")).not.toContainText("cumulative Claude CLI result summaries");
  await expect(page.locator("#report-coverage")).not.toContainText("Receipt-time summary records");
});

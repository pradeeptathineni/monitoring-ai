"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const number = new Intl.NumberFormat("en-US");
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const bucketNames = { input: "Input", cache_read: "Cache read", cache_write: "Cache write", output: "Output" };
const insightUnavailable = "Insights are unavailable in this snapshot.";
const titles = { overview: ["Every session. One place.", "Follow the work, understand the usage, keep the evidence."],
  sessions: ["See the whole working tree.", "Sessions, agents and workers, with evidence behind every state."],
  usage: ["Understand every token.", "Compare models and surfaces without losing cost qualifications."],
  events: ["The story behind the numbers.", "A chronological ledger of explicitly supplied activity."],
  report: ["Usage with context.", "A detailed, qualified report for the selected UTC period and filters."] };
let data, paused = false, busy = false, view = "overview", entity = null;
let lastSuccess = 0;
let reportController = null, reportRequestId = 0, reportData = null;

function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined && text !== null) result.textContent = text;
  if (className) result.className = className;
  return result;
}

function cash(value) {
  if (value === null) return "Unknown";
  try { return new Intl.NumberFormat("en-US", { style: "currency", currency: data.currency, minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(Number(value)); }
  catch { return `${value} ${data.currency}`; }
}

function showView(next) {
  if (!titles[next]) return;
  if (view === next && location.hash.slice(1) === next) return;
  if (view === "report" && next !== "report") cancelReport();
  view = next;
  $("#metrics").hidden = next === "report";
  $$(".view").forEach((section) => { section.hidden = section.id !== next; });
  $$(".nav, .mobile-nav button").forEach((button) => button.classList.toggle("active", button.dataset.view === next));
  $("#breadcrumb").textContent = { overview: "Overview", sessions: "Sessions & workers", usage: "Usage & costs", events: "Event stream", report: "Report" }[next];
  $("#title").textContent = titles[next][0];
  $("#subtitle").textContent = titles[next][1];
  location.hash = next;
  if (next === "report") loadReport();
}

function query() {
  const values = new URLSearchParams();
  new FormData($("#filters")).forEach((value, key) => { if (value) values.set(key, value); });
  if ($("#state-filter").value) values.set("state", $("#state-filter").value);
  return values;
}

function utcDate(date = new Date()) {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}

function applyReportPeriod(period) {
  const now = new Date(), until = utcDate(now), start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  let since = "";
  if (period === "today") since = until;
  else if (period === "7days") { start.setUTCDate(start.getUTCDate() - 6); since = utcDate(start); }
  else if (period === "30days") { start.setUTCDate(start.getUTCDate() - 29); since = utcDate(start); }
  else if (period === "thismonth") since = `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}-01`;
  else if (period === "6months") {
    const monthStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 6, 1));
    const monthEndDay = new Date(Date.UTC(monthStart.getUTCFullYear(), monthStart.getUTCMonth() + 1, 0)).getUTCDate();
    monthStart.setUTCDate(Math.min(now.getUTCDate(), monthEndDay)); since = utcDate(monthStart);
  }
  else if (period === "all") { since = ""; }
  else return;
  $("#filters [name=since]").value = since;
  $("#filters [name=until]").value = period === "all" ? "" : until;
  $("#filters").dispatchEvent(new Event("change", { bubbles: true }));
}

function cancelReport() {
  reportRequestId += 1;
  if (reportController) reportController.abort();
  reportController = null;
}

function reportCurrency(value, currency = reportData?.currency || data?.currency || "USD") {
  if (value === null || value === undefined || value === "") return "Unknown";
  if (typeof value === "object" && value.amount !== undefined) value = value.amount;
  try { return new Intl.NumberFormat("en-US", { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(Number(value)); }
  catch { return `${value} ${currency}`; }
}

function reportNumber(value) {
  if (value === null || value === undefined || value === "") return "Unknown";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? number.format(parsed) : String(value);
}

function reportTokens(metrics) {
  const total = metrics?.total_tokens ?? metrics?.tokens_total;
  if (total !== undefined && total !== null) return reportNumber(total);
  if (metrics?.tokens && typeof metrics.tokens === "object") return reportNumber(Object.values(metrics.tokens).reduce((sum, item) => sum + (Number(item) || 0), 0));
  return "Unknown";
}

function reportCalls(metrics) {
  return reportNumber(metrics?.calls ?? metrics?.records ?? metrics?.usage_records);
}

function reportRows(target, headers, rows, emptyMessage = "No matching records in this period.") {
  const root = $(target); root.replaceChildren();
  if (!Array.isArray(rows) || !rows.length) { root.append(node("p", emptyMessage, "empty")); return; }
  const grid = node("table"), head = node("thead"), heading = node("tr");
  for (const text of headers) heading.append(node("th", text));
  head.append(heading); grid.append(head);
  const body = node("tbody");
  for (const values of rows) {
    const tr = node("tr");
    for (const value of values) { const td = node("td", value); tr.append(td); }
    body.append(tr);
  }
  grid.append(body); root.append(grid);
}

function reportCost(metrics, basis, currency) {
  return reportCurrency(metrics?.costs?.[basis]?.amount ?? null, currency);
}

function renderProjectRows(rows, currency) {
  return (Array.isArray(rows) ? rows : []).map((row) => {
    const metrics = row.metrics || {};
    return [row.key ?? "Unknown", reportCalls(metrics), row.observed_sessions == null ? "Unknown" : reportNumber(row.observed_sessions), reportTokens(metrics), reportCost(metrics, "estimated", currency), reportCost(metrics, "billed", currency), row.known_estimate_per_priced_session == null ? "Unknown" : `${reportCurrency(row.known_estimate_per_priced_session, currency)} / priced session`];
  });
}

function renderModelRows(rows, currency) {
  return (Array.isArray(rows) ? rows : []).map((row) => {
    const metrics = row.metrics || {}, timing = row.timing || {};
    const rate = timing.output_tokens_per_second == null ? "Unknown" : `${reportNumber(timing.output_tokens_per_second)} tokens/s · ${reportNumber(timing.output_rate_records)} rate records`;
    return [row.key ?? "Unknown", reportCalls(metrics), reportTokens(metrics), row.cache_read_share_percent == null ? "Unknown" : `${reportNumber(row.cache_read_share_percent)}%`, reportCost(metrics, "estimated", currency), reportCost(metrics, "billed", currency), Number.isFinite(timing.p50_ms) ? `${number.format(timing.p50_ms)} ms` : "Unknown", Number.isFinite(timing.p95_ms) ? `${number.format(timing.p95_ms)} ms` : "Unknown", rate];
  });
}

function renderDailyRows(rows, currency) {
  return (Array.isArray(rows) ? rows : []).map((row) => {
    const metrics = row.metrics || {};
    return [row.key ?? "Unknown date", reportCalls(metrics), reportTokens(metrics), reportCost(metrics, "estimated", currency), reportCost(metrics, "billed", currency)];
  });
}

function setBreakdownNote(target, label, section) {
  const rows = Array.isArray(section?.rows) ? section.rows : [];
  const note = $(target);
  const singular = label === "Days" ? "day" : label.slice(0, -1).toLowerCase();
  note.textContent = section?.truncated
    ? `${number.format(rows.length)} rows displayed; this ${label.toLowerCase()} section is truncated at the configured bound. Aggregate report totals above include the selected range.`
    : `${number.format(rows.length)} ${rows.length === 1 ? singular : label.toLowerCase()} ${rows.length === 1 ? "row" : "rows"} shown for the selected range.`;
}

function renderReport() {
  const report = reportData, currency = report.currency || "USD", summary = report.summary || {}, metrics = summary.metrics || summary;
  const range = report.range || {};
  $("#report-range").textContent = `Current range: ${range.from || "start unknown"} through ${range.until || "end unknown"} UTC · target currency ${currency} · observed ${report.observed_at || "time unknown"}. ${report.previous_range ? `Prior range: ${report.previous_range.from} through ${report.previous_range.until} UTC.` : "No prior range is defined."}`;
  const estimated = metrics.costs?.estimated?.amount ?? metrics.estimated_cost ?? null;
  const estimatedRecords = metrics.costs?.estimated?.events ?? summary.estimated_records;
  $("#report-metrics").replaceChildren(
    metric("Usage records", reportCalls(metrics), "Recorded usage events; not calls across unconnected sources", "≋"),
    metric("Observed tokens", metrics.total_tokens == null ? reportTokens(metrics) : compact.format(metrics.total_tokens), "Disjoint token buckets counted once", "◷"),
    metric("Observed sessions", reportNumber(summary.observed_sessions ?? metrics.observed_sessions ?? summary.sessions), "Observed session identities; source coverage may be limited", "⌘"),
    metric("Known estimated cost", reportCurrency(estimated, currency), `${estimatedRecords == null ? "Coverage unknown" : `${reportNumber(estimatedRecords)} priced records`} · not a complete bill`, "≈"),
  );
  const measures = $("#report-session-measures"); measures.replaceChildren();
  measures.append(confidenceRow("Recorded cache-read share", summary.cache_read_share_percent == null ? "Unknown" : `${reportNumber(summary.cache_read_share_percent)}%`));
  measures.append(confidenceRow("Estimated session count", reportNumber(summary.estimated_session_count)));
  measures.append(confidenceRow("Known estimate per priced session", summary.known_estimate_per_priced_session == null ? "Unknown" : `${reportCurrency(summary.known_estimate_per_priced_session, currency)} / priced session`));
  measures.append(node("p", "Estimated session counts and per-priced-session amounts describe covered records only.", "confidence-note"));

  const comparisons = report.comparisons || {}, comparisonKeys = ["records", "tokens", "sessions", "estimated_cost", "billed_cost"];
  const compareFmt = (key, value) => value == null ? "Unknown" : key.includes("cost") ? reportCurrency(value, currency) : reportNumber(value);
  if (!report.previous_range) $("#report-comparisons").replaceChildren(node("p", "Prior-period comparisons are unavailable because no equal preceding range is defined for this selection.", "empty"));
  else {
    const values = comparisonKeys.map((key) => {
      const entry = comparisons[key];
      if (!entry || typeof entry !== "object") return [key.replaceAll("_", " "), "Unavailable", "Unavailable", "Unavailable", "Unavailable"];
      const reason = entry.coverage_reason === "incomplete-cost-coverage" ? " · incomplete cost coverage" : entry.coverage_reason ? ` · ${entry.coverage_reason.replaceAll("-", " ")}` : "";
      return [key.replaceAll("_", " "), compareFmt(key, entry.current), compareFmt(key, entry.previous), entry.change == null ? `Unavailable${reason}` : compareFmt(key, entry.change), entry.percent_change == null ? `Unavailable${reason}` : `${reportNumber(entry.percent_change)}%`];
    });
    reportRows("#report-comparisons", ["Measure", "Current", "Previous", "Change", "Change %"], values, "Prior-period comparison unavailable.");
  }
  const projects = report.breakdowns?.projects, models = report.breakdowns?.models, daily = report.breakdowns?.daily;
  reportRows("#report-projects", ["Project alias", "Usage records", "Observed sessions", "Tokens", `Known estimate · ${currency}`, `Reported billed · ${currency}`, "Known estimate / priced session"], renderProjectRows(projects?.rows, currency), "No project rows match this period and filter.");
  setBreakdownNote("#report-projects-note", "Projects", projects);
  reportRows("#report-models", ["Model", "Usage records", "Tokens", "Recorded cache-read share", `Known estimate · ${currency}`, `Reported billed · ${currency}`, "P50 reported duration", "P95 reported duration", "Reported output rate · sample count"], renderModelRows(models?.rows, currency), "No model rows match this period and filter.");
  setBreakdownNote("#report-models-note", "Models", models);
  reportRows("#report-daily", ["UTC date", "Usage records", "Tokens", `Known estimate · ${currency}`, `Reported billed · ${currency}`], renderDailyRows(daily?.rows, currency), "No daily rows match this period and filter.");
  setBreakdownNote("#report-daily-note", "Days", daily);

  const tools = report.tools || {}, toolSummary = $("#report-tools-summary"); toolSummary.replaceChildren();
  for (const [label, key] of [["Tool records", "records"], ["Errors", "errors"], ["Unclassified", "unclassified_records"]]) toolSummary.append(confidenceRow(label, reportNumber(tools[key])));
  const groups = $("#report-tool-groups"); groups.replaceChildren();
  const toolLabels = { categories: "Categories", names: "Tool names", mcp_servers: "MCP server aliases", shell_commands: "Shell tool / command aliases", skills: "Skill invocation aliases", agents: "Agent invocation aliases" };
  let foundGroup = false;
  for (const [key, label] of Object.entries(toolLabels)) {
    const values = tools[key]; if (!Array.isArray(values) || !values.length) continue;
    foundGroup = true; groups.append(node("h3", label));
    const wrap = node("div", null, "table-wrap"); groups.append(wrap);
    const headers = ["Name", "Category", "Records", "Errors", "Duration records", "Total duration", "P50", "P95"];
    const rows = values.map((row) => [row.key ?? "Unknown", row.category ?? "Unknown", reportNumber(row.records), reportNumber(row.errors), reportNumber(row.duration_records), row.total_duration_ms == null ? "Unknown" : `${reportNumber(row.total_duration_ms)} ms`, row.p50_ms == null ? "Unknown" : `${reportNumber(row.p50_ms)} ms`, row.p95_ms == null ? "Unknown" : `${reportNumber(row.p95_ms)} ms`]);
    const grid = node("table"), head = node("thead"), heading = node("tr"); headers.forEach((text) => heading.append(node("th", text))); head.append(heading); grid.append(head);
    const body = node("tbody"); rows.forEach((values) => { const tr = node("tr"); values.forEach((value) => tr.append(node("td", value))); body.append(tr); }); grid.append(body); wrap.append(grid);
  }
  if (!foundGroup) groups.append(node("p", "No categorized tool activity was reported for this period.", "empty"));
  if (Object.values(tools.truncated || {}).some(Boolean)) groups.append(node("p", "One or more tool breakdowns are truncated by the report bound.", "confidence-note"));

  const coverage = report.coverage || {}, coverageRoot = $("#report-coverage"); coverageRoot.replaceChildren();
  const fx = report.fx;
  const fxSummary = fx
    ? `Target currency ${currency}; supplied ${fx.base || "USD"}-base conversion book dated ${fx.as_of || "date unknown"}, provenance ${fx.provenance || "unknown"}. This dated display conversion makes no current-rate claim.`
    : `Target currency ${currency}; no conversion book supplied. Amounts requiring conversion remain excluded from converted totals or unknown. No current-rate claim is made.`;
  coverageRoot.append(confidenceRow("Currency and FX provenance", fxSummary));
  const entries = Object.entries(coverage);
  if (!entries.length) coverageRoot.append(node("p", "Coverage details are unavailable in this report.", "empty"));
  const unsupportedLabels = { "context-overhead": "Context overhead", "one-shot-rate": "One-shot rate", corrections: "Corrections", rework: "Rework", "time-to-first-edit": "Time to first edit" };
  for (const [key, value] of entries) {
    if (key === "unsupported" && Array.isArray(value)) {
      const labels = value.map((item) => unsupportedLabels[item] || item.replaceAll("-", " "));
      coverageRoot.append(confidenceRow("Unavailable measures", labels.length ? labels.join(" · ") : "None listed"));
    } else if (key === "truncated" && value && typeof value === "object") {
      const sections = Object.entries(value).filter(([, truncated]) => truncated).map(([name]) => name.replaceAll("_", " "));
      coverageRoot.append(confidenceRow("Truncated sections", sections.length ? sections.join(" · ") : "None"));
    } else if (key === "selection_basis") {
      coverageRoot.append(confidenceRow("Row ordering", "Projects/models: most recorded tokens first. Tools: most records first. Days: chronological."));
    } else if (typeof value === "boolean") coverageRoot.append(confidenceRow(key.replaceAll("_", " "), value ? "Yes" : "No"));
    else if (Array.isArray(value)) coverageRoot.append(confidenceRow(key.replaceAll("_", " "), value.map((item) => String(item).replaceAll("-", " ")).join(" · ") || "None"));
    else if (value && typeof value === "object") {
      const readable = Object.entries(value).map(([name, item]) => `${name.replaceAll("_", " ")}: ${typeof item === "boolean" ? (item ? "yes" : "no") : String(item)}`).join(" · ");
      coverageRoot.append(confidenceRow(key.replaceAll("_", " "), readable || "Unknown"));
    } else coverageRoot.append(confidenceRow(key.replaceAll("_", " "), reportNumber(value)));
  }
}

async function loadReport(force = true) {
  if (view !== "report") return;
  if (paused) {
    const last = reportData ? ` Last successful report observed at ${reportData.observed_at || "unknown time"} remains visible and may not match current filters.` : " No report observations have loaded yet.";
    $("#report-status").textContent = `Report refresh is paused.${last}`;
    $("#report-status").classList.remove("error");
    $("#report-content").hidden = !reportData;
    return;
  }
  if (!force && reportController) return;
  cancelReport();
  const requestId = reportRequestId, controller = new AbortController();
  reportController = controller;
  const retained = reportData ? ` Last successful report from ${reportData.observed_at || "an unknown time"} remains visible and may not match the current filters.` : " No report observations have loaded yet.";
  $("#report-status").textContent = `Loading report for the selected UTC range and filters.${retained}`;
  $("#report-status").classList.remove("error");
  try {
    const response = await fetch(`/api/v1/report?${query()}`, { signal: controller.signal, cache: "no-store" });
    if (!response.ok) throw new Error(`Report read failed (${response.status}).`);
    const value = await response.json();
    if (requestId !== reportRequestId || view !== "report") return;
    if (value.schema !== "monitoring.report.v1") throw new Error("The report endpoint returned an incompatible schema.");
    reportData = value;
    $("#report-content").hidden = false;
    const hasTruncation = Object.values(value.coverage?.truncated || {}).some(Boolean) || Object.values(value.tools?.truncated || {}).some(Boolean) || Object.values(value.breakdowns || {}).some((group) => Boolean(group?.truncated));
    $("#report-status").textContent = `Report observed at ${value.observed_at || "unknown time"} UTC.${hasTruncation ? " One or more report sections are truncated by configured bounds." : ""}`;
    renderReport();
  } catch (error) {
    if (error.name === "AbortError" || requestId !== reportRequestId || view !== "report") return;
    $("#report-status").classList.add("error");
    const last = reportData ? ` The last successful report observed at ${reportData.observed_at || "unknown time"} remains visible and may not match current filters.` : " No report observations have loaded yet.";
    $("#report-status").textContent = `${error.message}${last}`;
    $("#report-content").hidden = !reportData;
  } finally {
    if (requestId === reportRequestId) reportController = null;
  }
}

function metric(label, value, note, symbol) {
  const card = node("article", null, "metric");
  const heading = node("div", label, "metric-label");
  heading.append(node("span", symbol, "symbol"));
  card.append(heading, node("div", value, "value"), node("div", note, "metric-note"));
  return card;
}

function fillFacets() {
  for (const dimension of ["provider", "model", "surface", "project", "source", "session"]) {
    const select = $(`[name=${dimension}]`);
    const current = select.value;
    const desired = data.facets[dimension];
    if (JSON.stringify([...select.options].slice(1).map((o) => o.value)) !== JSON.stringify(desired)) {
      select.replaceChildren(select.options[0]);
      for (const value of desired) {
        const option = node("option", value); option.value = value; select.append(option);
      }
      select.value = current;
    }
  }
}

function insight() {
  const value = data?.insights;
  return value?.schema === "monitoring.insights.v1" ? value : null;
}

function confidenceRow(label, value) {
  const row = node("div", null, "confidence-row"); row.append(node("span", label), node("strong", value)); return row;
}

function renderInsights() {
  const value = insight(), freshness = $("#freshness"), pace = $("#pace"), quotas = $("#quotas");
  freshness.replaceChildren(); pace.replaceChildren(); quotas.replaceChildren();
  if (!value) {
    freshness.append(node("p", insightUnavailable, "empty"));
    pace.append(node("p", insightUnavailable, "empty"));
    quotas.append(node("p", insightUnavailable, "empty")); return;
  }
  const sources = Array.isArray(value.source_freshness) ? value.source_freshness : [];
  if (!sources.length) freshness.append(node("p", "No source freshness observations for this filter.", "empty"));
  for (const item of sources) {
    const row = node("div", null, "confidence-row freshness-row");
    const label = node("span", item.source); label.append(node("small", `${item.events} events${item.last_event_at ? ` · last ${item.last_event_at.replace("T", " ").replace("Z", " UTC")}` : " · no event time"}`));
    const age = Number.isFinite(item.age_seconds) ? `${number.format(Math.max(0, Math.round(item.age_seconds)))}s` : "Unknown age";
    row.append(label, node("strong", `${item.state || "unknown"} · ${age}`)); row.classList.add(`freshness-${item.state || "unknown"}`); freshness.append(row);
  }
  const windowMinutes = Number.isFinite(value.window?.minutes) ? value.window.minutes : null;
  const p = value.pace || {}, f = value.forecast || {};
  const duration = windowMinutes === null ? "selected window" : `selected ${number.format(windowMinutes)} minute window`;
  const tokenPace = p.tokens_per_minute == null || p.tokens_per_minute === "" ? null : Number(p.tokens_per_minute);
  pace.append(confidenceRow("Observed records", Number.isFinite(p.records) ? number.format(p.records) : "Unknown"));
  pace.append(confidenceRow("Observed tokens", Number.isFinite(p.tokens) ? compact.format(p.tokens) : "Unknown"));
  pace.append(confidenceRow("Token pace", Number.isFinite(tokenPace) ? `${number.format(tokenPace)} tokens/min` : "Unknown"));
  pace.append(confidenceRow("Estimated cost rate", p.estimated_cost_per_hour == null ? "Unknown" : `${cash(p.estimated_cost_per_hour)} / hour`));
  pace.append(confidenceRow("Priced / unknown / unconverted", `${p.estimated_priced_records ?? "?"} / ${p.unknown_cost_records ?? "?"} / ${p.unconverted_cost_records ?? "?"}`));
  const forecastValue = f.tokens == null ? "Unknown" : compact.format(f.tokens);
  const forecastCost = f.estimated_cost == null ? "Unknown" : cash(f.estimated_cost);
  const sampleCount = Number.isFinite(p.records) ? `${number.format(p.records)} usage records` : "Unknown sample count";
  const observedSpan = Number.isFinite(f.observed_span_seconds) && f.observed_span_seconds >= 0
    ? `${Math.floor(f.observed_span_seconds / 60)}m ${Math.round(f.observed_span_seconds % 60)}s observed span` : "observed span unavailable";
  const confidence = ["insufficient", "low", "limited"].includes(f.confidence) ? `confidence ${f.confidence} (heuristic)` : "";
  pace.append(node("p", `${f.state === "available" ? "Linear selected-window projection" : f.state === "partial" ? "Linear selected-window projection · partial priced coverage" : "Linear selected-window projection unavailable"} for the next ${f.horizon_minutes ?? 60} minutes: ${forecastValue} tokens · estimated cost ${forecastCost}. ${[sampleCount, observedSpan, confidence].filter(Boolean).join(" · ")}. ${f.assumption || ""} This is not predicted provider exhaustion or billed spend.`, "confidence-note"));
  pace.append(node("p", `Rate from ${duration}. Estimate projection coverage: ${p.estimated_priced_records ?? 0} priced records; ${p.unknown_cost_records ?? 0} unknown cost and ${p.unconverted_cost_records ?? 0} unconverted cost records. ${p.billed_records ?? 0} billed records are excluded from the estimate projection.`, "confidence-note"));

  const q = value.quotas || {}, windows = Array.isArray(q.windows) ? q.windows : [];
  const readStatus = q.read_status || {};
  const status = q.state === "ready" ? "Supplied quota observations configured" : q.state === "unavailable" ? "Quota observations unavailable" : "Quota observations unconfigured";
  const readLabel = readStatus.state === "unavailable" ? "Quota source unavailable" : readStatus.state === "following" ? "Quota source following" : readStatus.state === "reading" ? "Quota source reading" : readStatus.state === "static" ? "Quota source static" : readStatus.state === "unconfigured" ? "Quota source unconfigured" : "";
  const readDetail = [readLabel, readStatus.last_success_at ? `last successful read ${readStatus.last_success_at.replace("T", " ").replace("Z", " UTC")}` : "", readStatus.error || ""].filter(Boolean).join(" · ");
  quotas.append(node("p", `${status}. ${[q.note || "Quota observations are independent of usage filters and token costs.", readDetail].filter(Boolean).join(" ")}`, "prose"));
  if (!windows.length) quotas.append(node("p", "No provider quota windows are available. Unknown quota stays unknown.", "empty"));
  for (const item of windows) {
    const freshnessState = item.freshness || "unknown";
    const row = node("div", null, "quota-row");
    const head = node("div", null, "quota-head");
    const detail = [item.provider, item.account, item.label].filter(Boolean).join(" · ");
    head.append(node("strong", detail || item.source || "Provider window"), node("span", freshnessState));
    row.append(head);
    row.append(node("p", `${item.used_percent == null ? "Used: unknown" : `Used: ${number.format(item.used_percent)}%`} · ${item.remaining_percent == null ? "Remaining: unknown" : `Remaining: ${number.format(item.remaining_percent)}%`}${Number.isFinite(item.window_minutes) ? ` · ${number.format(item.window_minutes)} min window` : ""}`));
    row.append(node("p", `${item.source || "Source unknown"}${item.captured_at ? ` · captured ${item.captured_at.replace("T", " ").replace("Z", " UTC")}` : " · capture time unknown"}${item.resets_at ? ` · resets ${item.resets_at.replace("T", " ").replace("Z", " UTC")}` : " · reset time unknown"}${item.provenance ? ` · ${item.provenance}` : ""}`));
    if (freshnessState === "reset-passed") row.append(node("p", "Reset time has passed; replenishment has not been observed.", "quota-caveat"));
    else if (freshnessState !== "current") row.append(node("p", "This quota observation is not current.", "quota-caveat"));
    quotas.append(row);
  }
}

function inputTimestamp(value) {
  const timestamp = Date.parse(value || "");
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString().replace("T", " ").replace(".000Z", " UTC") : "Not recorded";
}

function inputCount(value) {
  return Number.isFinite(value) ? number.format(value) : "Unknown";
}

function renderInputHealth() {
  const root = $("#input-health"), status = $("#input-health-status"), inventory = data?.inputs;
  root.replaceChildren(); status.classList.remove("partial");
  if (inventory?.schema !== "monitoring.inputs.v1") {
    status.textContent = "Input inventory is unavailable in this snapshot. Explicit event history remains available.";
    root.append(node("p", "Connect an explicitly selected input to see read health here.", "empty"));
    return;
  }
  const inputs = Array.isArray(inventory.inputs) ? inventory.inputs : [];
  if (!inventory.configured) {
    status.textContent = `No explicit input manifest is configured. Snapshot observed ${inputTimestamp(data.observed_at)}.`;
    root.append(node("p", "No managed inputs to monitor.", "empty")); return;
  }
  const count = (reported, test) => Number.isFinite(reported) ? reported : inputs.filter(test).length;
  const healthy = count(inventory.healthy_inputs, (item) => item.state === "following");
  const unavailable = count(inventory.unavailable_inputs, (item) => item.state === "unavailable");
  const unread = count(inventory.unread_inputs, (item) => item.state === "unread");
  const partial = Boolean(inventory.degraded) || unavailable > 0 || unread > 0;
  status.textContent = partial
    ? `Partial input availability · ${inputCount(healthy)} following · ${inputCount(unavailable)} unavailable · ${inputCount(unread)} unread. Failed reads retain previously accepted event history. Snapshot observed ${inputTimestamp(data.observed_at)}.`
    : `${inputCount(healthy)} configured inputs following. Snapshot observed ${inputTimestamp(data.observed_at)}.`;
  status.classList.toggle("partial", partial);
  if (!inputs.length) { root.append(node("p", "No managed inputs are listed in the configured manifest.", "empty")); return; }
  for (const input of inputs) {
    const row = node("article", null, `input-health-row input-${input.state || "unknown"}`);
    const head = node("div", null, "input-health-head");
    const label = node("div", null, "input-health-label");
    label.append(node("strong", typeof input.id === "string" && input.id ? input.id : "Managed input"));
    const alias = [input.format, input.project].filter((value) => typeof value === "string" && value).join(" · ");
    if (alias) label.append(node("small", alias));
    const stateName = input.state === "following" ? "Following" : input.state === "unavailable" ? "Unavailable" : input.state === "unread" ? "Not yet read" : "Unknown";
    head.append(label, node("span", stateName, "input-state")); row.append(head);
    const dates = node("p", `Last attempt: ${inputTimestamp(input.last_attempt_at)} · Last success: ${inputTimestamp(input.last_success_at)}`, "input-health-dates"); row.append(dates);
    const counts = node("p", `${inputCount(input.records_seen)} records seen · ${inputCount(input.added)} added · ${inputCount(input.replayed)} replayed`, "input-health-counts"); row.append(counts);
    root.append(row);
  }
}

function bars(target, rows, split = false) {
  const root = $(target); root.replaceChildren();
  const sorted = [...rows].sort((a, b) => b.total_tokens - a.total_tokens).slice(0, 8);
  const max = Math.max(...sorted.map((row) => row.total_tokens), 1);
  if (!sorted.length) root.append(node("div", "No matching usage. Adjust the filters or import a source.", "empty"));
  for (const row of sorted) {
    const item = node("div", null, "bar-item"), label = node("div", null, "bar-label");
    label.append(node("span", row.key), node("span", `${compact.format(row.total_tokens)} tokens`));
    const track = node("div", null, "bar-track");
    track.setAttribute("role", "img"); track.setAttribute("aria-label", `${row.key}: ${row.total_tokens} tokens`);
    for (const [key, value] of Object.entries(split ? row.tokens : { input: row.total_tokens })) {
      const segment = node("i", null, "bucket-" + key);
      segment.style.width = `${100 * value / max}%`; track.append(segment);
    }
    item.append(label, track); root.append(item);
  }
}

function timeline() {
  const root = $("#timeline"); root.replaceChildren();
  const rows = data.entities.slice(0, 8);
  if (!rows.length) { root.append(node("p", "No matching sessions.", "empty")); return; }
  const start = Math.min(...rows.map((row) => Date.parse(row.started_at)));
  const end = Math.max(...rows.map((row) => Date.parse(row.last_event_at)), start + 1);
  const axis = node("div", null, "timeline-axis");
  for (const tick of [start, (start + end) / 2, end]) axis.append(node("span", new Date(tick).toISOString().slice(11, 16)));
  root.append(axis);
  for (const row of rows) {
    const line = node("div", null, "timeline-row");
    const name = node("button", (row.parent ? "↳ " : "") + row.entity, "timeline-name");
    name.append(node("span", row.entity_type, "entity-type"));
    name.addEventListener("click", () => inspect(row));
    const track = node("div", null, "timeline-track"), span = node("span", null, "timeline-span " + row.entity_type);
    span.style.left = `${100 * (Date.parse(row.started_at) - start) / (end - start)}%`;
    span.style.width = `${100 * (Date.parse(row.last_event_at) - Date.parse(row.started_at)) / (end - start)}%`;
    track.title = `${row.started_at} to ${row.last_event_at}`;
    track.append(span); line.append(name, track, state(row)); root.append(line);
  }
}

function state(row) {
  const status = node("span", row.state, "state " + row.state);
  status.title = `Reported: ${row.reported_state}. Evidence: ${row.status_at || "not supplied"}. Stale: ${row.stale}.`;
  return status;
}

function table(target, headers, rows) {
  const root = $(target); root.replaceChildren();
  if (!rows.length) { root.append(node("p", "No matching records. Adjust the filters or import a source.", "empty")); return; }
  const grid = node("table"), head = node("thead"), heading = node("tr");
  for (const text of headers) heading.append(node("th", text));
  head.append(heading); grid.append(head);
  const body = node("tbody");
  for (const values of rows) {
    const row = node("tr");
    for (const value of values) {
      const cell = node("td");
      if (value instanceof Node) cell.append(value); else cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
  grid.append(body); root.append(grid);
}

function inspect(row) {
  entity = row;
  $("#event-selection").textContent = `${row.entity_type}: ${row.entity} · source ${row.source} · session ${row.session}. The latest matching 100 events may not include its entire history.`;
  $("#clear-entity").hidden = false;
  showView("events"); renderEvents();
}

function renderEvents() {
  const rows = data.events.filter((row) => !entity || (row.entity === entity.entity && row.source === entity.source && row.session === entity.session));
  table("#event-table", ["Time · UTC", "Entity / source", "Kind", "Details", "Cost / basis"], rows.map((row) => {
    const id = node("div", row.entity); id.append(node("small", `${row.source} · ${row.surface}`));
    const detail = row.kind === "status" ? row.state : row.kind === "tool" ? `${row.tool} · ${row.outcome} · ${row.duration_ms ?? "?"} ms` : `${number.format(Object.values(row.tokens).reduce((a,b) => a+b, 0))} tokens · ${row.model}`;
    const cost = row.resolved_cost;
    return [row.timestamp.replace("T", " ").replace("Z", ""), id, row.kind, detail, cost ? `${cost.amount} ${cost.currency} · ${cost.basis}` : row.kind === "usage" ? "Unknown" : "—"];
  }));
}

function renderUsage() {
  const group = $("#group").value;
  table("#usage-table", [group, "Usage records", "Input", "Cache read", "Cache write", "Output", `Estimated · ${data.currency}`, `Billed · ${data.currency}`, "Unknown / no FX"], data.groups[group].map((row) => [
    row.key, number.format(row.calls), ...Object.values(row.tokens).map((v) => number.format(v)),
    cash(row.costs.estimated.amount), cash(row.costs.billed.amount), `${row.unknown_cost_events} / ${row.unconverted_cost_events}`,
  ]));
}

function retainInsights(reason) {
  const value = data?.insights;
  if (!value) return;
  const now = Date.now();
  for (const source of value.source_freshness || []) {
    const at = Date.parse(source.last_event_at);
    if (!Number.isFinite(at)) continue;
    const age = (now - at) / 1000;
    source.age_seconds = Math.trunc(age);
    source.state = age < 0 ? "future" : age <= 60 ? "recent" : "stale";
  }
  const quotas = value.quotas;
  if (!quotas) return;
  quotas.read_status = { ...quotas.read_status, state: "unavailable", error: reason };
  for (const window of quotas.windows || []) {
    const at = Date.parse(window.captured_at);
    if (!Number.isFinite(at)) continue;
    const age = (now - at) / 1000;
    window.age_seconds = Math.trunc(age);
    window.freshness = window.resets_at && Date.parse(window.resets_at) <= now ? "reset-passed"
      : age < 0 ? "future" : age <= 300 ? "current" : "stale";
  }
}

function render() {
  fillFacets();
  const summary = data.summary, coverage = data.coverage;
  const synthetic = data.facets.source.includes("synthetic");
  $("#scope").replaceChildren(node("span", `${number.format(coverage.matched_events)} of ${number.format(coverage.event_count)} events · explicitly supplied sources · refreshes every 2s${data.filters.state ? ` · state: ${data.filters.state}` : ""}`), node("strong", synthetic ? "SYNTHETIC DATA PRESENT · FICTIONAL COSTS" : "METADATA ONLY · COVERAGE IS LIMITED"));
  $("#metrics").replaceChildren(
    metric("Observed sessions", number.format(summary.sessions), `${summary.active} running entities · ${summary.attention} waiting or blocked`, "⌘"),
    metric("Token volume", compact.format(summary.total_tokens), `${summary.calls} usage records · cache included once`, "◷"),
    metric("Estimated cost", cash(summary.costs.estimated.amount), `${summary.costs.estimated.events} records priced · not a subscription balance`, "≈"),
    metric("Billed cost", cash(summary.costs.billed.amount), `${summary.costs.billed.events} records with declared billing evidence`, "$"),
  );
  bars("#model-bars", data.groups.model, true); bars("#project-bars", data.groups.project);
  bars("#daily", data.groups.date, true); timeline();
  $("#token-legend").replaceChildren(...Object.entries(bucketNames).map(([key, text]) => {
    const item = node("span"); item.append(node("i", null, "bucket-" + key), document.createTextNode(text)); return item;
  }));
  const confidence = $("#confidence"); confidence.replaceChildren();
  for (const [label, value] of [["Estimated records", summary.costs.estimated.events], ["Billed records", summary.costs.billed.events], ["Unpriced records", summary.unknown_cost_events], ["Missing exchange rate", summary.unconverted_cost_events]]) {
    const row = node("div", null, "confidence-row"); row.append(node("span", label), node("strong", number.format(value))); confidence.append(row);
  }
  confidence.append(node("p", "Known amounts cover only their labeled records. Estimates and billed amounts are never added for the same event. Lifecycle evidence expires after 60 seconds.", "confidence-note"));
  renderInsights();
  renderInputHealth();
  table("#entity-table", ["Entity / hierarchy", "State", "Model / surface", "Tokens", "Estimated", "Billed", "Last evidence · UTC"], data.entities.map((row) => {
    const name = node("button", (row.parent ? "↳ " : "") + row.entity, "entity-button");
    name.addEventListener("click", () => inspect(row));
    const title = node("div"); title.append(name, node("small", `${row.entity_type} · ${row.project}${row.parent ? ` · parent ${row.parent}${row.parent_observed ? "" : " (unobserved)"}` : ""}`));
    const model = node("div", row.model); model.append(node("small", row.surface));
    return [title, state(row), model, number.format(row.metrics.total_tokens), cash(row.metrics.costs.estimated.amount), cash(row.metrics.costs.billed.amount), row.status_at ? row.status_at.slice(11, 19) + (row.stale ? " · stale" : "") : "Not supplied"];
  }));
  renderUsage(); renderEvents();
  const info = $("#currency-info"); info.replaceChildren();
  info.append(node("p", `Display currency: ${data.currency}. ${data.fx ? `USD-based conversion as of ${data.fx.as_of}, provenance ${data.fx.provenance}. This is a display conversion, not an invoice exchange rate.` : "No conversion rates supplied. Amounts in other currencies stay unconverted."}`));
  for (const source of summary.cost_provenance) info.append(node("p", `${source.basis} · ${source.currency} · ${source.provenance}`));
  if (!summary.cost_provenance.length) info.append(node("p", "No cost evidence or matching dated model prices for these usage records."));
  $("#footnote").textContent = `Read only · ${summary.tools} tool records · ${summary.tool_errors} tool errors · revision ${data.revision}${coverage.entities_truncated ? " · entity list limited to 500" : ""}`;
}

async function refresh() {
  if (paused || busy) return;
  busy = true;
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(`/api/v1/snapshot?${query()}`, { signal: controller.signal, cache: "no-store" });
    if (!response.ok) throw new Error(`Read failed (${response.status}). Check the filters and ledger limits.`);
    const value = await response.json();
    if (value.schema !== "monitoring.snapshot.v1") throw new Error("The monitoring owner returned an incompatible schema.");
    data = value; lastSuccess = Date.now();
    $("#notice").hidden = data.source_status.state !== "unavailable";
    $("#notice").textContent = data.source_status.error || "";
    $("#connection").textContent = data.source_status.state === "following" ? "Following source" : data.source_status.state === "unavailable" ? "Source unavailable" : "History connected";
    $("#connection").classList.toggle("offline", data.source_status.state === "unavailable");
    $("#updated").textContent = `Updated ${data.observed_at.slice(11, 19)} UTC`;
    render();
  } catch (error) {
    $("#notice").hidden = false;
    $("#notice").textContent = `${error.message} ${data ? "Last observations remain visible and may be stale." : "No data loaded."}`;
    $("#connection").textContent = "Disconnected"; $("#connection").classList.add("offline");
    if (data) {
      // A failed browser read must not leave a running badge looking current
      for (const row of data.entities) if (!["completed", "failed", "cancelled"].includes(row.state)) { row.state = "unknown"; row.stale = true; }
      retainInsights("Monitoring owner unavailable; showing retained dated quota observations.");
      data.summary.active = 0; data.summary.attention = 0; render();
    }
  } finally { clearTimeout(timer); busy = false; }
}

$$("[data-view]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
$("#filters").addEventListener("change", (event) => {
  if (event.target?.name === "since" || event.target?.name === "until") $("#report-period").value = "custom";
  refresh(); if (view === "report") loadReport();
});
$("#filters").addEventListener("submit", (event) => event.preventDefault());
$("#filters").addEventListener("reset", () => { $("#state-filter").value = ""; setTimeout(() => { $("#report-period").value = "custom"; refresh(); if (view === "report") loadReport(); }, 0); });
$("#report-period").addEventListener("change", () => applyReportPeriod($("#report-period").value));
$("#state-filter").addEventListener("change", () => { refresh(); if (view === "report") loadReport(); });
$("#group").addEventListener("change", () => { if (data) renderUsage(); });
$("#pause").addEventListener("click", () => {
  paused = !paused; $("#pause").textContent = paused ? "Resume refresh" : "Pause refresh";
  if (paused) {
    $("#connection").textContent = "Refresh paused"; $("#connection").classList.add("offline");
    if (view === "report") cancelReport();
    if (data) {
      for (const row of data.entities) if (!["completed", "failed", "cancelled"].includes(row.state)) { row.state = "unknown"; row.stale = true; }
      data.summary.active = 0; data.summary.attention = 0;
    }
    retainInsights("Dashboard refresh paused; showing retained dated quota observations.");
    if (view === "report") {
      const last = reportData ? ` Last successful report observed at ${reportData.observed_at || "unknown time"} remains visible and may not match current filters.` : " No report observations have loaded yet.";
      $("#report-status").textContent = `Report refresh is paused.${last}`;
    }
    if (data) render();
  }
  else { refresh(); if (view === "report") loadReport(); }
});
$("#clear-entity").addEventListener("click", () => { entity = null; $("#event-selection").textContent = ""; $("#clear-entity").hidden = true; renderEvents(); });
document.addEventListener("keydown", (event) => {
  if (!event.ctrlKey && !event.metaKey && !event.altKey && !["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName) && ["1", "2", "3", "4", "5"].includes(event.key)) showView(Object.keys(titles)[Number(event.key) - 1]);
});
window.addEventListener("hashchange", () => showView(location.hash.slice(1) || "overview"));
showView(location.hash.slice(1) || "overview");
refresh();
setInterval(() => {
  if (paused && data) {
    retainInsights("Dashboard refresh paused; showing retained dated quota observations.");
    renderInsights();
  } else { refresh(); if (view === "report") loadReport(false); }
}, 2000);

"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const number = new Intl.NumberFormat("en-US");
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const bucketNames = { input: "Input", cache_read: "Cache read", cache_write: "Cache write", output: "Output" };
const insightUnavailable = "Usage analysis is unavailable in this snapshot.";
const titles = { overview: ["Overview", "Status evidence and usage from the sources selected for this ledger."],
  sessions: ["Sessions, agents and workers", "Recorded entities and parent relationships. Status uses the latest supplied lifecycle event."],
  usage: ["Usage and costs", "Group token usage and costs by model, provider, surface, project or date."],
  events: ["Events", "Latest 100 matching usage, status and tool records."],
  report: ["Usage report", "Usage and cost coverage for the selected UTC dates and filters."],
  settings: ["Settings", "Appearance preferences and setup guidance for explicitly selected sources."] };
let data, paused = false, busy = false, view = "overview", entity = null;
let settingsCategory = "all";
let entityHistory = null;
let collapsedHierarchyKeys = new Set();
let lastSuccess = 0;
let snapshotReadUnavailable = false;
let lastSnapshotQuery = null, ledgerChanges = [], ledgerChangeNote = "Waiting for the first snapshot.";
let reportController = null, reportRequestId = 0, reportData = null, reportDataQuery = null;
let comparison = null, comparisonController = null, comparisonId = 0, comparisonScope = null, comparisonChoices = null;
let comparisonMessage = "Choose two distinct sessions.";
let sessionList = null, sessionListController = null, sessionListId = 0;
let cachedRange = "all", cachedExactDate = "", cachedView = "daily";
let cachedDimensionFilters = { providers: "", models: "" };
let cachedUsageReceivedAt = 0, cachedFileAgeAtReceipt = null;
let cachedUsageIdentity = null, cachedUsageSampledAt = null, cachedUsageFileModifiedAt = null;
let activityReceivedAt = 0, activitySourceAgeAtReceipt = null;

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

function parseRoute(route) {
  const [requested, category] = String(route || "overview").replace(/^#/, "").split("/");
  const next = titles[requested] ? requested : "overview";
  const selectedCategory = next === "settings" && ["appearance", "sources", "thresholds"].includes(category) ? category : "all";
  return { next, category: selectedCategory, hash: next === "settings" && selectedCategory !== "all" ? `settings/${selectedCategory}` : next };
}

function showView(route) {
  const target = parseRoute(route), next = target.next;
  if (view === next && settingsCategory === target.category && location.hash.slice(1) === target.hash) return;
  if (view === "report" && next !== "report") { cancelReport(); cancelComparison(); sessionListController?.abort(); }
  if (view === "events" && next !== "events" && entityHistory?.pending) cancelEntityHistory("Entity history read canceled because you left the Event stream. Loaded pages are retained.");
  view = next;
  settingsCategory = target.category;
  $("#metrics").hidden = next === "report" || next === "settings";
  $("#filters").hidden = next === "settings";
  $("#scope").hidden = next === "settings";
  $$(".view").forEach((section) => { section.hidden = section.id !== next; });
  const settings = $("#settings");
  settings.dataset.category = target.category;
  $$("#settings [data-settings-category]").forEach((card) => { card.hidden = target.category !== "all" && card.dataset.settingsCategory !== target.category; });
  document.body.classList.toggle("has-section-navigation", next === "settings");
  $("#section-navigation").hidden = next !== "settings";
  $$("#primary-navigation [data-view]").forEach((button) => {
    const active = button.dataset.view === next;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $$("#section-navigation [data-settings-route]").forEach((link) => {
    const active = link.dataset.settingsRoute === target.category;
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  const baseBreadcrumb = { overview: "Overview", sessions: "Sessions & workers", usage: "Usage & costs", events: "Event stream", report: "Report", settings: "Settings" }[next];
  const categoryLabel = { appearance: "Appearance", sources: "Sources and connections", thresholds: "Thresholds" }[target.category];
  $("#breadcrumb").textContent = categoryLabel ? `${baseBreadcrumb} / ${categoryLabel}` : baseBreadcrumb;
  $("#title").textContent = target.category === "appearance" ? "Appearance" : target.category === "sources" ? "Sources and connections" : target.category === "thresholds" ? "Thresholds" : titles[next][0];
  $("#subtitle").textContent = target.category === "appearance" ? "Choose a preset or adjust the display theme for this browser." : target.category === "sources" ? "Setup guidance for explicitly selected, authorized inputs." : target.category === "thresholds" ? "The threshold is separate from provider quota and billing." : titles[next][1];
  if (location.hash.slice(1) !== target.hash) location.hash = target.hash;
  window.dispatchEvent(new CustomEvent("monitoring:navigate", { detail: { view: next } }));
  window.monitoringNavigation?.reveal(next);
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

function comparisonQuery() {
  const params = query();
  for (const key of ["source", "session", "state"]) params.delete(key);
  return params.toString();
}

function cancelComparison() {
  comparisonId += 1;
  if (comparisonController) {
    comparisonController.abort();
    comparisonMessage = "Comparison canceled. Choose Compare sessions to read again.";
  }
  comparisonController = null;
}

function renderSessionComparison() {
  const scope = JSON.stringify([data?.identity, comparisonQuery()]);
  if (comparisonScope !== scope) {
    cancelComparison(); comparison = null; comparisonScope = scope;
    sessionListId += 1; sessionListController?.abort(); sessionListController = null; sessionList = null;
    $("#session-list-status").textContent = "Find session aliases from accepted ledger records under the shared comparison filters.";
    comparisonMessage = "Choose two distinct sessions. Shared filters apply to both.";
  }
  const choices = [...new Set([...(data?.entities || []), ...(sessionList?.sessions || [])].map((row) => JSON.stringify([row.source, row.session])))].sort();
  const signature = JSON.stringify(choices);
  if (comparisonChoices !== signature) {
    comparisonChoices = signature;
    for (const selector of ["#compare-first", "#compare-second"]) {
      const select = $(selector), prior = select.value;
      select.replaceChildren(node("option", "Choose a session")); select.options[0].value = "";
      for (const key of choices) {
        const [source, session] = JSON.parse(key), option = node("option", `${source} / ${session}`);
        option.value = key; select.append(option);
      }
      select.value = choices.includes(prior) ? prior : "";
      if (prior && !select.value) {
        cancelComparison(); comparison = null;
        comparisonMessage = "A selected session is outside the current snapshot. Choose another pair.";
      }
    }
  }
  const first = $("#compare-first").value, second = $("#compare-second").value;
  const more = $("#session-list-more");
  more.hidden = !sessionList || sessionList.next_offset === null || sessionList.sessions.length >= 2000;
  more.disabled = paused || snapshotReadUnavailable || !!sessionListController;
  $("#session-list-form button[type=submit]").disabled = paused || snapshotReadUnavailable || !!sessionListController;
  $("#compare-sessions").disabled = !first || !second || first === second || paused || snapshotReadUnavailable || !!comparisonController;
  const status = $("#session-compare-status"), target = $("#session-compare-table");
  status.textContent = comparisonMessage;
  if (!comparison) { target.replaceChildren(); return; }
  const { reports, selections } = comparison;
  const [left, right] = reports, names = selections.map(([source, session]) => `${source} / ${session}`);
  status.textContent += ` Revision ${left.revision}. Observed ${left.observed_at} and ${right.observed_at}.`;
  if (data?.revision !== left.revision) status.textContent += " The current ledger revision differs; compare again for a current pair.";
  if (paused || snapshotReadUnavailable) status.textContent += " Refresh is paused or unavailable; these are retained observations.";
  const metrics = reports.map((report) => report.summary);
  const rows = [];
  const countRow = (label, values) => rows.push([label, ...values.map((value) => value == null ? "Unknown" : number.format(value)),
    values.every((value) => Number.isSafeInteger(value)) ? number.format(values[1] - values[0]) : "Unavailable"]);
  countRow("Usage records", metrics.map((value) => value.calls));
  countRow("Recorded tokens", metrics.map((value) => value.total_tokens));
  for (const bucket of ["input", "cache_read", "cache_write", "output"]) countRow(bucketNames[bucket], metrics.map((value) => value.tokens?.[bucket]));
  for (const basis of ["estimated", "billed"]) rows.push([basis === "estimated" ? "Known estimated cost" : "Reported billed cost",
    ...metrics.map((value) => `${reportCurrency(value.costs?.[basis]?.amount ?? null, left.currency)} · ${value.costs?.[basis]?.events ?? "unknown"} covered records`), "Coverage may differ"]);
  countRow("Unknown cost records", metrics.map((value) => value.unknown_cost_events));
  countRow("Unconverted cost records", metrics.map((value) => value.unconverted_cost_events));
  countRow("Tool records", reports.map((value) => value.tools?.records));
  countRow("Tool errors", reports.map((value) => value.tools?.errors));
  countRow("Unknown tool outcomes", reports.map((value) => value.tools?.unknown_outcomes));
  countRow("Receipt-time usage summaries", reports.map((value) => value.coverage?.receipt_time_summary_records));
  table("#session-compare-table", ["Measure", names[0], names[1], "Second minus first"], rows);
}

async function loadSessionChoices(after = 0) {
  if (paused || snapshotReadUnavailable) return;
  sessionListController?.abort();
  const id = ++sessionListId, scope = comparisonScope, identity = data?.identity;
  const controller = new AbortController(); sessionListController = controller;
  if (!after) sessionList = null;
  const params = new URLSearchParams(comparisonQuery());
  const search = after ? sessionList.search : $("#session-list-search").value.trim();
  if (search) params.set("search", search);
  params.set("limit", "100"); params.set("after", String(after));
  if (after) params.set("expected_revision", String(sessionList.revision));
  $("#session-list-status").textContent = "Reading recorded session aliases.";
  renderSessionComparison();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(`/api/v1/sessions?${params}`, { signal: controller.signal, cache: "no-store" });
    if (response.status === 409) throw new Error("The ledger changed. Choose Find sessions to restart the list.");
    if (!response.ok) throw new Error(`Session list unavailable (${response.status}). Older owners may not support this route.`);
    const value = await response.json();
    const alias = (text) => typeof text === "string" && /^[A-Za-z0-9_.:@-]{1,160}$/.test(text);
    if (value.schema !== "monitoring.sessions.v1" || value.identity !== identity || !Number.isSafeInteger(value.revision) ||
      !Number.isSafeInteger(value.total) || !Array.isArray(value.sessions) || value.sessions.length > 100 ||
      !value.sessions.every((row) => alias(row.source) && alias(row.session)) ||
      !(value.next_offset === null || (Number.isSafeInteger(value.next_offset) && value.next_offset > after)) ||
      (after && value.revision !== sessionList.revision)) throw new Error("Session list did not match this owner or page.");
    if (id !== sessionListId || scope !== comparisonScope) return;
    sessionList = { ...value, sessions: [...(after ? sessionList.sessions : []), ...value.sessions] };
    $("#session-list-status").textContent = `${sessionList.sessions.length} of ${value.total} recorded session choices loaded at revision ${value.revision}. ${sessionList.sessions.length >= 2000 ? "Choice limit reached; narrow the search or filters." : "Choose a pair below. These are recorded identities, not current workers."}`;
  } catch (error) {
    if (id === sessionListId) {
      if (sessionList) sessionList.next_offset = null;
      $("#session-list-status").textContent = error.name === "AbortError" ? "Session list read canceled or timed out. Choose Find sessions to retry." : error.message;
    }
  } finally {
    clearTimeout(timer);
    if (id === sessionListId) { sessionListController = null; renderSessionComparison(); }
  }
}

async function compareSessions() {
  const values = [$("#compare-first").value, $("#compare-second").value];
  if (paused || snapshotReadUnavailable || values.some((value) => !value) || values[0] === values[1]) return;
  cancelComparison(); comparison = null;
  const requestId = comparisonId, controller = new AbortController();
  comparisonController = controller;
  const scope = comparisonScope, base = comparisonQuery(), identity = data?.identity;
  const selections = values.map((value) => JSON.parse(value));
  comparisonMessage = "Reading both recorded sessions."; renderSessionComparison();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const reports = await Promise.all(selections.map(async ([source, session]) => {
      const params = new URLSearchParams(base); params.set("source", source); params.set("session", session);
      const response = await fetch(`/api/v1/report?${params}`, { signal: controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error(`Session report unavailable (${response.status}).`);
      const report = await response.json();
      if (report.schema !== "monitoring.report.v1" || report.identity !== identity ||
        report.filters?.source !== source || report.filters?.session !== session || !Number.isSafeInteger(report.revision))
        throw new Error("Session report did not match the selected owner or session.");
      return report;
    }));
    if (requestId !== comparisonId || scope !== comparisonScope || base !== comparisonQuery()) return;
    if (reports[0].revision !== reports[1].revision || reports[0].currency !== reports[1].currency)
      throw new Error("The ledger changed between session reads. Compare again; no mixed revisions are displayed.");
    comparison = { reports, selections };
    comparisonMessage = "Recorded session comparison. Count differences describe supplied records only; cost coverage stays separate.";
  } catch (error) {
    if (requestId === comparisonId) comparisonMessage = error.name === "AbortError" ? "Session comparison timed out or was canceled. Compare again to retry." : error.message;
  } finally {
    clearTimeout(timer);
    if (requestId === comparisonId) { comparisonController = null; renderSessionComparison(); }
  }
}

function updateReportDownload() {
  // A retained report can be saved only under the filters that produced it.
  const disabled = !reportData || reportDataQuery !== query().toString();
  $("#download-report").disabled = disabled;
  $("#download-report-csv").disabled = disabled;
}

function saveDisplayedReport(content, extension, type) {
  updateReportDownload();
  if ($("#download-report").disabled) return;
  const stamp = String(reportData.observed_at || "").replace(/[^0-9TZ.-]/g, "").slice(0, 32) || "unknown-time";
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `monitoring-report-${stamp}.${extension}`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadDisplayedReport() {
  updateReportDownload();
  if ($("#download-report").disabled) return;
  saveDisplayedReport(`${JSON.stringify(reportData, null, 2)}\n`, "json", "application/json");
}

function csvCell(value) {
  let text = value === null || value === undefined ? "" : String(value);
  // Spreadsheet applications may evaluate a leading identifier as a formula.
  if (/^[\s]*[=+@-]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

function reportCsv(report) {
  const headers = ["section", "key", "observed_at_utc", "ledger_revision", "period_from_utc", "period_until_utc",
    "filters_json", "currency", "calls", "observed_sessions", "input_tokens", "cache_read_tokens", "cache_write_tokens",
    "output_tokens", "estimated_amount", "estimated_cost_events", "billed_amount", "billed_cost_events",
    "unknown_cost_events", "unconverted_cost_events", "section_truncated", "coverage_scope",
    "ledger_events", "future_events_excluded", "receipt_time_summary_records"];
  const rows = [headers];
  const add = (section, key, metrics, sessions, truncated) => {
    const costs = metrics?.costs || {}, tokens = metrics?.tokens || {};
    rows.push([section, key, report.observed_at, report.revision, report.range?.from, report.range?.until,
      JSON.stringify(report.filters || {}), report.currency, metrics?.calls, sessions,
      tokens.input, tokens.cache_read, tokens.cache_write, tokens.output,
      costs.estimated?.amount, costs.estimated?.events, costs.billed?.amount, costs.billed?.events,
      metrics?.unknown_cost_events, metrics?.unconverted_cost_events, truncated,
      report.coverage?.scope, report.coverage?.ledger_events, report.coverage?.future_events_excluded,
      report.coverage?.receipt_time_summary_records]);
  };
  add("summary", "all", report.summary, report.summary?.observed_sessions, false);
  for (const section of ["projects", "models", "daily"]) {
    const group = report.breakdowns?.[section];
    for (const row of group?.rows || []) add(section, row.key, row.metrics, row.observed_sessions, Boolean(group.truncated));
  }
  return `\uFEFF${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}\r\n`;
}

function downloadDisplayedReportCsv() {
  updateReportDownload();
  if ($("#download-report-csv").disabled) return;
  saveDisplayedReport(reportCsv(reportData), "csv", "text/csv;charset=utf-8");
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

function renderToolTimeline(daily) {
  const root = $("#report-tool-timeline"); root.replaceChildren();
  if (!daily || !Array.isArray(daily.rows)) {
    root.append(node("p", "Daily tool activity is unavailable from this owner.", "empty"));
    return;
  }
  const kinds = ["shell", "mcp", "core", "skill", "agent", "other", "unclassified"];
  const validCount = (value) => Number.isSafeInteger(value) && value >= 0;
  const rows = daily.rows.filter((row) => row && /^\d{4}-\d{2}-\d{2}$/.test(row.date)
    && validCount(row.records) && row.records > 0 && validCount(row.errors)
    && validCount(row.unknown_outcomes) && row.errors + row.unknown_outcomes <= row.records
    && Array.isArray(row.categories) && row.categories.every((part) => part && kinds.includes(part.key)
      && validCount(part.records) && part.records > 0)
    && row.categories.reduce((sum, part) => sum + part.records, 0) === row.records).slice(-100);
  if (!rows.length) {
    root.append(node("p", "No recorded tool items match this period and filter.", "empty"));
    return;
  }
  const present = kinds.filter((kind) => rows.some((row) => row.categories.some((part) => part.key === kind)));
  const legend = node("div", null, "tool-timeline-legend");
  for (const kind of present) {
    const label = node("span", kind, "tool-timeline-legend-item");
    label.prepend(node("span", null, `tool-timeline-swatch tool-kind-${kind}`));
    legend.append(label);
  }
  root.append(legend);
  const maximum = Math.max(...rows.map((row) => row.records));
  const chart = node("div", null, "tool-timeline-chart");
  for (const row of rows) {
    const line = node("div", null, "tool-timeline-row");
    line.append(node("time", row.date));
    const track = node("div", null, "tool-timeline-track");
    const bar = node("div", null, "tool-timeline-bar");
    bar.style.width = `${(row.records / maximum) * 100}%`;
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", `${row.date}: ${row.records} tool records; ${row.errors} errors; ${row.unknown_outcomes} outcomes unknown; ${row.categories.map((part) => `${part.records} ${part.key}`).join(", ")}`);
    for (const part of row.categories) {
      const segment = node("span", null, `tool-timeline-segment tool-kind-${part.key}`);
      segment.style.width = `${(part.records / row.records) * 100}%`;
      segment.setAttribute("aria-hidden", "true");
      bar.append(segment);
    }
    track.append(bar); line.append(track);
    line.append(node("span", `${number.format(row.records)} · ${number.format(row.errors)} errors · ${number.format(row.unknown_outcomes)} unknown`, "tool-timeline-count"));
    chart.append(line);
  }
  root.append(chart);
  if (daily.truncated) root.append(node("p", "Showing the latest 100 recorded UTC dates; earlier tool dates are omitted from this chart. Report totals retain them.", "confidence-note"));
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
  const receiptSummaryRecords = report.coverage?.receipt_time_summary_records;
  const receiptSummaryNote = Number.isSafeInteger(receiptSummaryRecords) && receiptSummaryRecords > 0
    ? ` ${number.format(receiptSummaryRecords)} cumulative Claude CLI result summaries use their capture receipt date, not call time. Period groups can include earlier work. Summary totals retain these records.` : "";
  $("#report-range").textContent = `Current range: ${range.from || "start unknown"} through ${range.until || "end unknown"} UTC · target currency ${currency} · observed ${report.observed_at || "time unknown"}. ${report.previous_range ? `Prior range: ${report.previous_range.from} through ${report.previous_range.until} UTC.` : "No prior range is defined."}${receiptSummaryNote}`;
  const estimated = metrics.costs?.estimated?.amount ?? metrics.estimated_cost ?? null;
  const estimatedRecords = metrics.costs?.estimated?.events ?? summary.estimated_records;
  $("#report-metrics").replaceChildren(
    metric("Usage records", reportCalls(metrics), "Recorded usage events. Does not count calls from unconnected sources.", "≋"),
    metric("Observed tokens", metrics.total_tokens == null ? reportTokens(metrics) : compact.format(metrics.total_tokens), "Input, cache and output tokens counted once", "◷"),
    metric("Observed sessions", reportNumber(summary.observed_sessions ?? metrics.observed_sessions ?? summary.sessions), "Recorded session identities. Source coverage may be limited.", "⌘"),
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
  renderToolTimeline(tools.daily);
  for (const [label, key] of [["Tool records", "records"], ["Errors", "errors"], ["Outcome unknown", "unknown_outcomes"], ["Unclassified", "unclassified_records"]]) toolSummary.append(confidenceRow(label, reportNumber(tools[key])));
  const groups = $("#report-tool-groups"); groups.replaceChildren();
  const toolLabels = { categories: "Categories", names: "Tool names", mcp_servers: "MCP server aliases", shell_commands: "Shell tool / command aliases", skills: "Skill invocation aliases", agents: "Agent invocation aliases" };
  let foundGroup = false;
  for (const [key, label] of Object.entries(toolLabels)) {
    const values = tools[key]; if (!Array.isArray(values) || !values.length) continue;
    foundGroup = true; groups.append(node("h3", label));
    const wrap = node("div", null, "table-wrap"); groups.append(wrap);
    const headers = ["Name", "Category", "Records", "Errors", "Outcome unknown", "Duration records", "Total duration", "P50", "P95"];
    const rows = values.map((row) => [row.key ?? "Unknown", row.category ?? "Unknown", reportNumber(row.records), reportNumber(row.errors), reportNumber(row.unknown_outcomes), reportNumber(row.duration_records), row.total_duration_ms == null ? "Unknown" : `${reportNumber(row.total_duration_ms)} ms`, row.p50_ms == null ? "Unknown" : `${reportNumber(row.p50_ms)} ms`, row.p95_ms == null ? "Unknown" : `${reportNumber(row.p95_ms)} ms`]);
    const grid = node("table"), head = node("thead"), heading = node("tr"); headers.forEach((text) => heading.append(node("th", text))); head.append(heading); grid.append(head);
    const body = node("tbody"); rows.forEach((values) => { const tr = node("tr"); values.forEach((value) => tr.append(node("td", value))); body.append(tr); }); grid.append(body); wrap.append(grid);
  }
  if (!foundGroup) groups.append(node("p", "No categorized tool activity was reported for this period.", "empty"));
  if (Object.values(tools.truncated || {}).some(Boolean)) groups.append(node("p", "One or more tool breakdowns are truncated by the report bound.", "confidence-note"));

  const coverage = report.coverage || {}, coverageRoot = $("#report-coverage"); coverageRoot.replaceChildren();
  if (Number.isSafeInteger(receiptSummaryRecords) && receiptSummaryRecords > 0) {
    coverageRoot.append(confidenceRow("Receipt-time summary records", `${number.format(receiptSummaryRecords)} cumulative Claude CLI result summaries use the declared capture receipt date, not call time. Period groups can include earlier work. Report totals retain these records.`));
  }
  const fx = report.fx;
  const fxSummary = fx
    ? `Target currency ${currency}; supplied ${fx.base || "USD"}-base conversion book dated ${fx.as_of || "date unknown"}, provenance ${fx.provenance || "unknown"}. Rates are from that date.`
    : `Target currency ${currency}; no conversion book supplied. Amounts requiring conversion remain excluded from converted totals or unknown. No current-rate claim is made.`;
  coverageRoot.append(confidenceRow("Currency and FX provenance", fxSummary));
  const entries = Object.entries(coverage);
  if (!entries.length) coverageRoot.append(node("p", "Coverage details are unavailable in this report.", "empty"));
  const unsupportedLabels = { "context-overhead": "Context overhead", "one-shot-rate": "One-shot rate", corrections: "Corrections", rework: "Rework", "time-to-first-edit": "Time to first edit" };
  for (const [key, value] of entries) {
    if (key === "receipt_time_summary_records") {
      continue;
    } else if (key === "unsupported" && Array.isArray(value)) {
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
  updateReportDownload();
  if (paused) {
    cancelComparison();
    const last = reportData ? ` Last successful report observed at ${reportData.observed_at || "unknown time"} remains visible and may not match current filters.` : " No report observations have loaded yet.";
    $("#report-status").textContent = `Report refresh is paused.${last}`;
    $("#report-status").classList.remove("error");
    $("#report-content").hidden = !reportData;
    return;
  }
  if (!force && reportController) return;
  cancelReport();
  const requestId = reportRequestId, controller = new AbortController();
  const requestedQuery = query().toString();
  reportController = controller;
  const retained = reportData ? ` Last successful report from ${reportData.observed_at || "an unknown time"} remains visible and may not match the current filters.` : " No report observations have loaded yet.";
  $("#report-status").textContent = `Loading report for the selected UTC range and filters.${retained}`;
  $("#report-status").classList.remove("error");
  try {
    const response = await fetch(`/api/v1/report?${requestedQuery}`, { signal: controller.signal, cache: "no-store" });
    if (!response.ok) throw new Error(`Report read failed (${response.status}).`);
    const value = await response.json();
    if (requestId !== reportRequestId || view !== "report") return;
    if (value.schema !== "monitoring.report.v1") throw new Error("The report endpoint returned an incompatible schema.");
    reportData = value;
    reportDataQuery = requestedQuery;
    updateReportDownload();
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
  if (Number.isSafeInteger(p.excluded_summary_records) && p.excluded_summary_records > 0) {
    pace.append(node("p", `${number.format(p.excluded_summary_records)} cumulative Claude CLI result summaries are excluded from short-window pace and forecast because capture time is not call time. They remain in summary totals.`, "confidence-note"));
  }

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

function capabilityText(kind, value) {
  const labels = {
    usage: { "normalized-deltas": "Normalized usage deltas", "native-reconstruction": "Native usage reconstruction", "cumulative-result-deltas": "Claude CLI cumulative-result deltas", "completed-inference-spans": "Completed inference spans" },
    lifecycle: { "producer-supplied": "Producer-supplied lifecycle", "completed-agent-spans-only": "Agent invocation ends only", unsupported: "Lifecycle unsupported" },
    tools: { "producer-supplied": "Producer-supplied tool events", "completed-pairs": "Completed tool pairs", "completed-items": "Completed tool items", "completed-tool-spans": "Completed tool spans", unsupported: "Tool events unsupported" },
  };
  return labels[kind]?.[value] || `${kind}: ${value || "Unknown"}`;
}

function eventAgeText(item) {
  const freshness = item.event_freshness || "unknown";
  if (freshness === "unread") return "Unread · no accepted sample yet";
  if (freshness === "empty") return "Empty · no event timestamp in the accepted sample";
  const serverAge = item.event_age_seconds;
  if (typeof serverAge !== "number" || !Number.isFinite(serverAge) || !lastSuccess) return "Event freshness unknown";
  // The event age is measured by the owner at snapshot time. Advance it from
  // the browser's successful receipt time so pause/disconnect cannot freeze it.
  const age = serverAge + Math.max(0, (Date.now() - lastSuccess) / 1000);
  if (age < 0) return `Future event timestamp · ${number.format(Math.round(Math.abs(age)))} seconds ahead`;
  return `${age <= 60 ? "Recent" : "Stale"} · latest event ${number.format(Math.max(0, Math.round(age)))} seconds ago`;
}

function renderSourceCoverage() {
  const root = $("#source-coverage"), status = $("#source-coverage-status"), setup = $("#coverage-setup"), fallback = $("#input-inventory-fallback");
  const coverage = data?.source_coverage;
  const valid = coverage?.schema === "monitoring.source-coverage.v1";
  root.replaceChildren(); status.classList.remove("partial");
  fallback.hidden = valid;
  if (setup.dataset.hasCoverage !== String(valid)) {
    setup.dataset.hasCoverage = String(valid);
    setup.open = !valid;
  }
  if (!valid) {
    status.textContent = "Source coverage is unavailable in this snapshot. Existing analysis remains available.";
    root.append(node("p", "This inventory requires the newer source-coverage contract. Setup guidance is available in Settings.", "empty"));
    return;
  }
  const inputs = Array.isArray(coverage.inputs) ? coverage.inputs : [];
  const unavailable = inputs.filter((item) => item.read_state === "unavailable").length;
  const unread = inputs.filter((item) => item.read_state === "unread").length;
  const partial = unavailable > 0 || unread > 0;
  const retained = paused || snapshotReadUnavailable;
  const ownerQualification = paused
    ? ` Browser refresh is paused; this inventory is retained from the last successful browser snapshot received ${inputTimestamp(lastSuccess ? new Date(lastSuccess).toISOString() : null)}.`
    : snapshotReadUnavailable
      ? ` The Monitoring service is unavailable. This inventory is retained from the last successful browser snapshot received ${inputTimestamp(lastSuccess ? new Date(lastSuccess).toISOString() : null)}.`
      : " This describes the latest service response. It does not show whether workers are running.";
  status.textContent = `Global inventory · latest accepted input reads · ${inputCount(inputs.length)} inputs · ${unavailable} unavailable · ${unread} unread · owner snapshot observed ${inputTimestamp(coverage.observed_at)}.${ownerQualification} Coverage is incomplete across unconnected sources. Counts below may overlap and are not summed as total activity.`;
  status.classList.toggle("partial", partial);
  if (!inputs.length) { root.append(node("p", "No accepted input reads are listed in this coverage snapshot.", "empty")); return; }
  const recordFields = [["total", "Event records"], ["usage", "Usage records"], ["status", "Status records"], ["tool", "Tool records"], ["sessions", "Session identities"], ["entities", "Entity identities"], ["event_sources", "Event sources"]];
  const healthById = new Map((data.inputs?.inputs || []).map((item) => [item.id, item]));
  const timingFields = [["latest_event_at", "Latest event"], ["latest_usage_at", "Latest usage"], ["latest_status_at", "Latest status"], ["latest_tool_at", "Latest tool event"]];
  for (const item of inputs) {
    const row = node("article", null, `source-coverage-row coverage-${item.event_freshness || "unknown"}`);
    const head = node("div", null, "source-coverage-head"), info = node("div", null, "source-coverage-label");
    info.append(node("strong", typeof item.id === "string" && item.id ? item.id : "Managed input"));
    info.append(node("small", `${item.format || "Format unknown"}${healthById.get(item.id)?.project ? ` · project ${healthById.get(item.id).project}` : ""}`));
    const readState = item.read_state === "following" ? "Following at last owner read" : item.read_state === "unavailable" ? "Unavailable at last owner read" : item.read_state === "unread" ? "Unread at last owner read" : "Unknown at last owner read";
    head.append(info, node("span", `${readState}${retained ? " · retained browser read" : ""}`, `source-coverage-state coverage-read-${item.read_state || "unknown"}`)); row.append(head);
    const health = healthById.get(item.id);
    if (health) {
      row.append(node("p", `Last read attempt: ${inputTimestamp(health.last_attempt_at)} · last successful read: ${inputTimestamp(health.last_success_at)}`, "source-coverage-sample"));
      row.append(node("p", `Latest attempt: ${inputCount(health.records_seen)} records seen · ${inputCount(health.added)} added · ${inputCount(health.replayed)} replayed. These are read-attempt counts, not lifetime totals.`, "source-coverage-sample"));
      if (health.error === "Overlapping native records in another selected source. Accepted history is retained.") {
        row.append(node("p", "Repeated native records in another selected input. This input was refused; accepted history is retained. Review the source selection before restarting it.", "source-coverage-sample"));
      }
    }
    if (item.format === "claude-results") {
      row.append(node("p", "Completed Claude CLI result summaries include subagent aggregate usage, but not individual calls, turn times, or worker states. The capture timestamp is the declared receipt time, not provider event time. USD costs are client-side estimates, not billed spend or subscription balance. This route is not reconciled with a Claude JSONL input.", "source-coverage-sample"));
    }

    const capability = node("p", null, "source-coverage-capability");
    capability.append(node("span", "Capabilities"), node("span", ["usage", "lifecycle", "tools"].map((key) => capabilityText(key, item.capabilities?.[key])).join(" · ")));
    row.append(capability);
    const records = item.records;
    const kinds = node("div", null, "source-coverage-records");
    kinds.append(node("p", "Unique observations in this input's latest accepted read. Different inputs may overlap.", "source-coverage-subhead"));
    const counts = node("div", null, "source-coverage-count-grid");
    for (const [key, label] of recordFields) counts.append(confidenceRow(label, records == null ? "Unknown" : inputCount(records[key])));
    kinds.append(counts); row.append(kinds);

    const statusRecords = records?.status, toolRecords = records?.tool, capabilities = item.capabilities || {};
    const observed = node("p", null, "source-coverage-observed");
    const lifecycleObserved = capabilities.lifecycle === "unsupported" ? "No lifecycle support in this format" : Number.isFinite(statusRecords) && statusRecords > 0 ? `${inputCount(statusRecords)} producer status records observed` : statusRecords === 0 ? "No producer status records observed" : "Lifecycle records unknown";
    const toolsObserved = capabilities.tools === "unsupported" ? "No tool event support in this format" : Number.isFinite(toolRecords) && toolRecords > 0 ? `${inputCount(toolRecords)} tool records observed` : toolRecords === 0 ? "No tool records observed" : "Tool record count unknown";
    observed.append(node("span", `Lifecycle evidence: ${lifecycleObserved}`), node("span", `Tool evidence: ${toolsObserved}`)); row.append(observed);
    row.append(node("p", `Last accepted input sample: ${inputTimestamp(item.sampled_at)} · ${item.retained ? "previously accepted records retained" : "no retained prior read reported"}`, "source-coverage-sample"));
    row.append(node("p", `Event timestamp age: ${eventAgeText(item)}${item.latest_event_at ? ` · ${inputTimestamp(item.latest_event_at)}` : ""}`, "source-coverage-age"));
    const eventTimes = node("div", null, "source-coverage-times");
    for (const [key, label] of timingFields.slice(1)) eventTimes.append(confidenceRow(label, inputTimestamp(item[key])));
    row.append(eventTimes); root.append(row);
  }
}

function renderInputHealth() {
  const root = $("#input-health"), status = $("#input-health-status"), inventory = data?.inputs;
  root.replaceChildren(); status.classList.remove("partial");
  if (data?.source_coverage?.schema === "monitoring.source-coverage.v1") return;
  if (inventory?.schema !== "monitoring.inputs.v1") {
    status.textContent = "The input list is unavailable in this snapshot. Recorded event history remains available.";
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
  const retained = paused || snapshotReadUnavailable;
  const ownerQualification = paused
    ? ` Browser refresh is paused; this is retained from the last successful browser snapshot received ${inputTimestamp(lastSuccess ? new Date(lastSuccess).toISOString() : null)}.`
    : snapshotReadUnavailable
      ? ` The Monitoring service is unavailable. This inventory is retained from the last successful browser snapshot received ${inputTimestamp(lastSuccess ? new Date(lastSuccess).toISOString() : null)}.`
      : " This describes the latest service response. It does not show whether workers are running.";
  status.textContent = partial
    ? `Partial input availability · ${inputCount(healthy)} following · ${inputCount(unavailable)} unavailable · ${inputCount(unread)} unread. Following means at last owner read. Failed reads retain previously accepted event history. Owner snapshot observed ${inputTimestamp(data.observed_at)}.${ownerQualification}`
    : `${inputCount(healthy)} configured inputs following at last owner read. Owner snapshot observed ${inputTimestamp(data.observed_at)}.${ownerQualification}`;
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
    if (input.error === "Overlapping native records in another selected source. Accepted history is retained.") {
      row.append(node("p", "Repeated native records in another selected input. This input was refused; accepted history is retained.", "confidence-note"));
    }
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
  const age = lifecycleAgeText(row), stale = lifecycleEvidenceStale(row);
  status.title = `Reported: ${row.reported_state}. Evidence: ${row.status_at || "not supplied"}. ${age}. Stale: ${stale}.`;
  status.append(node("small", age, "state-evidence-age"));
  return status;
}

function evidenceAgeSeconds(timestamp) {
  const eventAt = Date.parse(timestamp || ""), observedAt = Date.parse(data?.observed_at || "");
  if (!Number.isFinite(eventAt) || !Number.isFinite(observedAt)) return null;
  const elapsed = lastSuccess ? Math.max(0, Date.now() - lastSuccess) : 0;
  return (observedAt - eventAt + elapsed) / 1000;
}

function evidenceAgeText(timestamp, label) {
  const age = evidenceAgeSeconds(timestamp);
  if (age === null) return `${label} age unknown`;
  const qualifier = age > 60 || age < -5 ? " · stale" : "";
  if (age < 0) return `${label} ${number.format(Math.ceil(Math.abs(age)))} seconds in the future${qualifier}`;
  return `${label} ${number.format(Math.floor(age))} seconds old${qualifier}`;
}

function lifecycleAgeText(row) {
  const age = evidenceAgeSeconds(row.status_at);
  if (age === null) return `Lifecycle evidence age unknown${row.stale ? " · stale" : ""}`;
  const qualifier = row.stale || age > 60 || age < -5 ? " · stale" : "";
  if (age < 0) return `Lifecycle evidence ${number.format(Math.ceil(Math.abs(age)))} seconds in the future${qualifier}`;
  return `Lifecycle evidence ${number.format(Math.floor(age))} seconds old${qualifier}`;
}

function lifecycleEvidenceStale(row) {
  const age = evidenceAgeSeconds(row.status_at);
  return Boolean(row.stale) || age === null || age > 60 || age < -5;
}

function lastEvidenceCellText(row) {
  const at = Date.parse(row.status_at || "");
  const label = Number.isFinite(at) ? `${new Date(at).toISOString().slice(11, 19)} UTC` : "Not supplied";
  return `${label}${lifecycleEvidenceStale(row) ? " · stale" : ""}`;
}

function updateLifecycleEvidenceAges() {
  if (!data) return;
  const updateState = (label, row) => {
    if (!row) return;
    label.textContent = lifecycleAgeText(row);
    const state = label.closest(".state");
    if (state) state.title = `Reported: ${row.reported_state}. Evidence: ${row.status_at || "not supplied"}. ${lifecycleAgeText(row)}. Stale: ${lifecycleEvidenceStale(row)}.`;
  };
  const entitiesByKey = new Map(data.entities.map((row) => [entityIdentityKey(row), row]));
  $$("#entity-table .state-evidence-age").forEach((label) => updateState(label, entitiesByKey.get(label.closest("tr")?.dataset.entityKey)));
  $$("#entity-table .last-evidence-cell").forEach((cell) => {
    const row = entitiesByKey.get(cell.closest("tr")?.dataset.entityKey);
    if (row) cell.textContent = lastEvidenceCellText(row);
  });
  $$("#timeline .state-evidence-age").forEach((label, index) => updateState(label, data.entities[index]));
  $$("#timeline .timeline-row").forEach((line, index) => {
    const row = data.entities[index];
    if (!row) return;
    const eventAge = evidenceAgeText(row.last_event_at, "Latest event evidence");
    const name = line.querySelector(".timeline-name"), track = line.querySelector(".timeline-track");
    if (name) name.title = eventAge;
    if (track) track.title = `${row.started_at} to ${row.last_event_at} · ${eventAge}`;
  });
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

function entityIdentityKey(row) {
  return JSON.stringify([row.source, row.session, row.entity]);
}

function compareEntities(a, b) {
  return [a.source, a.session, a.entity, a.entity_type].map(String).join("\u0000")
    .localeCompare([b.source, b.session, b.entity, b.entity_type].map(String).join("\u0000"));
}

function entityForest() {
  const rows = Array.isArray(data?.entities) ? [...data.entities].sort(compareEntities) : [];
  const byKey = new Map(), children = new Map(), roots = [], cycleKeys = new Set();
  for (const row of rows) {
    const key = entityIdentityKey(row);
    if (!byKey.has(key)) byKey.set(key, row);
    if (!children.has(key)) children.set(key, []);
  }
  for (const row of rows) {
    const key = entityIdentityKey(row);
    if (row.parent == null) { roots.push(row); continue; }
    const parentKey = entityIdentityKey({ source: row.source, session: row.session, entity: row.parent });
    if (parentKey === key) { cycleKeys.add(key); roots.push(row); }
    else if (byKey.has(parentKey)) children.get(parentKey).push(row);
    else roots.push(row);
  }
  for (const siblings of children.values()) siblings.sort(compareEntities);
  roots.sort(compareEntities);

  const discovered = new Set(), path = [];
  function discover(row) {
    const key = entityIdentityKey(row);
    const cycleAt = path.indexOf(key);
    if (cycleAt >= 0) {
      for (const member of path.slice(cycleAt)) cycleKeys.add(member);
      cycleKeys.add(key);
      return;
    }
    if (discovered.has(key)) return;
    discovered.add(key); path.push(key);
    for (const child of children.get(key) || []) discover(child);
    path.pop();
  }
  for (const root of roots) discover(root);
  // A malformed response may contain a cycle with no root. Show each component
  // from a deterministic starting point and cut the repeated edge during walk.
  for (const row of rows) if (!discovered.has(entityIdentityKey(row))) discover(row);

  const entries = [], visible = new Set();
  function display(row, depth) {
    const key = entityIdentityKey(row);
    if (visible.has(key)) return;
    visible.add(key);
    entries.push({ row, key, depth, childCount: (children.get(key) || []).length });
    if (!collapsedHierarchyKeys.has(key)) for (const child of children.get(key) || []) display(child, depth + 1);
  }
  for (const root of roots) display(root, 0);
  for (const row of rows) if (cycleKeys.has(entityIdentityKey(row)) && !visible.has(entityIdentityKey(row))) display(row, 0);
  return { entries, byKey, cycleKeys };
}

function renderEntityTable() {
  const { entries, byKey, cycleKeys } = entityForest();
  const note = $("#entity-hierarchy-note"), truncated = Boolean(data.coverage.entities_truncated);
  const notes = [];
  if (truncated) notes.push("The returned entity list is truncated at 500. Some parents or descendants may be outside this display.");
  if (cycleKeys.size) notes.push("A cyclic parent link was present in the response. The affected loop was cut for display.");
  if (!truncated && !cycleKeys.size) notes.push("Parent links use source, session and entity together. Missing parents and parents outside the current selection are identified below.");
  note.textContent = notes.join(" ");
  const values = entries.map(({ row, key, depth, childCount }) => {
    const treeCell = node("div", null, "entity-tree-cell");
    treeCell.style.paddingInlineStart = `${Math.min(depth, 7) * 14}px`;
    if (childCount > 0) {
      const collapsed = collapsedHierarchyKeys.has(key);
      const toggle = node("button", collapsed ? "▸" : "▾", "hierarchy-toggle");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.setAttribute("aria-label", `${collapsed ? "Expand" : "Collapse"} descendants of ${row.entity} · source ${row.source} · session ${row.session}`);
      toggle.title = toggle.getAttribute("aria-label");
      toggle.addEventListener("click", () => {
        if (collapsedHierarchyKeys.has(key)) collapsedHierarchyKeys.delete(key);
        else collapsedHierarchyKeys.add(key);
        renderEntityTable();
      });
      treeCell.append(toggle);
    } else {
      treeCell.append(node("span", "", "hierarchy-toggle-placeholder"));
    }

    const name = node("button", (row.parent ? "↳ " : "") + row.entity, "entity-button");
    name.addEventListener("click", () => inspect(row));
    const title = node("div", null, "entity-tree-label");
    title.append(name, node("small", `${row.entity_type} · ${row.project}${row.parent ? ` · parent ${row.parent}` : ""}`));
    const parentKey = row.parent == null ? null : entityIdentityKey({ source: row.source, session: row.session, entity: row.parent });
    let relation = "";
    if (row.parent != null && parentKey !== key && !byKey.has(parentKey)) {
      if (row.parent_observed === false) relation = `Parent ${row.parent} not observed in the available ledger.`;
      else if (row.parent_observed === true && truncated) relation = `Parent ${row.parent} was observed but is outside this selection or the truncated display.`;
      else if (row.parent_observed === true) relation = `Parent ${row.parent} was observed but is outside the current selection.`;
      else relation = `Parent ${row.parent} is not shown in the current selection.`;
    }
    const warnings = [];
    if (relation) warnings.push(relation);
    if (cycleKeys.has(key)) warnings.push("Cyclic hierarchy link. Display is safely cut.");
    if (warnings.length) title.append(node("small", warnings.join(" "), "hierarchy-qualification"));
    treeCell.append(title);
    const model = node("div", row.model); model.append(node("small", row.surface));
    const lastEvidence = node("span", lastEvidenceCellText(row), "last-evidence-cell");
    const rendered = [treeCell, state(row), model, number.format(row.metrics.total_tokens), cash(row.metrics.costs.estimated.amount), cash(row.metrics.costs.billed.amount), lastEvidence];
    rendered.entityKey = key;
    rendered.depth = depth;
    return rendered;
  });
  table("#entity-table", ["Entity / hierarchy", "State", "Model / surface", "Tokens", "Estimated", "Billed", "Last evidence · UTC"], values);
  $$("#entity-table tbody tr").forEach((row, index) => {
    row.dataset.entityKey = values[index].entityKey;
    row.dataset.depth = String(values[index].depth);
  });
}

function inspect(row) {
  cancelEntityHistory();
  entity = row;
  entityHistory = {
    entityKey: JSON.stringify([row.source, row.session, row.entity]), identity: data?.identity ?? null,
    revision: Number.isSafeInteger(data?.revision) ? data.revision : null,
    filterSignature: historyFilterSignature(), pages: [], total: null, nextOffset: null,
    pending: false, controller: null, requestId: 0, phase: "idle", message: "",
    error: false, filtersChanged: false, serverRevision: null, started: true,
  };
  $("#event-selection").textContent = `${row.entity_type}: ${row.entity} · source ${row.source} · session ${row.session}. Entity history is scoped to this exact identity.`;
  $("#event-description").textContent = "Paged history for this exact entity · usage deltas, status evidence and tool outcomes";
  $("#clear-entity").hidden = false;
  showView("events");
  $("#subtitle").textContent = "Paged history for this exact entity · the latest 100 records remain available when selection is cleared.";
  if (paused) {
    entityHistory.phase = "paused";
    entityHistory.message = "Refresh is paused. Entity history did not start; resume refresh, then choose Restart history to read it.";
    renderEvents();
  } else startEntityHistory();
}

function historyFilterSignature() {
  const params = query();
  params.delete("state");
  return params.toString();
}

function historyParams(after, state, selectedEntity) {
  const params = query();
  params.delete("state");
  params.set("source", selectedEntity.source);
  params.set("session", selectedEntity.session);
  params.set("entity", selectedEntity.entity);
  params.set("limit", "100");
  if (after > 0) params.set("after", String(after));
  if (state.revision !== null) params.set("expected_revision", String(state.revision));
  return params;
}

function cancelEntityHistory(message = "") {
  if (!entityHistory) return;
  entityHistory.requestId += 1;
  if (entityHistory.controller) entityHistory.controller.abort();
  if (entityHistory.timeoutTimer) clearTimeout(entityHistory.timeoutTimer);
  entityHistory.controller = null;
  entityHistory.timeoutTimer = null;
  entityHistory.pending = false;
  if (message) {
    entityHistory.phase = "cancelled";
    entityHistory.message = message;
    entityHistory.error = false;
  }
  renderEntityHistory();
}

function startEntityHistory() {
  if (!entity || !entityHistory) return;
  if (paused) {
    entityHistory.phase = "paused";
    entityHistory.message = "Refresh is paused. Entity history did not start; resume refresh, then choose Restart history to read it.";
    renderEntityHistory(); return;
  }
  const previousId = entityHistory.requestId;
  if (entityHistory.controller) entityHistory.controller.abort();
  if (entityHistory.timeoutTimer) clearTimeout(entityHistory.timeoutTimer);
  entityHistory = {
    entityKey: JSON.stringify([entity.source, entity.session, entity.entity]),
    identity: data?.identity ?? null,
    revision: null,
    filterSignature: historyFilterSignature(), pages: [], total: null, nextOffset: null,
    pending: false, controller: null, timeoutTimer: null, timedOut: false,
    requestId: previousId + 1, phase: "idle", message: "",
    error: false, filtersChanged: false, serverRevision: null, started: true,
  };
  if (entityHistory.identity === null) {
    entityHistory.phase = "error";
    entityHistory.error = true;
    entityHistory.message = "The current snapshot lacks an owner identity. No entity history was requested.";
    renderEntityHistory(); return;
  }
  fetchEntityHistoryPage(0);
}

async function fetchEntityHistoryPage(after) {
  if (!entity || !entityHistory || paused || entityHistory.pending || entityHistory.filtersChanged) return;
  const currentKey = JSON.stringify([entity.source, entity.session, entity.entity]);
  if (entityHistory.entityKey !== currentKey) return;
  if (historyFilterSignature() !== entityHistory.filterSignature) { markEntityHistoryFiltersChanged(); return; }
  const state = entityHistory, requestId = ++state.requestId, controller = new AbortController();
  state.controller = controller; state.pending = true; state.phase = "loading"; state.error = false;
  state.timedOut = false;
  state.timeoutTimer = setTimeout(() => { state.timedOut = true; controller.abort(); }, 8000);
  state.message = after === 0 ? "Loading the oldest matching entity events…" : "Loading the next entity events page…";
  renderEntityHistory();
  try {
    const response = await fetch(`/api/v1/events?${historyParams(after, state, entity)}`, { signal: controller.signal, cache: "no-store" });
    if (entityHistory !== state || requestId !== state.requestId || view !== "events" || state.entityKey !== currentKey) return;
    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      if (entityHistory !== state || requestId !== state.requestId || view !== "events" || state.entityKey !== currentKey) return;
      state.serverRevision = Number.isSafeInteger(conflict.revision) ? conflict.revision : null;
      state.phase = "conflict"; state.error = true;
      state.message = "The ledger changed while this history was loading. Loaded pages remain pinned to the original revision; restart history to begin a new page set.";
      return;
    }
    if (!response.ok) throw new Error("History read failed.");
    const value = await response.json();
    if (entityHistory !== state || requestId !== state.requestId || view !== "events" || state.entityKey !== currentKey) return;
    if (value.schema !== "monitoring.events.v1" || value.identity !== state.identity || !Number.isSafeInteger(value.revision) || value.revision < 0 || (state.revision !== null && value.revision !== state.revision)) {
      state.phase = "conflict"; state.error = true;
      state.message = "The history response did not match the owner identity and revision in the snapshot. No page was added; restart history after the snapshot updates.";
      return;
    }
    if (!Array.isArray(value.events) || !Number.isSafeInteger(value.total) || (value.next_offset !== null && !Number.isSafeInteger(value.next_offset))) {
      throw new Error("History response was incomplete.");
    }
    state.pages.push(value.events);
    if (state.revision === null) state.revision = value.revision;
    state.total = value.total;
    state.nextOffset = value.next_offset;
    state.phase = "loaded";
    state.message = "Entity history page loaded oldest first.";
  } catch (error) {
    if (entityHistory !== state || requestId !== state.requestId || view !== "events") return;
    state.phase = "error"; state.error = true;
    state.message = state.timedOut
      ? "Entity history read timed out. Any loaded pages remain available; use Restart history to retry."
      : error.name === "AbortError" ? "Entity history read was canceled. Loaded pages remain available; restart explicitly to try again."
        : "Entity history could not be read. Any loaded pages remain available; use Restart history to retry.";
  } finally {
    if (entityHistory === state && requestId === state.requestId) {
      clearTimeout(state.timeoutTimer); state.timeoutTimer = null;
      state.pending = false; state.controller = null;
      renderEntityHistory();
    }
  }
}

function markEntityHistoryFiltersChanged(force = false) {
  if (!entityHistory || !entity || (!force && historyFilterSignature() === entityHistory.filterSignature)) return;
  if (entityHistory.controller) cancelEntityHistory();
  entityHistory.filtersChanged = true;
  entityHistory.phase = "filters-changed";
  entityHistory.message = "Filters changed. Loaded pages remain under the previous filter set; restart history to apply current filters.";
  renderEntityHistory();
}

function entityHistoryRestartReason() {
  if (entityHistory?.filtersChanged) return "Filters changed. Loaded pages remain under the previous filter set; restart history to apply current filters.";
  if (entityHistory?.phase === "conflict") return "History revision or owner changed. Loaded pages remain pinned; restart history to begin a new page set.";
  return "";
}

function entityHistorySnapshotNote() {
  if (!entityHistory || !data) return "";
  if (data.identity !== entityHistory.identity) return `The current snapshot comes from a different owner instance. These pages remain pinned to revision ${entityHistory.revision ?? "unknown"}. Restart history to begin again.`;
  if (entityHistory.revision !== null && data.revision !== entityHistory.revision) return `The current snapshot is revision ${data.revision}; these pages remain pinned to revision ${entityHistory.revision}. Restart history to begin a new page set.`;
  return "";
}

function entityHistoryRows() {
  return entityHistory?.pages.flat() || [];
}

function historyEventCells(row) {
  const id = node("div", row.entity); id.append(node("small", `${row.source} · ${row.surface}`));
  const detail = row.kind === "status" ? row.state : row.kind === "tool" ? `${row.tool} · ${row.outcome} · ${row.duration_ms ?? "?"} ms` : `${number.format(Object.values(row.tokens || {}).reduce((a, b) => a + b, 0))} tokens · ${row.model}`;
  const cost = row.cost;
  const literalCost = cost && cost.amount !== undefined && cost.currency && cost.basis
    ? `${cost.amount} ${cost.currency} · ${cost.basis}${cost.provenance ? ` · ${cost.provenance}` : ""}`
    : row.kind === "usage" ? "No recorded cost" : "—";
  return [row.timestamp.replace("T", " ").replace("Z", ""), id, row.kind, detail, literalCost];
}

function renderEntityHistory() {
  const panel = $("#entity-history-panel");
  panel.hidden = !entity;
  if (!entity || !entityHistory) return;
  const rows = entityHistoryRows(), status = $("#entity-history-status");
  status.classList.toggle("error", entityHistory.error);
  const snapshotNote = entityHistorySnapshotNote();
  status.textContent = [entityHistory.message, snapshotNote].filter(Boolean).join(" ");
  $("#entity-history-scope").textContent = `Exact entity key: source ${entity.source} · session ${entity.session} · entity ${entity.entity}. Pages are oldest first, up to 100 events. Provider, model, surface, project and date filters apply; state is omitted. Recorded costs stay in original currency; no price book or conversion is applied.`;
  $("#entity-history-count").textContent = entityHistory.total === null
    ? "No history page has been loaded yet."
    : entityHistory.total === 0
      ? "No matching events were recorded for this entity and filter set."
      : `Showing ${number.format(rows.length)} of ${number.format(entityHistory.total)} matching events · owner revision ${entityHistory.revision}.`;
  const load = $("#load-more-events"), restart = $("#restart-entity-history");
  load.hidden = entityHistory.nextOffset === null;
  load.disabled = paused || entityHistory.pending || entityHistory.filtersChanged || entityHistory.phase === "conflict" || entityHistory.nextOffset === null;
  restart.hidden = !entityHistory.started && !rows.length;
  restart.disabled = paused || entityHistory.pending;
  const target = $("#event-table");
  target.classList.add("entity-history-table");
  if (rows.length) table("#event-table", ["Time · UTC", "Entity / source", "Kind", "Details", "Recorded cost · original currency"], rows.map(historyEventCells));
  else {
    const emptyText = entityHistory.pending ? "Loading matching events…" : entityHistory.total === 0 ? "No matching entity events." : "No history page is loaded.";
    target.replaceChildren(node("p", emptyText, "empty"));
  }
}

function renderEvents() {
  if (entity) { renderEntityHistory(); return; }
  $("#entity-history-panel").hidden = true;
  $("#event-description").textContent = "Latest 100 matching records · usage deltas, status evidence and tool outcomes";
  const target = $("#event-table"); target.classList.remove("entity-history-table");
  const rows = data.events;
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
  renderCodeburnCache();
  renderTokenThreshold();
  renderCostThresholds();
}

function renderTokenThreshold() {
  const panel = $("#token-threshold-panel"), status = $("#token-threshold-status"), details = $("#token-threshold-details");
  const value = data?.token_threshold;
  panel.hidden = value?.schema !== "monitoring.token-threshold.v1";
  if (panel.hidden) return;
  details.replaceChildren();
  const valid = [value.limit_tokens, value.observed_tokens, value.remaining_observed_tokens, value.usage_records,
    value.excluded_receipt_summaries, value.excluded_future_records].every((n) => Number.isSafeInteger(n) && n >= 0)
    && ["no-observations", "observed-below", "observed-reached"].includes(value.state);
  if (!valid) { status.textContent = "Threshold data is unavailable."; return; }
  status.textContent = value.state === "observed-reached" ? "Observed threshold reached" : value.state === "no-observations" ? "No counted usage observations today" : "Observed use below threshold";
  if (paused || snapshotReadUnavailable) status.textContent += " · retained snapshot; current use unknown";
  if (data.source_status?.state === "unavailable") status.textContent += " · selected source unavailable; accepted history retained";
  details.append(confidenceRow("Observed / threshold", `${number.format(value.observed_tokens)} / ${number.format(value.limit_tokens)} tokens`));
  details.append(confidenceRow("Headroom to threshold", `${number.format(value.remaining_observed_tokens)} tokens`));
  details.append(confidenceRow("UTC day", `${value.window?.from || "Unknown"} to ${value.window?.until || "Unknown"}`));
  details.append(confidenceRow("Usage records", number.format(value.usage_records)));
  details.append(confidenceRow("Excluded result summaries / future records", `${number.format(value.excluded_receipt_summaries)} / ${number.format(value.excluded_future_records)}`));
}

function renderCostThresholds() {
  const panel = $("#cost-threshold-panel"), status = $("#cost-threshold-status"), details = $("#cost-threshold-details");
  const value = data?.cost_thresholds;
  panel.hidden = value?.schema !== "monitoring.cost-thresholds.v1";
  if (panel.hidden) return;
  details.replaceChildren();
  if (!Array.isArray(value.items) || !value.items.length || !/^[A-Z]{3}$/.test(value.currency || "")) {
    status.textContent = "Cost threshold data is unavailable."; return;
  }
  status.textContent = "Known cost observations for the current UTC day";
  if (paused || snapshotReadUnavailable) status.textContent += " · retained snapshot; current use unknown";
  if (data.source_status?.state === "unavailable") status.textContent += " · selected source unavailable; accepted history retained";
  for (const item of value.items) {
    if (!["estimated", "billed"].includes(item.basis) ||
        !["no-observations", "observed-below", "observed-reached"].includes(item.state) ||
        !/^\d+(?:\.\d+)?$/.test(item.limit_amount || "")) {
      details.append(node("p", "A cost threshold row is unavailable.", "empty")); continue;
    }
    const basis = item.basis === "estimated" ? "Estimated" : "Billed";
    const observed = item.observed_amount === null ? "unknown" : item.observed_amount;
    const label = item.state === "observed-reached" ? "observed threshold reached" :
      item.state === "no-observations" ? "no converted observations" : "observed below threshold";
    details.append(confidenceRow(`${basis} cost · ${label}`, `${observed} / ${item.limit_amount} ${value.currency}`));
    details.append(confidenceRow(`${basis} coverage`, `${item.covered_records} converted · ${item.unpriced_records} unpriced · ${item.unconverted_records} missing FX · ${item.other_basis_records} other basis · ${item.future_records} future · ${item.receipt_time_records} receipt-time summaries`));
    details.append(confidenceRow(`${basis} provenance`, `${item.cost_provenance?.join(", ") || "none"}${item.provenance_truncated ? " and more" : ""}`));
  }
  details.append(confidenceRow("UTC day", `${value.window?.from || "Unknown"} to ${value.window?.until || "Unknown"}`));
}

function recordCodeburnActivityReceipt(snapshot) {
  const book = snapshot?.codeburn_activity?.schema === "monitoring.codeburn-activity.v1" ? snapshot.codeburn_activity : null;
  if (!book) { activityReceivedAt = 0; activitySourceAgeAtReceipt = null; return; }
  if (book.sampled_at === data?.codeburn_activity?.sampled_at && snapshot.identity === data?.identity) return;
  activityReceivedAt = Date.now();
  activitySourceAgeAtReceipt = Number.isFinite(book.source_age_seconds) ? book.source_age_seconds : null;
}

function renderCodeburnActivity() {
  const panel = $("#codeburn-activity-panel"), status = $("#codeburn-activity-status"), target = $("#codeburn-activity-table");
  const book = data?.codeburn_activity?.schema === "monitoring.codeburn-activity.v1" ? data.codeburn_activity : null;
  const readStatus = data?.codeburn_activity_status;
  panel.hidden = !book && !readStatus;
  if (panel.hidden) return;
  status.replaceChildren(); target.replaceChildren();
  if (!book) {
    status.append(node("p", readStatus?.state === "unavailable" ? "Selected CodeBurn status is unavailable. Current activity is unknown." : "Selected CodeBurn status has not been read. Current activity is unknown.", "confidence-note"));
    return;
  }
  const age = Number.isFinite(activitySourceAgeAtReceipt) ? activitySourceAgeAtReceipt + Math.max(0, Date.now() - activityReceivedAt) / 1000 : null;
  const ageText = age === null ? "unknown generation age" : age < 0 ? "generation time is ahead of this clock" : age < 60 ? `${number.format(Math.floor(age))}s since generation` : age < 3600 ? `${number.format(Math.floor(age / 60))}m since generation` : `${number.format(Math.floor(age / 3600))}h since generation`;
  const stale = age === null || age < 0 || age > 30 || readStatus?.state !== "following" || paused || snapshotReadUnavailable;
  status.append(node("p", `Source generated ${book.generated_at || "at an unknown time"} · ${ageText} · selected file last modified ${book.file_modified_at || "at an unknown time"}. ${stale ? "Current activity is unknown." : "Source observation is recent; transcript writes still do not prove process liveness."}`, "confidence-note"));
  if (readStatus?.state === "unavailable") status.append(node("p", "The selected file is unavailable. Showing the last accepted observation.", "confidence-note"));
  if (!Array.isArray(book.sessions)) {
    status.append(node("p", "This source output did not include a live-session block. Coverage is unknown.", "confidence-note"));
    return;
  }
  const window = Number.isSafeInteger(book.window_seconds) && book.window_seconds > 0 ? `${number.format(book.window_seconds)}s` : "unknown window";
  status.append(node("p", `${number.format(book.sessions.length)} session${book.sessions.length === 1 ? "" : "s"} reported with transcript writes in the ${window} source window at generation. This does not establish the number running now.`, "confidence-note"));
  if (!book.sessions.length) return;
  table("#codeburn-activity-table", ["Anonymous session", "Provider / model", "Last transcript write · UTC", "Context · source reported"], book.sessions.map((row) => {
    const context = Number.isSafeInteger(row?.context_tokens) && Number.isSafeInteger(row?.context_window) ? `${number.format(row.context_tokens)} / ${number.format(row.context_window)}` : "Unknown";
    return [row?.alias || "Unknown", `${row?.provider || "Unknown"} / ${row?.model || "Unknown"}`, row?.last_activity_at || "Unknown", context];
  }));
}

function renderClaudeResponses() {
  const panel = $("#claude-response-panel"), status = $("#claude-response-status"), target = $("#claude-response-table");
  const book = data?.claude_responses?.schema === "monitoring.claude-responses.v1" ? data.claude_responses : null;
  panel.hidden = !book;
  if (!book) return;
  status.replaceChildren(); target.replaceChildren();
  const selectedSource = $("#filters [name=source]")?.value || "";
  const selectedProject = $("#filters [name=project]")?.value || "";
  const selectedSession = $("#filters [name=session]")?.value || "";
  const rows = (Array.isArray(book.rows) ? book.rows : []).filter((row) =>
    (!selectedSource || row.source === selectedSource) && (!selectedProject || row.project === selectedProject) &&
    (!selectedSession || row.session === selectedSession));
  status.append(node("p", `${number.format(rows.length)} response span${rows.length === 1 ? "" : "s"} in the selected source/project/session scope. Other event filters do not apply. Current activity and session closure are unknown.`, "confidence-note"));
  if (book.unavailable_sources || snapshotReadUnavailable || paused) status.append(node("p", "A source read or dashboard refresh is unavailable or paused; rows are retained history.", "confidence-note"));
  if (book.truncated) status.append(node("p", "Only the latest 100 response spans are shown.", "confidence-note"));
  if (!rows.length) return;
  const evidence = { prompt_observed: "Prompt only", response_end_observed: "Response ended", stop_hook_observed: "Stop hook observed" };
  table("#claude-response-table", ["Source / session", "Prompt · UTC", "Response end · UTC", "Stop hook · UTC", "Evidence"], rows.map((row) => [
    `${row.source} / ${row.session?.length > 18 ? `${row.session.slice(0, 9)}…${row.session.slice(-4)}` : row.session || "Unknown"}`, row.prompt_at || "Unknown", row.response_end_at || "Not observed",
    row.stop_hook_at || "Not observed", `${evidence[row.evidence] || "Unknown"}${row.source_state === "unavailable" ? " · source unavailable" : ""}`,
  ]));
}

function renderCodexTurns() {
  const panel = $("#codex-turn-panel"), status = $("#codex-turn-status"), target = $("#codex-turn-table");
  const book = data?.codex_turns?.schema === "monitoring.codex-turns.v1" ? data.codex_turns : null;
  panel.hidden = !book;
  if (!book) return;
  status.replaceChildren(); target.replaceChildren();
  const selectedSource = $("#filters [name=source]")?.value || "";
  const selectedProject = $("#filters [name=project]")?.value || "";
  const selectedSession = $("#filters [name=session]")?.value || "";
  const rows = (Array.isArray(book.rows) ? book.rows : []).filter((row) =>
    (!selectedSource || row.source === selectedSource) && (!selectedProject || row.project === selectedProject) &&
    (!selectedSession || row.session === selectedSession));
  status.append(node("p", `${number.format(rows.length)} turn marker group${rows.length === 1 ? "" : "s"} in the selected source/project/session scope. Other event filters do not apply. Current session and process activity are unknown.`, "confidence-note"));
  if (book.unavailable_sources || snapshotReadUnavailable || paused) status.append(node("p", "A source read or dashboard refresh is unavailable or paused; markers are retained history.", "confidence-note"));
  if (book.ambiguous_turns) status.append(node("p", `${number.format(book.ambiguous_turns)} turn identity group${book.ambiguous_turns === 1 ? " was" : "s were"} omitted because markers conflicted or reversed time.`, "confidence-note"));
  if (book.truncated) status.append(node("p", "Only the latest 100 unambiguous turn groups are shown.", "confidence-note"));
  if (!rows.length) return;
  const evidence = { paired: "Both markers", "start-only": "Start only", "completion-only": "Completion only" };
  table("#codex-turn-table", ["Source / session / turn", "Start · UTC", "Completion · UTC", "Duration", "First token", "Markers"], rows.map((row) => [
    `${row.source} / ${row.session?.slice(0, 10) || "Unknown"}… / ${row.turn?.slice(0, 10) || "Unknown"}…`,
    row.start_at || "Not observed", row.complete_at || "Not observed",
    Number.isSafeInteger(row.duration_ms) ? `${number.format(row.duration_ms)} ms` : "Unknown",
    Number.isSafeInteger(row.time_to_first_token_ms) ? `${number.format(row.time_to_first_token_ms)} ms` : "Unknown",
    `${evidence[row.evidence] || "Unknown"}${row.source_state === "unavailable" ? " · source unavailable" : ""}`,
  ]));
}

function renderSelectedCache() {
  const panel = $("#selected-cache-panel"), status = $("#selected-cache-status"), target = $("#selected-cache-table");
  const book = data?.selected_cache_session?.schema === "monitoring.selected-cache-session.v1" ? data.selected_cache_session : null;
  const readStatus = data?.selected_cache_status;
  panel.hidden = !book && !readStatus;
  if (panel.hidden) return;
  status.replaceChildren(); target.replaceChildren();
  if (!book) {
    status.append(node("p", readStatus?.state === "unavailable" ? "Selected cache metadata is unavailable. Current activity is unknown." : "Waiting for selected cache metadata.", "confidence-note"));
    return;
  }
  const selectedSource = $("#filters [name=source]")?.value || "";
  const selectedProject = $("#filters [name=project]")?.value || "";
  if ((selectedSource && selectedSource !== book.source) || (selectedProject && selectedProject !== book.project)) {
    status.append(node("p", "The selected source or project filter excludes this cache view. Other event filters do not apply.", "confidence-note"));
    return;
  }
  status.append(node("p", `${number.format(book.total_turns)} cached turns · ${number.format(book.total_calls)} cached calls for ${book.source}. Native file modified ${book.native_modified_at}; cache member modified ${book.cache_modified_at}. Neither time proves current activity.`, "confidence-note"));
  if (readStatus?.state !== "following" || paused || snapshotReadUnavailable) status.append(node("p", "This view is retained; the current selected cache read is unavailable or paused.", "confidence-note"));
  if (book.truncated) status.append(node("p", "Only the latest 100 cached turns are shown.", "confidence-note"));
  if (!Array.isArray(book.turns) || !book.turns.length) {
    status.append(node("p", "No turns are represented in this cache member. Source coverage remains unknown.", "empty"));
    return;
  }
  table("#selected-cache-table", ["Turn", "Source timestamp · UTC", "Calls", "Models", "Input", "Output", "Cache read", "Cache write"], book.turns.map((row) => [
    number.format(row.ordinal), row.at, number.format(row.calls),
    (row.models || []).map((model) => `${model.id} (${number.format(model.calls)})`).join(", ") || "Unknown",
    number.format(row.tokens.input), number.format(row.tokens.output), number.format(row.tokens.cache_read), number.format(row.tokens.cache_write),
  ]));
}

function renderProcessHistory(root, sample) {
  const history = Array.isArray(sample.history) ? sample.history.slice(-60).filter((row) =>
    row && Number.isFinite(Date.parse(row.sampled_at)) && Number.isSafeInteger(row.segment) && row.segment >= 0 &&
    Number.isSafeInteger(row.count) && row.count >= 0 && Number.isFinite(row.cpu_percent) && row.cpu_percent >= 0 &&
    Number.isSafeInteger(row.rss_bytes) && row.rss_bytes >= 0) : [];
  if (history.length < 2) return;
  const box = node("div", null, "process-history");
  box.append(node("p", `Recent OS samples · ${number.format(history.length)} successful reads${sample.history_truncated ? " · latest 60 retained" : ""}. Gaps separate unavailable or missed reads.`, "confidence-note"));
  const svgElement = (name) => document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, label, unit, style] of [
    ["count", "Matching processes", "", "count"],
    ["cpu_percent", "Combined OS CPU", "%", "cpu"],
    ["rss_bytes", "Combined resident memory", " MiB", "memory"],
  ]) {
    const values = history.map((row) => key === "rss_bytes" ? row[key] / 1048576 : row[key]);
    const peak = Math.max(...values);
    const format = (value) => key === "cpu_percent" ? value.toFixed(1) : number.format(Math.round(value));
    const row = node("div", null, `process-trend-row process-trend-${style}`);
    row.append(node("p", `${label} · latest ${format(values.at(-1))}${unit} · observed peak ${format(peak)}${unit}`, "process-trend-label"));
    const svg = svgElement("svg");
    svg.setAttribute("viewBox", "0 0 320 58");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    const segments = new Map();
    history.forEach((point, index) => {
      const x = 10 + (index / (history.length - 1)) * 300;
      const y = 49 - (peak ? values[index] / peak : 0) * 39;
      if (!segments.has(point.segment)) segments.set(point.segment, []);
      segments.get(point.segment).push([x, y]);
    });
    for (const points of segments.values()) {
      if (points.length > 1) {
        const line = svgElement("polyline");
        line.setAttribute("points", points.map(([x, y]) => `${x},${y}`).join(" "));
        svg.append(line);
      } else {
        const dot = svgElement("circle");
        dot.setAttribute("cx", points[0][0]); dot.setAttribute("cy", points[0][1]); dot.setAttribute("r", "2.5");
        svg.append(dot);
      }
    }
    row.append(svg); box.append(row);
  }
  root.append(box);
}

function renderProcessSourceCoverage(root, sample, current) {
  if (!current) return;
  const families = [["claude", "Claude"], ["codex", "Codex"]];
  const coverage = data?.source_coverage?.schema === "monitoring.source-coverage.v1" &&
    Array.isArray(data.source_coverage.inputs) ? data.source_coverage : null;
  const comparison = node("div", null, "process-source-coverage");
  let shown = false;
  for (const [family, label] of families) {
    const count = sample.processes.filter((row) => String(row.executable).toLowerCase() === family).length;
    if (!count) continue;
    shown = true;
    const inputs = coverage ? coverage.inputs.filter((row) => row?.format === family) : [];
    let evidence;
    if (!coverage) evidence = "Selected native input coverage is unavailable from this owner.";
    else if (!inputs.length) evidence = `No ${label} session file is selected in this owner.`;
    else {
      const dated = inputs.filter((row) => Number.isFinite(Date.parse(row.latest_event_at)));
      const latest = dated.sort((a, b) => Date.parse(b.latest_event_at) - Date.parse(a.latest_event_at))[0];
      const unavailable = inputs.filter((row) => row.read_state === "unavailable").length;
      evidence = `${number.format(inputs.length)} selected ${label} input${inputs.length === 1 ? "" : "s"}; ${latest ? eventAgeText(latest) : "no accepted event time"}${unavailable ? `; ${number.format(unavailable)} input${unavailable === 1 ? "" : "s"} unavailable` : ""}.`;
    }
    comparison.append(node("p", `${label} executable observed in a recent OS sample (${sample.truncated ? "at least " : ""}${number.format(count)}). ${evidence} No process-to-file or process-to-session link is known.`, "confidence-note"));
  }
  if (shown) {
    const action = node("a", "Review source setup ↗", "process-coverage-action");
    action.href = "#settings/sources";
    comparison.append(action);
    root.append(comparison);
  }
}

function renderAppThreads() {
  const book = data?.app_threads, panel = $("#app-threads-panel");
  panel.hidden = !book;
  if (!book) return;
  const runtime = book.source === "codex-local-app-server";
  $("#app-thread-title").textContent = runtime ? "Codex runtime observations" : "App task observations";
  $("#app-thread-description").textContent = runtime ? "One database-only page from the selected local Codex host and project. Separate from ledger sessions." : "Codex and ChatGPT statuses reported by the app inventory, separate from ledger sessions.";
  $("#app-thread-limit").textContent = `${runtime ? "Selected project page only. Other hosts and ChatGPT conversations are unobserved." : "Recent app page only."} Titles, summaries, paths and raw thread IDs are omitted. Reported active/idle does not establish model inference, worker lifecycle, tokens or billing. Ledger filters do not apply. Capture older than 30 seconds has unknown current status.`;
  const age = (Date.now() - Date.parse(book.captured_at)) / 1000;
  const recent = Number.isFinite(age) && age >= -5 && age <= 30 && !paused && !snapshotReadUnavailable && book.read_state === "following";
  $("#app-thread-status").textContent = `Capture ${book.captured_at || "unavailable"} · ${recent ? (runtime ? "recent Codex runtime snapshot" : "recent app inventory") : `${runtime ? "current Codex runtime status unknown" : "current app status unknown"}; capture stale, unreadable or refresh paused`}. ${(book.threads || []).length} reported task rows; source coverage is incomplete.`;
  table("#app-thread-table", ["Task alias", "Source kind", "Reported status", "Thread updated · UTC", "Host class"],
    (book.threads || []).map((row) => [row.alias, row.kind, recent ? row.status : `Previously ${row.status} · current unknown`, row.updated_at, row.host]));
}

function renderOsProcesses() {
  const panel = $("#os-process-panel"), status = $("#os-process-status"), target = $("#os-process-table");
  const sample = data?.os_processes?.schema === "monitoring.os-processes.v1" ? data.os_processes : null;
  panel.hidden = !sample;
  const overview = $("#os-process-overview-panel"), overviewBody = $("#os-process-overview");
  overview.hidden = !sample;
  overviewBody.replaceChildren();
  if (!sample) return;
  status.replaceChildren(); target.replaceChildren();
  const age = sample.sampled_at ? (Date.now() - Date.parse(sample.sampled_at)) / 1000 : null;
  const current = sample.state === "following" && Number.isFinite(age) && age >= 0 && age <= 10 && !paused && !snapshotReadUnavailable;
  const interval = Number.isSafeInteger(sample.interval_seconds) ? `${sample.interval_seconds}s interval` : "interval unknown";
  if (sample.state === "following") {
    const counts = new Map();
    for (const row of sample.processes) counts.set(row.executable, (counts.get(row.executable) || 0) + 1);
    const names = [...counts].sort(([a], [b]) => a.localeCompare(b)).map(([name, count]) => `${name}: ${number.format(count)}`).join(" · ");
    overviewBody.append(node("p", `${number.format(sample.processes.length)} matching executables in ${current ? "recent" : "last"} OS sample ${sample.sampled_at} · ${interval}. ${current ? "" : "Current presence is unknown. "}No process is linked to a recorded session or worker.`, "confidence-note"));
    if (names) overviewBody.append(node("p", names, "confidence-note"));
    renderProcessSourceCoverage(overviewBody, sample, current);
  } else {
    overviewBody.append(node("p", sample.state === "unavailable" ? "OS process read unavailable. Current process presence is unknown." : "Waiting for the first OS process sample.", "confidence-note"));
  }
  renderProcessHistory(overviewBody, sample);
  if (!current) {
    status.append(node("p", sample.state === "unavailable" ? "OS process read unavailable. Current process presence is unknown." :
      sample.state === "unread" ? "Waiting for the first OS process sample." :
      `Last OS sample ${sample.sampled_at || "unknown"} is retained; current process presence is unknown.`, "confidence-note"));
  } else {
    status.append(node("p", `${number.format(sample.processes.length)} matching executables in OS sample ${sample.sampled_at} · ${interval}. Session and worker mappings are unknown.`, "confidence-note"));
  }
  if (sample.state !== "following") return;
  if (!sample.processes.length) status.append(node("p", "No matching executable names were visible in this OS sample. Other AI activity may exist.", "empty"));
  else table("#os-process-table", ["Executable", "PID / parent", "CPU", "RSS", "Started · UTC", "Session / worker"],
    sample.processes.map((row) => [row.executable, `${row.pid} / ${row.parent_pid}`, `${row.cpu_percent}%`,
      `${number.format(Math.round(row.rss_bytes / 1048576))} MiB`, row.started_at, "Unknown"]));
  if (sample.no_longer_observed?.length) status.append(node("p", `${number.format(sample.no_longer_observed.length)} previously sampled process${sample.no_longer_observed.length === 1 ? " is" : "es are"} absent from this OS sample. This is not a session completion event.`, "confidence-note"));
  if (sample.changed_count) status.append(node("p", `${number.format(sample.changed_count)} continuing process${sample.changed_count === 1 ? " has" : "es have"} changed CPU or RSS since the previous sample.`, "confidence-note"));
  if (sample.truncated) status.append(node("p", "Process rows are limited to 200; the matching set is incomplete.", "confidence-note"));
}

function cachedUsage() {
  const value = data?.cached_usage;
  return value?.schema === "monitoring.cached-usage.v1" ? value : null;
}

function cachedCount(value) {
  return Number.isSafeInteger(value) && value >= 0 ? number.format(value) : "Unknown";
}

function cachedUsd(value) {
  if (typeof value !== "string" || !/^-?\d+(?:\.\d+)?$/.test(value) || !Number.isFinite(Number(value))) return "Unknown";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(Number(value));
}

function cachedFileAge() {
  if (!Number.isFinite(cachedFileAgeAtReceipt)) return "Unknown age";
  const age = cachedFileAgeAtReceipt + Math.max(0, Date.now() - cachedUsageReceivedAt) / 1000;
  if (age < 0) return `modified time ${number.format(Math.round(Math.abs(age)))}s after last read`;
  if (age < 60) return `${number.format(Math.round(age))}s since file modification`;
  if (age < 3600) return `${number.format(Math.floor(age / 60))}m since file modification`;
  if (age < 86400) return `${number.format(Math.floor(age / 3600))}h since file modification`;
  return `${number.format(Math.floor(age / 86400))}d since file modification`;
}

function recordCachedUsageReceipt(snapshot) {
  const book = snapshot?.cached_usage?.schema === "monitoring.cached-usage.v1" ? snapshot.cached_usage : null;
  const sameBook = book && snapshot.identity === cachedUsageIdentity
    && book.sampled_at === cachedUsageSampledAt
    && book.file_modified_at === cachedUsageFileModifiedAt;
  if (sameBook) return;
  cachedUsageIdentity = book ? snapshot.identity : null;
  cachedUsageSampledAt = book?.sampled_at ?? null;
  cachedUsageFileModifiedAt = book?.file_modified_at ?? null;
  cachedUsageReceivedAt = Date.now();
  cachedFileAgeAtReceipt = Number.isFinite(book?.file_age_seconds) ? book.file_age_seconds : null;
}

function renderCodeburnCache() {
  const panel = $("#codeburn-cache-panel"), target = $("#codeburn-cache-table"), status = $("#codeburn-cache-status");
  const book = cachedUsage(), readStatus = data?.cached_usage_status || book?.read_status || null;
  panel.hidden = !book && !readStatus;
  if (panel.hidden) return;
  status.replaceChildren(); target.replaceChildren();
  $("#codeburn-cache-breakdown-controls").hidden = !book;
  const state = readStatus?.state || "unknown";
  const labels = { following: "Selected CodeBurn cache is readable", unavailable: "Selected CodeBurn cache is unavailable", unread: "Selected CodeBurn cache has not been read yet" };
  status.append(node("p", labels[state] || "CodeBurn cache read status is unknown.", state === "unavailable" ? "codeburn-cache-read unavailable" : "codeburn-cache-read"));
  if (!book) {
    status.append(node("p", state === "unavailable"
      ? "No prior cached summary is available. The current read failure does not mean usage was zero or that the provider was idle."
      : "No readable cached daily summary is available yet. This does not establish provider idleness.", "confidence-note"));
    return;
  }

  const sampledAt = book.sampled_at ? `${book.sampled_at.replace("T", " ").replace("Z", " UTC")}` : "unknown time";
  const modifiedAt = book.file_modified_at ? book.file_modified_at.replace("T", " ").replace("Z", " UTC") : "unknown time";
  const meta = [
    `Sampled ${sampledAt}`,
    `file modified ${modifiedAt}`,
    cachedFileAge(),
    `format ${book.format_version ?? "unknown"}`,
    `date basis ${book.date_basis === "codeburn-local-calendar" ? "CodeBurn local calendar" : "unknown"}`,
  ];
  status.append(node("p", meta.join(" · "), "codeburn-cache-meta"));
  if (state === "unavailable") {
    const successful = readStatus.last_success_at || (book.retained ? sampledAt : null);
    status.append(node("p", `A prior cached snapshot${successful ? ` last read ${successful.replace("T", " ").replace("Z", " UTC")}` : ""} remains visible after the failed read.`, "confidence-note"));
  }
  if (book.complete === false) status.append(node("p", "This CodeBurn cache is marked incomplete.", "confidence-note"));
  if (book.watermark_trusted === false) status.append(node("p", "The cache watermark is not trusted.", "confidence-note"));
  else if (book.watermark_trusted === null || book.watermark_trusted === undefined) status.append(node("p", "Cache watermark trust is unknown.", "confidence-note"));
  if (book.last_computed_date) status.append(node("p", `Last computed date ${book.last_computed_date} · source cache marker, not a live activity time.`, "confidence-note"));

  const viewSelect = $("#codeburn-cache-view");
  viewSelect.value = cachedView;
  const dimension = cachedView === "providers" ? "providers" : cachedView === "models" ? "models" : null;
  $("#codeburn-provider-filter-wrap").hidden = dimension !== "providers";
  $("#codeburn-model-filter-wrap").hidden = dimension !== "models";

  const allDays = Array.isArray(book.days) ? [...book.days].filter((day) => /^\d{4}-\d{2}-\d{2}$/.test(day?.date || "")).sort((a, b) => b.date.localeCompare(a.date)) : [];
  const range = $("#codeburn-cache-range"), dateWrap = $("#codeburn-cache-date-wrap"), date = $("#codeburn-cache-date");
  range.value = cachedRange;
  date.value = cachedExactDate;
  dateWrap.hidden = cachedRange !== "date";
  let days = allDays;
  if (cachedRange === "7" || cachedRange === "30") days = allDays.slice(0, Number(cachedRange));
  else if (cachedRange === "date") days = cachedExactDate ? allDays.filter((day) => day.date === cachedExactDate) : [];
  if (!days.length) {
    target.append(node("p", allDays.length ? "Choose a date with a cached daily row." : "No cached daily rows are available. An empty cache does not establish provider idleness.", "empty"));
    return;
  }
  const buckets = ["input", "output", "cache_read", "cache_write"];
  const makeDateCell = (day) => {
    const dateCell = node("span", day.date);
    if (day.carried === true) dateCell.append(node("small", "Carried summary", "codeburn-carried"));
    return dateCell;
  };
  const columns = ["Date · CodeBurn local calendar", ...buckets.map((key) => bucketNames[key]), "Calls", "Sessions", "Source-reported USD"];
  if (cachedView === "daily") {
    table("#codeburn-cache-table", columns, days.map((day) => {
      const tokens = day.tokens || {};
      return [makeDateCell(day), ...buckets.map((key) => cachedCount(tokens[key])), cachedCount(day.calls), cachedCount(day.sessions), cachedUsd(day.source_cost_usd)];
    }));
    return;
  }

  const noun = dimension === "providers" ? "provider" : "model";
  const filterSelect = dimension === "providers" ? $("#codeburn-provider-filter") : $("#codeburn-model-filter");
  const represented = days.map((day) => day.breakdowns?.[dimension]);
  const mapped = represented.filter(Array.isArray);
  const emptyMaps = mapped.filter((items) => items.length === 0).length;
  const unknownMaps = days.length - mapped.length;
  const aliases = [...new Set(mapped.flatMap((items) => items
    .map((item) => item?.id)
    .filter((id) => typeof id === "string" && id.length > 0)))].sort((a, b) => a.localeCompare(b));
  filterSelect.replaceChildren(node("option", `All ${noun}s`));
  filterSelect.options[0].value = "";
  for (const alias of aliases) {
    const option = node("option", alias);
    option.value = alias;
    filterSelect.append(option);
  }
  if (!aliases.includes(cachedDimensionFilters[dimension])) cachedDimensionFilters[dimension] = "";
  filterSelect.value = cachedDimensionFilters[dimension];
  status.append(node("p", `${noun[0].toUpperCase()}${noun.slice(1)} breakdown maps represented for ${number.format(mapped.length)} of ${number.format(days.length)} selected cached days · ${number.format(emptyMaps)} empty maps · ${number.format(unknownMaps)} unknown or unavailable maps. Empty arrays mean no rows are represented; they do not establish completeness or zero usage. This view is a marginal ${noun} rollup; it does not cross-filter the other dimension or intersect Monitoring event filters.`, "codeburn-cache-meta"));
  const selectedAlias = cachedDimensionFilters[dimension];
  const rows = [];
  for (const day of days) {
    const entries = day.breakdowns?.[dimension];
    if (!Array.isArray(entries)) {
      if (!selectedAlias) rows.push([makeDateCell(day), `Unknown ${noun} breakdown`, "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown"]);
      continue;
    }
    for (const item of entries) {
      const id = typeof item?.id === "string" && item.id.length ? item.id : `Unknown ${noun} identifier`;
      if (selectedAlias && id !== selectedAlias) continue;
      const tokens = item?.tokens || {};
      rows.push([makeDateCell(day), id, ...buckets.map((key) => cachedCount(tokens[key])), cachedCount(item?.calls), cachedCount(item?.sessions), cachedUsd(item?.source_cost_usd)]);
    }
  }
  if (!rows.length) {
    target.append(node("p", "No breakdown rows are represented for this selection. This does not establish zero activity.", "empty"));
    return;
  }
  table("#codeburn-cache-table", [columns[0], noun[0].toUpperCase() + noun.slice(1), ...columns.slice(1)], rows);
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
  const receiptSummaryCount = coverage.receipt_time_summary_records;
  const receiptScopeNote = Number.isSafeInteger(receiptSummaryCount) && receiptSummaryCount > 0
    ? ` ${number.format(receiptSummaryCount)} cumulative Claude CLI result summaries use capture receipt time, not call time. Date groups can include earlier work. Summary totals retain them.` : "";
  $("#scope").replaceChildren(node("span", `${number.format(coverage.matched_events)} of ${number.format(coverage.event_count)} events · explicitly supplied sources · refreshes every 2s${data.filters.state ? ` · state: ${data.filters.state}` : ""}.${receiptScopeNote}`), node("strong", synthetic ? "SYNTHETIC DATA PRESENT · FICTIONAL COSTS" : "METADATA ONLY · COVERAGE IS LIMITED"));
  $("#metrics").replaceChildren(
    metric("Observed sessions", number.format(summary.sessions), `${summary.active} running entities · ${summary.attention} waiting or blocked`, "⌘"),
    metric("Token volume", compact.format(summary.total_tokens), `${summary.calls} usage records · cache included once`, "◷"),
    metric("Estimated cost", cash(summary.costs.estimated.amount), `${summary.costs.estimated.events} records priced · not a subscription balance`, "≈"),
    metric("Billed cost", cash(summary.costs.billed.amount), `${summary.costs.billed.events} records with declared billing evidence`, "$"),
  );
  bars("#model-bars", data.groups.model, true); bars("#project-bars", data.groups.project);
  bars("#daily", data.groups.date, true); timeline();
  renderLedgerChanges();
  $("#token-legend").replaceChildren(...Object.entries(bucketNames).map(([key, text]) => {
    const item = node("span"); item.append(node("i", null, "bucket-" + key), document.createTextNode(text)); return item;
  }));
  const confidence = $("#confidence"); confidence.replaceChildren();
  for (const [label, value] of [["Estimated records", summary.costs.estimated.events], ["Billed records", summary.costs.billed.events], ["Unpriced records", summary.unknown_cost_events], ["Missing exchange rate", summary.unconverted_cost_events]]) {
    const row = node("div", null, "confidence-row"); row.append(node("span", label), node("strong", number.format(value))); confidence.append(row);
  }
  confidence.append(node("p", "Known amounts cover only their labeled records. Estimates and billed amounts are never added for the same event. Lifecycle evidence expires after 60 seconds.", "confidence-note"));
  renderInsights();
  renderSourceCoverage();
  renderInputHealth();
  renderEntityTable();
  renderClaudeResponses();
  renderCodexTurns();
  renderSelectedCache();
  renderOsProcesses();
  renderCodeburnActivity();
  renderUsage(); renderEvents();
  renderAppThreads();
  renderSessionComparison();
  const info = $("#currency-info"); info.replaceChildren();
  info.append(node("p", `Display currency: ${data.currency}. ${data.fx ? `USD-based conversion as of ${data.fx.as_of}, provenance ${data.fx.provenance}. This is a display conversion, not an invoice exchange rate.` : "No conversion rates supplied. Amounts in other currencies stay unconverted."}`));
  for (const source of summary.cost_provenance) info.append(node("p", `${source.basis} · ${source.currency} · ${source.provenance}`));
  if (!summary.cost_provenance.length) info.append(node("p", "No cost evidence or matching dated model prices for these usage records."));
  $("#footnote").textContent = `Read only · ${summary.tools} tool records · ${summary.tool_errors} tool errors · revision ${data.revision}${coverage.entities_truncated ? " · entity list limited to 500" : ""}`;
}

function recordLedgerChanges(next, requestedQuery) {
  const previous = data;
  if (!previous || previous.identity !== next.identity || requestedQuery !== lastSnapshotQuery ||
      next.revision < previous.revision) {
    ledgerChanges = [];
    ledgerChangeNote = !previous ? "Watching from the first successful snapshot. Earlier records are in Event stream."
      : previous.identity !== next.identity ? "Owner changed. Watching from this owner's first snapshot."
        : "Filter scope or ledger revision changed. Watching from this snapshot.";
  } else if (next.revision > previous.revision) {
    const earlier = new Map((previous.events || []).map((row) => [JSON.stringify([row.source, row.id]), row]));
    const visible = [];
    for (const row of next.events || []) {
      const prior = earlier.get(JSON.stringify([row.source, row.id]));
      if (prior && JSON.stringify(prior) === JSON.stringify(row)) continue;
      visible.push({ kind: prior ? "Revised" : "Added", row, observed_at: next.observed_at });
    }
    ledgerChanges = [...visible, ...ledgerChanges].slice(0, 8);
    ledgerChangeNote = visible.length
      ? `${visible.length} changed record${visible.length === 1 ? "" : "s"} visible in the latest snapshot. This list keeps the latest eight observations.`
      : "Ledger revision changed, but no changed record is visible in the latest 100 matching records.";
  }
  lastSnapshotQuery = requestedQuery;
}

function renderLedgerChanges() {
  const status = $("#ledger-changes-status"), root = $("#ledger-changes");
  if (paused || snapshotReadUnavailable) {
    status.textContent = `${paused ? "Browser refresh is paused" : "The Monitoring service is unavailable"}. Prior changes are retained; new activity is unknown.`;
  } else status.textContent = ledgerChangeNote;
  root.replaceChildren();
  if (!ledgerChanges.length) {
    root.append(node("p", "No changes observed by this browser under the current filters yet.", "empty"));
    return;
  }
  for (const change of ledgerChanges) {
    const row = change.row;
    const detail = row.kind === "usage"
      ? `${Object.values(row.tokens || {}).reduce((sum, value) => sum + value, 0)} recorded tokens`
      : row.kind === "status" ? `state ${row.state}`
        : row.kind === "tool" ? `tool ${row.tool} · ${row.outcome}` : row.kind;
    const item = node("li", null, "ledger-change");
    item.append(node("strong", `${change.kind} ${row.kind} · ${row.source} / ${row.session}`),
      node("span", `${detail} · source time ${row.timestamp} · observed ${change.observed_at}`));
    const history = node("button", "Inspect entity history", "ledger-change-inspect quiet");
    history.type = "button";
    history.setAttribute("aria-label", `Inspect history for ${row.entity} in ${row.session}`);
    history.addEventListener("click", () => inspect(row));
    item.append(history);
    root.append(item);
  }
}

async function refresh() {
  if (paused || busy) return;
  busy = true;
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 8000);
  try {
    const requestedQuery = query().toString();
    const response = await fetch(`/api/v1/snapshot?${requestedQuery}`, { signal: controller.signal, cache: "no-store" });
    if (!response.ok) throw new Error(`Read failed (${response.status}). Check the filters and ledger limits.`);
    const value = await response.json();
    if (value.schema !== "monitoring.snapshot.v1") throw new Error("The Monitoring service returned an unsupported data format.");
    if (data && value.identity !== data.identity) collapsedHierarchyKeys.clear();
    recordLedgerChanges(value, requestedQuery);
    recordCachedUsageReceipt(value);
    recordCodeburnActivityReceipt(value);
    data = value; lastSuccess = Date.now(); snapshotReadUnavailable = false;
    $("#notice").hidden = data.source_status.state !== "unavailable";
    $("#notice").textContent = data.source_status.error || "";
    $("#connection").textContent = data.source_status.state === "following" ? "Following source" : data.source_status.state === "unavailable" ? "Source unavailable" : "Ledger available";
    $("#connection").classList.toggle("offline", data.source_status.state === "unavailable");
    $("#updated").textContent = `Updated ${data.observed_at.slice(11, 19)} UTC`;
    render();
  } catch (error) {
    snapshotReadUnavailable = true;
    $("#notice").hidden = false;
    $("#notice").textContent = `${error.message} ${data ? "Last observations remain visible and may be stale." : "No data loaded."}`;
    $("#connection").textContent = "Disconnected"; $("#connection").classList.add("offline");
    if (data) {
      // A failed browser read must not leave a running badge looking current
      for (const row of data.entities) if (!["completed", "failed", "cancelled"].includes(row.state)) { row.state = "unknown"; row.stale = true; }
      retainInsights("The Monitoring service is unavailable. Showing previously recorded quota observations.");
      data.summary.active = 0; data.summary.attention = 0; render();
    }
  } finally { clearTimeout(timer); busy = false; }
}

$$("[data-view]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
$("#filters").addEventListener("change", (event) => {
  markEntityHistoryFiltersChanged();
  renderSessionComparison();
  if (event.target?.name === "since" || event.target?.name === "until") $("#report-period").value = "custom";
  refresh(); if (view === "report") loadReport();
});
$("#filters").addEventListener("submit", (event) => event.preventDefault());
$("#filters").addEventListener("reset", () => { markEntityHistoryFiltersChanged(true); $("#state-filter").value = ""; setTimeout(() => { $("#report-period").value = "custom"; renderSessionComparison(); refresh(); if (view === "report") loadReport(); }, 0); });
$("#report-period").addEventListener("change", () => applyReportPeriod($("#report-period").value));
$("#download-report").addEventListener("click", downloadDisplayedReport);
$("#download-report-csv").addEventListener("click", downloadDisplayedReportCsv);
$("#state-filter").addEventListener("change", () => { refresh(); if (view === "report") loadReport(); });
$("#group").addEventListener("change", () => { if (data) renderUsage(); });
$("#codeburn-cache-range").addEventListener("change", () => {
  cachedRange = $("#codeburn-cache-range").value;
  if (data) renderCodeburnCache();
});
$("#codeburn-cache-date").addEventListener("change", () => {
  cachedExactDate = $("#codeburn-cache-date").value;
  if (data) renderCodeburnCache();
});
$("#codeburn-cache-view").addEventListener("change", () => {
  cachedView = $("#codeburn-cache-view").value;
  if (data) renderCodeburnCache();
});
$("#codeburn-provider-filter").addEventListener("change", () => {
  cachedDimensionFilters.providers = $("#codeburn-provider-filter").value;
  if (data) renderCodeburnCache();
});
$("#codeburn-model-filter").addEventListener("change", () => {
  cachedDimensionFilters.models = $("#codeburn-model-filter").value;
  if (data) renderCodeburnCache();
});
$("#pause").addEventListener("click", () => {
  paused = !paused; $("#pause").textContent = paused ? "Resume refresh" : "Pause refresh";
  if (paused) {
    cancelComparison();
    sessionListController?.abort();
    $("#connection").textContent = "Refresh paused"; $("#connection").classList.add("offline");
    if (view === "report") cancelReport();
    if (entityHistory?.pending) cancelEntityHistory("Entity history read canceled because refresh was paused. Loaded pages are retained; resume and retry explicitly.");
    else if (entityHistory) {
      const restartReason = entityHistoryRestartReason();
      entityHistory.message = restartReason
        ? `${restartReason} Refresh is paused; loaded pages are retained.`
        : "Refresh is paused. Loaded entity pages are retained; resume before loading more or restarting history.";
      renderEntityHistory();
    }
    if (data) {
      for (const row of data.entities) if (!["completed", "failed", "cancelled"].includes(row.state)) { row.state = "unknown"; row.stale = true; }
      data.summary.active = 0; data.summary.attention = 0;
    }
    retainInsights("Dashboard refresh is paused. Showing previously recorded quota observations.");
    if (view === "report") {
      const last = reportData ? ` Last successful report observed at ${reportData.observed_at || "unknown time"} remains visible and may not match current filters.` : " No report observations have loaded yet.";
      $("#report-status").textContent = `Report refresh is paused.${last}`;
    }
    if (data) render();
  } else {
    if (entityHistory) {
      const restartReason = entityHistoryRestartReason();
      entityHistory.message = restartReason
        ? `${restartReason} Refresh resumed.`
        : "Refresh resumed. Loaded pages are retained; choose Load more events or Restart history to continue.";
      renderEntityHistory();
    }
    refresh(); if (view === "report") loadReport();
  }
});
$("#load-more-events").addEventListener("click", () => {
  if (!entityHistory || paused || entityHistory.pending || entityHistory.filtersChanged || entityHistory.nextOffset === null) return;
  fetchEntityHistoryPage(entityHistory.nextOffset);
});
$("#restart-entity-history").addEventListener("click", () => {
  if (!entityHistory || !entity || paused || entityHistory.pending) return;
  startEntityHistory();
});
$("#compare-sessions").addEventListener("click", compareSessions);
$("#session-list-form").addEventListener("submit", (event) => { event.preventDefault(); loadSessionChoices(); });
$("#session-list-more").addEventListener("click", () => { if (sessionList?.next_offset != null) loadSessionChoices(sessionList.next_offset); });
for (const selector of ["#compare-first", "#compare-second"]) $(selector).addEventListener("change", () => {
  cancelComparison(); comparison = null; comparisonMessage = "Selection changed. Choose Compare sessions to read this pair."; renderSessionComparison();
});
$("#clear-entity").addEventListener("click", () => {
  cancelEntityHistory(); entity = null; entityHistory = null;
  $("#event-selection").textContent = ""; $("#clear-entity").hidden = true;
  $("#subtitle").textContent = titles.events[1]; renderEvents();
});
document.addEventListener("keydown", (event) => {
  if (!event.ctrlKey && !event.metaKey && !event.altKey && !["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName) && ["1", "2", "3", "4", "5", "6"].includes(event.key)) showView(Object.keys(titles)[Number(event.key) - 1]);
});
window.addEventListener("hashchange", () => showView(location.hash.slice(1) || "overview"));
showView(location.hash.slice(1) || "overview");
refresh();
setInterval(() => {
  if (data) updateLifecycleEvidenceAges();
  if (data) renderAppThreads();
  if (paused && data) {
    retainInsights("Dashboard refresh is paused. Showing previously recorded quota observations.");
    renderInsights();
    renderSourceCoverage();
    renderInputHealth();
    renderClaudeResponses();
    renderCodexTurns();
    renderSelectedCache();
    renderOsProcesses();
    renderCodeburnCache();
    renderCodeburnActivity();
  } else { refresh(); if (view === "report") loadReport(false); }
}, 2000);

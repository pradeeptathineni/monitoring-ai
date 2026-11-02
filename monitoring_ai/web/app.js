"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const number = new Intl.NumberFormat("en-US");
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const bucketNames = { input: "Input", cache_read: "Cache read", cache_write: "Cache write", output: "Output" };
const titles = { overview: ["Every session. One place.", "Follow the work, understand the usage, keep the evidence."],
  sessions: ["See the whole working tree.", "Sessions, agents and workers, with evidence behind every state."],
  usage: ["Understand every token.", "Compare models and surfaces without losing cost qualifications."],
  events: ["The story behind the numbers.", "A chronological ledger of explicitly supplied activity."] };
let data, paused = false, busy = false, view = "overview", entity = null;
let lastSuccess = 0;

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
  view = next;
  $$(".view").forEach((section) => { section.hidden = section.id !== next; });
  $$(".nav, .mobile-nav button").forEach((button) => button.classList.toggle("active", button.dataset.view === next));
  $("#breadcrumb").textContent = { overview: "Overview", sessions: "Sessions & workers", usage: "Usage & costs", events: "Event stream" }[next];
  $("#title").textContent = titles[next][0];
  $("#subtitle").textContent = titles[next][1];
  location.hash = next;
}

function query() {
  const values = new URLSearchParams();
  new FormData($("#filters")).forEach((value, key) => { if (value) values.set(key, value); });
  if ($("#state-filter").value) values.set("state", $("#state-filter").value);
  return values;
}

function metric(label, value, note, symbol) {
  const card = node("article", null, "metric");
  const heading = node("div", label, "metric-label");
  heading.append(node("span", symbol, "symbol"));
  card.append(heading, node("div", value, "value"), node("div", note, "metric-note"));
  return card;
}

function fillFacets() {
  for (const dimension of ["provider", "model", "surface", "project"]) {
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
      data.summary.active = 0; data.summary.attention = 0; render();
    }
  } finally { clearTimeout(timer); busy = false; }
}

$$("[data-view]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
$("#filters").addEventListener("change", refresh);
$("#filters").addEventListener("submit", (event) => event.preventDefault());
$("#filters").addEventListener("reset", () => { $("#state-filter").value = ""; setTimeout(refresh, 0); });
$("#state-filter").addEventListener("change", refresh);
$("#group").addEventListener("change", () => { if (data) renderUsage(); });
$("#pause").addEventListener("click", () => {
  paused = !paused; $("#pause").textContent = paused ? "Resume refresh" : "Pause refresh";
  if (paused) { $("#connection").textContent = "Refresh paused"; $("#connection").classList.add("offline"); }
  else refresh();
});
$("#clear-entity").addEventListener("click", () => { entity = null; $("#event-selection").textContent = ""; $("#clear-entity").hidden = true; renderEvents(); });
document.addEventListener("keydown", (event) => {
  if (!event.ctrlKey && !event.metaKey && !event.altKey && !["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName) && ["1", "2", "3", "4"].includes(event.key)) showView(Object.keys(titles)[Number(event.key) - 1]);
});
window.addEventListener("hashchange", () => showView(location.hash.slice(1) || "overview"));
showView(location.hash.slice(1) || "overview");
refresh();
setInterval(refresh, 2000);

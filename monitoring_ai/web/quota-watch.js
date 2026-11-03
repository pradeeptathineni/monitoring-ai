"use strict";

// This preference changes presentation only; it grants no collection or spending access
(() => {
  const percent = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 });
  const KEY = "monitoring-ai.quota-watch.v1";
  const select = document.querySelector("#quota-watch-window");
  const threshold = document.querySelector("#quota-watch-percent");
  const status = document.querySelector("#quota-watch-status");
  const banner = document.querySelector("#quota-watch-banner");
  const windowsFor = data => {
    const rows = data?.insights?.quotas?.windows;
    return Array.isArray(rows) ? rows.filter(row => row && ["source", "provider", "account", "id", "provenance"].every(field => typeof row[field] === "string" && row[field])) : [];
  };
  const key = row => JSON.stringify([row.source, row.provider, row.account, row.id, row.window_minutes, row.provenance]);
  const title = row => [row.provider, row.account, row.label, row.source, `${row.window_minutes ?? "unknown"} min`].join(" · ");
  let preference = null, note = "", context = null, optionsSignature = null;

  function read() {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw === null) { preference = null; return; }
      if (raw.length > 4000) throw Error();
      const row = JSON.parse(raw);
      if (!row || Object.keys(row).sort().join(",") !== "owner,remaining,schema,window" ||
          row.schema !== "monitoring.quota-watch.v1" || typeof row.owner !== "string" || !row.owner ||
          row.owner.length > 200 || typeof row.window !== "string" || row.window.length > 2000 ||
          !Number.isFinite(row.remaining) || row.remaining < 0 || row.remaining > 100) throw Error();
      preference = row;
      threshold.value = String(row.remaining);
    } catch {
      preference = null;
      note = "Saved reserve watch could not be read. No watch is active until you save a valid choice.";
    }
  }

  function evaluate(data, paused, unavailable) {
    if (!preference) return { state: "off", message: "Reserve watch is off." };
    const unknown = reason => ({ state: "unknown", message: `Reserve watch unknown: ${reason}.` });
    if (!data) return unknown("waiting for an owner snapshot");
    if (data.identity !== preference.owner) return unknown("the Monitoring owner changed; review and save again");
    const quota = data?.insights?.quotas;
    const matches = windowsFor(data).filter(row => key(row) === preference.window);
    if (matches.length !== 1) return unknown("the saved quota window is missing or ambiguous");
    const row = matches[0], age = Date.now() - Date.parse(row.captured_at);
    const reset = row.resets_at == null ? null : Date.parse(row.resets_at);
    if (paused || unavailable || document.hidden || quota.state !== "ready" ||
        !["following", "static"].includes(quota.read_status?.state) || row.freshness !== "current" ||
        !Number.isFinite(age) || age < 0 || age > 300000 ||
        (reset !== null && (!Number.isFinite(reset) || reset <= Date.now())) ||
        !Number.isFinite(row.remaining_percent) || row.remaining_percent < 0 || row.remaining_percent > 100) {
      return unknown("a current readable quota observation is required");
    }
    const reached = row.remaining_percent <= preference.remaining;
    return { state: reached ? "reached" : "above", message:
      `${title(row)}: ${percent.format(row.remaining_percent)}% remaining is ${reached ? "at or below" : "above"} the ${percent.format(preference.remaining)}% reserve. Source observation ${row.captured_at}.` };
  }

  function update(data, paused, unavailable) {
    context = { data, paused, unavailable };
    const windows = windowsFor(data);
    const selected = select.value || preference?.window || "";
    const signature = JSON.stringify(windows.map(row => [key(row), title(row)]));
    if (signature !== optionsSignature) {
      select.replaceChildren(new Option("Choose a supplied quota window", ""));
      const seen = new Set();
      for (const row of windows) {
        const identity = key(row);
        if (!seen.has(identity)) { select.append(new Option(title(row), identity)); seen.add(identity); }
      }
      if (selected && !seen.has(selected)) {
        const missing = new Option("Saved window unavailable; choose another window", selected);
        missing.disabled = true; select.append(missing);
      }
      select.value = selected;
      optionsSignature = signature;
    }
    const result = evaluate(data, paused, unavailable);
    const message = [note, result.message].filter(Boolean).join(" ");
    if (status.textContent !== message) status.textContent = message;
    banner.hidden = !["reached", "unknown"].includes(result.state);
    banner.dataset.state = result.state;
    if (banner.textContent !== result.message) banner.textContent = result.message;
    return result;
  }

  document.querySelector("#quota-watch-save").addEventListener("click", () => {
    const value = threshold.value.trim(), remaining = Number(value);
    const rows = windowsFor(context?.data).filter(row => key(row) === select.value);
    if (!value || !Number.isFinite(remaining) || remaining < 0 || remaining > 100 || rows.length !== 1 ||
        typeof context?.data?.identity !== "string" || !context.data.identity) {
      note = "Not saved. Choose one supplied window and a reserve from 0 to 100 percent.";
    } else {
      preference = { schema: "monitoring.quota-watch.v1", owner: context.data.identity, window: select.value, remaining };
      try { localStorage.setItem(KEY, JSON.stringify(preference)); note = "Saved in this browser for this owner and exact quota window."; }
      catch { note = "Browser storage is unavailable. This reserve watch applies only to this page."; }
    }
    if (context) update(context.data, context.paused, context.unavailable);
  });
  document.querySelector("#quota-watch-disable").addEventListener("click", () => {
    preference = null;
    try { localStorage.removeItem(KEY); note = "Reserve watch disabled."; }
    catch { note = "Disabled on this page. Browser storage is unavailable; an earlier saved choice may remain."; }
    if (context) update(context.data, context.paused, context.unavailable);
  });
  window.addEventListener("storage", event => {
    if (event.key !== KEY && event.key !== null) return;
    note = ""; read(); optionsSignature = null;
    if (context) update(context.data, context.paused, context.unavailable);
  });
  read();
  window.monitoringQuotaWatch = { update };
})();

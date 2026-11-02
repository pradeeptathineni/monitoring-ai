"use strict";

const STORAGE_KEY = "monitoring-ai.navigation.collapsed.v1";
const shell = document.getElementById("monitoring-workspace");
const toggle = document.getElementById("navigation-toggle");
const status = document.getElementById("navigation-status");
const primary = document.getElementById("primary-navigation");
const mobile = window.matchMedia?.("(max-width: 860px)");
let storageAvailable = true;

function setStatus(message = "") {
  status.textContent = message;
  status.hidden = !message;
}

function savedMode() {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "collapsed" || value === "expanded" ? value : "expanded";
  } catch {
    storageAvailable = false;
    setStatus("Navigation preference storage is unavailable. The toggle still works for this page.");
    return "expanded";
  }
}

function applyMode(mode, { persist = false, announceFailure = false } = {}) {
  const collapsed = mode === "collapsed";
  shell.dataset.navigation = collapsed ? "collapsed" : "expanded";
  toggle.setAttribute("aria-expanded", String(!collapsed));
  toggle.setAttribute("aria-label", collapsed ? "Expand navigation" : "Collapse navigation");
  toggle.title = collapsed ? "Expand navigation" : "Collapse navigation";
  toggle.querySelector("span").textContent = collapsed ? "›" : "‹";
  if (persist && storageAvailable) {
    try {
      window.localStorage.setItem(STORAGE_KEY, mode);
      setStatus("");
    } catch {
      storageAvailable = false;
      if (announceFailure) setStatus("Could not save navigation preference. This choice is active for this page only.");
    }
  } else if (persist && announceFailure) {
    setStatus("Navigation preference storage is unavailable. This choice is active for this page only.");
  }
}

toggle.addEventListener("click", () => {
  applyMode(shell.dataset.navigation === "collapsed" ? "expanded" : "collapsed", {
    persist: true,
    announceFailure: true,
  });
});

window.addEventListener("storage", (event) => {
  if (event.key !== STORAGE_KEY && event.key !== null) return;
  if (event.newValue === "collapsed" || event.newValue === "expanded") applyMode(event.newValue);
  else if (event.newValue === null) applyMode("expanded");
  else setStatus("Another tab saved an invalid navigation preference; the current layout remains active.");
});

function reveal(view) {
  if (!mobile?.matches) return;
  const active = primary.querySelector(`[data-view="${CSS.escape(view)}"]`);
  active?.scrollIntoView({ block: "nearest", inline: "nearest" });
  if (view === "settings") {
    document.querySelector('#section-navigation [aria-current="page"]')
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
}

window.monitoringNavigation = { reveal };
window.addEventListener("monitoring:navigate", (event) => reveal(event.detail?.view));
mobile?.addEventListener?.("change", () => reveal(primary.querySelector('[aria-current="page"]')?.dataset.view));
applyMode(savedMode());
reveal(primary.querySelector('[aria-current="page"]')?.dataset.view);

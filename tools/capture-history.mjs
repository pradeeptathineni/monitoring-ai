// Capture scoped entity history from a temporary fictional producer and ledger.
import { chromium, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const temporary = await mkdtemp(path.join(tmpdir(), "monitoring-history-"));
const input = path.join(temporary, "fictional.jsonl");
const rows = [];
const start = Date.now() - 24 * 60 * 60 * 1000;
for (let i = 0; i < 270; i++) {
  const target = i < 145;
  const row = { schema: "monitoring.event.v1", source: "synthetic", id: `usage-${i}`,
    session: target ? "example-session" : "other-session", entity: target ? "example-worker" : "other-session",
    parent: target ? "example-session" : null, entity_type: target ? "worker" : "session",
    timestamp: new Date(start + i * 60000).toISOString(), kind: "usage", provider: "example",
    model: "fictional-model", surface: "example-export", project: "example-project",
    tokens: { input: 1200 + i, cache_read: 900, cache_write: 0, output: 180 + i } };
  if (i % 3) row.cost = { amount: "0.024", currency: "USD", basis: i % 2 ? "estimated" : "billed", provenance: "fictional-evidence" };
  rows.push(row);
}
let server, browser;
try {
  await writeFile(input, rows.map((row) => JSON.stringify(row)).join("\n") + "\n", { mode: 0o600 });
  server = spawn(process.env.PYTHON || "python3", ["-m", "monitoring_ai", "serve", "--file", input,
    "--db", path.join(temporary, "fictional.sqlite"), "--port", "0"],
  { cwd: root, stdio: ["ignore", "pipe", "pipe"] });
  const base = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Temporary history server did not start")), 15000);
    const lines = createInterface({ input: server.stdout });
    lines.once("line", (line) => {
      clearTimeout(timer); lines.close();
      const match = line.match(/http:\/\/127\.0\.0\.1:\d+/);
      match ? resolve(match[0]) : reject(new Error("Unexpected history server response"));
    });
    server.once("exit", () => { clearTimeout(timer); reject(new Error("Temporary history server exited")); });
  });
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(base + "/#sessions");
  const snapshot = await (await page.request.get(base + "/api/v1/snapshot")).json();
  const capabilities = await (await page.request.get(base + "/api/v1/capabilities")).json();
  if (!capabilities.read_only || !capabilities.event_filters?.includes("entity")) {
    throw new Error("Temporary owner must support scoped read-only history");
  }
  if (snapshot.events.length !== 100 || snapshot.events.some((row) => row.entity === "example-worker")) {
    throw new Error("Fictional target must be outside the latest snapshot events");
  }
  await page.getByRole("button", { name: "↳ example-worker", exact: true }).click();
  await expect(page.locator("#event-table tbody tr")).toHaveCount(100);
  await page.locator("#load-more-events").click();
  await expect(page.locator("#event-table tbody tr")).toHaveCount(145);
  await expect(page.locator("#entity-history-count")).toContainText("145");
  await page.screenshot({ path: path.join(root, "docs", "entity-history.png"), animations: "disabled" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("#entity-history-panel").scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(root, "docs", "entity-history-mobile.png"), animations: "disabled" });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  if (overflow || errors.length) throw new Error("History capture had overflow or browser errors");
  const receipt = { schema: "monitoring.history-demo.v1", version: capabilities.version, synthetic: true,
    source_records: 270, target_records: 145, loaded_records: 145, pages: 2,
    latest_snapshot_excludes_target: true, mobile_overflow: false, page_errors: errors.length };
  await writeFile(path.join(root, "docs", "entity-history.json"), JSON.stringify(receipt, null, 2) + "\n");
  console.log(JSON.stringify(receipt));
} finally {
  await browser?.close();
  if (server && server.exitCode === null) {
    await new Promise((resolve) => { server.once("exit", resolve); server.kill("SIGTERM"); });
  }
  await rm(temporary, { recursive: true, force: true });
}

// Reproduce the public gallery using only fictional files and a private temporary server.
import { chromium } from "@playwright/test";
import { spawn, execFile } from "node:child_process";
import { mkdtemp, mkdir, writeFile, copyFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createInterface } from "node:readline";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { presets, contrastRatio } from "../monitoring_ai/web/theme/index.js";

const execute = promisify(execFile);
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const temporary = await mkdtemp(path.join(tmpdir(), "monitoring-gallery-"));
const output = path.join(root, "docs", "gallery");
const python = process.env.PYTHON || "python3";
const events = path.join(temporary, "fictional.jsonl");
const manifest = path.join(temporary, "fictional-sources.json");
const quota = path.join(temporary, "fictional-quotas.json");
let server, browser;

function fail(message) { throw new Error(message); }
function rgbHex(value) {
  const components = value.match(/[\d.]+/g)?.slice(0, 3).map(Number);
  if (components?.length !== 3) fail("Unexpected computed color");
  return "#" + components.map((n) => Math.round(n).toString(16).padStart(2, "0")).join("");
}

try {
  await mkdir(output, { recursive: true });
  await execute(python, ["-m", "monitoring_ai", "demo", events], { cwd: root });
  await execute(python, ["-m", "monitoring_ai", "sources", "create", "--output", manifest,
    "--file", events, "--id", "fictional-gallery", "--format", "normalized"], { cwd: root });
  const book = await execute(python, ["-c", "import json; from monitoring_ai.demo import demo_quotas; print(json.dumps(demo_quotas()))"], { cwd: root });
  await writeFile(quota, book.stdout, { mode: 0o600 });
  server = spawn(python, ["-m", "monitoring_ai", "serve", "--sources", manifest, "--quota-file", quota,
    "--db", path.join(temporary, "fictional.sqlite"), "--port", "0"], { cwd: root, stdio: ["ignore", "pipe", "pipe"] });
  let diagnostics = "";
  server.stderr.on("data", (chunk) => { diagnostics = (diagnostics + chunk).slice(-4000); });
  const base = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("Temporary gallery server did not start")), 15000);
    const lines = createInterface({ input: server.stdout });
    lines.once("line", (line) => {
      clearTimeout(timeout); lines.close();
      const match = line.match(/http:\/\/127\.0\.0\.1:\d+/);
      match ? resolve(match[0]) : reject(new Error("Unexpected gallery server response"));
    });
    server.once("exit", (code) => {
      clearTimeout(timeout);
      reject(new Error(`Temporary gallery server exited (${code}): ${diagnostics}`));
    });
  });
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(base + "/#settings");
  await page.locator("#appearance-preset").waitFor({ state: "attached" });
  await page.waitForFunction(() => document.querySelectorAll("#metrics .metric").length === 4);

  async function choose(id) {
    await page.locator('[data-view="settings"]:visible').first().click();
    if (!(await page.locator("#appearance-settings").evaluate((element) => element.open))) {
      await page.locator("#appearance-settings summary").click();
    }
    await page.selectOption("#appearance-preset", id);
    await page.waitForFunction((expected) => document.documentElement.dataset.theme === expected, id);
  }

  const captures = [];
  for (const preset of presets) {
    await choose(preset.id);
    await page.locator('[data-view="overview"]:visible').first().click();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.evaluate(() => document.fonts.ready);
    if (await page.locator("#appearance-controls").isVisible()) fail("Appearance is visible in Overview");
    if (await page.locator("#coverage-setup").isVisible()) fail("Configuration is visible in Overview");
    const rendered = await page.evaluate(() => ({
      id: document.documentElement.dataset.theme,
      scheme: document.documentElement.style.colorScheme,
      background: getComputedStyle(document.body).backgroundColor,
      text: getComputedStyle(document.body).color,
      overflow: document.documentElement.scrollWidth > innerWidth,
    }));
    if (rendered.id !== preset.id || rendered.scheme !== preset.scheme || rendered.overflow) fail("Incorrect rendered preset");
    if (rgbHex(rendered.background) !== preset.tokens.surfaceCanvas || rgbHex(rendered.text) !== preset.tokens.textPrimary) fail("Theme tokens did not reach the dashboard");
    const contrast = contrastRatio(rgbHex(rendered.text), rgbHex(rendered.background));
    if (contrast < 4.5) fail("Insufficient rendered body contrast");
    const filename = `${preset.id}.png`;
    await page.screenshot({ animations: "disabled", path: path.join(output, filename) });
    captures.push({ preset: preset.id, label: preset.label, file: filename, scheme: rendered.scheme,
      canvas: rgbHex(rendered.background), text: rgbHex(rendered.text), body_contrast: Number(contrast.toFixed(2)) });
  }

  await choose("neon1-light");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ animations: "disabled", path: path.join(output, "settings.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await choose("forest-light");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ animations: "disabled", path: path.join(output, "settings-mobile.png") });
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) fail("Settings mobile overflow");
  await page.locator('[data-view="overview"]:visible').first().click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ animations: "disabled", path: path.join(output, "overview-mobile.png") });
  const panel = page.locator("article").filter({ has: page.locator("#source-coverage") });
  await panel.screenshot({ animations: "disabled", path: path.join(output, "coverage-mobile.png") });
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) fail("Overview mobile overflow");
  if (errors.length) fail(errors.join("; "));

  const version = (await execute(python, ["-m", "monitoring_ai", "--version"], { cwd: root })).stdout.trim();
  const receipt = { schema: "monitoring.demo-gallery.v1", version, synthetic: true, view: "overview",
    viewport: { width: 1440, height: 1000 }, settings_separate: true, presets: captures,
    mobile_overflow: false, page_errors: errors.length };
  await writeFile(path.join(output, "gallery.json"), JSON.stringify(receipt, null, 2) + "\n");
  const markdown = ["# Dashboard theme gallery", "", "All six images use the same fictional data and Overview layout. Appearance and source configuration live in Settings. No Deep screens, personal usage or account observations are included.", "",
    ...captures.flatMap((item) => [`## ${item.label}`, "", `![${item.label} fictional Monitoring dashboard](${item.file})`, ""]),
    "## Settings and mobile", "", "[Settings desktop](settings.png) · [Settings phone](settings-mobile.png) · [Overview phone](overview-mobile.png) · [Coverage phone](coverage-mobile.png)", "",
    "[Verification receipt](gallery.json) records rendered scheme, semantic colors and body contrast. Checks cover intended presets, hidden Overview configuration, no page errors and mobile overflow. This is representative synthetic rendering, not complete accessibility or live-provider qualification.", "",
    "Reproduce with Node 24 and the development dependencies installed: `node tools/capture-gallery.mjs`. It starts and stops its own temporary synthetic loopback server and uses an independent headless browser. No account or network source is contacted.", ""];
  await writeFile(path.join(output, "README.md"), markdown.join("\n"));
  await copyFile(path.join(output, "neon1-dark.png"), path.join(root, "docs", "observatory.png"));
  await copyFile(path.join(output, "neon1-dark.png"), path.join(root, "docs", "theme-dark.png"));
  await copyFile(path.join(output, "neon1-light.png"), path.join(root, "docs", "theme-light.png"));
  await copyFile(path.join(output, "settings-mobile.png"), path.join(root, "docs", "theme-mobile.png"));
  await copyFile(path.join(output, "overview-mobile.png"), path.join(root, "docs", "mobile.png"));
  console.log(JSON.stringify(receipt));
} finally {
  await browser?.close();
  if (server && server.exitCode === null) {
    await new Promise((resolve) => { server.once("exit", resolve); server.kill("SIGTERM"); });
  }
  await rm(temporary, { recursive: true, force: true });
}

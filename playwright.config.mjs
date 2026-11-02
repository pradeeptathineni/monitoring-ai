import { defineConfig } from "@playwright/test";
import os from "node:os";
import path from "node:path";
import { randomUUID } from "node:crypto";

const db = path.join(os.tmpdir(), `monitoring-browser-${randomUUID()}.sqlite`);
export default defineConfig({
  testDir: "tests/browser",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:15274", headless: true },
  webServer: {
    command: `python3 -m monitoring_ai serve --demo --db "${db}" --port 15274`,
    url: "http://127.0.0.1:15274/health",
    reuseExistingServer: false,
    timeout: 15000,
  },
});

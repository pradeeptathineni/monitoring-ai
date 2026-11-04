import { defineConfig } from '@playwright/test';
import base from './playwright.config.mjs';

// Keep the explicit counterfactual rate-book trial independent of ordinary owners
export default defineConfig({
  ...base,
  testDir: 'tests/scenario-browser',
  webServer: {
    ...base.webServer,
    command: base.webServer.command + ' --scenario-pricing examples/scenario-pricing.json --scenario-label fictional-rates',
  },
});

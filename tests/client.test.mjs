import assert from "node:assert/strict";
import test from "node:test";
import { decodeReport, decodeSnapshot, monitoringOwner } from "../integrations/deep-ai/client.ts";

const metrics = { calls: 1, tokens: { input: 10, cache_read: 0, cache_write: 0, output: 2 },
  total_tokens: 12, costs: { estimated: { amount: null, events: 0 }, billed: { amount: "0", events: 1 } },
  unknown_cost_events: 0, unconverted_cost_events: 0, tools: 0, tool_errors: 0 };
const snapshot = { schema: "monitoring.snapshot.v1", identity: "owner-one", revision: 1,
  observed_at: "2026-11-02T20:00:00Z", currency: "USD",
  coverage: { scope: "explicit files", event_count: 1, matched_events: 1, entities_truncated: false },
  source_status: { state: "static", last_success_at: null, error: null },
  summary: { ...metrics, sessions: 1, entities: 1, active: 0, attention: 0 }, entities: [] };

function managedInputs() {
  return { schema: "monitoring.inputs.v1", configured: true,
    inputs: [
      { id: "normalized-a", format: "normalized", project: "app", state: "following",
        last_attempt_at: "2026-11-02T20:00:00Z", last_success_at: "2026-11-02T19:59:59Z",
        records_seen: 2, added: 1, replayed: 1, error: null },
      { id: "native-b", format: "codex", project: null, state: "unavailable",
        last_attempt_at: "2026-11-02T20:00:00Z", last_success_at: null,
        records_seen: null, added: 0, replayed: 0, error: "Input unavailable." },
      { id: "native-c", format: "claude", project: "app", state: "unread",
        last_attempt_at: null, last_success_at: null, records_seen: null,
        added: 0, replayed: 0, error: null },
    ], healthy_inputs: 1, unavailable_inputs: 1, unread_inputs: 1 };
}

test("unavailable amounts stay null and malformed costs refuse", () => {
  assert.equal(decodeSnapshot(snapshot).summary.costs.estimated.amount, null);
  const broken = structuredClone(snapshot);
  broken.summary.costs.billed.amount = "NaN";
  assert.throws(() => decodeSnapshot(broken), /incompatible/);
});

test("optional managed-input health is strict and remains backward compatible", () => {
  assert.equal(decodeSnapshot(snapshot).identity, "owner-one"); // Older owners omit inputs.
  const value = structuredClone(snapshot);
  value.inputs = managedInputs();
  value.source_status.degraded = true;
  assert.equal(decodeSnapshot(value).inputs.unavailable_inputs, 1);
  const observerPostRead = structuredClone(value);
  observerPostRead.inputs.inputs[0].last_success_at = "2026-11-02T20:00:01Z";
  assert.equal(decodeSnapshot(observerPostRead).inputs.inputs[0].last_success_at, "2026-11-02T20:00:01Z");
  const nextAttemptInFlight = structuredClone(value);
  nextAttemptInFlight.inputs.inputs[0].last_attempt_at = "2026-11-02T20:00:02Z";
  nextAttemptInFlight.inputs.inputs[0].last_success_at = "2026-11-02T20:00:01Z";
  assert.equal(decodeSnapshot(nextAttemptInFlight).inputs.inputs[0].last_attempt_at, "2026-11-02T20:00:02Z");
  for (const mutate of [
    (inputs) => { inputs.inputs[0].path = "/private/source.jsonl"; },
    (inputs) => { inputs.inputs[0].id = "../private"; },
    (inputs) => { inputs.inputs[1].error = "/private/secret"; },
    (inputs) => { inputs.healthy_inputs = 2; },
    (inputs) => { inputs.inputs[0].records_seen = 3; },
  ]) {
    const malformed = structuredClone(value);
    mutate(malformed.inputs);
    assert.throws(() => decodeSnapshot(malformed), /incompatible/);
  }
  assert.throws(() => decodeSnapshot({ ...value, source_status: { ...value.source_status, degraded: "yes" } }), /incompatible/);
  const group = { rows: [], truncated: false };
  const report = { schema: "monitoring.report.v1", identity: "owner-one", revision: 1,
    observed_at: snapshot.observed_at, currency: "USD", range: { from: null, until: snapshot.observed_at },
    previous_range: null, summary: metrics, breakdowns: { projects: group, models: group, daily: group },
    tools: {}, comparisons: {}, coverage: {}, inputs: managedInputs() };
  const malformedReport = structuredClone(report);
  malformedReport.inputs.inputs[0].path = "/private/source.jsonl";
  assert.throws(() => decodeReport(malformedReport), /incompatible/);
});

test("only an operator-supplied plain loopback origin is accepted", () => {
  for (const url of ["https://example.com", "http://localhost:5274", "http://127.0.0.1:5274/private", "http://secret@127.0.0.1:5274"]) {
    assert.throws(() => monitoringOwner(url), /loopback/);
  }
});

test("a restarted owner cannot silently replace a bound owner", async (context) => {
  let current = snapshot;
  context.mock.method(globalThis, "fetch", async () => ({ ok: true, json: async () => current }));
  const owner = monitoringOwner("http://127.0.0.1:5274");
  await owner.snapshot();
  current = { ...snapshot, identity: "owner-two" };
  await assert.rejects(owner.snapshot(), /restarted/);
  owner.reconnect();
  assert.equal((await owner.snapshot()).identity, "owner-two");
});

test("owner failures remain unavailable instead of producing empty data", async (context) => {
  context.mock.method(globalThis, "fetch", async () => ({ ok: false, status: 503 }));
  await assert.rejects(monitoringOwner("http://127.0.0.1:5274").snapshot(), /unavailable/);
});

test("optional insight data rejects impossible quota percentages and preserves unknown", () => {
  const value = structuredClone(snapshot);
  value.insights = { schema: "monitoring.insights.v1", window: { minutes: 60 },
    pace: { records: 0, tokens: 0, tokens_per_minute: "0", estimated_cost_per_hour: null,
      estimated_priced_records: 0, unknown_cost_records: 0, unconverted_cost_records: 0, billed_records: 0 },
    forecast: { horizon_minutes: 60, state: "insufficient-data", confidence: "insufficient", tokens: null, estimated_cost: null },
    source_freshness: [], quotas: { windows: [{ used_percent: null, remaining_percent: null,
      captured_at: "2026-11-02T20:00:00Z", freshness: "stale" }] } };
  assert.equal(decodeSnapshot(value).insights.quotas.windows[0].remaining_percent, null);
  value.insights.quotas.windows[0].used_percent = 101;
  assert.throws(() => decodeSnapshot(value), /incompatible/);
});

test("report reads preserve unknown amounts and share the pinned owner identity", async (context) => {
  const group = { rows: [], truncated: false };
  let report = { schema: "monitoring.report.v1", identity: "owner-one", revision: 1,
    observed_at: snapshot.observed_at, currency: "USD", range: { from: null, until: snapshot.observed_at },
    previous_range: null, summary: metrics, breakdowns: { projects: group, models: group, daily: group },
    tools: {}, comparisons: {}, coverage: {}, inputs: managedInputs() };
  const snapshotWithInputs = { ...snapshot, inputs: managedInputs() };
  context.mock.method(globalThis, "fetch", async (url) => ({ ok: true,
    json: async () => new URL(url).pathname.endsWith("report") ? report : snapshotWithInputs }));
  const owner = monitoringOwner("http://127.0.0.1:5274");
  assert.equal((await owner.snapshot()).inputs.healthy_inputs, 1);
  assert.equal((await owner.report()).summary.costs.estimated.amount, null);
  assert.equal((await owner.report()).inputs.unread_inputs, 1);
  report = { ...report, identity: "owner-two" };
  await assert.rejects(owner.report(), /restarted/);
  assert.throws(() => decodeReport({ ...report, summary: { ...metrics, total_tokens: -1 } }), /incompatible/);
});

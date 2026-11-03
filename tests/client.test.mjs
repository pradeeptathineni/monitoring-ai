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

function sourceCoverage() {
  return { schema: "monitoring.source-coverage.v1", observed_at: "2026-11-02T20:00:00Z",
    scope: "latest-accepted-input-reads", complete_coverage: false,
    inputs: [
      { id: "normalized-a", format: "normalized",
        capabilities: { usage: "normalized-deltas", lifecycle: "producer-supplied", tools: "producer-supplied" },
        sampled_at: "2026-11-02T19:59:59Z",
        records: { total: 3, usage: 1, status: 1, tool: 1, sessions: 2, entities: 2, event_sources: 2 },
        latest_event_at: "2026-11-02T20:00:00Z", latest_usage_at: "2026-11-02T20:00:00Z",
        latest_status_at: "2026-11-02T19:59:00Z", latest_tool_at: "2026-11-02T19:58:00Z",
        read_state: "following", retained: false, event_age_seconds: 0, event_freshness: "recent" },
      { id: "claude-usage", format: "claude",
        capabilities: { usage: "native-reconstruction", lifecycle: "unsupported", tools: "completed-pairs" },
        sampled_at: "2026-11-02T19:59:59Z",
        records: { total: 1, usage: 1, status: 0, tool: 0, sessions: 1, entities: 1, event_sources: 1 },
        latest_event_at: "2026-11-02T20:00:02Z", latest_usage_at: "2026-11-02T20:00:02Z",
        latest_status_at: null, latest_tool_at: null,
        read_state: "unavailable", retained: true, event_age_seconds: -2, event_freshness: "future" },
      { id: "unread-codex", format: "codex",
        capabilities: { usage: "native-reconstruction", lifecycle: "unsupported", tools: "unsupported" },
        sampled_at: null, records: null, latest_event_at: null, latest_usage_at: null,
        latest_status_at: null, latest_tool_at: null, read_state: "unread", retained: false,
        event_age_seconds: null, event_freshness: "unread" },
      { id: "empty-codex", format: "codex",
        capabilities: { usage: "native-reconstruction", lifecycle: "unsupported", tools: "unsupported" },
        sampled_at: "2026-11-02T19:59:59Z",
        records: { total: 0, usage: 0, status: 0, tool: 0, sessions: 0, entities: 0, event_sources: 0 },
        latest_event_at: null, latest_usage_at: null, latest_status_at: null, latest_tool_at: null,
        read_state: "unavailable", retained: true, event_age_seconds: null, event_freshness: "empty" },
      { id: "failed-first-read", format: "normalized",
        capabilities: { usage: "normalized-deltas", lifecycle: "producer-supplied", tools: "producer-supplied" },
        sampled_at: null, records: null, latest_event_at: null, latest_usage_at: null,
        latest_status_at: null, latest_tool_at: null, read_state: "unavailable", retained: false,
        event_age_seconds: null, event_freshness: "unread" },
    ] };
}

test("unavailable amounts stay null and malformed costs refuse", () => {
  assert.equal(decodeSnapshot(snapshot).summary.costs.estimated.amount, null);
  const broken = structuredClone(snapshot);
  broken.summary.costs.billed.amount = "NaN";
  assert.throws(() => decodeSnapshot(broken), /incompatible/);
});

test("optional daily token threshold is qualified and rejects inconsistent totals", () => {
  const value = structuredClone(snapshot);
  value.token_threshold = { schema: "monitoring.token-threshold.v1", scope: "all-accepted-events-unfiltered",
    complete_coverage: false, window: { from: "2026-11-02T00:00:00.000Z", until: "2026-11-03T00:00:00.000Z",
      observed_at: snapshot.observed_at }, limit_tokens: 10, observed_tokens: 12,
    remaining_observed_tokens: 0, state: "observed-reached", usage_records: 1,
    excluded_receipt_summaries: 0, excluded_future_records: 0,
    note: "Observed events only; no provider enforcement." };
  assert.equal(decodeSnapshot(value).token_threshold.state, "observed-reached");
  for (const mutation of [
    (row) => { row.complete_coverage = true; },
    (row) => { row.remaining_observed_tokens = 2; },
    (row) => { row.state = "observed-below"; },
    (row) => { row.private_path = "/private/source"; },
  ]) {
    const invalid = structuredClone(value);
    mutation(invalid.token_threshold);
    assert.throws(() => decodeSnapshot(invalid), /incompatible/);
  }
});

test("optional daily cost thresholds preserve separate bases and refuse private fields", () => {
  const value = structuredClone(snapshot);
  value.cost_thresholds = {
    schema: "monitoring.cost-thresholds.v1", scope: "all-accepted-events-unfiltered",
    complete_coverage: false, currency: "USD", note: "Known subset only.",
    window: { from: "2026-11-02T00:00:00.000Z", until: "2026-11-03T00:00:00.000Z",
      observed_at: snapshot.observed_at },
    items: [{ basis: "estimated", limit_amount: "2", observed_amount: "3.25",
      remaining_observed_amount: "0", state: "observed-reached", covered_records: 1,
      receipt_time_records: 1, unpriced_records: 0, unconverted_records: 0,
      other_basis_records: 0, future_records: 0, cost_provenance: ["fixture"], provenance_truncated: false }],
  };
  assert.equal(decodeSnapshot(value).cost_thresholds.items[0].basis, "estimated");
  for (const mutation of [
    (row) => { row.complete_coverage = true; },
    (row) => { row.items[0].basis = "billed"; row.items.push(structuredClone(row.items[0])); },
    (row) => { row.items[0].observed_amount = "NaN"; },
    (row) => { row.items[0].covered_records = 0; },
    (row) => { row.items[0].private_path = "/private/source"; },
  ]) {
    const invalid = structuredClone(value);
    mutation(invalid.cost_thresholds);
    assert.throws(() => decodeSnapshot(invalid), /incompatible/);
  }
});

test("optional Codex turn markers stay separate and reject private or contradictory fields", () => {
  const value = structuredClone(snapshot);
  value.codex_turns = { schema: "monitoring.codex-turns.v1", configured_sources: 1,
    unavailable_sources: 0, ambiguous_turns: 0, truncated: false, rows: [{
      source: "chosen", project: "fictional", session: `s-${"a".repeat(32)}`,
      turn: `t-${"b".repeat(32)}`, start_at: "2026-11-02T19:00:00.000Z",
      complete_at: "2026-11-02T19:00:04.000Z", duration_ms: 4000,
      time_to_first_token_ms: 700, evidence: "paired", source_state: "following",
    }] };
  assert.equal(decodeSnapshot(value).codex_turns.rows[0].evidence, "paired");
  for (const mutate of [
    (row) => { row.raw_turn_id = "private"; },
    (row) => { row.start_at = "2026-11-02T19:00:05.000Z"; },
    (row) => { row.evidence = "session-completed"; },
    (row) => { row.duration_ms = -1; },
  ]) {
    const invalid = structuredClone(value);
    mutate(invalid.codex_turns.rows[0]);
    assert.throws(() => decodeSnapshot(invalid), /incompatible/);
  }
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

test("optional source coverage validates capabilities, states, counts and unknowns", () => {
  assert.equal(decodeSnapshot(snapshot).identity, "owner-one"); // Older owners omit source coverage.
  const value = structuredClone(snapshot);
  value.source_coverage = sourceCoverage();
  const accepted = decodeSnapshot(value).source_coverage;
  assert.equal(accepted.inputs[1].event_freshness, "future");
  assert.equal(accepted.inputs[2].records, null);
  assert.equal(accepted.inputs[3].event_freshness, "empty");
  assert.equal(accepted.inputs[4].read_state, "unavailable");

  for (const mutate of [
    (coverage) => { coverage.inputs[0].path = "/private/source.jsonl"; },
    (coverage) => { coverage.complete_coverage = true; },
    (coverage) => { coverage.inputs[0].records.usage = 2; },
    (coverage) => { coverage.inputs[0].capabilities.lifecycle = "all-workers"; },
    (coverage) => { coverage.inputs[0].event_freshness = "unread"; },
    (coverage) => { coverage.inputs[0].event_age_seconds = Infinity; },
    (coverage) => { coverage.inputs[1].retained = false; },
    (coverage) => { coverage.inputs[2].latest_event_at = "2026-11-02"; },
    (coverage) => { coverage.inputs[3].records.total = 1; },
  ]) {
    const malformed = structuredClone(value);
    mutate(malformed.source_coverage);
    assert.throws(() => decodeSnapshot(malformed), /incompatible/);
  }
  const duplicate = structuredClone(value);
  duplicate.source_coverage.inputs[1].id = duplicate.source_coverage.inputs[0].id;
  assert.throws(() => decodeSnapshot(duplicate), /incompatible/);
});

test("claude-results is a distinct cumulative-result source format with closed capabilities", () => {
  const value = structuredClone(snapshot);
  value.inputs = managedInputs();
  value.inputs.inputs[2].format = "claude-results";
  value.source_coverage = sourceCoverage();
  const coverage = structuredClone(value.source_coverage.inputs[1]);
  coverage.id = "claude-results-input";
  coverage.format = "claude-results";
  coverage.capabilities = { usage: "cumulative-result-deltas", lifecycle: "unsupported", tools: "unsupported" };
  value.source_coverage.inputs.push(coverage);
  const accepted = decodeSnapshot(value);
  assert.equal(accepted.inputs.inputs[2].format, "claude-results");
  assert.equal(accepted.source_coverage.inputs[5].capabilities.usage, "cumulative-result-deltas");

  const group = { rows: [], truncated: false };
  const report = { schema: "monitoring.report.v1", identity: "owner-one", revision: 1,
    observed_at: snapshot.observed_at, currency: "USD", range: { from: null, until: snapshot.observed_at },
    previous_range: null, summary: metrics, breakdowns: { projects: group, models: group, daily: group },
    tools: {}, comparisons: {}, coverage: {}, inputs: value.inputs, source_coverage: value.source_coverage };
  assert.equal(decodeReport(report).source_coverage.inputs[5].format, "claude-results");

  const malformed = structuredClone(value);
  malformed.source_coverage.inputs[5].capabilities.usage = "native-reconstruction";
  assert.throws(() => decodeSnapshot(malformed), /incompatible/);
  const unknownFormat = structuredClone(value);
  unknownFormat.inputs.inputs[2].format = "claude-result";
  assert.throws(() => decodeSnapshot(unknownFormat), /incompatible/);
});

test("selected OTLP file capabilities are accepted without broadening other formats", () => {
  const value = structuredClone(snapshot);
  value.inputs = managedInputs();
  value.inputs.inputs[2].format = "otlp-jsonl";
  value.source_coverage = sourceCoverage();
  const coverage = structuredClone(value.source_coverage.inputs[1]);
  coverage.id = "selected-traces";
  coverage.format = "otlp-jsonl";
  coverage.capabilities = { usage: "completed-inference-spans", lifecycle: "completed-agent-spans-only", tools: "completed-tool-spans" };
  value.source_coverage.inputs.push(coverage);
  assert.equal(decodeSnapshot(value).source_coverage.inputs.at(-1).format, "otlp-jsonl");
  const wrong = structuredClone(value);
  wrong.source_coverage.inputs.at(-1).capabilities.lifecycle = "producer-supplied";
  assert.throws(() => decodeSnapshot(wrong), /incompatible/);
});

test("Codex completed-item tool coverage is additive and older owners remain readable", () => {
  const value = structuredClone(snapshot);
  value.source_coverage = sourceCoverage();
  const codex = value.source_coverage.inputs[2];
  codex.capabilities.tools = "completed-items";
  assert.equal(decodeSnapshot(value).source_coverage.inputs[2].capabilities.tools, "completed-items");
  codex.capabilities.tools = "unsupported";
  assert.equal(decodeSnapshot(value).source_coverage.inputs[2].capabilities.tools, "unsupported");
  codex.capabilities.tools = "producer-supplied";
  assert.throws(() => decodeSnapshot(value), /incompatible/);
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

test("receipt-time summary qualifiers are optional and validate as nonnegative counts", () => {
  assert.equal(decodeSnapshot(snapshot).coverage.receipt_time_summary_records, undefined);
  const value = structuredClone(snapshot);
  value.coverage.receipt_time_summary_records = 2;
  value.insights = { schema: "monitoring.insights.v1", window: { minutes: 60, from: snapshot.observed_at, until: snapshot.observed_at },
    pace: { records: 4, tokens: 40, tokens_per_minute: "1", estimated_cost_per_hour: null,
      estimated_priced_records: 1, unknown_cost_records: 1, unconverted_cost_records: 0,
      billed_records: 0, excluded_summary_records: 2 },
    forecast: { horizon_minutes: 60, state: "available", tokens: 60, estimated_cost: null,
      assumption: "fixture", observed_span_seconds: 600, confidence: "limited" },
    source_freshness: [], quotas: { windows: [] } };
  assert.equal(decodeSnapshot(value).insights.pace.excluded_summary_records, 2);
  const malformedCoverage = structuredClone(value);
  malformedCoverage.coverage.receipt_time_summary_records = -1;
  assert.throws(() => decodeSnapshot(malformedCoverage), /incompatible/);
  const malformedPace = structuredClone(value);
  malformedPace.insights.pace.excluded_summary_records = 1.5;
  assert.throws(() => decodeSnapshot(malformedPace), /incompatible/);

  const group = { rows: [], truncated: false };
  const report = { schema: "monitoring.report.v1", identity: "owner-one", revision: 1,
    observed_at: snapshot.observed_at, currency: "USD", range: { from: null, until: snapshot.observed_at },
    previous_range: null, summary: metrics, breakdowns: { projects: group, models: group, daily: group },
    tools: {}, comparisons: {}, coverage: { receipt_time_summary_records: 2 } };
  assert.equal(decodeReport(report).coverage.receipt_time_summary_records, 2);
  assert.throws(() => decodeReport({ ...report, coverage: { receipt_time_summary_records: "2" } }), /incompatible/);
});

test("report reads preserve unknown amounts and share the pinned owner identity", async (context) => {
  const group = { rows: [], truncated: false };
  let report = { schema: "monitoring.report.v1", identity: "owner-one", revision: 1,
    observed_at: snapshot.observed_at, currency: "USD", range: { from: null, until: snapshot.observed_at },
    previous_range: null, summary: metrics, breakdowns: { projects: group, models: group, daily: group },
    tools: {}, comparisons: {}, coverage: {}, inputs: managedInputs(), source_coverage: sourceCoverage() };
  const snapshotWithInputs = { ...snapshot, inputs: managedInputs(), source_coverage: sourceCoverage() };
  context.mock.method(globalThis, "fetch", async (url) => ({ ok: true,
    json: async () => new URL(url).pathname.endsWith("report") ? report : snapshotWithInputs }));
  const owner = monitoringOwner("http://127.0.0.1:5274");
  assert.equal((await owner.snapshot()).inputs.healthy_inputs, 1);
  assert.equal((await owner.snapshot()).source_coverage.inputs[1].format, "claude");
  assert.equal((await owner.report()).summary.costs.estimated.amount, null);
  assert.equal((await owner.report()).inputs.unread_inputs, 1);
  assert.equal((await owner.report()).source_coverage.inputs[2].records, null);
  report = { ...report, identity: "owner-two" };
  await assert.rejects(owner.report(), /restarted/);
  assert.throws(() => decodeReport({ ...report, summary: { ...metrics, total_tokens: -1 } }), /incompatible/);
});

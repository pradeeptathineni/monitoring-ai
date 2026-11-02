import assert from "node:assert/strict";
import test from "node:test";
import { decodeSnapshot, monitoringOwner } from "../integrations/deep-ai/client.ts";

const metrics = { calls: 1, tokens: { input: 10, cache_read: 0, cache_write: 0, output: 2 },
  total_tokens: 12, costs: { estimated: { amount: null, events: 0 }, billed: { amount: "0", events: 1 } },
  unknown_cost_events: 0, unconverted_cost_events: 0, tools: 0, tool_errors: 0 };
const snapshot = { schema: "monitoring.snapshot.v1", identity: "owner-one", revision: 1,
  observed_at: "2026-11-02T20:00:00Z", currency: "USD",
  coverage: { scope: "explicit files", event_count: 1, matched_events: 1, entities_truncated: false },
  source_status: { state: "static", last_success_at: null, error: null },
  summary: { ...metrics, sessions: 1, entities: 1, active: 0, attention: 0 }, entities: [] };

test("unavailable amounts stay null and malformed costs refuse", () => {
  assert.equal(decodeSnapshot(snapshot).summary.costs.estimated.amount, null);
  const broken = structuredClone(snapshot);
  broken.summary.costs.billed.amount = "NaN";
  assert.throws(() => decodeSnapshot(broken), /incompatible/);
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

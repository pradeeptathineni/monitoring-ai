/** Read only from an operator-configured Monitoring AI owner on the local host. */
export type Filters = Partial<Record<
  "provider" | "model" | "surface" | "project" | "source" | "session" |
  "since" | "until" | "state", string
>>;

export type Cost = { amount: string | null; events: number };
export type Metrics = {
  calls: number;
  tokens: { input: number; cache_read: number; cache_write: number; output: number };
  total_tokens: number;
  costs: { estimated: Cost; billed: Cost };
  unknown_cost_events: number;
  unconverted_cost_events: number;
  tools: number;
  tool_errors: number;
};
export type Entity = {
  source: string; session: string; entity: string; parent: string | null;
  entity_type: "session" | "agent" | "worker";
  state: string; reported_state: string; stale: boolean;
  status_at: string | null; last_event_at: string; metrics: Metrics;
};
export type Snapshot = {
  schema: "monitoring.snapshot.v1";
  identity: string;
  revision: number;
  observed_at: string;
  currency: string;
  coverage: { scope: string; event_count: number; matched_events: number; entities_truncated: boolean };
  source_status: { state: "static" | "following" | "unavailable"; last_success_at: string | null; error: string | null };
  summary: Metrics & { sessions: number; entities: number; active: number; attention: number };
  entities: Entity[];
  insights?: {
    schema: "monitoring.insights.v1";
    window: { minutes: number; from: string; until: string };
    pace: { records: number; tokens: number; tokens_per_minute: string;
      estimated_cost_per_hour: string | null; estimated_priced_records: number;
      unknown_cost_records: number; unconverted_cost_records: number; billed_records: number };
    forecast: { state: "insufficient-data" | "partial" | "available"; tokens: number | null;
      estimated_cost: string | null; horizon_minutes: number; assumption: string;
      observed_span_seconds: number; confidence: "insufficient" | "low" | "limited" };
    source_freshness: Array<{ source: string; last_event_at: string; age_seconds: number;
      state: "recent" | "stale" | "future" | "unknown"; events: number }>;
    quotas: { state: string; note: string; windows: Array<{
      source: string; provider: string; account: string; provenance: string; captured_at: string;
      id: string; label: string; used_percent: number | null; remaining_percent: number | null;
      window_minutes: number | null; resets_at: string | null; freshness: string; age_seconds: number;
    }>; read_status?: { state: string; last_success_at: string | null; error: string | null } };
  };
};

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function metrics(value: unknown): value is Metrics {
  if (!record(value) || !record(value.tokens) || !record(value.costs)) return false;
  const counts = [value.calls, value.total_tokens, value.unknown_cost_events,
    value.unconverted_cost_events, value.tools, value.tool_errors,
    value.tokens.input, value.tokens.cache_read, value.tokens.cache_write, value.tokens.output];
  if (!counts.every((n) => Number.isSafeInteger(n) && (n as number) >= 0)) return false;
  return [value.costs.estimated, value.costs.billed].every((cost) => record(cost) &&
    Number.isSafeInteger(cost.events) && (cost.events as number) >= 0 &&
    (cost.amount === null || (typeof cost.amount === "string" && /^\d+(\.\d+)?$/.test(cost.amount))));
}

function decimal(value: unknown): boolean {
  return value === null || (typeof value === "string" && /^\d+(\.\d+)?$/.test(value));
}

function insights(value: unknown): boolean {
  if (!record(value) || value.schema !== "monitoring.insights.v1" || !record(value.window) ||
      !record(value.pace) || !record(value.forecast) || !record(value.quotas) ||
      !Array.isArray(value.source_freshness) || !Array.isArray(value.quotas.windows) || value.quotas.windows.length > 500) return false;
  const pace = value.pace, forecast = value.forecast;
  if (![pace.records, pace.tokens, pace.estimated_priced_records, pace.unknown_cost_records,
    pace.unconverted_cost_records, pace.billed_records, forecast.horizon_minutes]
      .every((n) => Number.isSafeInteger(n) && (n as number) >= 0) ||
      typeof pace.tokens_per_minute !== "string" || !decimal(pace.tokens_per_minute) ||
      !decimal(pace.estimated_cost_per_hour) || !decimal(forecast.estimated_cost) ||
      !["insufficient-data", "partial", "available"].includes(String(forecast.state)) ||
      !["insufficient", "low", "limited"].includes(String(forecast.confidence)) ||
      !(forecast.tokens === null || (Number.isSafeInteger(forecast.tokens) && (forecast.tokens as number) >= 0))) return false;
  return value.quotas.windows.every((row) => record(row) &&
    [row.used_percent, row.remaining_percent].every((n) => n === null ||
      (typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 100)) &&
    ["current", "stale", "future", "reset-passed"].includes(String(row.freshness)) &&
    typeof row.captured_at === "string" && Number.isFinite(Date.parse(row.captured_at)));
}

/** Refuse incompatible data rather than making an unavailable owner look empty. */
export function decodeSnapshot(value: unknown): Snapshot {
  if (!record(value) || value.schema !== "monitoring.snapshot.v1" ||
      typeof value.identity !== "string" || !Number.isSafeInteger(value.revision) || (value.revision as number) < 0 ||
      typeof value.observed_at !== "string" || !Number.isFinite(Date.parse(value.observed_at)) ||
      typeof value.currency !== "string" || !/^[A-Z]{3}$/.test(value.currency) || !record(value.coverage) ||
      !Number.isSafeInteger(value.coverage.event_count) || !Number.isSafeInteger(value.coverage.matched_events) ||
      typeof value.coverage.scope !== "string" || typeof value.coverage.entities_truncated !== "boolean" ||
      !record(value.source_status) || !["static", "following", "unavailable"].includes(String(value.source_status.state)) ||
      !metrics(value.summary) || !record(value.summary) ||
      ![value.summary.sessions, value.summary.entities, value.summary.active, value.summary.attention]
        .every((n) => Number.isSafeInteger(n) && (n as number) >= 0) ||
      !Array.isArray(value.entities) || value.entities.length > 500 ||
      (value.insights !== undefined && !insights(value.insights)) ||
      !value.entities.every((row) => record(row) && typeof row.source === "string" &&
        typeof row.session === "string" && typeof row.entity === "string" &&
        (row.parent === null || typeof row.parent === "string") && typeof row.stale === "boolean" &&
        ["session", "agent", "worker"].includes(String(row.entity_type)) && typeof row.state === "string" && metrics(row.metrics))) {
    throw new Error("Monitoring AI returned an incompatible snapshot");
  }
  return value as unknown as Snapshot;
}

export function monitoringOwner(endpoint: string) {
  const base = new URL(endpoint);
  if (base.protocol !== "http:" || base.hostname !== "127.0.0.1" ||
      base.username || base.password || base.pathname !== "/" || base.search || base.hash) {
    throw new Error("Configure a plain loopback Monitoring AI origin on the Deep host");
  }
  let identity: string | undefined;
  return {
    async snapshot(filters: Filters = {}): Promise<Snapshot> {
      const url = new URL("/api/v1/snapshot", base);
      for (const [key, value] of Object.entries(filters)) if (value) url.searchParams.set(key, value);
      const response = await fetch(url, { signal: AbortSignal.timeout(8000), redirect: "error" });
      if (!response.ok) throw new Error(`Monitoring AI unavailable (${response.status})`);
      const snapshot = decodeSnapshot(await response.json());
      if (identity && snapshot.identity !== identity) {
        throw new Error("Monitoring AI restarted. Reconnect deliberately before adopting its observations");
      }
      identity = snapshot.identity;
      return snapshot;
    },
    reconnect() { identity = undefined; },
  };
}

# Monitoring owner contract v1

Monitoring AI owns immutable usage observations, normalized token accounting, cost qualifications and their derived views. Agent runtimes own execution. Providers own authoritative billing and quota statements. Deep owns presentation and navigation.

## Input

A UTF-8 JSONL stream carries `monitoring.event.v1` objects. [The example](../examples/events.jsonl) is fictional. Unknown top-level fields are rejected. IDs and dimensions accept only short identifier characters, not paths or arbitrary free text.

| Field | Meaning |
| --- | --- |
| `schema` | Exactly `monitoring.event.v1` |
| `source`, `id` | Stable producer namespace and immutable event identity. The pair deduplicates replay. |
| `session`, `entity`, `parent` | Entity identity is the source/session/entity tuple. Parent is an entity ID within that source/session, or null. Missing parents remain unobserved. |
| `entity_type` | `session`, `agent` or `worker`. Parent/type/project cannot change within an entity. Cycles refuse. |
| `timestamp` | ISO timestamp with timezone, normalized to UTC milliseconds. Evidence time comes from the producer. |
| `kind` | `usage`, `status` or `tool` |
| `provider`, `model`, `surface`, `project` | Explicit aliases. Unknown dimensions use `unknown`. A project alias does not authorize filesystem access. |
| `tokens` | Usage only. Nonnegative integer deltas in disjoint `input`, `cache_read`, `cache_write`, `output` buckets. Missing buckets are zero. |
| `cost` | Optional usage evidence with decimal `amount`, ISO-style three-letter `currency`, `basis` of `estimated` or `billed`, and identifier `provenance`. |
| `state` | Status only. `running`, `waiting`, `blocked`, `completed`, `failed`, `cancelled`, `unknown`. |
| `tool`, `outcome` | Tool records only. Tool name and `ok`, `error` or `unknown`. No arguments or file paths. |
| `duration_ms` | Optional nonnegative duration in milliseconds. It is not inferred from chronology. |

Usage records are deltas, never cumulative snapshots. Producers must identify a call once, including streamed copies and subagent routing. A parent must not include its children's usage and also report those child deltas separately. The native Codex adapter computes deltas from complete cumulative counters, subtracts cached input from total input, and leaves reasoning inside output. Claude input excludes its separate cache buckets. These formats are not interchangeable.

Imports are transactional. Identical source/ID content is replay. Changed content refuses the entire batch. There is no implicit correction or deletion API in v1. Corrected source accounting needs a reviewed migration, not a second alias added to the same ledger. Separate source IDs intentionally have separate identities. Cross-source overlap is not inferred or automatically deduplicated.

## Read API

The standalone server listens only on `127.0.0.1`, default port 5274. Host checking and no browser CORS prevent casual cross-origin access. This is a local boundary, not multi-user authentication. Host applications should proxy reads through their own server. Do not expose this service on a public interface or add it to a tunnel.

| Route | Response |
| --- | --- |
| `GET /health` | `monitoring.health.v1`, server health and followed-source status |
| `GET /api/v1/capabilities` | `monitoring.capabilities.v1`, owner version, instance identity, read routes, filters and bounds |
| `GET /api/v1/snapshot` | `monitoring.snapshot.v1`, revision, observation time, cost coverage, grouped totals, up to 500 entities and latest 100 matching events |
| `GET /api/v1/events` | `monitoring.events.v1`, chronological records and a bounded next offset |

Snapshot filters are exact `provider`, `model`, `surface`, `project`, `source`, `session`, `state`, `since` and `until`. Date-only bounds include the whole UTC day. Timestamp bounds are inclusive. Repeated or unknown parameters refuse. State filters use current evidence across the entire ledger. Usage/date filters select event accounting, while entity lifecycle still uses that entity's latest ledger status. This distinction prevents an old running event from becoming current merely because the completed event lies outside a usage filter.

Event pages accept the same filters except state, plus `limit` (1–500, default 100), `after` (nonnegative offset) and `expected_revision`. Start at offset zero, then pass the returned revision on later pages. A changed ledger returns HTTP 409. Restart paging instead of concatenating pages from different revisions. Ordering is timestamp/source/event ID. Events arriving late can change chronological positions.

The process instance `identity` changes when the server restarts. `revision` is the ledger's committed event sequence. Neither establishes process liveness or accepted task completion. Consumers should pin the instance identity and deliberately reconnect after it changes. A successful read updates `observed_at`, never event timestamps. HTTP 400 reports invalid filters or read limits, 403 a host/origin mismatch, 404 a missing route and 503 an unavailable ledger.

## Interpretation

`reported_state` retains the latest producer status. Nonterminal evidence older than 60 seconds, more than five seconds in the future, or absent becomes `state: unknown` with `stale: true`. Terminal states remain recorded outcomes, even when historical. A reported completed session does not establish task acceptance or correctness. Recent tokens do not make a session running. Source-file following status is independent from entity status. An unavailable source retains accepted history.

Costs are decimal strings. Estimated and billed amounts are disjoint subsets of usage records. Event cost evidence takes precedence over the optional dated model price book. No estimate is created if the exact provider/model/date has no matching price. Totals show covered event counts, `unknown_cost_events` and `unconverted_cost_events`. An amount of null means no covered amount, including when all rates are missing. An amount of `"0"` means a known zero. Do not turn null into zero or claim a partial total is complete.

Prices apply per million disjoint tokens over a `[from, until)` interval. Overlapping exact model intervals refuse. The producer is responsible for choosing a rate that fits the actual tier, cache policy and contract. This alpha has no automatic tier, discount or subscription billing engine. Converted views use an explicit USD-based rate book with `as_of` and `provenance`. These are display rates, not historical invoice rates. There is no automatic current-rate claim.

No prompts, model responses, account credentials, raw tool arguments or arbitrary metadata enter the normalized ledger. Native imports discard those fields before storage. Dimension aliases still reveal operational information, so metadata is private by default. This service neither launches nor cancels agents. Observation IDs must never be treated as execution authority.

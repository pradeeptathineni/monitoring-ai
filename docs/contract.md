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
| `tool_category`, `tool_server` | Optional tool-only category `core`, `mcp`, `shell`, `skill`, `agent` or `other`. MCP may include a server alias. Untagged tools remain unclassified. |
| `duration_ms` | Optional nonnegative duration in milliseconds. It is not inferred from chronology. |

Usage records are deltas, never cumulative snapshots. Producers must identify a call once, including streamed copies and subagent routing. A parent must not include its children's usage and also report those child deltas separately. The native Codex adapter computes deltas from complete cumulative counters, subtracts cached input from total input, and leaves reasoning inside output. Claude input excludes its separate cache buckets. These formats are not interchangeable.

Imports are transactional. Identical source/ID content is replay. Changed content refuses the entire batch. There is no implicit correction or deletion API in v1. Corrected source accounting needs a reviewed migration, not a second alias added to the same ledger. Separate source IDs intentionally have separate identities. Cross-source overlap is not automatically deduplicated. Exact selected native identity overlap is refused as described below; other collector combinations remain unresolved.

From `v0.8.2`, the optional [runtime producer helper](runtime-producers.md) writes this same contract to a new private file. It accepts caller-owned metadata and does not inspect, execute or cancel processes. Runtime authority and observation identity remain separate. No input or read-API schema changes are required.

From `v0.8.29`, [Claude CLI stream capture](claude-cli-stream.md) projects one explicitly piped native invocation's init/result frames into this existing normalized status format. The start and end timestamps are local frame-receipt observations. The child agent entity describes the CLI invocation only; the parent conversation lifecycle and any OS process relationship remain unobserved. Incomplete streams end unknown. Its normalized status file emits no usage or cost and adds no source format or read-API field. From v0.8.31, an opt-in second private file can hold the sanitized final result for the existing `claude-results` adapter. Selecting it with the same event source alias joins the hashed session identity without changing the HTTP schema. Its cumulative receipt-time tokens and client estimates can overlap separately selected native transcripts; operators must choose one accounting route per work scope.

## Read API

The standalone server listens only on `127.0.0.1`, default port 5274. Host checking and no browser CORS prevent casual cross-origin access. This is a local boundary, not multi-user authentication. Host applications should proxy reads through their own server. Do not expose this service on a public interface or add it to a tunnel.

| Route | Response |
| --- | --- |
| `GET /health` | `monitoring.health.v1`, server health and followed-source status |
| `GET /api/v1/capabilities` | `monitoring.capabilities.v1`, owner version, instance identity, read routes, filters and bounds |
| `GET /api/v1/snapshot` | `monitoring.snapshot.v1`, revision, observation time, cost coverage, grouped totals, up to 500 entities and latest 100 matching events |
| `GET /api/v1/events` | `monitoring.events.v1`, chronological records and a bounded next offset |
| `GET /api/v1/report` | `monitoring.report.v1`, qualified period comparisons, project/model/day and tool breakdowns |

When `--codeburn-status` is explicitly configured, snapshot and report add nullable `codeburn_activity` (`monitoring.codeburn-activity.v1`) and independent `codeburn_activity_status`; health adds the status only. Unconfigured owners omit them. The optional view replaces its own prior observation and never advances the event revision. Its rows are recent transcript-write evidence at CodeBurn's generation time, not normalized lifecycle events or process liveness. See [CodeBurn status output](codeburn-status.md) for privacy, freshness and missing-block semantics. Event filters do not apply to this view.

Snapshot filters are exact `provider`, `model`, `surface`, `project`, `source`, `session`, `state`, `since` and `until`. Date-only bounds include the whole UTC day. Timestamp bounds are inclusive. Repeated or unknown parameters refuse. State filters use current evidence across the entire ledger. Usage/date filters select event accounting, while entity lifecycle still uses that entity's latest ledger status. This distinction prevents an old running event from becoming current merely because the completed event lies outside a usage filter.

Event pages accept the same filters except state, plus `limit` (1–500, default 100), `after` (nonnegative offset) and `expected_revision`. Start at offset zero, then pass the returned revision on later pages. A changed ledger returns HTTP 409. Restart paging instead of concatenating pages from different revisions. Ordering is timestamp/source/event ID. Events arriving late can change chronological positions.

From 0.8.0a1, event pages also accept `entity` when both `source` and `session` are supplied. All three are exact identifier aliases. This scopes an entity within its source/session identity, rather than combining unrelated workers that share a name. Snapshot and report filters are unchanged. Capabilities advertises the event route's supported filters separately as `event_filters`. Older owners may omit that field and reject `entity`.

The dashboard's entity history reads pages from the beginning in chronological order. It preserves the current usage dimensions and date bounds but omits the current-state filter. Its page count is the selected entity's recorded history within those filters, not all machine activity. The first page must match the displayed snapshot's process identity. Later pages must retain that identity and the first page's revision. A changed ledger, restarted owner or failed read requires an explicit history restart or retry. Loaded pages remain dated observations and never establish a live worker or task acceptance. Ordinary Events mode continues to show the snapshot's latest 100 matching records.

The process instance `identity` changes when the server restarts. `revision` is the ledger's committed event sequence. Neither establishes process liveness or accepted task completion. Consumers should pin the instance identity and deliberately reconnect after it changes. A successful read updates `observed_at`, never event timestamps. HTTP 400 reports invalid filters or read limits, 403 a host/origin mismatch, 404 a missing route and 503 an unavailable ledger.

## Managed input health

From `v0.8.1`, the additional native format `claude-results` reads timestamped cumulative result frames. It requires a project alias. Its coverage capability is usage `cumulative-result-deltas`, lifecycle `unsupported` and tools `unsupported`. Response schema IDs and normalized event fields are unchanged. Consumers that validate format enums must admit this value before connecting to an owner configured with it. See the [result-frame contract and accounting limits](claude-results.md).

Receipt-time summaries on surface `claude-code-result` remain in ledger totals and receipt-date reports, with additive `coverage.receipt_time_summary_records` on snapshots and reports. They are excluded from short-window pace and forecasts, with additive `insights.pace.excluded_summary_records` counting otherwise matching recent summaries. New consumers validate these optional nonnegative counts when present and still accept older owners without them. Report counts describe only the current selected period. Capture freshness does not establish when the underlying calls occurred.

`serve --sources` reads one explicitly selected private `monitoring.sources.v1` manifest at startup. Its closed top-level fields are `schema` and `sources`. One to 32 entries contain required `id`, `format` and absolute `path`, with optional `project`. Formats are `normalized`, `claude`, `codex`, `claude-results` and `otlp-jsonl`. Native formats require a project alias. Normalized project must be absent or null because event dimensions remain authoritative. IDs and project aliases use the event identifier grammar. Duplicate IDs, canonical selected paths, unknown fields and duplicate JSON keys refuse. The manifest is a regular file of at most 64 KiB. Source files retain the existing 32 MiB/50,000-record bounds. Missing source files can be selected and report unavailable. Directory inputs and final-file symlinks refuse. Parent directory aliases are canonicalized without following a final-file symlink.

Capabilities advertises `input_observations: monitoring.inputs.v1`. Configured snapshot, report and health responses include additive `inputs` with that schema, `configured: true`, ordered `inputs` rows and `healthy_inputs`, `unavailable_inputs`, `unread_inputs` counts. Older or single-file owners may omit it. Consumers must continue accepting those owners. This is source inventory, independent of usage filters.

Rows expose only `id`, `format`, `project`, `state`, `last_attempt_at`, `last_success_at`, `records_seen`, `added`, `replayed` and `error`. States are `unread`, `following` and `unavailable`. Paths, filenames, native errors and source content are excluded. Timestamps are nullable UTC observations. Counts describe the latest attempt; a failed read has unknown `records_seen`, whereas an ingestion rejection can retain the known attempted count. Failure reports zero added/replayed, never a partial commit. Last successful read remains unchanged on failure. Generic errors are operational categories, not private exception text.

Each source batch commits atomically and independently. Replay remains idempotent, conflicting identities refuse that batch, and a failed input does not remove prior ledger events or stop later inputs. An explicit Claude follower may replay an identical message at a later native row timestamp, preserving the first event time. A strictly later same-message row may correct token counts: the current normalized event changes atomically, the private correction audit retains both payloads and the read revision advances, invalidating pinned pages. Ambiguous same-time or older unrecognized versions and changed model/identity refuse. This exception does not apply to normalized producers. The explicit Codex follower incrementally reads completed appended lines; a replacement or truncation must replay the entire accepted ordered usage prefix before any new deltas, otherwise its batch refuses and accepted history remains. Recovery accepts the originally selected file without re-reading the manifest. Changing the manifest requires a deliberate restart. File replacement, truncation or source removal never deletes accepted history. Duplicate content under different paths and native aliases is not reconciled automatically; operators must select one observation route per activity.

For manifest-selected native Claude and Codex files, check, disposable preview and live following refuse a repeated projected `(session, kind, event ID)` identity across separate source aliases of the same format. The later input batch rolls back atomically; other selected inputs keep advancing. Its path-free input status names the overlap. An already accepted duplicate in an older ledger is retained and reported unavailable on subsequent reads rather than silently deleted. This guards exact repeated native observations, not cross-format collector overlap, complete session coverage or every copied activity. Normalized producers keep their independent source namespaces.

Legacy `source_status.state` retains its `static`/`following`/`unavailable` vocabulary. A completed multi-input refresh is `following` only if all inputs succeeded. Otherwise it is `unavailable`, with additive `degraded: true` when some inputs succeeded. Per-input outcomes provide the more specific explanation. Snapshot, report and health responses derive aggregate source status from the same copied input inventory they include, so a recovered input does not leave the response with a stale unavailable summary. Polling success and instance identity never establish worker liveness. Event evidence age remains independent. Slow input reads do not hold the API's observation lock.

## Selected cached session metadata

The optional [selected CodeBurn session-cache view](codeburn-session-cache.md) adds `monitoring.selected-cache-session.v1` only when explicitly configured with one selected native source ID and a cache directory. Its `selected_cache_status` and nullable `selected_cache_session` are independent of the event ledger and daily cache. It reads only the exact matching member after direct native-file access and fingerprint checks. Native and cache timestamps are file-write evidence, not running work. Cached calls may overlap native normalized usage and are never added to event totals or costs. Older Deep consumers may ignore this optional field.

## Cached daily usage

The optional [CodeBurn cached-usage view](codeburn-cache.md) uses its own `monitoring.cached-usage.v1` replacement contract. Its local-calendar daily totals never become events or advance the ledger revision. Snapshot/report event filters do not apply to that separate view. The supplied Deep reader remains unchanged and does not project this optional addition.

## Source coverage

Configured snapshot and report responses add optional `source_coverage` with schema `monitoring.source-coverage.v1`. Capabilities advertises that schema. Owners without this addition remain compatible. The top-level object contains `observed_at`, `scope: latest-accepted-input-reads`, `complete_coverage: false` and ordered `inputs`. Usage filters do not filter this source inventory.

Each row contains `id`, `format`, `capabilities`, `sampled_at`, `records`, `latest_event_at`, `latest_usage_at`, `latest_status_at`, `latest_tool_at`, `read_state`, `retained`, `event_age_seconds` and `event_freshness`. Paths, row bodies and costs are excluded. Supported capabilities are:

| Format | Usage | Lifecycle | Tools |
| --- | --- | --- | --- |
| normalized | normalized-deltas | producer-supplied | producer-supplied |
| claude | native-reconstruction | unsupported | completed-pairs |
| codex | native-reconstruction | unsupported | completed-items |
| claude-results | cumulative-result-deltas | unsupported | unsupported |
| otlp-jsonl | completed-inference-spans | completed-agent-spans-only | completed-tool-spans |

The `otlp-jsonl` format requires a project alias, accepts one compact OTLP/JSON trace batch per complete line and emits only bounded completed GenAI spans. Its status events describe agent invocation ends, not running agents or full workflow outcomes. See [selected OTLP export limits](otlp-jsonl.md). Strict Deep consumers must admit this additive format and capability tuple before reading an owner configured with it. Capabilities describe the adapter, not evidence that a producer supplied all those observations. Native usage timestamps never become lifecycle evidence. `records` is null before a successful accepted read, or counts unique `(source,id)` observations as `total`, `usage`, `status`, `tool`, plus distinct `sessions`, `entities`, `event_sources`. Identical copies count once. Conflicting copies refuse. Session identity includes event source, and entity identity also includes session. Counts can overlap across inputs and are not global ledger totals. The existing input-health `records_seen` still counts original read rows. Older owners may advertise Codex tools as `unsupported`; the Deep client accepts both values.

For an explicitly selected Codex JSONL file, `completed-items` covers recognized `event_msg/item_completed` records of type `CommandExecution`, `McpToolCall` and `FileChange`. They become normalized `tool` events with hashed item identity, source timestamp, type and bounded reported duration. Safe MCP server/tool aliases are retained; command text, arguments, output, working paths and file changes are discarded before ledger ingestion. Shell exit code zero with completed status is `ok`; a failed status or nonzero exit is `error`. MCP and file-change completion alone does not prove success and remains `unknown` unless status is explicitly failed. Tool-item timestamps are historical source evidence, not worker liveness. These events carry no token or cost measure and do not change session lifecycle.

`sampled_at` and latest-kind timestamps are nullable UTC strings. Sample time never replaces event evidence time. Event age is signed seconds from `observed_at` to `latest_event_at`. Freshness is `future` for a later event, `recent` through 60 seconds, `stale` afterward, `empty` for a successful empty read and `unread` without an accepted sample. Failure retains the prior accepted summary with `retained: true` while `read_state` becomes unavailable. Coverage is cached only after atomic ledger acceptance. Polling and metadata reads do not refresh events. This inventory neither proves complete machine coverage nor makes a worker active.

The CLI `sources create` writes one validated private selection without overwriting an existing path. `sources add --sources OLD --output NEW --file FILE --id ID` creates a new validated private manifest with one more explicitly selected source; the old manifest stays unchanged and existing outputs refuse. Both commands validate source metadata without reading source content. `sources check` emits `monitoring.source-check.v1` with per-input parsing and summary results, without creating or changing a ledger. It reports readability and supplied evidence rather than ingestion acceptance against prior history. A running owner loads its manifest once and must restart to use a newly created selection. Setup never discovers paths, installs hooks or creates credentials.

## Interpretation

`reported_state` retains the latest producer status. Nonterminal evidence older than 60 seconds, more than five seconds in the future, or absent becomes `state: unknown` with `stale: true`. Terminal states remain recorded outcomes, even when historical. A reported completed session does not establish task acceptance or correctness. Recent tokens do not make a session running. Source-file following status is independent from entity status. An unavailable source retains accepted history.

Costs are decimal strings. Estimated and billed amounts are disjoint subsets of usage records. Event cost evidence takes precedence over the optional dated model price book. No estimate is created if the exact provider/model/date has no matching price. Totals show covered event counts, `unknown_cost_events` and `unconverted_cost_events`. An amount of null means no covered amount, including when all rates are missing. An amount of `"0"` means a known zero. Do not turn null into zero or claim a partial total is complete.

Prices apply per million disjoint tokens over a `[from, until)` interval. Overlapping exact model intervals refuse. The producer is responsible for choosing a rate that fits the actual tier, cache policy and contract. This alpha has no automatic tier, discount or subscription billing engine. Converted views use an explicit USD-based rate book with `as_of` and `provenance`. These are display rates, not historical invoice rates. There is no automatic current-rate claim.

No prompts, model responses, account credentials, raw tool arguments or arbitrary metadata enter the normalized ledger. Native imports discard those fields before storage. Dimension aliases still reveal operational information, so metadata is private by default. This service neither launches nor cancels agents. Observation IDs must never be treated as execution authority.

## Usage insights and quota observations

Snapshot v1 now includes an additive `insights` object with schema `monitoring.insights.v1`. Older consumers may ignore it. New consumers must handle its absence when connected to an older owner. The supplied Deep reader preserves that compatibility.

`window` is a trailing 60-minute interval ending at snapshot observation time. Only matching recorded usage within that interval contributes to `pace`. Future records do not contribute. The token rate divides recorded volume by the complete configured window, including quiet periods. `forecast` projects that rate for 60 minutes only when two distinct usage times span at least a minute. It reports observed span, record count and heuristic confidence. Spans below ten minutes receive `low` confidence. Longer spans receive `limited`, never statistical certainty. Sparse sampling and bursts can make the scenario unrepresentative.

Estimated cost rate includes only the priced estimated subset. Coverage counters separately identify estimated-priced, unknown, unconverted and billed records. Billed records never become forecast spend. The estimated cost projection stays null if pricing is incomplete, any conversion is missing, or billed records are excluded. Token projection can still be available with partial cost coverage. Source freshness derives from latest matching event time and does not establish process ownership or live workers.

Quota input is a separate JSON object:

```json
{
  "schema": "monitoring.quotas.v1",
  "snapshots": [{
    "source": "example-import",
    "provider": "example",
    "account": "fictional-account",
    "captured_at": "2026-11-02T20:00:00Z",
    "provenance": "fictional-quota",
    "windows": [{
      "id": "five-hour",
      "label": "five-hour",
      "used_percent": 42,
      "window_minutes": 300,
      "resets_at": "2026-11-02T22:00:00Z"
    }]
  }]
}
```

Snapshot/window fields are closed. Dimension values are identifier aliases. Used percentage is in 0–100 units or null, duration is a positive integer in minutes or null, and reset is a timezone-qualified timestamp or null. Unknown never becomes zero. Repeated source/provider/account/window identities refuse, as do more than 100 snapshots or 500 total windows. Quota files are bounded to 1 MiB and require an explicitly selected regular file. Reading a file does not advance its supplied capture time.

`insights.quotas` keeps these windows independent of event-source IDs and usage filters. There is no inferred mapping between an account quota and a project or session. Freshness is `current` within five minutes, `stale` afterward, `future` for later capture times, or `reset-passed` when the reported reset has passed. Values remain visible in all states. A passed reset does not establish new capacity. `remaining_percent` is arithmetic from the supplied used percentage, not permission to start work.

`read_status` independently reports source configuration, a read in progress, last successful receipt and unavailable reads. A successful read does not make an old imported observation current. A failed source retains the prior dated book. Browser disconnection or paused refresh marks retained observations as unavailable for new reads and continues aging their displayed capture times.

The opt-in installed Codex collector launches its own stdio app-server process, performs `initialize`, `initialized` and `account/rateLimits/read`, then cleans only that owned process group. The protocol exchange has a deadline and a 1 MiB output bound. It never sends thread, turn, login or mutation methods, reads credentials itself, or copies account IDs. Native `captured_at` records local receipt time because the response has no qualified upstream sample timestamp. A present rate-limit map takes precedence over the legacy view, including an empty map. Unknown fields such as paid credits are discarded rather than reinterpreted as subscription allowance. Server shutdown waits for the bounded quota worker to clean up.

A raw Codex account response can also be normalized with `quota --file response.json --format codex --captured-at ORIGINAL_RESPONSE_TIME`. The original time is required. This conversion does not establish a live connection. From v0.8.32, an opt-in [Claude CLI rate-limit event capture](claude-rate-limit.md) writes the same private normalized quota-book schema. It retains per-window frame receipt times and reported resets. It is not an account poll, does not expose raw session IDs or overage details, and cannot authorize spending or resets. No Claude account endpoint reader or hook ships here.

## Observed daily token threshold

`serve --token-threshold N` and `top --token-threshold N` accept an explicit positive integer up to one trillion tokens. When configured, snapshot and `top --json` add `token_threshold` with schema `monitoring.token-threshold.v1`. Older owners omit the field; consumers should retain that compatibility. The source selection and ledger are unchanged by this option.
Capabilities advertises `token_threshold: monitoring.token-threshold.v1` only when configured.

The block reports `scope: all-accepted-events-unfiltered`, `complete_coverage: false`, a current UTC-day `[from, until)` window, read `observed_at`, `limit_tokens`, `observed_tokens`, `remaining_observed_tokens`, `usage_records`, `excluded_receipt_summaries`, `excluded_future_records` and `state`. State is `no-observations`, `observed-below` or `observed-reached`. Every accepted usage record with a producer timestamp from midnight UTC through the read time contributes its disjoint token buckets, regardless of dashboard filters. Status and tool records do not contribute. Future-dated rows are excluded until their timestamp arrives. Cumulative Claude CLI result summaries are excluded because their capture receipt time cannot assign work to that day.

This is an on-screen threshold over the accepted ledger, not a provider quota, complete account usage, billed cost, notification or call-enforcement mechanism. Missing or unavailable sources can make actual use higher. The observation does not trigger model calls, send a message or reset any allowance. The supplied Deep reader validates this optional block but need not display it.

## Observed daily cost thresholds

`serve` and `top` optionally accept `--estimated-cost-threshold AMOUNT` and `--billed-cost-threshold AMOUNT`. Each is a positive plain decimal with at most six fractional digits, no greater than one billion in the selected display currency. When either is configured, snapshot and `top --json` add `cost_thresholds` with schema `monitoring.cost-thresholds.v1`. Capabilities advertises this optional block only when configured. Older owners omit it, so a strict Deep reader must explicitly admit the new field before selecting this owner version.

The block contains `scope: all-accepted-events-unfiltered`, `complete_coverage: false`, `currency`, a UTC-day `[from, until)` window with read `observed_at`, and one `items` row per configured basis. Each row has its own `limit_amount`, nullable `observed_amount` and `remaining_observed_amount`, `state` (`no-observations`, `observed-below`, `observed-reached`), covered, unpriced, unconverted, other-basis, future and receipt-time record counts, and up to 20 cost provenance labels with a truncation flag. Amounts are decimal strings. Every accepted usage record in the elapsed UTC day is considered independently of dashboard filters. Explicit event cost takes precedence over a matching dated price estimate. A price or exchange rate is never guessed. Billed and estimated records cannot contribute to each other's threshold. Future-dated rows wait until their timestamp arrives. Claude CLI cumulative result summaries count on receipt day because individual call dates are unavailable; that differs from the activity-day token threshold.

These are local on-screen comparisons of known subsets, not provider quota, a complete invoice, an outbound notification or spending enforcement. A missing source or cost can make actual use higher. The ledger and provider account are unchanged.

## Detailed report

`GET /api/v1/report` accepts the same dimension, date and current-state filters as snapshots. State selection shares the snapshot evidence logic across the full ledger, even if the latest status lies outside the chosen usage period. It never revives old running evidence as current. Future records remain in the ledger but are excluded from reports, with an explicit count. Requested end times after the read time are clamped to observation time.

Response fields include `identity`, `revision`, `observed_at`, `filters`, current `range`, `previous_range`, `summary`, `breakdowns`, `tools`, `comparisons` and `coverage`. Project/model/day groups live under `breakdowns.<name>.rows`, with truncation flags. Project and model groups rank by recorded tokens, tool groups by record count, and dates chronologically. Each group is limited to 100 displayed rows while totals retain the entire selected data. Coverage records selection and truncation explicitly.

`currency` identifies displayed cost units. `fx` returns the supplied dated conversion book or null, preserving its rate date and provenance. A converted report never claims that those are current market or invoice rates. The CLI's JSON export includes the same qualifications.

The browser's **Download displayed report JSON** saves the last successfully displayed `monitoring.report.v1` response without adding a new endpoint or recomputing values. It is disabled until a report loads or when current filters differ from the displayed report's request. A retained response may be downloaded after a read failure or during paused refresh under its original filters; `observed_at`, revision, normalized filters and coverage remain in the file. It is a local, user-initiated download of private aggregates and aliases, not a live provider export or raw event/transcript dump.

An explicit `since` anchors a comparison. The previous inclusive millisecond interval ends immediately before the current interval and has the same elapsed length. Its exact bounds are returned. This is not an implicit day-over-day or month-over-month claim. All recorded history has no fabricated earlier observation period. Source coverage can differ between intervals. Zero prior baselines produce no infinite percentage.

The legacy `calls` field counts normalized usage records. It does not establish all provider requests or calls across unconnected tools. Observed sessions are distinct source/session pairs among matching events, including tool/status-only sessions. They are not a live worker count or proof of complete coverage.

`known_estimate_per_priced_session` divides the converted estimated subtotal by distinct sessions with at least one converted estimated record. It remains a qualified subtotal if those sessions also contain unpriced or billed records. Estimated and billed changes are calculated only when both periods have complete converted coverage for the chosen basis. Otherwise current/previous known amounts remain visible while change and percentage are null with a coverage reason.

Recorded cache-read share is `cache_read / (input + cache_read + cache_write)` over normalized buckets. A zero denominator is unknown. This is a recorded token share, not a universal provider cache-hit metric. Reported duration percentiles use nearest-rank observed values. Output rate sums output tokens and positive explicit durations over the timed subset only. Missing durations stay missing, and no duration is inferred from timestamp gaps. This is end-to-end recorded rate, not model streaming speed.

Tool groups use explicit normalized categories. MCP groups require an explicit server alias. Shell, skill and agent groups describe invocation aliases, never raw arguments or live worker identities. Additive `tools.unknown_outcomes` at report and group-row level counts records without success or failure evidence; older owners may omit it and consumers should display unknown rather than zero. Claude's explicit-file adapter can classify recognized native tool names and exact MCP namespaces, pair completed tool-use/results out of order, and reject conflicting replay metadata. Incomplete pairs are uncounted. It never retains result text or reads tool arguments for command or skill names.

`tools.daily` is an additive bounded series over the same selected tool events as `tools.records`. Its `rows` are ascending UTC dates, each with `records`, `errors`, `unknown_outcomes` and explicit category counts. It shows the latest 100 recorded dates and sets `truncated: true` when older dates are omitted; aggregate tool totals retain all selected dates. Missing dates are not inserted as zero-activity evidence. The browser plots category proportions within each day and relative daily volume, with counts and outcomes in text and accessible labels. Old owners without this field remain readable and show an unavailable timeline. No date bucket implies worker liveness, complete coverage or billed cost.

Context overhead, one-shot rate, workflow correction rate, time to first edit and rework lack supported evidence in v1 and are explicitly unavailable. A corrected token count is an accounting revision, not a workflow correction measure. Importing a screenshot or seeing a long timeline does not establish those measures. Account quota remains outside report period comparisons and does not authorize work or spending.

## Read-only ingestion readiness

`sources preview --sources MANIFEST [--db EXISTING_LEDGER] [--json]` returns `monitoring.source-readiness.v1`. This is an explicit local CLI operation, with no new HTTP write or source-selection route. Its `read_only: true` means accepted source-ledger history is unchanged. Ingestion runs against a private disposable copy, not the supplied ledger. Native metadata parsing and explicit source permissions remain unchanged.

The response contains `observed_at`, `scope: explicit-inputs-and-ledger-snapshot`, `base` (`kind`, original `revision` and `events`), `ready`, ordered `inputs` and a scope note. Each input has `id`, `format`, `state` (`ready`, `unavailable`, `rejected`), optional `summary`, optional `proposed` (`added`, `replayed`) and a generic `error`. The summary is the same path-free unique-observation metadata used by source coverage. A rejected attempt may still expose its valid attempted summary while `proposed` remains null. No proposed events have been accepted into real history.

Inputs are attempted in manifest order against the same temporary copy. Successful earlier inputs affect later replay/conflict results. Rejected batches roll back and do not stop later inputs. The 100,000-event view bound is checked before accepting a proposed batch into the copy. Counts are not invoices, process ownership or guaranteed future acceptance.

An explicit base must be a regular existing ledger with the required events columns. Parent directory aliases are canonicalized and final symlinks refuse. SQLite read-only backup includes committed WAL and excludes uncommitted writes. Copying has a five-second progress deadline and a 512 MiB source-file bound. The copy is private and is removed even when the consuming preview fails. Source paths, native errors and row bodies never appear in readiness diagnostics. The ledger snapshot is consistent, while selected source files are read sequentially and can change afterward.

## Optional local OS process samples

The standalone terminal may use `top --sources` to refresh one explicitly selected manifest and its ledger without starting HTTP. Optional `top --processes` samples macOS executable names separately. One-shot `top --json` uses the existing additive `inputs`, `source_coverage` and `os_processes` blocks; the HTTP read schema and Deep receiving contract do not change. See [terminal top](terminal-top.md).

With `serve --processes` on macOS, snapshot and report may include `os_processes: monitoring.os-processes.v1`; capabilities advertises the optional schema. The independent block records read state, sample time, allowlisted executable basenames, PID/parent PID, CPU percent, resident bytes and UTC process start time, plus changed and no-longer-observed counts and the configured 2–60 second sample interval. From v0.8.26 optional `history` contains up to 60 aggregate successful samples and `history_truncated` reports the in-memory limit; segment changes mark failed or missed reads. Older owners omit those fields. It never advances ledger revision or changes entity lifecycle. Session and worker mappings are null. Browser pause/read loss makes current presence unknown. See [process coverage](os-processes.md).

An explicitly selected Claude JSONL input may add `claude_responses: monitoring.claude-responses.v1` to snapshot and report, with an optional capability advertisement. This independent block has `configured_sources`, `unavailable_sources`, `truncated` and at most 100 latest `rows`. Each row has `source`, `project`, hashed `session`, `prompt_at`, nullable `response_end_at`, nullable `stop_hook_at`, `evidence` (`prompt_observed`, `response_end_observed` or `stop_hook_observed`) and `source_state`. It is native response evidence, not a normalized session state or process relation. Source/project/session filters are applied only in the browser; other ledger filters do not apply. See [Claude native source](claude-native.md).

An explicitly selected Codex JSONL input may add `codex_turns: monitoring.codex-turns.v1` to snapshot and report, with an optional capability advertisement. This independent block has `configured_sources`, `unavailable_sources`, `truncated`, `ambiguous_turns` and at most 100 latest unambiguous `rows`. Each row contains source/project aliases, hashed `session` and `turn`, nullable `start_at` and `complete_at`, nullable source-reported `duration_ms` and `time_to_first_token_ms`, `evidence` (`paired`, `start-only` or `completion-only`) and `source_state`. Conflicting duplicate markers and reversed start/completion order are omitted and counted as ambiguous. Missing or malformed optional markers do not cause otherwise valid usage to fail. Source/project/session filters apply in the browser; other ledger filters do not. These markers neither enter the usage ledger nor change session/worker lifecycle. A completed turn does not prove session closure or task acceptance; an unmatched start does not prove current activity. Owners without this block remain compatible. See [Codex native source](codex-native.md).

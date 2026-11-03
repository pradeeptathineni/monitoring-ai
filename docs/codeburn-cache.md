# CodeBurn cached usage

Monitoring can read one explicitly selected CodeBurn v74 daily cache, produced by CodeBurn 0.9.27. It does not run CodeBurn, scan native session directories, install hooks or contact a provider.

```sh
python3 -m monitoring_ai serve --db /path/to/private/events.sqlite \
  --codeburn-cache /path/to/selected/daily-cache.v74.json
```

Open **Usage & costs**. The CodeBurn panel has its own calendar-date selection. Event provider/model/project/date filters do not apply to these cached aggregates. Configure the file in your startup command. Settings describes the connection separately from the data view.

## Accounting and scope

Daily records supply calls, sessions, recorded input/output/cache-read/cache-write token buckets and a source-reported USD amount. Several CodeBurn adapters subtract overlapping cached input, but every provider branch has not been qualified. The view therefore preserves the four buckets without summing them into a provider-authoritative total. The reader discards project paths, names, origins and all unrelated fields. From v0.8.4, it also projects the separate model/provider daily rollups described below. It does not open CodeBurn's separate session cache or any native transcript.

These replaceable daily totals stay separate from the immutable event ledger. They never create synthetic sessions, worker states, call timestamps, token pace or price estimates. They are excluded from event totals and forecasts, which avoids adding overlapping observations twice. A day can be revised downward as well as upward. The whole next valid cache replaces the previous cache view.

`source_cost_usd` retains the supplied amount without assigning a billing basis. The v74 daily row has no complete billed-versus-estimated split. It is neither an invoice nor a subscription balance. Ledger cost cards keep their own estimated, billed and unknown meanings. Costs in this panel remain USD even if the event view uses another display currency.

Dates use CodeBurn's local-calendar buckets. They are not UTC timestamps. Per-day session counts must not be summed into a unique-session count across days. Carried rows, incomplete coverage and an unknown or false computation watermark remain visible qualifications. The cache's latest computed date is not proof that all provider activity was captured.

## Freshness and failure

The selected file is read every five seconds. Read time proves only that Monitoring parsed the file. `file_modified_at` and its signed age describe the file's last write, not the latest AI call. CodeBurn refreshes its cache on demand through its own workflows, so Monitoring does not promise an upstream update cadence. Repeated successful reads cannot make old activity current.

A failed read retains the last accepted totals and marks the source unavailable. Before the first successful read, data is unknown. An empty valid cache does not establish provider idleness. Polling resumes against the same selected path. Monitoring never changes the source file or deletes event history.

The reader accepts only integer format version 74. It checks a regular final file without following a symlink, caps reads at 32 MiB and 3,650 day rows, and rejects duplicate JSON keys/dates, malformed dates, missing or invalid counters and invalid costs. Errors exclude paths and raw content. Other versions need separate source review before support is expanded.

## Read contract

Capabilities advertises `cached_usage: monitoring.cached-usage.v1`. When configured, snapshot and report responses include independent `cached_usage_status` and nullable `cached_usage`. Unconfigured owners omit both. Existing event schemas, revisions, filters and ledger consumers remain unchanged.

Status contains `state` (`unread`, `following` or `unavailable`), nullable `last_success_at` and a nullable generic `error`. A successful book contains `schema`, `source: codeburn-daily-cache`, `format_version: 74`, `sampled_at`, `file_modified_at`, signed `file_age_seconds`, `date_basis: codeburn-local-calendar`, `complete`, nullable `watermark_trusted`, nullable `last_computed_date`, ordered `days`, `read_status` and `retained`. Each day contains `date`, decimal-string `source_cost_usd`, integer `calls` and `sessions`, `tokens` with `input`, `output`, `cache_read`, `cache_write`, boolean `carried` and optional `breakdowns` as described below. No source path or arbitrary metadata is returned.

This optional view has its own replacement and freshness semantics. It does not advance the ledger revision. Receiving applications must opt into displaying it rather than interpreting these rows as normalized events.

## Qualification

An explicitly selected local cache from installed CodeBurn 0.9.27 passed exact daily-counter and source-amount comparison through the parser, API and independent dashboard. Repeated file polls advanced file age without changing the underlying values. Removing and restoring a private metadata-only projection verified retained totals and recovery without modifying the original cache. Raw data, actual values, source paths and screenshots remain private. Public browser cases use fictional rows.

This establishes daily-cache compatibility. The observed cache was historical, so it does not establish current native session activity, live worker coverage or complete provider billing.

## Provider and model rows

From v0.8.4, the cache panel can show **Daily totals**, **Providers by day** or **Models by day**. Choose one provider or model alias within that view, alongside the existing cache-date controls. Rows retain their original date. No costs or counts are combined across days. The event view filters remain separate.

Each day adds optional `breakdowns` with `models` and `providers`. A missing map is null, meaning unknown attribution. An empty list means no represented rows, which does not establish complete coverage or zero usage. Each row contains `id`, `calls`, `source_cost_usd`, nullable `sessions` and the four recorded `tokens` buckets. Model session counts are unknown. Missing legacy provider buckets or session counts remain null, while known zero stays zero. Malformed present values refuse the cache read and retain the prior accepted snapshot.

Provider and model maps are separate marginals. Their rows cannot be intersected to infer a provider/model pair. Nested provider model maps are deliberately omitted from this version. The UI reports selected-date coverage without filling missing attribution from other totals.

Short identifier labels are retained. Ordinary ASCII model names with spaces become readable bounded aliases with a digest suffix. Paths, controls, unsupported characters and consecutive dots become opaque digest aliases, with collisions refused. Raw unsafe keys never reach the API. Each map is capped at 128 rows and the whole cache at 2,048 projected breakdown rows, in addition to the existing file/day limits. These bounds do not change event-ledger limits.

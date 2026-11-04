# Selected Codex app turns

This optional source reads bounded turn metadata through the supported app `read_thread` tool in an already authorized assistant runtime. It does not read native session directories, create a host or account connection, run a model or install ongoing access. A standalone Monitoring process cannot invoke that tool itself.

The [finite selected-turn recipe](../integrations/codex-app/selected-turns.js) accepts one to four explicit Codex task selections. Each read uses `turnLimit: 4`, `includeOutputs: false` and `maxOutputCharsPerItem: 1`. It accepts the observed schema version 1, newest-first metadata page, validates the returned task/host identity and discards messages, titles, previews, paths, item bodies and outputs before invoking any local capture process. It does not follow turn-page cursors. The `wait_threads` interface is not used and cannot wait on its calling task.

```js
await captureSelectedCodexTurns({
  projectRoot: "/path/to/monitoring-ai",
  output: "/private/selected-codex-turns.json",
  targets: [{ threadId: "explicitly-selected-codex-task", hostId: "local" }],
  samples: 1,
  replace: false,
});
```

Run the recipe only inside the supported assistant tool runtime. IDs must come from a supported explicit task selection. The recipe does not discover tasks itself. One to three finite samples are allowed, ten seconds apart. Later samples replace the same selected-task set. Use a new output for a different selection. An error stops the recipe and leaves the prior export to age normally. No subscription reset, paid model call or API credential is used.

For a manually supplied metadata projection, `sources capture-codex-app-turns --output FILE --captured-at ORIGINAL_TIME` reads a JSON array from stdin. Each task has only `id`, `host`, `updated_at`, `observed_at`, `has_more` and `turns`. Each turn has only `id`, `status`, `started_at`, `completed_at`, `duration_ms` and `error_present`. Timestamps use ISO time with timezone. Missing source timing/duration is null. Statuses are `inProgress`, `completed`, `failed`, `interrupted` or `unknown`. New unsupported status strings become unknown. Error bodies are never accepted.

New exports are atomically published mode 0600. Task and turn IDs are hashed before writing. The task alias shares the authoritative Codex app-ID family with recent/history app inventory. It does not identify a ledger session, native transcript turn or process. Turn aliases have their own namespace. Extra fields, repeated identities, oversized pages, invalid timing, changed selection and older/conflicting replacements refuse. The reader accepts at most 64,000 bytes, four tasks and four turns per task.

```sh
python3 -m monitoring_ai serve --db /private/ledger.sqlite \
  --app-turns /private/selected-codex-turns.json
# The same selection is available in the terminal:
python3 -m monitoring_ai top --watch --db /private/ledger.sqlite \
  --app-turns /private/selected-codex-turns.json
```

Sessions shows **Selected Codex app turns** with task-alias and current-status filters. **Show app task** is offered only when an explicitly selected app inventory contains the same authoritative task alias. It focuses that inventory row without joining ledger or OS identities. Ledger filters and app-inventory filters do not filter turn rows. Source start/end time, reported duration and metadata observation time remain separate.

Each task's status is current only while its own metadata observation is no older than 30 seconds, the batch capture is recent, and source/browser reads are available and unpaused. A new batch does not renew an older task observation. Otherwise prior reported status is retained with unknown current status. A selected task with no supplied turns also has unknown turn status. Read failure retains dated metadata and recovers through the existing selected-file path.

An in-progress turn can be waiting for a tool or approval and does not prove model inference. A completed turn does not establish task, conversation, worker or process completion. Error presence describes a reported turn error without its details. Missing end time or duration is not computed. Counts cover only explicitly selected task pages, with older pages and all other tasks unobserved. This source supplies no token, cost, quota or billing evidence.

## Optional consumer contract

`/api/v1/capabilities` advertises `capabilities.codex_app_turns: "monitoring.codex-app-turns.v1"` only when selected. Snapshot v1 may then include `codex_app_turns` with `schema`, `source: "codex-app-read-thread"`, `scope: "selected-task-turn-pages"`, `complete_coverage: false`, `captured_at`, `tasks`, `read_at`, derived `age_seconds`/`freshness` and `read_state`. An unavailable first read has the schema, unavailable state and empty tasks. A later failure retains prior rows and capture times.

Each exported task has `alias`, `host`, `updated_at`, `observed_at`, `has_more` and `turns`. Each exported turn has `alias`, `status`, `started_at`, `completed_at`, `duration_ms` and `error_present`. Derived root freshness does not replace per-task age checks. A consumer must preserve incomplete coverage, unknown timing and the distinction between a reported turn and a worker/session lifecycle.

This optional block never enters normalized ledger events, report accounting or active-entity counts. Older consumers may ignore it. Deep owns admission, validation and presentation of this new block. Accepting earlier app-inventory profiles does not establish selected-turn adoption or managed UAT.

One real finite trial qualified two selected Codex tasks through capture, the optional owner block, browser filtering, app-alias focus and pause-to-unknown. Both in-progress and completed turns were present, with source timing fields and no ledger activity. Temporary services stopped after the trial. It did not qualify general native lifecycle, every task/host, token accounting or unattended acquisition. Unsupported status strings and malformed metadata have focused fixture checks. Error-bearing, failed and interrupted turns remain unqualified against actual producer data.

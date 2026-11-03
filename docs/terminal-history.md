# Inspect entity history in the terminal

`history` reads one exact source/session/entity from an existing Monitoring ledger. It requires no web server or provider connection and does not create a missing ledger. It opens the selected SQLite file read-only. Use the aliases shown in Sessions or Event stream, not a file path or a guessed worker relationship.

```sh
python3 -m monitoring_ai history \
  --db /private/monitoring-ledger.sqlite \
  --source example-source --session example-session --entity example-worker \
  --limit 100
```

Records are oldest first. The terminal shows the ledger revision, matching count, source timestamps, usage tokens, supplied cost basis/currency/provenance, lifecycle states and tool outcomes. A missing cost is shown as unreported; this command does not apply a price book or exchange rate. Provider, model, surface, project and UTC date filters are optional. Current-state filtering is intentionally absent because history includes prior states.

For another page, reuse the same selection and filters with the displayed `--after` and `--expected-revision` values. A changed revision refuses the later page; restart at offset zero. `--json` emits `monitoring.history-cli.v1` with `revision`, `total`, `events` and `next_offset`. This is a local CLI page without a running owner's identity, distinct from the HTTP `monitoring.events.v1` contract. Each page has at most 500 records and the alpha ledger read limit is 100,000 events.

The command reads only normalized metadata already accepted into the ledger. Source timestamps can be old even when a file was just read. A recorded `running` status is historical evidence, not proof that a process or worker still exists. Process samples and provider quota remain separate.

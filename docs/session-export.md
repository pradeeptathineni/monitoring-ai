# Export one recorded session

`export-session` publishes selected normalized metadata to a new private JSONL file. It does not copy the whole ledger, read native provider files or contact a provider. Use exact recorded source/session aliases from session search or history.

```sh
python3 -m monitoring_ai export-session \
  --db /private/current-ledger.sqlite --output /private/selected-session.jsonl \
  --source example-source --session example-session --expected-revision 123
python3 -m monitoring_ai import \
  --db /private/another-ledger.sqlite --file /private/selected-session.jsonl
```

The source ledger is copied through the existing read-only consistent-snapshot path, including committed WAL data, then validated. The optional expected revision refuses if the source ledger changed. Entity, provider, model, surface, project and UTC date filters can narrow the exact session. Current-state filtering is absent so older status records are retained. Filters may omit parents; missing parent observations stay unknown.

The output is chronological `monitoring.event.v1` JSONL, mode 0600, published atomically without overwriting any existing path. Selections exceeding 50,000 events or 32 MiB refuse rather than publish a truncated file, so the output remains within normal import limits. Narrow the filters if needed. An empty selection produces an empty export with a zero-event receipt. The JSON receipt records the source ledger revision, filters, event/byte counts and SHA-256. It omits local paths. If directory durability cannot be confirmed after publication, the command explicitly reports that the file exists and needs verification before retrying.

Source IDs and event IDs are preserved, so importing the identical export again adds no events. A conflicting accepted ID refuses rather than becoming another call or silently replacing usage. Recorded estimated/billed cost, currency and provenance are retained; missing costs stay missing. A price book or FX conversion is not applied. Current accepted event values are exported, without correction audits, native usage versions, source-health inventory, quota snapshots, OS samples, app task observations or provider-native supplementary markers. Use a full private ledger backup when those ledger audit tables must be preserved.

A destination ledger has its own revision and owner identity. Importing does not upgrade or reconnect a running Deep owner. A deliberately selected normalized source can consume the file through the existing contract, with source coverage still limited to the exported records. This transfers recorded history and does not turn an old event into live activity. Treat exported aliases and usage as private operational data.

# Codex and ChatGPT app task observations

An explicitly selected metadata export can show the task status reported by the app's supported read-only inventory. It uses no native session-directory access, transcript parser, provider credential or process/session correlation. This is separate from event-ledger sessions and workers. App `active` and `idle` describe the app's task state, not whether a model is currently generating tokens or whether a worker completed its task.

An authorized bridge first selects only `id`, `kind`, `status`, `host` and `updated_at` from a supported inventory response. Convert the source update time to an ISO timestamp, map host class to `local`, `connected` or `unknown`, and send the selected rows to the capture command. Do not pipe a complete response containing titles, summaries, prompts or paths. The command refuses extra fields, hashes IDs, retains source update time, and requires the original inventory observation time.

```sh
# selected-metadata.json contains only the five selected fields per row
python3 -m monitoring_ai sources capture-app-threads \
  --output /private/app-tasks.json --captured-at ORIGINAL_OBSERVATION_TIME \
  < selected-metadata.json
python3 -m monitoring_ai serve --db /private/ledger.sqlite \
  --app-threads /private/app-tasks.json
# Later supported inventory read: same capture command with --replace
```

New outputs are mode 0600. Replacement requires an existing owned private export and refuses older or conflicting capture times. No raw thread ID, title, summary, project or working directory enters the export. The browser and terminal show aliases, app kind, reported status, source thread update time and host class. Unsupported task statuses become unknown.

The selected `monitoring.app-threads.v1` file is reread on snapshot requests. A read does not advance `captured_at`. Captures older than 30 seconds, unreadable files, paused views or lost owner reads make current app status unknown; previous rows remain historical observations. The file represents a recent app page, not every session or complete account coverage. Counts are not added to ledger sessions, active workers, tokens or costs. The report contract is unchanged.

One actual supported app inventory page (schemaVersion 4) was qualified through capture, private export, owner snapshot and headless UI. It supplied both Codex and ChatGPT task kinds; a later real capture changed Codex's reported status counts. This establishes current app-reported task observation, not general native lifecycle, inference-call usage or a deployed continuous collector. Sustained recent visibility requires an authorized bridge to refresh the selected export from supported inventory reads. No persistent bridge, hook, credential or background task is installed by this feature. Existing CLI result capture remains a separate Claude source; this inventory supplies no Claude or OpenAI API request rows.

The [finite refresh recipe](../integrations/codex-app) provides a supported assistant-runtime bridge for one to three inventory reads ten seconds apart. It was qualified with three genuine source refreshes and corresponding recent UI updates. It stops automatically and installs no persistent access. The standalone still needs the selected export to be refreshed after that run; stale rows remain unknown.

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

## Local Codex host candidate

The [official app-server protocol](https://developers.openai.com/codex/app-server) exposes thread metadata over local transports. Its `thread/list` parameter `useStateDbOnly: true` avoids JSONL scan-and-repair. The installed Codex CLI 0.160.0 generated schema confirms that parameter, and its proxy command targets an already running local control socket.

The read-only daemon-version check found no default daemon socket during qualification. No daemon was started, alternate private endpoint discovered or denied session path read. A future adapter must attach to an explicitly selected host that actually owns the relevant work, enforce database-only reads and discard names, previews, paths and other unselected fields locally. Starting an empty daemon would not establish current desktop visibility. This candidate has no actual source-to-UI qualification and does not supply the Codex/ChatGPT inventory export above.

A long-running connection, new daemon or expanded source access needs its concrete host, executable, data scope and stop path reviewed before use. The current finite assistant bridge remains the qualified acquisition route for that app inventory.

## Browse one Codex history page

The finite bridge also supports `view: "codex-history"`, reading one supported non-archived Codex task metadata page with limit 50. For explicitly supplied metadata rows, `sources capture-app-threads --codex-history` selects this profile. It refuses non-Codex kinds and more than 50 rows. The export uses source `codex-app-task-history` and scope `codex-history-page`; recent combined app capture remains separate. New captures require a new private file, and replacement cannot silently change an existing file's source profile.

Use the [bounded capture recipe](../integrations/codex-app) before selecting the private output with `--app-threads`. The view supports kind, current-status and alias filters, with 20-row pages over the supplied capture. Filtering active on a stale, paused or unavailable capture yields no currently active rows because their current status is unknown. Prior reported status remains visible when inspecting unknown rows. These are contextual data controls, not new saved configuration.

One page is not a full account roster or all active work. UI pagination stays within that capture. Source update time and capture time remain distinct. This profile makes no ChatGPT history claim and does not infer model activity, worker lifecycle, tokens or billing. Strict Deep consumers need the exact source/scope variant accepted. A real source capture qualified the standalone workflow and a prepared isolated Deep receiving patch; managed adoption and unattended acquisition remain pending.

## Inspect changes between captures

Expand **App status changes** to see up to 24 metadata changes observed in the open tab. The first fresh capture establishes a baseline. Later fresh captures from the same owner, source and scope report changed app statuses, changed source metadata, newly supplied aliases and aliases absent from the latest captured page. Absence has unknown current status and does not imply completion or deletion. Capture time and browser observation time are shown separately. Task-table filters do not hide these observations.

Pause, stale data, failed reads and capture gaps longer than 30 seconds break comparison continuity. The next fresh capture establishes another baseline without claiming changes across that gap. Older observations remain explicitly historical. Changing owner/source/scope or removing the app source clears the list. **Clear app observations** empties it while retaining the current baseline. Reloading or closing the tab discards everything. Nothing is written to local storage or the ledger, and this control adds no source access or refresh capability.

A finite real supported Codex capture trial observed a reported app-status change through the normal owner refresh and this list. Pause changed current visibility to unknown. No model call, ledger lifecycle, persistent access or managed Deep change was involved. This qualifies bounded observation of app-reported changes, not complete live coverage or unattended acquisition.

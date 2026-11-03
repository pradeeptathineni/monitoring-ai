# CodeBurn status output

Monitoring can read one explicitly selected `menubar-status.json` produced by an existing CodeBurn 0.9.27 installation. This is an optional compatibility adapter, implemented in Monitoring with Python's standard library. CodeBurn is not a Monitoring runtime dependency. The adapter reads only the selected output file; it does not run CodeBurn, scan native transcripts, install hooks, contact providers or change the file.

```sh
python3 -m monitoring_ai serve --db /path/to/private/events.sqlite \
  --codeburn-status /path/to/selected/menubar-status.json
```

The Sessions view places these observations below Monitoring's event-backed sessions. CodeBurn's `liveSessions` block reports transcripts written inside its source window. A transcript write does not prove that its process is running. `generated_at` is when CodeBurn built the output; `file_modified_at` is when the selected file was written; `sampled_at` is when Monitoring read it. The source and file ages remain separate. Monitoring polls the selected file every five seconds, but a fresh read of an old file does not make the underlying observations current. The dashboard treats generation older than 30 seconds, unreadable output, a paused read or a disconnected owner as unknown current visibility.

An absent `liveSessions` block means unknown coverage. An empty `sessions` list means CodeBurn represented no transcript writes inside its window **at generation**; it does not establish current idleness or complete provider coverage. Each present row carries an ephemeral anonymous alias, provider/model identifier, start time, last transcript write time and nullable context token/window counts. It never supplies an event ledger state, per-call usage or billed cost. Monitoring drops the source's raw session ID, project, branch, `idleSeconds` and all unrelated content. The alias is keyed with a new process-local salt each server start and is unsuitable for joining across restarts or other sources.

`monitoring.codeburn-activity.v1` is an optional snapshot/report field with independent `codeburn_activity_status`. Unconfigured owners omit both. Status can be `unread`, `following` or `unavailable`. A failed read retains the previous accepted observation and marks it unavailable; no earlier observation means current visibility is unknown. A new valid file replaces the whole view, including a downward or empty revision. These observations do not create normalized events, advance the ledger revision or join Monitoring's session hierarchy. Receiving applications must opt in explicitly.

The parser accepts the inspected menubar output shape from CodeBurn 0.9.27. It rejects symlinks, nonregular or over-2 MiB files, malformed JSON, duplicate keys, invalid timestamps/counters and more than 1,000 session rows. Validation errors returned to the owner contain no path or raw content. The source format has no independent version field, so a future producer change requires renewed compatibility review.

The actual selected local status file was qualified through the parser and read-only dashboard without opening transcripts or publishing private rows. Its observed generation time was old and its session block empty. That proves the unavailable/empty presentation path, not live-session detection under active load. A fresh source with represented sessions and general provider compatibility remain to be qualified. Native provider collectors remain part of Monitoring's own core roadmap rather than relying on CodeBurn to run.

# Follow selected sources in the terminal

`top` can read an existing ledger, or own reads from an explicit private source selection. A separate web server is not required for the second form. The selection is loaded once at startup and contains only paths the operator chose. Monitoring does not scan for other sessions, start agents or install hooks.

```sh
python3 -m monitoring_ai top \
  --sources /private/selected-sources.json \
  --db /private/monitoring-ledger.sqlite \
  --watch
```

`--watch` requires an interactive terminal. It refreshes selected files and the displayed ledger every two seconds until Ctrl-C. A missing selected file stays unavailable, then becomes readable if that exact path appears. Accepted history remains visible when a later read fails. The terminal lists each selected source's read state, accepted record count and latest event age. A successful read of an old event still says stale; an unavailable source does not mean the provider is idle.

While watching, the terminal also lists up to five newly visible or revised ledger records since this `top` process started. It compares the latest 100 matching records at each read, keeps source event time separate from read observation time and says when a revision changed outside that visible window. A new record may have been backfilled and does not prove a worker is running. One-shot `top --json` and the HTTP read schema are unchanged.

On macOS, add `--processes` to sample allowlisted executable names, PID, CPU and resident memory separately. `--process-interval N` chooses a 2–60 second interval, default five. No arguments, environment or conversation content are read. The process list does not identify a recorded session or worker. An unavailable OS sample makes current presence unknown. On other platforms, leave this option off.

Use `--json` without `--watch` for one selected-source refresh and a machine-readable snapshot, including `inputs`, `source_coverage` and optional `os_processes`. Without `--sources`, `top` only reads its existing ledger; it does not follow native files. Avoid running `top --sources` and `serve --sources` as competing owners of the same selected inputs and ledger. If another owner already follows them, point `top --db` at that ledger without `--sources`.

For a new Claude CLI invocation, first create a [private structured-stream capture](claude-cli-stream.md) as a normalized selected input. `top --sources` can run before the capture file exists; it will show the invocation after the native init frame arrives and its reported result after completion. Its parent conversation remains unobserved, and source age continues to advance after the invocation. Tokens and costs require separate reviewed usage evidence. A project-only Claude Code 2.1.308 invocation qualified the missing → running → completed terminal transition; public examples remain fictional.

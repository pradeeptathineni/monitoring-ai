# Capture one Claude CLI invocation

Claude Code supports `--output-format stream-json --verbose` for a printed invocation. Monitoring can consume that stdout as it arrives and write normalized start/end status to a new private file. An optional second file holds sanitized per-model result totals. This path was checked against installed Claude Code 2.1.308 and the [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference). It does not read Claude's saved session directory, install hooks, start an account connection or run the model itself.

In one terminal, choose new absolute output paths in an existing private directory. One command creates the linked status/result selection before the files exist:

```sh
python3 -m monitoring_ai sources select-claude-stream \
  --output /private/claude-status-sources.json \
  --status-file /private/claude-stream-events.jsonl \
  --result-file /private/claude-result-summary.jsonl \
  --source-id owned-claude-cli --project my-project
python3 -m monitoring_ai serve --sources /private/claude-status-sources.json \
  --db /private/claude-stream-ledger.sqlite
```

In another terminal, pipe one invocation's stdout through the capture command:

```sh
claude -p --output-format stream-json --verbose --no-session-persistence \
  "Your authorized project task" | \
python3 -m monitoring_ai sources capture-claude-stream \
  --output /private/claude-stream-events.jsonl \
  --result-output /private/claude-result-summary.jsonl \
  --source-id owned-claude-cli --project my-project
```

The capture command consumes the native JSON stream and prints a small status receipt. By default it does not forward prompt or reply text. Use `--forward` only when you deliberately want the unchanged native stream on stdout for your own downstream consumer; the receipt then goes to stderr. The status file remains metadata-only and mode 0600. Use a new status file for each invocation. Re-capturing the same native stream at a new time under the same source ID can conflict with immutable event IDs. The optional `--result-output` creates a second 0600 metadata file only after a matching result arrives. Its selected input reports unavailable until then.

After capture, run `sources check --sources /private/claude-status-sources.json` to verify both files. The selection command parses existing selected files before publication to check the status source/project alias and observed session match. It refuses malformed or mismatched existing content and never overwrites a manifest. It permits missing output files so the observer can start first. The quota observation is not a ledger source: pass its separate private file with `serve --quota-file` or `top --quota-file` when configured.

The `system/init` frame becomes a `running` observation of that CLI invocation. A matching `result` with `is_error: false` and `subtype: success` becomes `completed`; an explicit CLI error becomes `failed`. An incomplete stream, malformed row after init or unqualified result becomes `unknown`. These timestamps are when the local capture received the frames, not model-token or provider billing times. A running observation ages to unknown if output stops before a result.

The invocation is an agent entity under the hashed native conversation identity. The parent remains unobserved unless another selected source supplies it. `completed` means the CLI invocation reported success, not that its answer is correct, a worker process was identified, or the wider conversation closed. The status file emits no tokens or costs because assistant rows can repeat the same message usage. With `--result-output`, the sanitized result file uses the existing [Claude result accounting](claude-results.md) adapter. It records cumulative per-model tokens at receipt time and any source-reported client cost as an **estimate**, not a bill. Its first result can include earlier work in a resumed session and subagent usage. It is a summary observation, not an API-call count. Do not select an overlapping native transcript or result source for the same work without reconciling its accounting. Native IDs remain only in the private result frame and are hashed before ledger storage. Prompt and reply bodies, tool inputs, paths and commands are not written to Monitoring's events.

Here the result input ID matches `--source-id`, while the normalized file has a distinct manifest ID. Their projected events share one source alias and hashed native session. Result usage belongs to the session entity, and CLI invocation status belongs to its agent child. Neither identifies an OS process. The receipt reports `captured`, `unavailable`, `failed` or `uncertain` for the optional summary. A failed or uncertain publication makes the capture command exit nonzero after draining the native stream. Inspect a potentially published private file before retrying an uncertain write.

The normalized event file uses the [existing owner contract](contract.md); no Deep client schema change is needed. A Deep owner must explicitly select the new file and restart under its normal managed-service procedure. A file read alone never establishes that an invocation is still active.

An optional [Claude CLI rate-limit observation](claude-rate-limit.md) can be captured from the same stream with `--quota-output`. It is a separate dated quota file, never an event-ledger token or cost. The latest valid observation for each supported window retains its own receipt time.

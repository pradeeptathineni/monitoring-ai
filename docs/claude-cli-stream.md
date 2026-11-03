# Capture one Claude CLI invocation

Claude Code supports `--output-format stream-json --verbose` for a printed invocation. Monitoring can consume that stdout as it arrives and write only normalized start/end status to a new private file. This path was checked against installed Claude Code 2.1.308 and the [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference). It does not read Claude's saved session directory, install hooks, start an account connection or run the model itself.

In one terminal, choose a new absolute event-file path and create a selection before the file exists:

```sh
python3 -m monitoring_ai sources create \
  --output /private/claude-stream-sources.json \
  --file /private/claude-stream-events.jsonl \
  --id owned-claude-cli --format normalized
python3 -m monitoring_ai serve --sources /private/claude-stream-sources.json \
  --db /private/claude-stream-ledger.sqlite
```

In another terminal, pipe one invocation's stdout through the capture command:

```sh
claude -p --output-format stream-json --verbose --no-session-persistence \
  "Your authorized project task" | \
python3 -m monitoring_ai sources capture-claude-stream \
  --output /private/claude-stream-events.jsonl \
  --source-id owned-claude-cli --project my-project
```

The capture command consumes the native JSON stream and prints a small status receipt. By default it does not forward prompt or reply text. Use `--forward` only when you deliberately want the unchanged native stream on stdout for your own downstream consumer; the receipt then goes to stderr. The event file remains metadata-only and mode 0600. Use a new event file for each invocation. Re-capturing the same native stream at a new time under the same source ID can conflict with immutable event IDs.

The `system/init` frame becomes a `running` observation of that CLI invocation. A matching `result` with `is_error: false` and `subtype: success` becomes `completed`; an explicit CLI error becomes `failed`. An incomplete stream, malformed row after init or unqualified result becomes `unknown`. These timestamps are when the local capture received the frames, not model-token or provider billing times. A running observation ages to unknown if output stops before a result.

The invocation is an agent entity under the hashed native conversation identity. A missing parent remains unobserved. `completed` means the CLI invocation reported success, not that its answer is correct, a worker process was identified, or the wider conversation closed. The stream capture emits no tokens or costs: assistant rows can repeat the same message usage, and a final result can summarize the whole invocation or subagents. Use a separate reviewed accounting source without double-counting overlapping activity. Native IDs, prompt and reply bodies, tool inputs, paths and commands are not written to Monitoring's events.

The normalized event file uses the [existing owner contract](contract.md); no Deep client schema change is needed. A Deep owner must explicitly select the new file and restart under its normal managed-service procedure. A file read alone never establishes that an invocation is still active.

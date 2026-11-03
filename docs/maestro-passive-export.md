# Receive a Maestro passive event export

Maestro AI's published `intentional_ai.monitoring_follow` command emits normalized `monitoring.event.v1` JSONL for one **explicitly selected** campaign or attempt receipt. Monitoring AI can follow that file as a `normalized` source. This uses an existing file contract; Monitoring does not import Maestro's runtime, launch a campaign, request a model, or install a hook. The commands below require an authorized Maestro target and a new private output directory.

From a Maestro checkout containing the passive exporter (`c1dd063` or a compatible later revision), run the exporter in a terminal. Replace both absolute paths with ones you have chosen. `mktemp -d` makes a private temporary directory; preserve the output somewhere private if you need history after a restart.

```sh
umask 077
export MAESTRO_ROOT=/absolute/path/to/maestro-ai
export MONITORING_EXPORT_DIR="$(mktemp -d)"
PYTHONPATH="$MAESTRO_ROOT/src" python3 -m intentional_ai.monitoring_follow \
  /absolute/path/to/authorized/campaign --source maestro --project my-project \
  --follow --seconds 30 \
  > "$MONITORING_EXPORT_DIR/events.jsonl" 2> "$MONITORING_EXPORT_DIR/checkpoint.txt"
```

Check that the exporter exited successfully. Its checkpoint records the cursor, scope and prefix digest, visibility, completeness, gap, partial tail, closure and observation failure. An empty export can mean no supported event was observed; it does not mean no work ran. A gap or observation failure means coverage is incomplete. Preserve the checkpoint privately. To resume after a nonzero cursor, pass its `--after`, `--scope` and `--cursor-sha256` together to Maestro; append only new, complete exported lines to the same selected file. Do not replace accepted lines or assign a different Monitoring source alias to the same attempt. Maestro [documents its bounded replay and refusal rules](https://github.com/pradeeptathineni/maestro-ai/blob/main/docs/monitoring-follow.md).

Select the exported file from the Monitoring AI checkout:

```sh
python3 -m monitoring_ai setup --output "$MONITORING_EXPORT_DIR/sources.json"
python3 -m monitoring_ai sources preview --sources "$MONITORING_EXPORT_DIR/sources.json"
python3 -m monitoring_ai serve --sources "$MONITORING_EXPORT_DIR/sources.json" \
  --db "$MONITORING_EXPORT_DIR/events.sqlite"
```

At the setup prompt, enter the exact `events.jsonl` path and choose `normalized`. The browser's Settings → Sources panel reports the selected input's last read and latest event timestamp separately. Use the source filter to inspect Maestro observations. The same qualified source and timestamps are in the standalone read API for a Deep consumer after its owner has deliberately restarted with that selection; this documentation does not upgrade or reconfigure a running Deep owner.

Maestro's exporter can project process start/exit, terminal claims and allowlisted tool events. Its hashed attempt identity is related to its own run identity; Monitoring does not infer a relationship to an independently observed OS process or provider session. A process exit is not task acceptance. The export does not provide token usage, a billed cost or subscription balance; those remain unknown unless a separate authoritative source is explicitly connected. File-read recency is not a heartbeat. Only source events themselves can support lifecycle evidence, which ages when no new evidence arrives.

The [fictional receiving fixture](../tests/fixtures/maestro-passive-fictional.jsonl) was generated from Maestro's `monitoring_export.export_batch` at `c1dd063` and contains process-start, command-result and process-exit observations. It exercises Monitoring's selected-file check, ledger replay, read response, source timestamp, failure retention and recovery in CI. It is **not** evidence of an actual Maestro task or live worker. In this development task, no genuine task-owned Maestro event feed was available at the selected workspace and temporary locations; no real campaign was started to manufacture monitoring activity.

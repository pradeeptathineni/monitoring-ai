# Inspect one bounded source

Use one existing private selection, or create a new selection for a file you explicitly choose. Keep the original selection and ledger unchanged while qualifying a new owner:

```sh
python3 -m monitoring_ai sources check --sources /path/to/selected.json --json
python3 -m monitoring_ai sources preview --sources /path/to/selected.json --json
python3 -m monitoring_ai serve --sources /path/to/selected.json --db /path/to/new-scratch.sqlite
```

In Overview, inspect source coverage and original event age. Open Usage and costs for disjoint token buckets and original cost evidence. Open an exact entity's history or session timeline for its recorded chronology. No status records means lifecycle is unsupported or unobserved. A process sample has no session relationship unless the producer supplied an independently qualified link.

Stop this owner with Ctrl-C. Restart the same command with the same source aliases and scratch ledger. Original history should reopen without new accounting. A new process identity requires a deliberate consumer reconnect. Never promote this trial by changing a managed service's selection.

A readable old file is historical evidence. Read time, original event time, collector checkpoint time and provider billing remain separate. Codex nonlegacy history modes and declared Claude forks refuse until their accounting is qualified. Preserve the refusal and accepted history. Do not strip declarations or change aliases to make the source pass.

## Explain a finite collector separately

For an explicitly selected supported Codex family, retain stdout checkpoints privately. Run the collector in one terminal and the inspecting owner in another:

```sh
# A new checkpoint log preserves the original attempt
umask 077
python3 -m monitoring_ai follow-codex-family \
  --sources /path/to/family.json --output /path/to/new-usage.jsonl \
  --seconds 30 --interval 2 > /path/to/new-checkpoints.jsonl

# Stop this inspecting owner deliberately after the trial
python3 -m monitoring_ai serve --file /path/to/new-usage.jsonl \
  --collector-checkpoints /path/to/new-checkpoints.jsonl \
  --db /path/to/new-family-scratch.sqlite
```

Overview shows **Collector checkpoints** independently of event-file readability. Open **Collector evidence** for original source/reconciliation/checkpoint times and owner identity. An upstream refusal may leave a readable output and retained ledger. A stopped collector does not complete a provider session. Interrupted collectors may have no final stopped checkpoint and become unknown when the last checkpoint expires. Keep original logs and inspect uncertain output before an explicit `--append` recovery into the same output with a new checkpoint log. Restart the inspecting owner deliberately to select that new log.

The selected checkpoint log is not automatically authenticated or bound to output bytes. For a finite export, retain its separate JSON receipt and use `inspect-codex-export --receipt RECEIPT --file OUTPUT` to check exact digest, size and per-source counts. That match proves byte consistency only. See [family export limits](codex-replay-export.md), [collector contract](contract.md#optional-collector-checkpoints) and [native Claude qualification](claude-native.md).

Use disposable synthetic inputs for partial writes, rotation, revisions, unsupported history and read-loss trials. Never mutate the original native input to exercise a failure. Real append qualification requires an ordinary independently authorized producer append, not a model call made solely for telemetry.

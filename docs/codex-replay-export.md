# Selected Codex family export

`export-codex-family` reads an explicit Codex-only source selection and publishes one new private normalized usage file. It reconciles proven copied cumulative prefixes before export. It does not change a source file, ledger, running owner or provider account.

## Run it

Select complete files you are authorized to read. Include every declared ancestor. Keep stable source aliases and the selection private:

```sh
python3 -m monitoring_ai sources create --output /path/to/parent-selection.json \
  --file /path/to/parent.jsonl --id parent --format codex --project my-project
python3 -m monitoring_ai sources add --sources /path/to/parent-selection.json \
  --output /path/to/family-selection.json --file /path/to/child.jsonl \
  --id child --format codex --project my-project
python3 -m monitoring_ai export-codex-family \
  --sources /path/to/family-selection.json --output /path/to/reconciled-usage.jsonl
python3 -m monitoring_ai serve --file /path/to/reconciled-usage.jsonl \
  --db /path/to/new-analysis.sqlite
```

The output is mode 0600 and must not already exist. Its receipt reports exported and copied usage-record counts per input alias, excluded per-response-record counts, byte count and digest. It contains no selected paths, raw session or parent IDs, prompts, tool arguments or response bodies. The output preserves the original normalized source/session/event identities, usage timestamps and models. Costs remain unknown. Explicit dated pricing can supply estimates later.

Choose this normalized bundle as the source for analysis. Do not also add independently aliased copies of its native files or another collector's overlapping data. Cross-format overlap is not automatically resolved. Original timestamps remain historical evidence after import. Export time does not establish current activity.

## What is proven

Every declared parent must be selected exactly once. Usage checkpoints must be ordered and cumulative counters cannot decrease. The child must contain the entire exact sequence of its parent's checkpoints before the declared fork time. Only that prefix is omitted. Copied timestamps can be rewritten by the provider, so counter equality proves the prefix while source timing bounds new work. New child checkpoints must follow the fork time. Parent activity after the fork remains separate work, even when its counter later equals the child's new counter.

Missing or duplicate parents, cycles, conflicting declarations, partial or mismatched prefixes, ambiguous fork-time ties and partial final lines refuse the whole export. Input metadata is checked again after reading all files. A changed selected file refuses without publishing. The combined selection is bounded to 32 files, 32 MiB and 50,000 rows. Existing targets refuse. If directory durability fails after the new file is linked, the CLI reports an uncertain outcome. Inspect that file before any retry.

A fictional parent/child workflow exports 193 distinct tokens from input histories that would otherwise sum to 358. A child-first selection yields the same result. A three-generation fixture preserves ancestor accounting. Importing the resulting normalized file twice adds its records only once. These are fictional qualifications. No actual provider fork, sustained following or billed amount has been demonstrated.

## Remaining limits

Non-null `history_base`, top-level `parent_thread_id`, `subagent_history_start_ordinal` and `forked_from_ordinal_exclusive` metadata refuse, as does a non-null `history_mode` other than `legacy`. The same refusal applies during family following. Absent/null fields keep the existing behavior. These declarations need separate accounting qualification; a matching cumulative prefix does not qualify rollout offsets or inherited context boundaries. See the [pinned producer inspection and limits](codex-native.md#declared-parent-histories). Top-level `compacted` records currently refuse. Separate `token_usage_record` rows are counted as ignored and never added to cumulative counters. Per-response compaction accounting remains unqualified. Undeclared copied histories cannot be recognized. No missing-parent heuristic, directory discovery or timing-only identity match is used.

This is a finite export of usage only. It omits native tools, turn markers and lifecycle. It does not supply a live family follower, repair old ledger rows or link forks to runtime workers. The ordinary native adapter continues to refuse declared parent histories. Apply dashboard date filters after reconciliation so an excluded parent period cannot hide the accounting evidence.

Deep can consume an owner reading the normalized bundle through its existing read contract. Managed source selection and promotion remain the receiving owner's responsibility. No Deep deployment is implied by exporting a file.

## Follow for a fixed interval

```sh
python3 -m monitoring_ai follow-codex-family \
  --sources /path/to/family-selection.json --output /path/to/followed-usage.jsonl \
  --seconds 300 --interval 2
```

Run an independently configured owner against that normalized output. The collector stops after its selected interval and closes the writer. The default is 30 seconds, with a maximum of one hour. It installs no daemon, hooks, credentials or automatic restart. Stop it early with Ctrl-C.

The source selection is loaded once. Editing its manifest does not broaden an active collector's inputs. Metadata is polled at intervals of 1–60 seconds. Unchanged generations skip full parsing. A change in any selected parent or child invalidates the entire family and reruns reconciliation. Every previously emitted `(source,id)` must remain present with identical content before a new event can append. Partial lines, missing parents and changed history retain the last accepted output. A complete valid restore can recover. No token correction is inferred.

Each stdout checkpoint reports collection state, receipt time, last accepted source time, added records and incomplete coverage. The final stopped checkpoint reports polls, full reads, failed polls and accepted records. These are collector observations, not provider lifecycle. A final unavailable source state exits with code 2. An uncertain output write stops immediately without retry. Preserve the file and inspect it before explicit recovery.

To deliberately resume an existing owned mode-0600 output, supply `--append`. The full selected snapshot must reproduce every accepted event before additional output is written. Existing targets refuse without that flag. Do not modify an output while a writer owns it.

The downstream normalized-file owner observes the emitted records and their original timestamps. It does not automatically receive the collector's stdout failures or final stop. Retain those checkpoints separately. Likewise, the export receipt's copied-prefix proof is not embedded in `monitoring.event.v1` or automatically visible in Deep. Preserved source/event identity and unknown costs are not proof that every provider call was observed.

Following is qualified with fictional append, partial-line, parent-generation, restore, replay, explicit-resume and finite-process workflows. The existing selected historical Codex `0.162.0-alpha.17.2` header has no declared parent. It cannot qualify a real fork. No denied fresh-session directory was read, and no genuine Maestro feed was present in the inspected source selections.

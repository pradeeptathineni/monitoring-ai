# Explicit Codex JSONL source

Select one Codex session JSONL file you own or are authorized to read. Monitoring does not discover session directories, read credentials, run Codex or install hooks. Keep its path in private configuration:

```sh
python3 -m monitoring_ai serve --db /path/to/private/events.sqlite \
  --file /path/to/selected/codex-session.jsonl --format codex \
  --source-id owned-codex --project my-project
```

The local server rereads the selected bounded file every two seconds. It defers an incomplete final line, and accepted event IDs replay safely on restart. A parsed row is reduced before joining the retained row list: session identity from `session_meta`, model from `turn_context`, and time plus four cumulative counter fields from `event_msg`/`token_count`. Prompt text, reasoning, tool arguments/results, working paths, account identifiers and unrelated rows are discarded. Raw bytes and each JSON object still exist transiently in the local reader while parsing. The source is capped at 32 MiB and 50,000 records; Codex does not yet have the incremental cursor used by the Claude follower.

The adapter turns successive cumulative input, cached-input and output totals into disjoint event deltas. Cached input is subtracted from total input; reasoning output is already included in output. Identical cumulative totals add nothing, and decreasing totals or cached input greater than input refuse rather than fabricating a reset. A nonzero `cache_write_input_tokens` value refuses until its upstream accounting relationship is qualified. The separate `token_usage_record` rows are not imported or added to these cumulative deltas: on the selected source, their per-response sum did not equal the final cumulative buckets. Native session identity is hashed before ledger storage. No lifecycle or billed cost is inferred; dated model prices, if supplied separately, yield estimates only.

One explicitly selected historical Codex source was checked locally. Its **embedded** `session_meta` producer version is `0.162.0-alpha.17.2`; the installed CLI inventory's `0.160.0` is a different observation and does not label this file. The 5,263,905-byte file had 1,310 JSONL rows. The projected and prior full parser yielded the same 126 unique usage events. Their disjoint input/cache-read/output sums matched the last cumulative counters, replay added zero events, and the loopback API exposed revision 126 with unknown cost and no running-state claim. Private headless Usage and Sessions captures rendered with zero page errors. Five full parser reads had a 39.618 ms median. The file was historical and unchanged during qualification, so no fresh append, general version coverage, current worker state or all-provider request attribution is claimed. Public tests use fictional records; actual source paths, values and screenshots remain private.

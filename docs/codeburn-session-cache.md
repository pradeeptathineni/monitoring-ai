# Selected CodeBurn session cache

This optional view reads **one** CodeBurn 0.9.27 session-cache v10 member associated with an explicitly selected Claude or Codex native file. It exposes turn-level call and token metadata from an existing cache. Monitoring does not run CodeBurn or require it as a runtime package.

First select the native file through `setup` or `sources create`. The file must remain directly readable; a cache copy never grants access to a native file that cannot be opened. Inspect it once:

```sh
python3 -m monitoring_ai sources inspect-cache --sources /path/to/private-sources.json \
  --id selected-source --cache-dir /path/to/CodeBurn/session-cache.v10
```

To show the same view in **Sessions & workers**, start a new local owner deliberately:

```sh
python3 -m monitoring_ai serve --sources /path/to/private-sources.json \
  --db /path/to/private-ledger.sqlite \
  --codeburn-session-cache /path/to/CodeBurn/session-cache.v10 \
  --cache-source-id selected-source
```

The reader checks the complete envelope and provider index, finds one index row by the selected file's canonical path, and verifies the native file's device, inode, size and modification time before and after reading the indexed member. It opens no other member, refuses symlinks and unsafe names, bounds reads, and rejects a cache generation that changes mid-read. An unavailable or stale member retains the last accepted view with an unavailable status. File modification times do not establish current work.

The cache member contains raw conversation and command material. Monitoring discards it before output, retaining only source/project aliases, turn ordinal and source timestamp, call count, safe model aliases, and separate input/output/cache-read/cache-write/reasoning counters. At most the latest 100 turns appear, with overall turn/call counts and a truncation flag. No raw session IDs, prompts, commands, tool arguments, project paths, costs, worker relationships or message text enter the API, ledger or public demos. Keep CLI output private because even metadata may reveal work patterns.

This is a **replaceable cache view**, not native usage ingestion. CodeBurn `CachedCall` counts need not match Monitoring's normalized native deltas; the two may overlap, and buckets are not summed into a non-overlapping total. The selected members inspected here have no reliable billed/estimated amount split, so the view reports no cost. No lifecycle or process mapping is inferred. Missing cache data means unknown coverage, not zero activity.

When configured, capabilities advertises `selected_cache_session: monitoring.selected-cache-session.v1`; snapshot/report add `selected_cache_status` and nullable `selected_cache_session`, and health adds only the status. Unconfigured owners omit them. The view does not change ledger revision or event filters. The source/project filter can hide the panel rows; other event filters do not apply. The supplied Deep consumer can ignore this additive view until separately adopted.

The installed cache layout and field meanings were inspected from the CodeBurn 0.9.27 MIT source map pinned in [reuse findings](reuse.md). The reader and dashboard projection are original code. No CodeBurn application source is copied.

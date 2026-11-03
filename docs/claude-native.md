# Explicit Claude Code JSONL source

Choose one Claude Code JSONL file you own or are authorized to read. Monitoring does not discover home-directory sessions, read credentials, run Claude or install hooks. A selected file can be followed as it appends:

```sh
python3 -m monitoring_ai serve --db /path/to/private/events.sqlite \
  --file /path/to/selected/claude-session.jsonl --format claude \
  --source-id owned-claude --project my-project
```

The local server rereads the bounded selected file every two seconds. It ignores an incomplete final JSON line until the writer completes it. Each parsed native line is reduced before it joins the accumulated row list: assistant ID, model, four usage counters and structured tool-use IDs/names; user tool-result ID and error flag; source type, session ID and timestamp. Prompts, response text, tool arguments/results, working paths and unrelated metadata are not retained in that row list or emitted through the API. The file's raw bytes and each JSON object are transiently present in the local reader while parsing. The reader currently caps the selected file at 32 MiB and 50,000 rows; it is not an incremental cursor.

Native session IDs are hashed before ledger storage. The same source alias and project alias must be reused across restarts. A message ID supplies one usage event. Later copies in the complete selected file with the same normalized usage but a different native row timestamp keep the **first accepted event timestamp** and add nothing. Changed counters or model for an accepted message refuse that read and retain prior accepted history; there is no mutable correction protocol. Keep the original rows: after truncation or rotation, replaying only a later copy with a different timestamp can conflict with the ledger. An exact duplicate or incomplete trailing line does not advance the ledger revision. Completed structured tool-use/result pairs yield metadata-only tool records; incomplete pairs remain uncounted.

The four token buckets are input, cache read, cache write and output as reported by the native message. No cost is assigned unless an explicit dated price book separately covers the model; that is an estimate, not billed spend. Native usage rows carry no lifecycle status. A recent usage record, a followed file or a present CLI process does not prove a worker is running. The Sessions view therefore shows unknown lifecycle when no status evidence is supplied. Session totals may overlap completed CLI result summaries or another collector; do not import the same conversation through two routes under different aliases.

One actual task-owned Claude Code 2.1.308 project-only session was checked. Its first assistant message was followed into the ledger, and a resumed second message appended to the **same** native file while Monitoring was running. The API revision moved from one to two, with the first timestamp stable and no source read failure. Native per-message token buckets matched the two normalized usage events, and the private dashboard displayed the real session and usage without raw text, paths or tool arguments. Public tests use fictional records. Broader provider versions, advisor/sidechain accounting, file rotation, larger-history incremental reads, source overlap and corrections remain unqualified.

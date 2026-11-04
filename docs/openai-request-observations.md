# Explicit API request observations

An already received Responses API object can supply request status without emitting token usage or session lifecycle. The caller chooses source, project and session grouping aliases and supplies the original receipt time. Monitoring makes no API call.

```sh
python3 -m monitoring_ai observe-openai-response --file /path/to/received-response.json \
  --output /path/to/private-observations.jsonl --observed-at ORIGINAL_RECEIPT_UTC \
  --source my-caller --project my-project --session my-group
python3 -m monitoring_ai openai-observations --file /path/to/private-observations.jsonl
```

Use `--append` deliberately for later observations in an existing owned mode-0600 history. Exact repeated captures write nothing. Conflicting observations at the same receipt time refuse. A later retrieval is another observation of the same response ID, not another inference. The latest view keeps one row per source/response identity and preserves original receipt freshness. Old completed or failed outcomes stay reported outcomes with stale receipt time. They do not close a conversation or establish worker activity.

`record_observation` accepts a received response dictionary directly. It does not save response content. The output retains a hashed response ID, explicit grouping aliases, model, supplied status, caller receipt time, optional provider creation time and whether an error object was present. HTTP request IDs, error bodies, output, tools, tokens, costs and arbitrary metadata are discarded. Existing completed-response usage capture stays separate. Failed/interrupted response observations do not turn missing usage into zero.

The supported statuses come from the pinned official openai-python `0f8e5f382861120809b1d149a18c744f725746a0` ResponseStatus schema. Provider creation time may be fractional. Completion time is not required for this status observation, and receipt time is never substituted as provider completion time. The older usage adapter still needs qualified completion timing.

This is an explicit capture/inspection workflow with fictional qualification. No ongoing API caller was modified, provider job launched, hook installed or persistent host created. Publication and Deep admission remain separate. Capture failure never authorizes retrying the provider request. An uncertain append stops and preserves the file for inspection before explicit recovery.

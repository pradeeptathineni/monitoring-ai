"""Reconcile explicit complete Codex families into a new private usage file."""

import stat

from .adapters import MAX_BYTES, codex, codex_row, parse_jsonl, read_source
from .event_export import MAX_EXPORT_EVENTS, publish_events
from .events import timestamp, validate
from .metrics import now_iso
from .sources import load_sources
from .store import stable_id


def _signature(path):
    """Detect selected-file changes without following a final symlink."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Replay export requires regular selected files")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _project(row):
    """Keep only counters and hashed declared relationships for reconciliation."""
    kind, payload = row.get("type"), row.get("payload")
    if kind == "compacted":
        raise ValueError("Compaction-bearing Codex histories require separate accounting qualification")
    if kind == "token_usage_record":
        return {"ignored_response_record": True}
    if kind == "session_meta" and isinstance(payload, dict):
        identity = payload.get("id")
        if not isinstance(identity, str) or not 0 < len(identity) <= 1000:
            raise ValueError("Replay export requires valid session metadata")
        parent = payload.get("forked_from_id")
        source = payload.get("source")
        subagent = source.get("subagent") if isinstance(source, dict) else None
        spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
        nested = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
        declarations = [value for value in (parent, nested) if value is not None]
        if any(not isinstance(value, str) or not 0 < len(value) <= 1000 for value in declarations):
            raise ValueError("Replay export requires valid declared parent metadata")
        if len(set(declarations)) > 1:
            raise ValueError("Replay export cannot resolve conflicting parent declarations")
        # Raw session identity is needed transiently by the existing event adapter
        return dict(type=kind, payload=dict(id=identity), session=stable_id(identity),
                    parent=stable_id(declarations[0]) if declarations else None,
                    fork_at=timestamp(row.get("timestamp")) if declarations else None)
    if kind == "turn_context" or (kind == "event_msg" and isinstance(payload, dict)
                                  and payload.get("type") == "token_count"):
        return codex_row(row)
    return {}


def _read(source):
    """Project a finite complete file into cumulative checkpoints and usage events."""
    content = read_source(source.path)
    if content and not content.endswith(b"\n"):
        raise ValueError("Finish the selected Codex file's last line before replay export")
    rows = parse_jsonl(content, strict=True, shape=_project)
    metadata = [row for row in rows if row.get("type") == "session_meta"]
    if len(metadata) != 1 or not rows or rows[0].get("type") != "session_meta":
        raise ValueError("Replay export requires one leading session metadata record per input")
    info = metadata[0]
    events = [validate(event) for event in codex(rows, source.id, source.project)]
    totals, usage = [0, 0, 0], []
    for event in events:
        tokens = event["tokens"]
        totals = [totals[0] + tokens["input"] + tokens["cache_read"],
                  totals[1] + tokens["cache_read"], totals[2] + tokens["output"]]
        usage.append(dict(at=event["timestamp"], total=tuple(totals)))
    return (dict(session=info["session"], parent=info["parent"], fork_at=info["fork_at"], usage=usage),
            events, len(rows), sum(row.get("ignored_response_record", False) for row in rows), len(content))


def export_codex_family(manifest, output):
    """Publish only proven independent usage; refuse incomplete or changing families."""
    from .codex_replay import prefix_lengths

    sources = load_sources(manifest)
    if any(source.format != "codex" for source in sources):
        raise ValueError("Replay export requires a selection containing only Codex files")
    before = [_signature(source.path) for source in sources]
    if sum(info[2] for info in before) > MAX_BYTES:
        raise ValueError("Selected Codex family exceeds the combined 32 MiB limit")
    samples, byte_count, row_count = [], 0, 0
    for source in sources:
        sample = _read(source)
        byte_count += sample[4]
        row_count += sample[2]
        if row_count > MAX_EXPORT_EVENTS or byte_count > MAX_BYTES:
            raise ValueError("Selected Codex family exceeds the combined input limit")
        samples.append(sample)
    if before != [_signature(source.path) for source in sources]:
        raise ValueError("Selected Codex files changed during reconciliation; no export was published")
    prefixes = prefix_lengths([sample[0] for sample in samples])
    exported, summaries = [], []
    for source, (session, events, _, ignored, _) in zip(sources, samples):
        copied = prefixes[session["session"]]
        novel = events[copied:]
        exported.extend(novel)
        summaries.append(dict(source=source.id, copied_usage_records=copied,
                              exported_usage_records=len(novel), ignored_response_records=ignored))
    exported.sort(key=lambda row: (row["timestamp"], row["source"], row["id"]))
    publication = publish_events(output, exported)
    return dict(schema="monitoring.codex-replay-export.v1", observed_at=now_iso(),
                **publication, sources=summaries, complete_coverage=False,
                coverage="selected-proven-cumulative-prefixes", costs="unknown",
                lifecycle="not-exported", compaction="unsupported")

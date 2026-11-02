"""Read only explicitly named files. Native text, paths and arguments are discarded."""

import json
import os
from pathlib import Path
import re
import stat

from .events import SCHEMA, validate
from .store import stable_id

MAX_BYTES = 32 * 1024 * 1024


def read_jsonl(path, *, live=False):
    # Canonicalize directory aliases such as macOS /tmp; refuse symlink files
    selected = Path(path).absolute()
    if selected.is_symlink():
        raise ValueError("Select a regular source file, not a symlink")
    path = selected.parent.resolve() / selected.name
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("Source must be a regular file of at most 32 MiB")
        content = stream.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError("Source exceeds 32 MiB")
    lines = content.splitlines(keepends=True)
    if live and lines and not lines[-1].endswith(b"\n"):
        lines.pop()  # A producer may still be writing the final record
    rows = []
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            raise ValueError(f"Invalid JSON at line {index}") from None
        if not isinstance(row, dict):
            raise ValueError(f"Expected an object at line {index}")
        rows.append(row)
        if len(rows) > 50000:
            raise ValueError("Source exceeds 50,000 records")
    return rows


def safe_label(value, fallback="unknown"):
    value = value if isinstance(value, str) else fallback
    value = re.sub(r"[^A-Za-z0-9_.:@-]", "-", value or fallback)[:120]
    return value or fallback


def base(source, session, project, provider, model, surface, at, identity):
    # Native identities are hashed so source file names and working paths never reach the ledger
    if not isinstance(session, str) or not session or len(session) > 1000:
        raise ValueError("Native session identity must be a nonempty string")
    session = "s-" + stable_id(session)
    return dict(
        schema=SCHEMA, id=stable_id(identity), source=source,
        session=session, entity=session, parent=None, entity_type="session",
        timestamp=at, kind="usage", project=project, provider=provider,
        model=safe_label(model), surface=surface,
    )


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError("Native token counts must be nonnegative integers")
    return value


def claude(rows, source, project):
    events = {}
    for row in rows:
        message = row.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        identity = message.get("id")
        session = row.get("sessionId")
        if not identity or not session or not row.get("timestamp"):
            raise ValueError("Claude usage requires message ID, session ID and timestamp")
        event = base(source, session, project, "anthropic", message.get("model"),
                     "claude-code", row["timestamp"], (session, identity))
        event["tokens"] = {
            "input": count(usage.get("input_tokens", 0)),
            "cache_read": count(usage.get("cache_read_input_tokens", 0)),
            "cache_write": count(usage.get("cache_creation_input_tokens", 0)),
            "output": count(usage.get("output_tokens", 0)),
        }
        # Streaming copies commonly repeat a message. Identical usage is replay, conflicting usage refuses
        previous = events.get(event["id"])
        if previous and previous != event:
            raise ValueError("Claude message usage changed. Export final messages before importing")
        events[event["id"]] = event
    from .claude_tools import claude_tool_events

    return list(events.values()) + claude_tool_events(rows, source, project)


def codex(rows, source, project):
    session, model = None, "unknown"
    previous = None
    events = []
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row.get("type") == "session_meta":
            identity = payload.get("id")
            if session is not None and session != identity:
                raise ValueError("A Codex file must contain one session")
            session = identity
        elif row.get("type") == "turn_context":
            model = payload.get("model") or "unknown"
        elif row.get("type") == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            total = info.get("total_token_usage") if isinstance(info, dict) else None
            if not isinstance(total, dict):
                continue
            if not session:
                raise ValueError("Codex usage needs preceding session metadata")
            current = {key: count(total.get(key, 0)) for key in (
                "input_tokens", "cached_input_tokens", "output_tokens"
            )}
            if previous == current:
                continue
            delta = {key: value - (previous or {}).get(key, 0) for key, value in current.items()}
            if min(delta.values()) < 0:
                raise ValueError("Codex cumulative counters decreased. Supply a complete nonreset session export")
            if delta["cached_input_tokens"] > delta["input_tokens"]:
                raise ValueError("Codex cached input exceeds total input")
            event = base(source, session, project, "openai", model, "codex",
                         row.get("timestamp"), (session, current))
            # Cached input is a subset of Codex input. Reasoning is already part of output
            event["tokens"] = {
                "input": delta["input_tokens"] - delta["cached_input_tokens"],
                "cache_read": delta["cached_input_tokens"],
                "cache_write": 0, "output": delta["output_tokens"],
            }
            events.append(event)
            previous = current
    return events


def load(path, format="normalized", source=None, project=None, live=False):
    rows = read_jsonl(path, live=live)
    if format == "normalized":
        return [validate(row) for row in rows]
    if not source or not project:
        raise ValueError("Native imports require --source-id and --project aliases")
    events = {"claude": claude, "codex": codex}[format](rows, source, project)
    return [validate(event) for event in events]

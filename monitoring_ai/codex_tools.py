"""Completed Codex tool-item metadata; native commands and results are discarded."""

import re

from .events import timestamp
from .store import stable_id

ITEM_TYPES = {"CommandExecution", "McpToolCall", "FileChange"}
SAFE_ALIAS = re.compile(r"[A-Za-z0-9_.:@-]{1,120}\Z")


def _duration(value):
    if not isinstance(value, dict):
        return None
    seconds, nanos = value.get("secs"), value.get("nanos")
    if type(seconds) is not int or type(nanos) is not int or not 0 <= seconds <= 10**9 or not 0 <= nanos < 10**9:
        return None
    millis = seconds * 1000 + nanos // 1_000_000
    return millis if millis <= 10**12 else None


def project_completed_item(row):
    """Retain only an allowlisted completed item's identity, timing and outcome hints."""
    payload = row.get("payload")
    item = payload.get("item") if isinstance(payload, dict) else None
    if not isinstance(item, dict) or not isinstance(item.get("type"), str) or item["type"] not in ITEM_TYPES:
        return {}
    identity = item.get("id")
    if not isinstance(identity, str) or not 0 < len(identity) <= 1000:
        return {}
    try:
        at = timestamp(row.get("timestamp"))
    except ValueError:
        return {}  # Optional items do not make otherwise valid usage unreadable.
    slim = {"type": item["type"], "item": "i-" + stable_id(identity),
            "status": item.get("status") if item.get("status") in ("completed", "failed") else None,
            "duration_ms": _duration(item.get("duration"))}
    if item["type"] == "CommandExecution":
        code = item.get("exit_code")
        slim["exit_zero"] = code == 0 if type(code) is int else None
    if item["type"] == "McpToolCall":
        for key in ("server", "tool"):
            value = item.get(key)
            slim[key] = value if isinstance(value, str) and SAFE_ALIAS.fullmatch(value) else None
    return {"type": "event_msg", "timestamp": at,
            "payload": {"type": "item_completed", "item": slim}}


def completed_tool_event(row, source, project, session, model):
    """Map a projected native completion to one normalized, source-limited tool event."""
    from .adapters import base  # adapters imports this module lazily

    payload = row.get("payload")
    item = payload.get("item") if isinstance(payload, dict) else None
    if not isinstance(item, dict) or not isinstance(item.get("type"), str) or item["type"] not in ITEM_TYPES:
        return None
    native_type = item["type"]
    event = base(source, session, project, "openai", model, "codex", row["timestamp"],
                 ("tool", session, item["item"]))
    event["kind"] = "tool"
    if native_type == "CommandExecution":
        event.update(tool="CommandExecution", tool_category="shell")
        status, zero = item.get("status"), item.get("exit_zero")
        event["outcome"] = ("error" if status == "failed" or zero is False else
                            "ok" if status == "completed" and zero is True else "unknown")
    elif native_type == "McpToolCall":
        event.update(tool=item.get("tool") or "McpToolCall", tool_category="mcp",
                     outcome="error" if item.get("status") == "failed" else "unknown")
        if item.get("server"):
            event["tool_server"] = item["server"]
    else:
        event.update(tool="FileChange", tool_category="core",
                     outcome="error" if item.get("status") == "failed" else "unknown")
    if item.get("duration_ms") is not None:
        event["duration_ms"] = item["duration_ms"]
    return event

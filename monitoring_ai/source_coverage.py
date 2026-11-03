"""Metadata-only coverage summaries for explicitly configured input reads."""

from .events import timestamp
from .metrics import now_iso, seconds

_CAPABILITIES = {
    "normalized": {"usage": "normalized-deltas", "lifecycle": "producer-supplied", "tools": "producer-supplied"},
    "claude": {"usage": "native-reconstruction", "lifecycle": "unsupported", "tools": "completed-pairs"},
    "claude-results": {"usage": "cumulative-result-deltas", "lifecycle": "unsupported", "tools": "unsupported"},
    "codex": {"usage": "native-reconstruction", "lifecycle": "unsupported", "tools": "completed-items"},
}

_KINDS = ("usage", "status", "tool")


def source_capabilities(format_name):
    """Return coverage boundaries for a supported source adapter."""
    try:
        return dict(_CAPABILITIES[format_name])
    except (KeyError, TypeError):
        raise ValueError("Unsupported source format") from None


def source_summary(source, rows, *, sampled_at):
    """Aggregate validated rows without retaining any row, path, or cost data."""
    summary_time = timestamp(sampled_at)
    capabilities = source_capabilities(source.format)
    counts = {"total": 0, "usage": 0, "status": 0, "tool": 0}
    sessions = set()
    entities = set()
    event_sources = set()
    latest = {"event": None, "usage": None, "status": None, "tool": None}
    latest_seconds = {key: None for key in latest}
    identities = {}

    for row in rows:
        identity = (row["source"], row["id"])
        previous = identities.get(identity)
        if previous is not None:
            if previous != row:
                raise ValueError("Conflicting event identity in source coverage")
            continue
        identities[identity] = row
        kind = row["kind"]
        if kind not in _KINDS:
            raise ValueError("Unsupported event kind")
        event_time = timestamp(row["timestamp"])
        event_epoch = seconds(event_time)
        counts["total"] += 1
        counts[kind] += 1
        event_sources.add(row["source"])
        sessions.add((row["source"], row["session"]))
        entities.add((row["source"], row["session"], row["entity"]))
        for key in ("event", kind):
            if latest_seconds[key] is None or event_epoch > latest_seconds[key]:
                latest_seconds[key] = event_epoch
                latest[key] = event_time

    return {
        "id": source.id,
        "format": source.format,
        "capabilities": capabilities,
        "sampled_at": summary_time,
        "records": {**counts, "sessions": len(sessions), "entities": len(entities),
                    "event_sources": len(event_sources)},
        "latest_event_at": latest["event"],
        "latest_usage_at": latest["usage"],
        "latest_status_at": latest["status"],
        "latest_tool_at": latest["tool"],
    }


def _empty_summary(source):
    return {
        "id": source.id,
        "format": source.format,
        "capabilities": source_capabilities(source.format),
        "sampled_at": None,
        "records": None,
        "latest_event_at": None,
        "latest_usage_at": None,
        "latest_status_at": None,
        "latest_tool_at": None,
    }


def coverage_inputs(sources, statuses, summaries, *, now):
    """Shape cached metadata into a point-in-time coverage response."""
    now_time = timestamp(now)
    now_epoch = seconds(now_time)
    result = []
    for source, status, summary in zip(sources, statuses, summaries):
        value = dict(summary) if summary is not None else _empty_summary(source)
        if summary is not None:
            value["records"] = dict(summary["records"])
            value["capabilities"] = dict(summary["capabilities"])
        latest_at = value["latest_event_at"]
        if summary is None:
            age, freshness = None, "unread"
        elif latest_at is None:
            age, freshness = None, "empty"
        else:
            age = now_epoch - seconds(latest_at)
            freshness = "future" if age < 0 else "recent" if age <= 60 else "stale"
        value.update(
            read_state=status["state"],
            retained=status["state"] == "unavailable" and summary is not None,
            event_age_seconds=age,
            event_freshness=freshness,
        )
        result.append(value)
    return result


def source_coverage(sources, statuses, summaries, *, now=None):
    """Create the point-in-time top-level coverage object."""
    observed = timestamp(now or now_iso())
    return {
        "schema": "monitoring.source-coverage.v1",
        "observed_at": observed,
        "scope": "latest-accepted-input-reads",
        "complete_coverage": False,
        "inputs": coverage_inputs(sources, statuses, summaries, now=observed),
    }

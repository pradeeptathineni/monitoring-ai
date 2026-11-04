"""Pure summary of explicit finite collector checkpoints. Local receipt evidence only."""
from datetime import datetime

_SCHEMA = "monitoring.codex-family-follow.v1"
_ORDINARY = {"schema", "state", "observed_at", "last_success_at", "latest_event_at",
             "added", "accepted_records", "complete_coverage", "error"}
_STOPPED = {"schema", "state", "last_source_state", "observed_at", "added_total",
            "accepted_records", "polls", "full_reads", "failed_polls",
            "complete_coverage", "finite"}
_REASONS = {
    ("following", "current"): "collector-following",
    ("unavailable", "current"): "collection-source-unavailable",
    ("stopped", "current"): "collector-stopped",
    ("unknown", "stale"): "checkpoint-stale",
    ("unknown", "future"): "checkpoint-in-future",
    ("unknown", "empty"): "no-checkpoints",
}
_BAD = "malformed collector checkpoints"


def _time(value):
    """Read supplied observation time without substituting receipt time."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        except ValueError:
            raise ValueError(_BAD) from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(_BAD)
    return value


def _opt_time(value):
    """Preserve absent timing as unknown."""
    return None if value is None else _time(value)


def _count(value, bound=50000):
    """Bound plain checkpoint counts."""
    if type(value) is not int or not 0 <= value <= bound:
        raise ValueError(_BAD)
    return value


def _iso(value):
    """Serialize known observation times only."""
    return None if value is None else value.isoformat()


def _summarize(rows, observed_at, max_age_seconds):
    """Keep collector state, source state and evidence freshness separate."""
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 3600:
        raise ValueError(_BAD)
    now = _time(observed_at)
    if not isinstance(rows, (list, tuple)) or len(rows) > 5000:
        raise ValueError(_BAD)

    last_at = state = accepted = None
    success = event = None
    prev_stopped = False
    source_state = None
    for row in rows:
        if not isinstance(row, dict) or row.get("schema") != _SCHEMA:
            raise ValueError(_BAD)
        kind = row.get("state")
        if kind == "stopped":
            if set(row) != _STOPPED or prev_stopped:
                raise ValueError(_BAD)
            if row["last_source_state"] not in ("following", "unavailable", "unread"):
                raise ValueError(_BAD)
            if row["complete_coverage"] is not False or row["finite"] is not True:
                raise ValueError(_BAD)
            at = _time(row["observed_at"])
            added = _count(row["added_total"])
            acc = _count(row["accepted_records"])
            polls = _count(row["polls"], 100000)
            if (_count(row["full_reads"], 100000) > polls
                    or _count(row["failed_polls"], 100000) > polls or added > acc):
                raise ValueError(_BAD)
            source_state = row["last_source_state"]
            if accepted is not None and acc < accepted:
                raise ValueError(_BAD)
            prev_stopped = True
        elif kind in ("following", "unavailable"):
            if set(row) != _ORDINARY or row["complete_coverage"] is not False:
                raise ValueError(_BAD)
            if row["error"] is not None and not isinstance(row["error"], str):
                raise ValueError(_BAD)  # error text is validated, then dropped
            at = _time(row["observed_at"])
            s, e = _opt_time(row["last_success_at"]), _opt_time(row["latest_event_at"])
            _count(row["added"])
            acc = _count(row["accepted_records"])
            if not prev_stopped and accepted is not None and acc < accepted:
                raise ValueError(_BAD)
            source_state = kind
            if prev_stopped:  # new explicit run: context resets
                success = event = None
            prev_stopped = False
            success = s if s is not None else success
            event = e if e is not None else event
        else:
            raise ValueError(_BAD)
        if last_at is not None and at < last_at:
            raise ValueError(_BAD)
        last_at, state, accepted = at, kind, acc

    if last_at is None:
        freshness, out_state, reported = "empty", "unknown", None
    else:
        age = (now - last_at).total_seconds()
        freshness = "future" if age < 0 else "stale" if age > max_age_seconds else "current"
        out_state = state if freshness == "current" else "unknown"
        reported = None if freshness == "current" else state
    return {
        "schema": "monitoring.collector-health.v1",
        "scope": "explicit-collector-checkpoints",
        "complete_coverage": False,
        "state": out_state,
        "reported_state": reported,
        "last_source_state": source_state,
        "freshness": freshness,
        "last_checkpoint_at": _iso(last_at),
        "last_success_at": _iso(success),
        "latest_event_at": _iso(event),
        "accepted_records": accepted,
        "reason": _REASONS[(out_state, freshness)],
    }


def project_checkpoints(rows, observed_at, max_age_seconds=125):
    """Project selected checkpoint metadata without exposing arbitrary error text."""
    try:
        return _summarize(rows, observed_at, max_age_seconds)
    except Exception:
        raise ValueError(_BAD) from None


def read_checkpoints(path, observed_at, max_age_seconds=125):
    """Read one bounded selected checkpoint file without creating a ledger."""
    from .adapters import read_source, parse_jsonl
    content = read_source(path)
    if len(content) > 1024 * 1024:
        raise ValueError(_BAD)
    partial = bool(content and not content.endswith(b"\n"))
    rows = parse_jsonl(content, live=True, strict=True)
    result = project_checkpoints(rows, observed_at, max_age_seconds)
    result["partial_tail"] = partial
    return result

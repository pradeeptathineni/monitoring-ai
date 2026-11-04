"""Pure, metadata-only OpenAI Response request observations.

No IO, network, clock, SDK import, or provider call. Records only that a caller
received a Response with an explicit status; it does not establish live
connection, complete account coverage, verified billing, or conversation end.
Token receipts for completed responses stay in the existing normalized-usage path.
"""
import math
import re
from datetime import datetime, timezone

from monitoring_ai.adapters import safe_label
from monitoring_ai.events import label, timestamp
from monitoring_ai.store import stable_id

# openai-python pin 0f8e5f382861120809b1d149a18c744f725746a0 ResponseStatus
STATUSES = frozenset(
    {"completed", "failed", "in_progress", "cancelled", "queued", "incomplete"}
)
FRAME_SCHEMA = "monitoring.openai-request-observation.v1"
BOOK_SCHEMA = "monitoring.openai-request-observations.v1"
SCOPE = "explicit-caller-received-responses"
MAX_ID = 1000
MAX_FRAMES = 5000
MAX_IDENTITIES = 100
FRAME_KEYS = frozenset(
    {
        "schema", "id", "source", "project", "session", "provider", "model",
        "status", "observed_at", "created_at", "has_error", "complete_coverage",
    }
)
_MSG = "invalid openai observation"


def _bad():
    """Return one content-free projection failure."""
    return ValueError(_MSG)


def _get(raw, name):
    """Read only an allowlisted response field."""
    if isinstance(raw, dict):
        return raw.get(name)
    return getattr(raw, name, None)


def _parse(value):
    """Parse a normalized ISO timestamp (str or datetime) to aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    else:
        raise _bad()
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise _bad()
    return dt.astimezone(timezone.utc)


def _norm_time(value):
    """Normalize an explicit observation time."""
    out = timestamp(value)
    _parse(out)
    return out if isinstance(out, str) else _parse(out).isoformat().replace("+00:00", "Z")


def _created_iso(value):
    """Keep provider creation time separate from receipt time."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _bad()
    if not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise _bad()
    dt = datetime.fromtimestamp(value, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _status(value):
    """Require an explicitly supported provider status."""
    value = getattr(value, "value", value)
    if not isinstance(value, str) or value not in STATUSES:
        raise _bad()
    return value


def project_response(raw, *, source, project, session, observed_at):
    """Project one received Response into a closed metadata observation frame."""
    try:
        if not isinstance(raw, dict) or raw.get("object") != "response":
            raise _bad()
        rid = _get(raw, "id")
        if not isinstance(rid, str) or not rid or len(rid) > MAX_ID:
            raise _bad()
        status = _status(_get(raw, "status"))
        model = _get(raw, "model")
        if model is None:
            model_out = "unknown"
        elif isinstance(model, str):
            model_out = safe_label(model)
        else:
            raise _bad()
        created = _created_iso(_get(raw, "created_at"))
        return {
            "schema": FRAME_SCHEMA,
            "id": "response-" + stable_id("openai-response", rid),
            "source": label(source, "source"),
            "project": label(project, "project"),
            "session": label(session, "session"),
            "provider": "openai",
            "model": model_out,
            "status": status,
            "observed_at": _norm_time(observed_at),
            "created_at": created,
            "has_error": _get(raw, "error") is not None,
            "complete_coverage": False,
        }
    except Exception:
        raise _bad() from None


def _validate_frame(f):
    """Validate closed metadata before selecting or replaying it."""
    if not isinstance(f, dict) or set(f) != FRAME_KEYS:
        raise _bad()
    if f["schema"] != FRAME_SCHEMA or f["provider"] != "openai":
        raise _bad()
    if f["complete_coverage"] is not False or not isinstance(f["has_error"], bool):
        raise _bad()
    if _status(f["status"]) != f["status"]:
        raise _bad()
    rid = f["id"]
    if not isinstance(rid, str) or not re.fullmatch(r"response-[0-9a-f]{32}", rid):
        raise _bad()
    for k in ("source", "project", "session"):
        if not isinstance(f[k], str) or label(f[k], k) != f[k]:
            raise _bad()
    if not isinstance(f["model"], str) or not f["model"] or safe_label(f["model"]) != f["model"]:
        raise _bad()
    if _norm_time(f["observed_at"]) != f["observed_at"]:
        raise _bad()
    if f["created_at"] is not None:
        if not isinstance(f["created_at"], str):
            raise _bad()
        _parse(f["created_at"])
    return _parse(f["observed_at"])


def latest_observations(frames, observed_at, max_age_seconds=125):
    """Select the latest explicit receipt per (source, id) with explicit freshness."""
    try:
        if not isinstance(frames, (list, tuple)) or len(frames) > MAX_FRAMES:
            raise _bad()
        if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 3600:
            raise _bad()
        now_text = _norm_time(observed_at)
        now = _parse(now_text)

        groups, bindings = {}, {}
        for f in frames:
            when = _validate_frame(f)
            ident = (f["source"], f["id"])
            if ident not in groups and len(groups) >= MAX_IDENTITIES:
                raise _bad()
            seen = groups.setdefault(ident, {})
            prior = seen.get(when)
            if prior is not None:
                if prior != f:
                    raise _bad()
                continue
            binding = tuple(f[k] for k in ("project", "session", "model", "created_at"))
            if ident in bindings and bindings[ident] != binding:
                raise _bad()
            bindings[ident] = binding
            seen[when] = f

        rows = []
        for ident in sorted(groups):
            seen = groups[ident]
            when = max(seen)
            frame = dict(seen[when])
            age = (now - when).total_seconds()
            if age < 0:
                fresh = "future"
            elif age <= max_age_seconds:
                fresh = "current"
            else:
                fresh = "stale"
            frame["reported_status"] = frame["status"]
            frame["freshness"] = fresh
            rows.append(frame)
        return {
            "schema": BOOK_SCHEMA,
            "scope": SCOPE,
            "complete_coverage": False,
            "observed_at": now_text,
            "rows": rows,
            "truncated": False,
        }
    except Exception:
        raise _bad() from None


def record_observation(raw, output, *, source, project, session, observed_at, append=False):
    """Record a response already received by the caller; never call or retry a provider."""
    from .result_capture import publish_private_json, append_private_frame
    frame = project_response(raw, source=source, project=project, session=session, observed_at=observed_at)
    if append:
        def validate_history(frames):
            """Refuse conflicting receipt identities and skip exact repeated captures."""
            replayed = frame in frames[:-1]
            latest_observations(frames[:-1] if replayed else frames, frame["observed_at"])
            return not replayed
        written = append_private_frame(output, frame, validate_history)
    else:
        publish_private_json(output, frame)
        written = True
    return dict(schema="monitoring.openai-observation-capture.v1", id=frame["id"],
                status=frame["status"], observed_at=frame["observed_at"],
                added=int(written), replayed=int(not written), complete_coverage=False)


def capture_observation(file, output, **options):
    """Project one explicitly selected response without saving its content."""
    import json
    from .adapters import read_source, unique_keys, reject_constant
    raw = json.loads(read_source(file), object_pairs_hook=unique_keys, parse_constant=reject_constant)
    return record_observation(raw, output, **options)


def read_observations(file, observed_at, max_age_seconds=125):
    """Inspect a selected private observation history without creating a ledger."""
    from .adapters import read_source, parse_jsonl
    content = read_source(file)
    if content and not content.endswith(b"\n"):
        raise ValueError(_MSG)
    return latest_observations(parse_jsonl(content, strict=True), observed_at, max_age_seconds)

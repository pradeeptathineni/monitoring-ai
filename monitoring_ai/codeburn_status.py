"""Read explicitly selected CodeBurn menubar status snapshots."""

from datetime import datetime, timezone
import hashlib
import json
import os
import stat

from .events import label, timestamp
from .codeburn_breakdowns import _row_id

MAX_BYTES = 2 * 1024 * 1024
MAX_SESSIONS = 1000
MAX_WINDOW_SECONDS = 24 * 60 * 60
MAX_COUNT = 10**12
READ_ERROR = "CodeBurn status output could not be read or validated"
_SESSION_FIELDS = {
    "id", "provider", "model", "contextTokens", "contextWindow",
    "startedAt", "lastActivityAt",
}


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(READ_ERROR)
        value[key] = item
    return value


def _constant(_value):
    raise ValueError(READ_ERROR)


def _read(path):
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        flags |= getattr(os, "O_CLOEXEC", 0)
        fd = os.open(os.fspath(path), flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise ValueError(READ_ERROR)
            payload = stream.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise ValueError(READ_ERROR)
            return payload, info
    except (OSError, TypeError, ValueError):
        raise ValueError(READ_ERROR) from None


def _count(value, *, nullable=False, positive=False):
    if value is None and nullable:
        return None
    if type(value) is not int or not (1 if positive else 0) <= value <= MAX_COUNT:
        raise ValueError(READ_ERROR)
    return value


def _iso_utc(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _session_alias(native_id, salt):
    if not isinstance(native_id, str) or not native_id or len(native_id) > 1000:
        raise ValueError(READ_ERROR)
    digest = hashlib.blake2b(native_id.encode("utf-8"), key=salt, digest_size=16).hexdigest()
    return f"s-{digest}"


def read_codeburn_status(path, *, alias_salt):
    """Read a status snapshot; aliases are private to the caller's process salt."""
    if not isinstance(alias_salt, bytes) or not 16 <= len(alias_salt) <= 64:
        raise ValueError(READ_ERROR)
    payload, info = _read(path)
    try:
        raw = json.loads(payload, object_pairs_hook=_object, parse_constant=_constant)
        if not isinstance(raw, dict) or not isinstance(raw.get("current"), dict):
            raise ValueError(READ_ERROR)
        generated_at = timestamp(raw.get("generated"))
        has_block = "liveSessions" in raw
        block = raw.get("liveSessions")
        if has_block and not isinstance(block, dict):
            raise ValueError(READ_ERROR)

        sessions = None
        window_seconds = None
        if has_block:
            window_seconds = _count(block.get("windowSeconds"), positive=True)
            if window_seconds > MAX_WINDOW_SECONDS:
                raise ValueError(READ_ERROR)
            rows = block.get("sessions")
            if not isinstance(rows, list) or len(rows) > MAX_SESSIONS:
                raise ValueError(READ_ERROR)
            sessions = []
            aliases = set()
            for row in rows:
                if not isinstance(row, dict) or not _SESSION_FIELDS <= row.keys():
                    raise ValueError(READ_ERROR)
                alias = _session_alias(row.get("id"), alias_salt)
                if alias in aliases:
                    raise ValueError(READ_ERROR)
                aliases.add(alias)
                provider = label(row.get("provider"), "provider")
                model = None if row.get("model") is None else _row_id(row["model"], model=True)
                started_at = timestamp(row.get("startedAt"))
                last_activity_at = timestamp(row.get("lastActivityAt"))
                context_tokens = _count(row.get("contextTokens"), nullable=True)
                context_window = _count(row.get("contextWindow"), nullable=True, positive=True)
                sessions.append({
                    "alias": alias,
                    "provider": provider,
                    "model": model,
                    "started_at": started_at,
                    "last_activity_at": last_activity_at,
                    "context_tokens": context_tokens,
                    "context_window": context_window,
                })

        sampled_at = datetime.now(timezone.utc)
        modified_at = datetime.fromtimestamp(info.st_mtime, timezone.utc)
        file_age_seconds = sampled_at.timestamp() - info.st_mtime
        source_age_seconds = sampled_at.timestamp() - datetime.fromisoformat(
            generated_at.replace("Z", "+00:00")
        ).timestamp()
        if not all(-10**12 < value < 10**12 for value in (file_age_seconds, source_age_seconds)):
            raise ValueError(READ_ERROR)
        return {
            "schema": "monitoring.codeburn-activity.v1",
            "coverage": "recent-transcript-write-not-process-liveness",
            "sampled_at": _iso_utc(sampled_at),
            "file_modified_at": _iso_utc(modified_at),
            "file_age_seconds": file_age_seconds,
            "generated_at": generated_at,
            "source_age_seconds": source_age_seconds,
            "window_seconds": window_seconds,
            "sessions": sessions,
        }
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(READ_ERROR) from None

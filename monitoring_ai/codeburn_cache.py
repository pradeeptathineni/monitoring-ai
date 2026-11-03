"""Read path-selected CodeBurn v74 daily usage aggregates."""

from datetime import date, datetime, timezone
import json
import os
import stat

from .events import amount
from .codeburn_breakdowns import project_breakdowns

MAX_BYTES = 32 * 1024 * 1024
MAX_DAYS = 3650
MAX_BREAKDOWN_ROWS = 2048
FORMAT_VERSION = 74
READ_ERROR = "CodeBurn daily cache could not be read or validated"


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(READ_ERROR)
        value[key] = item
    return value


def _constant(_value):
    raise ValueError(READ_ERROR)


def _iso_utc(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _calendar_date(value, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(READ_ERROR)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(READ_ERROR) from None
    if parsed.isoformat() != value:
        raise ValueError(READ_ERROR)
    return value


def _count(value):
    if type(value) is not int or not 0 <= value <= 10**15:
        raise ValueError(READ_ERROR)
    return value


def _cost(value):
    try:
        return amount(value)
    except (TypeError, ValueError):
        raise ValueError(READ_ERROR) from None


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


def read_codeburn_cache(path):
    """Read one explicitly selected, bounded v74 cache and return safe daily totals."""
    payload, info = _read(path)
    try:
        raw = json.loads(payload, object_pairs_hook=_object, parse_constant=_constant, parse_float=str)
        if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != FORMAT_VERSION:
            raise ValueError(READ_ERROR)
        days_raw = raw.get("days")
        if not isinstance(days_raw, list) or len(days_raw) > MAX_DAYS:
            raise ValueError(READ_ERROR)
        complete = raw.get("complete", False)
        if type(complete) is not bool:
            raise ValueError(READ_ERROR)
        watermark_trusted = raw.get("watermarkTrusted")
        if watermark_trusted is not None and type(watermark_trusted) is not bool:
            raise ValueError(READ_ERROR)
        last_computed = _calendar_date(raw.get("lastComputedDate"), nullable=True)

        days = []
        breakdown_rows = 0
        seen_dates = set()
        for item in days_raw:
            if not isinstance(item, dict):
                raise ValueError(READ_ERROR)
            day = _calendar_date(item.get("date"))
            if day in seen_dates:
                raise ValueError(READ_ERROR)
            seen_dates.add(day)
            carried = item.get("carried", False)
            if type(carried) is not bool:
                raise ValueError(READ_ERROR)
            tokens = {
                "input": _count(item.get("inputTokens")),
                "output": _count(item.get("outputTokens")),
                "cache_read": _count(item.get("cacheReadTokens")),
                "cache_write": _count(item.get("cacheWriteTokens")),
            }
            breakdowns = project_breakdowns(item)
            breakdown_rows += sum(len(rows) for rows in breakdowns.values() if rows is not None)
            if breakdown_rows > MAX_BREAKDOWN_ROWS:
                raise ValueError(READ_ERROR)
            days.append({
                "date": day,
                "source_cost_usd": _cost(item.get("cost")),
                "calls": _count(item.get("calls")),
                "sessions": _count(item.get("sessions")),
                "tokens": tokens,
                "carried": carried,
                "breakdowns": breakdowns,
            })
        days.sort(key=lambda entry: entry["date"])

        sampled_at = datetime.now(timezone.utc)
        modified = datetime.fromtimestamp(info.st_mtime, timezone.utc)
        age_seconds = sampled_at.timestamp() - info.st_mtime
        if not (-10**12 < age_seconds < 10**12):
            raise ValueError(READ_ERROR)
        return {
            "schema": "monitoring.cached-usage.v1",
            "source": "codeburn-daily-cache",
            "format_version": FORMAT_VERSION,
            "sampled_at": _iso_utc(sampled_at),
            "file_modified_at": _iso_utc(modified),
            "file_age_seconds": age_seconds,
            "date_basis": "codeburn-local-calendar",
            "complete": complete,
            "watermark_trusted": watermark_trusted,
            "last_computed_date": last_computed,
            "days": days,
        }
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(READ_ERROR) from None

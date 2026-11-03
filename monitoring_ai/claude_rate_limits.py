"""Sanitize one observed Claude CLI rate-limit event without account access."""

from datetime import datetime, timezone
from decimal import Decimal
import math

from .insights import validate_quota_book


WINDOWS = {"five_hour": 300, "seven_day": 10080,
           "seven_day_opus": 10080, "seven_day_sonnet": 10080}


def _window(name, raw):
    if not isinstance(raw, dict):
        raise ValueError("Invalid Claude CLI rate-limit window")
    utilization = raw.get("utilization")
    if utilization is not None:
        if type(utilization) not in (int, float) or not math.isfinite(utilization) or not 0 <= utilization <= 1:
            raise ValueError("Invalid Claude CLI rate-limit utilization")
        used = float(Decimal(str(utilization)) * 100)
    else:
        used = None
    reset = raw.get("resetsAt")
    if reset is not None:
        if type(reset) is not int or not 0 <= reset <= 253402300799:
            raise ValueError("Invalid Claude CLI rate-limit reset")
        reset = datetime.fromtimestamp(reset, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return dict(id=name.replace("_", "-"), label=name.replace("_", "-"),
                used_percent=used, window_minutes=WINDOWS[name], resets_at=reset)


def claude_rate_limit_book(row, *, captured_at, source, account):
    """Project supported CLI-reported windows, discarding IDs and overage fields."""
    if not isinstance(row, dict) or row.get("type") != "rate_limit_event":
        raise ValueError("Invalid Claude CLI rate-limit event")
    info = row.get("rate_limit_info")
    if not isinstance(info, dict) or info.get("status") not in {"allowed", "allowed_warning", "rejected"}:
        raise ValueError("Invalid Claude CLI rate-limit status")
    raw_windows = info.get("unifiedWindows")
    if raw_windows is None:
        kind = info.get("rateLimitType")
        raw_windows = {kind: info} if kind in WINDOWS else {}
    if not isinstance(raw_windows, dict):
        raise ValueError("Invalid Claude CLI rate-limit windows")
    windows = [_window(name, raw_windows[name]) for name in WINDOWS if name in raw_windows]
    if not windows:
        raise ValueError("Claude CLI rate-limit event has no supported window")
    return validate_quota_book({"schema": "monitoring.quotas.v1", "snapshots": [{
        "source": source, "provider": "anthropic", "account": account,
        "captured_at": captured_at,
        "provenance": "claude-cli-rate-limit-" + info["status"],
        "windows": windows,
    }]})


def merge_quota_books(existing, observed, *, source, account):
    """Keep the latest dated observation for each selected CLI window."""
    existing = validate_quota_book(existing)
    observed = validate_quota_book(observed)
    latest = {}
    for book in (existing, observed):
        for snapshot in book["snapshots"]:
            if (snapshot["source"], snapshot["provider"], snapshot["account"]) != (source, "anthropic", account):
                raise ValueError("Quota file does not match this Claude capture alias")
            for window in snapshot["windows"]:
                prior = latest.get(window["id"])
                if prior is None or snapshot["captured_at"] > prior["captured_at"]:
                    latest[window["id"]] = dict(snapshot, windows=[window])
                elif snapshot["captured_at"] == prior["captured_at"] and (window != prior["windows"][0] or
                                                                            snapshot["provenance"] != prior["provenance"]):
                    raise ValueError("Conflicting Claude quota observation at the same time")
    return validate_quota_book({"schema": "monitoring.quotas.v1",
                                "snapshots": [latest[key] for key in sorted(latest)]})

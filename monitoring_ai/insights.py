"""Usage pace, source freshness and independent quota snapshot interpretation."""

from datetime import datetime, timezone
from decimal import Decimal

from .events import BUCKETS, label, timestamp
from .metrics import Exchange, Pricing, seconds

QUOTA_SCHEMA = "monitoring.quotas.v1"
MAX_QUOTA_SNAPSHOTS = 100
MAX_QUOTA_WINDOWS = 500
_SNAPSHOT_FIELDS = {"source", "provider", "account", "captured_at", "provenance", "windows"}
_WINDOW_FIELDS = {"id", "label", "used_percent", "window_minutes", "resets_at"}


def _instant(value):
    return datetime.fromisoformat(timestamp(value).replace("Z", "+00:00")).timestamp()


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate_quota_book(raw):
    """Validate an alias-only quota snapshot book and return its canonical form."""
    if not isinstance(raw, dict) or set(raw) != {"schema", "snapshots"} or raw.get("schema") != QUOTA_SCHEMA or not isinstance(raw["snapshots"], list):
        raise ValueError("Quota book needs monitoring.quotas.v1 and snapshots")
    if len(raw["snapshots"]) > MAX_QUOTA_SNAPSHOTS:
        raise ValueError("Quota book exceeds snapshot limit")
    snapshots = []
    identities = set()
    window_count = 0
    for snapshot in raw["snapshots"]:
        if not isinstance(snapshot, dict) or set(snapshot) != _SNAPSHOT_FIELDS or not isinstance(snapshot["windows"], list):
            raise ValueError("Quota snapshots need exact alias-only fields")
        row = {key: label(snapshot[key], key) for key in ("source", "provider", "account", "provenance")}
        row["captured_at"] = timestamp(snapshot["captured_at"])
        row["windows"] = []
        window_count += len(snapshot["windows"])
        if window_count > MAX_QUOTA_WINDOWS:
            raise ValueError("Quota book exceeds window limit")
        for window in snapshot["windows"]:
            if not isinstance(window, dict) or set(window) != _WINDOW_FIELDS:
                raise ValueError("Quota windows need exact fields")
            item = {"id": label(window["id"], "id"), "label": label(window["label"], "label")}
            identity = (row["source"], row["provider"], row["account"], item["id"])
            if identity in identities:
                raise ValueError("Duplicate quota window identity")
            identities.add(identity)
            pct = window["used_percent"]
            if pct is not None:
                if isinstance(pct, bool):
                    raise ValueError("Quota percentage must be between 0 and 100")
                try:
                    pct = Decimal(str(pct))
                except Exception:
                    raise ValueError("Quota percentage must be between 0 and 100") from None
                if not pct.is_finite() or not 0 <= pct <= 100:
                    raise ValueError("Quota percentage must be between 0 and 100")
                # JSON percentages are numeric values; Decimal is used for
                # validation and subtraction, then emitted as a finite float.
                pct = float(pct)
            item["used_percent"] = pct
            duration = window["window_minutes"]
            if duration is not None and (type(duration) is not int or duration <= 0):
                raise ValueError("Quota window duration must be a positive integer or null")
            item["window_minutes"] = duration
            item["resets_at"] = None if window["resets_at"] is None else timestamp(window["resets_at"])
            row["windows"].append(item)
        snapshots.append(row)
    return {"schema": QUOTA_SCHEMA, "snapshots": snapshots}


def _quota_view(book, now_epoch):
    windows = []
    for snapshot in book["snapshots"]:
        captured = seconds(snapshot["captured_at"])
        exact_age = now_epoch - captured
        age = int(exact_age)
        for window in snapshot["windows"]:
            reset_passed = window["resets_at"] is not None and seconds(window["resets_at"]) <= now_epoch
            freshness = "reset-passed" if reset_passed else "future" if exact_age < 0 else "current" if exact_age <= 300 else "stale"
            used = window["used_percent"]
            windows.append({
                "source": snapshot["source"], "provider": snapshot["provider"], "account": snapshot["account"],
                "provenance": snapshot["provenance"], "captured_at": snapshot["captured_at"],
                "id": window["id"], "label": window["label"], "used_percent": used,
                "remaining_percent": 100.0 - used if used is not None else None,
                "window_minutes": window["window_minutes"], "resets_at": window["resets_at"],
                "freshness": freshness, "age_seconds": age,
            })
    return {"state": "ready" if book["snapshots"] else "unconfigured", "windows": windows,
            "note": "Account-wide quota snapshots are independent of selected usage filters, token costs and worker lifecycle."}


def build_insights(events, *, now, pricing, fx, quota_book=None, window_minutes=60):
    """Build bounded usage pace, cautious linear forecast and independent quota view."""
    if type(window_minutes) is not int or window_minutes <= 0:
        raise ValueError("window_minutes must be a positive integer")
    now_at = seconds(timestamp(now))
    start = now_at - window_minutes * 60
    recent_usage = [e for e in events if e.get("kind") == "usage" and start <= seconds(e["timestamp"]) <= now_at]
    excluded_summaries = [e for e in recent_usage if e.get("surface") == "claude-code-result"]
    recent = [e for e in recent_usage if e.get("surface") != "claude-code-result"]
    token_total = sum(sum(e["tokens"].get(bucket, 0) for bucket in BUCKETS) for e in recent)
    by_time = sorted({seconds(e["timestamp"]) for e in recent})
    observed_span = by_time[-1] - by_time[0] if len(by_time) >= 2 else 0
    token_rate = Decimal(token_total) / Decimal(window_minutes)
    estimate = Decimal(0)
    priced = unknown = unconverted = billed = 0
    for event in recent:
        cost = pricing.cost(event) if pricing else None
        if cost is None:
            unknown += 1
            continue
        if cost.get("basis") == "billed":
            billed += 1
            continue
        if cost.get("basis") != "estimated":
            continue
        value = fx.convert(cost["amount"], cost["currency"]) if fx else None
        if value is None:
            unconverted += 1
        else:
            estimate += value
            priced += 1
    forecast_ready = len(by_time) >= 2 and observed_span >= 60
    projected_tokens = int(token_rate * 60) if forecast_ready else None
    projected_cost = format(estimate * Decimal(60) / Decimal(window_minutes), "f") if forecast_ready and priced and not unconverted and not unknown and not billed else None
    state = "insufficient-data" if not forecast_ready else "partial" if unknown or unconverted or billed or priced == 0 else "available"

    last_by_source = {}
    counts = {}
    for event in events:
        source = event.get("source", "unknown")
        counts[source] = counts.get(source, 0) + 1
        event_time = seconds(event["timestamp"])
        last_by_source[source] = max(last_by_source.get(source, float("-inf")), event_time)
    freshness = []
    for source in sorted(counts):
        last = last_by_source[source]
        exact_age = now_at - last
        age = int(exact_age)
        freshness.append({"source": source, "last_event_at": _iso(last), "age_seconds": age,
                          "state": "future" if exact_age < 0 else "recent" if exact_age <= 60 else "stale", "events": counts[source]})
    book = validate_quota_book(quota_book) if quota_book is not None else {"schema": QUOTA_SCHEMA, "snapshots": []}
    return {
        "schema": "monitoring.insights.v1",
        "window": {"minutes": window_minutes, "from": _iso(start), "until": _iso(now_at)},
        "pace": {"records": len(recent), "excluded_summary_records": len(excluded_summaries),
                 "tokens": token_total, "tokens_per_minute": format(token_rate, "f"),
                 "estimated_cost_per_hour": format(estimate * Decimal(60) / Decimal(window_minutes), "f") if priced else None,
                 "estimated_priced_records": priced, "unknown_cost_records": unknown,
                 "unconverted_cost_records": unconverted, "billed_records": billed},
        "forecast": {"horizon_minutes": 60, "observed_span_seconds": observed_span,
                     "confidence": "insufficient" if not forecast_ready else "low" if observed_span < 600 else "limited",
                     "tokens": projected_tokens, "estimated_cost": projected_cost,
                     "assumption": "Linear extrapolation of the selected recorded usage window; not provider quota or a bill.", "state": state},
        "source_freshness": freshness,
        "quotas": _quota_view(book, now_at),
    }

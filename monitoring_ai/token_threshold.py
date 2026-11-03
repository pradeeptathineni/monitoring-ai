"""Read-only UTC-day threshold over accepted, timestamped token observations."""

from datetime import datetime, timedelta

from .events import BUCKETS, timestamp

SCHEMA = "monitoring.token-threshold.v1"
MAX_LIMIT = 1_000_000_000_000


def validate_limit(value):
    if type(value) is not int or not 1 <= value <= MAX_LIMIT:
        raise ValueError("Token threshold must be an integer from 1 to one trillion")
    return value


def evaluate_token_threshold(events, *, limit, now):
    """Count only accepted usage with producer timestamps in today's elapsed UTC interval.

    Cumulative Claude CLI results have receipt timestamps, not call timestamps,
    so they cannot be assigned to an activity day. Future-dated records are
    excluded until their timestamp arrives. No other source is inferred.
    """
    limit = validate_limit(limit)
    observed_at = timestamp(now)
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    start = observed.replace(hour=0, minute=0, second=0, microsecond=0)
    until = start + timedelta(days=1)
    total = records = excluded_summaries = excluded_future = 0
    for event in events:
        if event["kind"] != "usage" or event["timestamp"][:10] != start.date().isoformat():
            continue
        if event.get("surface") == "claude-code-result":
            excluded_summaries += 1
        elif event["timestamp"] > observed_at:
            excluded_future += 1
        else:
            records += 1
            total += sum(event["tokens"].get(bucket, 0) for bucket in BUCKETS)
    return {
        "schema": SCHEMA,
        "scope": "all-accepted-events-unfiltered",
        "complete_coverage": False,
        "window": {
            "from": start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "until": until.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "observed_at": observed_at,
        },
        "limit_tokens": limit,
        "observed_tokens": total,
        "remaining_observed_tokens": max(0, limit - total),
        "state": "no-observations" if not records else "observed-reached" if total >= limit else "observed-below",
        "usage_records": records,
        "excluded_receipt_summaries": excluded_summaries,
        "excluded_future_records": excluded_future,
        "note": "This is an on-screen threshold over accepted observations, not provider quota, complete account usage, billed spend or an enforcement limit. No notification is sent.",
    }

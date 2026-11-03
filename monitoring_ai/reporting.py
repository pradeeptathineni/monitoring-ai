"""Select comparable elapsed intervals without pretending recorded history is complete."""

from datetime import datetime, timedelta, timezone

from .events import timestamp
from .metrics import filters, now_iso, select_events
from .reports import build_report


def _date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def detailed_report(revision, events, query, pricing, fx, now=None):
    now = timestamp(now or now_iso())
    query = filters(query or {})
    eligible = select_events(events, {k: v for k, v in query.items() if k not in {"since", "until"}}, now)
    future = sum(e["timestamp"] > now for e in eligible)
    eligible = [e for e in eligible if e["timestamp"] <= now]
    until = min(query.get("until", now), now)
    since = query.get("since")
    if since and since > until:
        raise ValueError("The report period begins after its observation time")
    current = [e for e in eligible if (since is None or e["timestamp"] >= since) and e["timestamp"] <= until]
    previous, previous_range = None, None
    comparison_reason = "All recorded history has no inferred preceding observation period."
    if since:
        # Equal inclusive millisecond spans, immediately adjacent with no overlap
        start, end = _date(since), _date(until)
        width = end - start + timedelta(milliseconds=1)
        try:
            prior_end = start - timedelta(milliseconds=1)
            prior_start = start - width
            previous_range = {"from": _iso(prior_start), "until": _iso(prior_end)}
            previous = [e for e in eligible if previous_range["from"] <= e["timestamp"] <= previous_range["until"]]
            comparison_reason = "Immediately preceding interval of equal elapsed length; source coverage can differ."
        except OverflowError:
            comparison_reason = "The preceding interval is outside the supported timestamp range."
    current_range = {"from": since or min((e["timestamp"] for e in current), default=None), "until": until}
    report = build_report(current, previous_events=previous, current_range=current_range,
                          previous_range=previous_range, pricing=pricing, fx=fx, now=now)
    report.update(revision=revision, filters=query)
    report["coverage"].update(ledger_events=len(events), future_events_excluded=future,
                               receipt_time_summary_records=sum(
                                   e.get("kind") == "usage" and e.get("surface") == "claude-code-result"
                                   for e in current
                               ),
                               comparison_basis=comparison_reason, until_clamped_to_observation=query.get("until", now) > now)
    return report

"""Detailed, coverage-qualified reports over preselected monitoring events."""

from collections import defaultdict
from decimal import Decimal

from .metrics import totals

GROUP_LIMIT = 100
TOOL_CATEGORIES = {"core", "mcp", "shell", "skill", "agent", "other"}
UNSUPPORTED = ["context-overhead", "one-shot-rate", "corrections", "time-to-first-edit", "rework"]


def _sessions(events):
    return {(event["source"], event["session"]) for event in events}


def _usage(events):
    return [event for event in events if event.get("kind") == "usage"]


def _share(events):
    usage = _usage(events)
    uncached_and_cached_input = sum(
        event["tokens"].get("input", 0) + event["tokens"].get("cache_read", 0) + event["tokens"].get("cache_write", 0)
        for event in usage
    )
    if not uncached_and_cached_input:
        return None
    cached = sum(event["tokens"].get("cache_read", 0) for event in usage)
    return format(Decimal(cached) * 100 / Decimal(uncached_and_cached_input), "f")


def _session_estimate(events, pricing, fx):
    session_amounts = defaultdict(Decimal)
    for event in _usage(events):
        cost = pricing.cost(event) if pricing else None
        if cost is None or cost.get("basis") != "estimated":
            continue
        converted = fx.convert(cost["amount"], cost["currency"]) if fx else None
        if converted is not None:
            session_amounts[(event["source"], event["session"])] += converted
    total = sum(session_amounts.values(), Decimal(0))
    count = len(session_amounts)
    return count, format(total / count, "f") if count else None


def _percentile(values, percentile):
    """Nearest-rank percentile, returning an observed duration."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, (len(ordered) * percentile + 99) // 100)
    return ordered[rank - 1]


def _timing(events):
    usage = _usage(events)
    durations = [event["duration_ms"] for event in usage if "duration_ms" in event]
    positive = [event for event in usage if event.get("duration_ms", 0) > 0]
    duration_sum = sum(event["duration_ms"] for event in positive)
    output_tokens = sum(event["tokens"].get("output", 0) for event in positive)
    rate = format(Decimal(output_tokens) * 1000 / Decimal(duration_sum), "f") if duration_sum else None
    return {
        "records": len(durations),
        "p50_ms": _percentile(durations, 50),
        "p95_ms": _percentile(durations, 95),
        "output_tokens_per_second": rate,
        "output_rate_records": len(positive),
    }


def _metrics_row(key, events, pricing, fx):
    observed_sessions = len(_sessions(events))
    estimate_sessions, known_average = _session_estimate(events, pricing, fx)
    return {
        "key": key,
        "metrics": totals(events, pricing, fx),
        "observed_sessions": observed_sessions,
        "estimated_session_count": estimate_sessions,
        "known_estimate_per_priced_session": known_average,
        "cache_read_share_percent": _share(events),
        "timing": _timing(events),
    }


def _breakdowns(events, pricing, fx):
    result = {}
    for dimension, key_fn in (
        ("projects", lambda event: event.get("project", "unknown")),
        ("models", lambda event: event.get("model", "unknown")),
        ("daily", lambda event: event["timestamp"][:10]),
    ):
        grouped = defaultdict(list)
        for event in events:
            grouped[key_fn(event)].append(event)
        keys = sorted(grouped)
        rows = [_metrics_row(key, grouped[key], pricing, fx) for key in keys]
        if dimension == "daily":
            rows.sort(key=lambda row: row["key"])
        else:
            rows.sort(key=lambda row: (-row["metrics"]["total_tokens"], row["key"]))
        result[dimension] = {
            "rows": rows[:GROUP_LIMIT],
            "truncated": len(keys) > GROUP_LIMIT,
        }
    return result


def _tool_row(key, category, events):
    durations = [event["duration_ms"] for event in events if "duration_ms" in event]
    return {
        "key": key,
        "category": category,
        "records": len(events),
        "errors": sum(event.get("outcome") == "error" for event in events),
        "unknown_outcomes": sum(event.get("outcome") == "unknown" for event in events),
        "duration_records": len(durations),
        "total_duration_ms": sum(durations) if durations else None,
        "p50_ms": _percentile(durations, 50),
        "p95_ms": _percentile(durations, 95),
    }


def _tool_groups(events):
    tools = [event for event in events if event.get("kind") == "tool"]
    normalized = []
    for event in tools:
        category = event.get("tool_category")
        if category not in TOOL_CATEGORIES:
            category = "unclassified"
        normalized.append((event, category))
    categories = defaultdict(list)
    names = defaultdict(list)
    servers = defaultdict(list)
    shells = defaultdict(list)
    skills = defaultdict(list)
    agents = defaultdict(list)
    for event, category in normalized:
        categories[category].append(event)
        name = event.get("tool", "unknown")
        names[name].append((event, category))
        if category == "mcp" and event.get("tool_server") is not None:
            servers[event["tool_server"]].append(event)
        elif category == "shell":
            shells[name].append(event)
        elif category == "skill":
            skills[name].append(event)
        elif category == "agent":
            agents[name].append(event)
    by_day = defaultdict(list)
    for event, category in normalized:
        by_day[event["timestamp"][:10]].append((event, category))
    days = []
    for date, observations in sorted(by_day.items()):
        per_category = defaultdict(int)
        for _, category in observations:
            per_category[category] += 1
        days.append({
            "date": date,
            "records": len(observations),
            "errors": sum(event.get("outcome") == "error" for event, _ in observations),
            "unknown_outcomes": sum(event.get("outcome") == "unknown" for event, _ in observations),
            "categories": [{"key": key, "records": per_category[key]} for key in sorted(per_category)],
        })
    error_count = sum(event.get("outcome") == "error" for event in tools)
    unknown_count = sum(event.get("outcome") == "unknown" for event in tools)

    def plain(rows):
        return [_tool_row(key, category, grouped) for key, category, grouped in rows]

    grouped = {
        "categories": plain((key, key, categories[key]) for key in sorted(categories)),
        "names": [_tool_row(key, _dominant_or_mixed(values), [item[0] for item in values]) for key, values in sorted(names.items())],
        "mcp_servers": plain((key, "mcp", servers[key]) for key in sorted(servers)),
        "shell_commands": plain((key, "shell", shells[key]) for key in sorted(shells)),
        "skills": plain((key, "skill", skills[key]) for key in sorted(skills)),
        "agents": plain((key, "agent", agents[key]) for key in sorted(agents)),
    }
    for rows in grouped.values():
        rows.sort(key=lambda row: (-row["records"], row["key"]))
    truncated = {name: len(rows) > GROUP_LIMIT for name, rows in grouped.items()}
    truncated["daily"] = len(days) > GROUP_LIMIT
    return {
        "records": len(tools),
        "errors": error_count,
        "unknown_outcomes": unknown_count,
        "unclassified_records": len(categories.get("unclassified", [])),
        **{name: rows[:GROUP_LIMIT] for name, rows in grouped.items()},
        "daily": {"rows": days[-GROUP_LIMIT:], "truncated": truncated["daily"]},
        "truncated": truncated,
    }


def _dominant_or_mixed(values):
    categories = {category for _, category in values}
    return next(iter(categories)) if len(categories) == 1 else "mixed"


def _period(events, pricing, fx):
    usage = _usage(events)
    metrics = totals(events, pricing, fx)
    estimated_sessions, known_average = _session_estimate(events, pricing, fx)
    return {
        "metrics": metrics,
        "records": len(usage),
        "tokens": metrics["total_tokens"],
        "sessions": len(_sessions(events)),
        "estimated_cost": metrics["costs"]["estimated"]["amount"],
        "billed_cost": metrics["costs"]["billed"]["amount"],
        "estimated_session_count": estimated_sessions,
        "known_estimate_per_priced_session": known_average,
    }


def _cost_complete(period, basis):
    return period["records"] > 0 and period["metrics"]["costs"][basis]["events"] == period["records"]


def _comparison_value(current, previous, *, is_cost=False, basis=None):
    current_value = current[basis + "_cost"] if is_cost else current
    if previous is None:
        return {"current": current_value, "previous": None, "change": None, "percent_change": None,
                **({"coverage_reason": "previous-period-unavailable"} if is_cost else {})}
    if is_cost:
        complete = _cost_complete(current, basis) and _cost_complete(previous, basis)
        reason = None if complete else "incomplete-cost-coverage"
        current_amount, previous_amount = current_value, previous[basis + "_cost"]
        if not complete:
            return {"current": current_amount, "previous": previous_amount, "change": None,
                    "percent_change": None, "coverage_reason": reason}
        current_display, previous_display = current_amount, previous_amount
        current_value, previous_value = Decimal(current_amount), Decimal(previous_amount)
    else:
        current_value, previous_value = current, previous
    change = current_value - previous_value
    percent_change = None if previous_value == 0 else format(change * 100 / previous_value, "f")
    change = format(change, "f") if isinstance(change, Decimal) else change
    return {"current": current if not is_cost else current_display,
            "previous": previous if not is_cost else previous_display,
            "change": change, "percent_change": percent_change,
            **({"coverage_reason": None} if is_cost else {})}


def _comparisons(current, previous):
    return {
        "records": _comparison_value(current["records"], previous["records"] if previous is not None else None),
        "tokens": _comparison_value(current["tokens"], previous["tokens"] if previous is not None else None),
        "sessions": _comparison_value(current["sessions"], previous["sessions"] if previous is not None else None),
        "estimated_cost": _comparison_value(current, previous, is_cost=True, basis="estimated"),
        "billed_cost": _comparison_value(current, previous, is_cost=True, basis="billed"),
    }


def build_report(current_events, *, previous_events=None, current_range=None, previous_range=None,
                 pricing, fx, now):
    """Build a bounded detailed report from already selected normalized events."""
    current_events = list(current_events)
    previous_rows = list(previous_events) if previous_events is not None else None
    current = _period(current_events, pricing, fx)
    previous = _period(previous_rows, pricing, fx) if previous_rows is not None else None
    summary = totals(current_events, pricing, fx)
    summary.update({
        "observed_sessions": len(_sessions(current_events)),
        "estimated_session_count": current["estimated_session_count"],
        "known_estimate_per_priced_session": current["known_estimate_per_priced_session"],
        "cache_read_share_percent": _share(current_events),
    })
    usage_records = _usage(current_events)
    tool_records = [event for event in current_events if event.get("kind") == "tool"]
    breakdowns = _breakdowns(current_events, pricing, fx)
    tool_summary = _tool_groups(current_events)
    coverage = {
        "scope": "preselected explicitly recorded events",
        "unknown_cost_events": summary["unknown_cost_events"],
        "unconverted_cost_events": summary["unconverted_cost_events"],
        "missing_usage_duration_records": sum("duration_ms" not in event for event in usage_records),
        "missing_tool_duration_records": sum("duration_ms" not in event for event in tool_records),
        "duration_basis": "End-to-end recorded duration, not model streaming speed.",
        "unsupported": list(UNSUPPORTED),
        "selection_basis": {
            "projects": "recorded_total_tokens_desc_then_key",
            "models": "recorded_total_tokens_desc_then_key",
            "daily": "date_ascending",
            "tools": "records_desc_then_key",
        },
        "truncated": {**{name: value["truncated"] for name, value in breakdowns.items()},
                       **{f"tools_{name}": value for name, value in tool_summary["truncated"].items()}},
    }
    return {
        "schema": "monitoring.report.v1",
        "observed_at": now,
        "currency": fx.currency,
        "fx": fx.raw,
        "range": current_range,
        "previous_range": previous_range,
        "summary": summary,
        "breakdowns": breakdowns,
        "tools": tool_summary,
        "comparisons": _comparisons(current, previous),
        "coverage": coverage,
    }

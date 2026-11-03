"""Observed daily cost thresholds with separate estimate and bill evidence."""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from .events import timestamp

SCHEMA = "monitoring.cost-thresholds.v1"
MAX_LIMIT = Decimal("1000000000")
BASES = ("estimated", "billed")


def validate_limits(limits):
    result = {}
    for basis, value in limits.items():
        if basis not in BASES or isinstance(value, bool):
            raise ValueError("Cost threshold needs an estimated or billed basis")
        supplied = str(value)
        if len(supplied) > 18 or not re.fullmatch(r"(?:0|[1-9]\d{0,9})(?:\.\d{1,6})?", supplied):
            raise ValueError("Cost threshold needs a plain decimal amount with at most six fractional digits")
        try:
            parsed = Decimal(supplied)
        except (InvalidOperation, ValueError):
            raise ValueError("Cost threshold needs a positive decimal amount") from None
        if not parsed.is_finite() or not 0 < parsed <= MAX_LIMIT:
            raise ValueError("Cost threshold needs a positive finite amount up to one billion")
        result[basis] = parsed
    if not result:
        raise ValueError("Configure at least one cost threshold")
    return result


def evaluate_cost_thresholds(events, *, limits, pricing, fx, now):
    """Compare known converted subsets for today's elapsed UTC interval.

    A Claude CLI result has a receipt timestamp rather than individual call
    times. It is counted in the receipt day, and the response labels that fact.
    No unpriced or unconverted event is guessed into a threshold.
    """
    limits = validate_limits(limits)
    observed_at = timestamp(now)
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    start = observed.replace(hour=0, minute=0, second=0, microsecond=0)
    until = start + timedelta(days=1)
    start_at = start.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    until_at = until.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    items = {basis: dict(amount=Decimal(0), records=0, receipt_records=0,
                         unpriced=0, unconverted=0, opposite_basis=0, future=0,
                         provenance=set()) for basis in limits}
    for event in events:
        if event["kind"] != "usage" or not start_at <= event["timestamp"] < until_at:
            continue
        if event["timestamp"] > observed_at:
            for item in items.values():
                item["future"] += 1
            continue
        cost = pricing.cost(event)
        for basis, item in items.items():
            if cost is None:
                item["unpriced"] += 1
            elif cost["basis"] != basis:
                item["opposite_basis"] += 1
            else:
                converted = fx.convert(cost["amount"], cost["currency"])
                if converted is None:
                    item["unconverted"] += 1
                else:
                    item["amount"] += converted
                    item["records"] += 1
                    item["provenance"].add(cost["provenance"])
                    if event.get("surface") == "claude-code-result":
                        item["receipt_records"] += 1
    rows = []
    for basis, item in items.items():
        amount = item["amount"]
        rows.append({
            "basis": basis, "limit_amount": format(limits[basis], "f"),
            "observed_amount": format(amount, "f") if item["records"] else None,
            "remaining_observed_amount": format(max(Decimal(0), limits[basis] - amount), "f")
            if item["records"] else None,
            "state": "no-observations" if not item["records"] else
                     "observed-reached" if amount >= limits[basis] else "observed-below",
            "covered_records": item["records"], "receipt_time_records": item["receipt_records"],
            "unpriced_records": item["unpriced"], "unconverted_records": item["unconverted"],
            "other_basis_records": item["opposite_basis"], "future_records": item["future"],
            "cost_provenance": sorted(item["provenance"])[:20],
            "provenance_truncated": len(item["provenance"]) > 20,
        })
    return {
        "schema": SCHEMA, "scope": "all-accepted-events-unfiltered",
        "complete_coverage": False, "currency": fx.currency,
        "window": {"from": start_at, "until": until_at, "observed_at": observed_at},
        "items": rows,
        "note": "Local on-screen comparison of known cost evidence only. Estimates and bills stay separate. Missing sources, prices and conversions make actual totals unknown. This does not notify, enforce spending or establish provider billing.",
    }

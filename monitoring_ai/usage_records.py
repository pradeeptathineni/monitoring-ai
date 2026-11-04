"""Explain recorded usage costs without repricing supplied costs or inferring calls."""

from collections import Counter
from decimal import Decimal

from .events import BUCKETS, timestamp
from .metrics import filters, now_iso, selected

COST_STATES = {"estimated", "billed", "unpriced", "unconverted"}


def explain_usage(event, pricing, fx):
    cost = pricing.cost(event)
    method = "recorded" if "cost" in event else "price-book" if cost is not None else "unpriced"
    rate, bucket_costs = None, None
    if method == "price-book":
        matched = next(item for item in pricing.rates if
                       (event["provider"], event["model"]) == (item["provider"], item["model"])
                       and item["from"] <= event["timestamp"] < item["until"])
        rate = {key: str(value) if key in BUCKETS else value for key, value in matched.items()}
        bucket_costs = {key: format(Decimal(event["tokens"][key]) * matched[key] / 1000000, "f") for key in BUCKETS}
    display, state = None, "unpriced"
    explanation = "No recorded cost or matching dated model price."
    if cost is not None:
        amount = fx.convert(cost["amount"], cost["currency"])
        if amount is None:
            state = "unconverted"
            explanation = f"Missing conversion from {cost['currency']} to {fx.currency}."
        else:
            state = cost["basis"]
            display = dict(cost, amount=format(amount, "f"), currency=fx.currency)
            explanation = f"Recorded {cost['basis']} cost." if method == "recorded" else "Estimate from the matching dated model price."
    return dict(event=event, pricing_method=method, cost_state=state, explanation=explanation,
                native_cost=cost, display_cost=display, rate=rate, bucket_costs=bucket_costs)


def usage_page(revision, events, query, pricing, fx, *, limit=50, after=0, cost_state="", now=None):
    if type(limit) is not int or not 1 <= limit <= 200 or type(after) is not int or not 0 <= after <= 100000 or "state" in query:
        raise ValueError("Use pages of 1–200, a nonnegative bounded offset and omit current state")
    if cost_state and cost_state not in COST_STATES:
        raise ValueError("Unsupported usage cost state")
    query = filters(query)
    observed = timestamp(now or now_iso())
    matching = [explain_usage(event, pricing, fx) for event in events if event["kind"] == "usage" and selected(event, query)]
    coverage = Counter(row["cost_state"] for row in matching)
    rows = [row for row in matching if not cost_state or row["cost_state"] == cost_state]
    rows.sort(key=lambda row: (row["event"]["timestamp"], row["event"]["source"], row["event"]["id"]), reverse=True)
    return dict(schema="monitoring.usage-records.v1", revision=revision, observed_at=observed,
                currency=fx.currency, fx=fx.raw, filters=query, cost_state=cost_state,
                scope="matching-accepted-ledger-usage", complete_coverage=False,
                records=rows[after:after + limit], total=len(rows), matching_usage_records=len(matching),
                cost_coverage={key: coverage[key] for key in sorted(COST_STATES)},
                contains_synthetic_records=any(row["event"]["source"] == "synthetic" for row in rows),
                future_dated_records=sum(row["event"]["timestamp"] > observed for row in rows),
                offset=after, limit=limit, next_offset=after + limit if after + limit < len(rows) else None)

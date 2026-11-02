"""Aggregate disjoint usage and keep cost basis, coverage and observation age visible."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import json

from .events import BUCKETS, amount, label, timestamp

DIMENSIONS = ("provider", "model", "surface", "project", "source", "session")
TERMINAL = {"completed", "failed", "cancelled"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def seconds(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class Pricing:
    def __init__(self, path=None):
        self.rates = []
        if path:
            raw = json.loads(path.read_text())
            if not isinstance(raw, dict) or raw.get("schema") != "monitoring.pricing.v1" or not isinstance(raw.get("rates"), list):
                raise ValueError("Invalid pricing book")
            for item in raw["rates"]:
                if not isinstance(item, dict) or set(item) != {"provider", "model", "currency", "from", "until", "provenance", *BUCKETS}:
                    raise ValueError("Each price needs exact model, dates, provenance and four rates")
                for field in ("provider", "model", "provenance"):
                    label(item[field], field)
                if not isinstance(item["currency"], str) or len(item["currency"]) != 3 or not item["currency"].isupper():
                    raise ValueError("Invalid price currency")
                item = dict(item, **{key: Decimal(amount(item[key])) for key in BUCKETS})
                item["from"], item["until"] = timestamp(item["from"]), timestamp(item["until"])
                if item["until"] <= item["from"]:
                    raise ValueError("Invalid price date range")
                for other in self.rates:
                    if all(item[k] == other[k] for k in ("provider", "model")) and max(item["from"], other["from"]) < min(item["until"], other["until"]):
                        raise ValueError("Overlapping price intervals")
                self.rates.append(item)

    def cost(self, event):
        if "cost" in event:
            return event["cost"]
        for rate in self.rates:
            if (event["provider"], event["model"]) == (rate["provider"], rate["model"]) and rate["from"] <= event["timestamp"] < rate["until"]:
                total = sum(Decimal(event["tokens"][key]) * rate[key] for key in BUCKETS) / 1000000
                return dict(amount=str(total), currency=rate["currency"], basis="estimated", provenance=rate["provenance"])
        return None


class Exchange:
    def __init__(self, currency="USD", path=None):
        self.currency = currency
        self.raw = None
        if path:
            self.raw = json.loads(path.read_text())
            if not isinstance(self.raw, dict) or set(self.raw) != {"schema", "base", "as_of", "provenance", "rates"} or self.raw["schema"] != "monitoring.fx.v1" or self.raw["base"] != "USD" or not isinstance(self.raw["rates"], dict):
                raise ValueError("FX book needs USD base, dated rates and provenance")
            self.raw["as_of"] = timestamp(self.raw["as_of"])
            label(self.raw["provenance"], "provenance")
            for key, value in self.raw["rates"].items():
                if len(key) != 3 or not key.isupper() or Decimal(amount(value)) <= 0:
                    raise ValueError("Invalid exchange rate")
            if Decimal(str(self.raw["rates"].get("USD", 1))) != 1:
                raise ValueError("USD base rate must be one")

    def convert(self, value, source):
        if source == self.currency:
            return Decimal(value)
        rates = {"USD": "1", **(self.raw or {}).get("rates", {})}
        if source not in rates or self.currency not in rates:
            return None
        return Decimal(value) / Decimal(str(rates[source])) * Decimal(str(rates[self.currency]))


def filters(query):
    allowed = {*DIMENSIONS, "since", "until", "state"}
    if set(query) - allowed:
        raise ValueError("Unknown filter")
    result = {}
    for key, value in query.items():
        if key in {"since", "until"}:
            # Date inputs use an inclusive UTC day, timestamp inputs use exact inclusive bounds
            if len(value) == 10:
                value += "T00:00:00Z" if key == "since" else "T23:59:59.999Z"
            result[key] = timestamp(value)
        else:
            result[key] = label(value, key)
    if "since" in result and "until" in result and result["since"] > result["until"]:
        raise ValueError("Since must precede until")
    return result


def selected(event, query):
    return all(
        event["timestamp"] >= value if key == "since" else
        event["timestamp"] <= value if key == "until" else
        event.get(key) == value
        for key, value in query.items() if key != "state"
    )


def totals(events, pricing, fx):
    buckets = Counter({key: 0 for key in BUCKETS})
    sums = {"estimated": Decimal(0), "billed": Decimal(0)}
    covered = Counter()
    unknown = unconverted = calls = tools = errors = duration = 0
    provenance = set()
    for event in events:
        if event["kind"] == "tool":
            tools += 1
            errors += event["outcome"] == "error"
            duration += event.get("duration_ms", 0)
        if event["kind"] != "usage":
            continue
        calls += 1
        buckets.update(event["tokens"])
        cost = pricing.cost(event)
        if cost is None:
            unknown += 1
            continue
        value = fx.convert(cost["amount"], cost["currency"])
        provenance.add((cost["basis"], cost["provenance"], cost["currency"]))
        if value is None:
            unconverted += 1
            continue
        sums[cost["basis"]] += value
        covered[cost["basis"]] += 1
    return dict(
        calls=calls, tokens=dict(buckets), total_tokens=sum(buckets.values()),
        costs={key: {"amount": format(sums[key], "f") if covered[key] else None,
                     "events": covered[key]} for key in sums},
        unknown_cost_events=unknown, unconverted_cost_events=unconverted,
        cost_provenance=[dict(basis=b, provenance=p, currency=c) for b, p, c in sorted(provenance)],
        tools=tools, tool_errors=errors, tool_duration_ms=duration,
    )


def entity_key(event):
    return (event["source"], event["session"], event["entity"])


def entity_observations(events, now):
    by_entity = defaultdict(list)
    for event in events:
        by_entity[entity_key(event)].append(event)
    entities = {}
    for key, rows in by_entity.items():
        rows = sorted(rows, key=lambda row: (row["timestamp"], row["id"]))
        status = next((r for r in reversed(rows) if r["kind"] == "status"), None)
        age = max(0, seconds(now) - seconds(status["timestamp"])) if status else None
        state = status["state"] if status else "unknown"
        stale = age is None or age > 60 or seconds(status["timestamp"]) > seconds(now) + 5
        effective = "unknown" if state not in TERMINAL and stale else state
        first, last = rows[0], rows[-1]
        entities[key] = dict(
            source=key[0], session=key[1], entity=key[2], parent=first["parent"],
            entity_type=first["entity_type"], project=first["project"],
            provider=last["provider"], model=last["model"], surface=last["surface"],
            started_at=first["timestamp"], last_event_at=last["timestamp"],
            state=effective, reported_state=state,
            status_at=status["timestamp"] if status else None,
            evidence_age_seconds=round(age) if age is not None else None,
            stale=stale,
            parent_observed=first["parent"] is None or (key[0], key[1], first["parent"]) in by_entity,
        )
    return entities


def select_events(events, query=None, now=None, entities=None):
    query = filters(query or {})
    if entities is None and "state" in query:
        entities = entity_observations(events, now or now_iso())
    return [e for e in events if selected(e, query) and (
        "state" not in query or entities[entity_key(e)]["state"] == query["state"]
    )]


def snapshot(revision, events, query=None, pricing=None, fx=None, now=None, quota_book=None):
    from .insights import build_insights

    query = filters(query or {})
    pricing, fx, now = pricing or Pricing(), fx or Exchange(), now or now_iso()
    entities = entity_observations(events, now)
    matched = select_events(events, query, now, entities)
    matched_by_entity = defaultdict(list)
    for event in matched:
        matched_by_entity[entity_key(event)].append(event)
    rows = [dict(entities[key], metrics=totals(values, pricing, fx)) for key, values in matched_by_entity.items()]
    rows.sort(key=lambda row: (row["last_event_at"], row["entity"]), reverse=True)
    groups = {}
    for dimension in (*DIMENSIONS[:4], "date"):
        values = defaultdict(list)
        for event in matched:
            values[event["timestamp"][:10] if dimension == "date" else event[dimension]].append(event)
        groups[dimension] = [dict(key=key, **totals(group, pricing, fx)) for key, group in sorted(values.items())]
    facets = {d: sorted({e[d] for e in events}) for d in DIMENSIONS}
    recent = [dict(event, resolved_cost=pricing.cost(event) if event["kind"] == "usage" else None) for event in reversed(matched[-100:])]
    return dict(
        schema="monitoring.snapshot.v1", revision=revision, observed_at=now,
        currency=fx.currency, fx=fx.raw, filters=query,
        coverage={"scope": "explicitly supplied files only", "event_count": len(events),
                  "matched_events": len(matched), "live_window_seconds": 60,
                  "read_limit": 100000, "entity_limit": 500,
                  "entities_truncated": len(rows) > 500},
        summary=dict(totals(matched, pricing, fx), entities=len(rows),
                     sessions=len({(e["source"], e["session"]) for e in matched}),
                     active=sum(r["state"] == "running" for r in rows),
                     attention=sum(r["state"] in {"waiting", "blocked"} for r in rows)),
        entities=rows[:500], groups=groups, facets=facets, events=recent,
        insights=build_insights(matched, now=now, pricing=pricing, fx=fx, quota_book=quota_book),
    )

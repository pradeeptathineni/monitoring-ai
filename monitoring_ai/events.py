"""Validate metadata-only usage deltas before they enter the ledger."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

SCHEMA = "monitoring.event.v1"
BUCKETS = ("input", "cache_read", "cache_write", "output")
STATES = {"running", "waiting", "blocked", "completed", "failed", "cancelled", "unknown"}
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:@-]{1,160}\Z")
FIELDS = {
    "schema", "id", "source", "session", "entity", "parent", "entity_type",
    "timestamp", "kind", "provider", "model", "surface", "project", "state",
    "tokens", "cost", "duration_ms", "tool", "outcome",
}


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Expected a timezone-qualified ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError("Timestamp needs a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def amount(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Expected a nonnegative decimal amount")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Invalid amount") from None
    if not number.is_finite() or number < 0 or number > Decimal("1e12"):
        raise ValueError("Amount is out of range")
    return format(number, "f")


def label(value, field):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a short identifier, not a path or free text")
    return value


def validate(raw):
    if not isinstance(raw, dict) or set(raw) - FIELDS:
        raise ValueError("Unknown event fields. Prompts and arbitrary metadata are not accepted")
    if raw.get("schema") != SCHEMA:
        raise ValueError("Unsupported event schema")
    event = {key: label(raw.get(key), key) for key in (
        "id", "source", "session", "entity", "provider", "model", "surface", "project"
    )}
    event.update(schema=SCHEMA, timestamp=timestamp(raw.get("timestamp")))
    event["parent"] = None if raw.get("parent") is None else label(raw["parent"], "parent")
    if event["parent"] == event["entity"]:
        raise ValueError("An entity cannot parent itself")
    if raw.get("entity_type") not in {"session", "agent", "worker"}:
        raise ValueError("Invalid entity_type")
    event["entity_type"] = raw["entity_type"]
    kind = raw.get("kind")
    if kind not in {"usage", "status", "tool"}:
        raise ValueError("Invalid event kind")
    event["kind"] = kind
    if kind == "usage":
        tokens = raw.get("tokens")
        if not isinstance(tokens, dict) or set(tokens) - set(BUCKETS):
            raise ValueError("Expected disjoint token buckets")
        if not tokens:
            raise ValueError("Usage needs token counts")
        for key in BUCKETS:
            value = tokens.get(key, 0)
            if type(value) is not int or not 0 <= value <= 10**12:
                raise ValueError("Token counts must be nonnegative integers")
        event["tokens"] = {key: tokens.get(key, 0) for key in BUCKETS}
        if "cost" in raw:
            cost = raw["cost"]
            if not isinstance(cost, dict) or set(cost) != {"amount", "currency", "basis", "provenance"}:
                raise ValueError("Cost needs amount, currency, basis and provenance")
            if cost["basis"] not in {"estimated", "billed"}:
                raise ValueError("Cost basis must be estimated or billed")
            if not isinstance(cost["currency"], str) or not re.fullmatch(r"[A-Z]{3}", cost["currency"]):
                raise ValueError("Currency must be an uppercase three-letter code")
            event["cost"] = dict(cost, amount=amount(cost["amount"]), provenance=label(cost["provenance"], "provenance"))
    elif "tokens" in raw or "cost" in raw:
        raise ValueError("Only usage events can carry tokens and costs")
    if kind == "status":
        if raw.get("state") not in STATES:
            raise ValueError("Invalid lifecycle state")
        event["state"] = raw["state"]
    elif "state" in raw:
        raise ValueError("Only status events can set lifecycle state")
    if kind == "tool":
        event["tool"] = label(raw.get("tool"), "tool")
        if raw.get("outcome") not in {"ok", "error", "unknown"}:
            raise ValueError("Invalid tool outcome")
        event["outcome"] = raw["outcome"]
    if "duration_ms" in raw:
        if type(raw["duration_ms"]) is not int or not 0 <= raw["duration_ms"] <= 10**12:
            raise ValueError("Invalid duration_ms")
        event["duration_ms"] = raw["duration_ms"]
    if kind != "tool" and ("tool" in raw or "outcome" in raw):
        raise ValueError("Tool fields require a tool event")
    return event

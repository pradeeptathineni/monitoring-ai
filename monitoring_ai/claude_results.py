"""Parse sanitized cumulative Claude CLI result frames into usage deltas.

Result frames contain whole-session model totals (including resumed work and
subagents). Callers must retain a complete, ordered capture history so the
first emitted event remains a stable import boundary.
"""

from decimal import Decimal

from . import adapters
from .events import SCHEMA, amount, label, timestamp, validate
from .store import stable_id

FRAME_SCHEMA = "monitoring.claude-result.v1"
_COUNTERS = (
    ("inputTokens", "input"),
    ("cacheReadInputTokens", "cache_read"),
    ("cacheCreationInputTokens", "cache_write"),
    ("outputTokens", "output"),
)


def _frame_result(raw):
    if not isinstance(raw, dict):
        raise ValueError("Invalid Claude result frame")
    if "schema" in raw:
        if raw.get("schema") != FRAME_SCHEMA or not isinstance(raw.get("result"), dict):
            raise ValueError("Invalid Claude result frame")
        return raw["result"]
    return raw


def _session(value):
    if not isinstance(value, str) or not value or len(value) > 1000:
        raise ValueError("Invalid Claude result frame")
    return value


def _model_usage(value):
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError("Invalid Claude result frame")
    clean = {}
    for model, detail in value.items():
        if not isinstance(model, str) or not model or len(model) > 1000 or not isinstance(detail, dict):
            raise ValueError("Invalid Claude result frame")
        counters = {}
        for native_name, _ in _COUNTERS:
            number = detail.get(native_name)
            if type(number) is not int or not 0 <= number <= 10**12:
                raise ValueError("Invalid Claude result frame")
            counters[native_name] = number
        cost = detail.get("costUSD")
        if cost is not None:
            try:
                cost = amount(cost)
            except (ValueError, TypeError):
                raise ValueError("Invalid Claude result frame") from None
        provider = detail.get("provider")
        if provider is not None and not isinstance(provider, str):
            raise ValueError("Invalid Claude result frame")
        item = dict(counters, costUSD=cost)
        if provider is not None:
            item["provider"] = "firstParty" if provider == "firstParty" else "unknown"
        clean[model] = item
    return clean


def capture_result(raw, captured_at):
    """Return a privacy-limited wrapper for one native ``type=result`` frame."""
    result = _frame_result(raw)
    if result.get("type") != "result":
        raise ValueError("Invalid Claude result frame")
    if "modelUsage" not in result or not isinstance(result["modelUsage"], dict):
        raise ValueError("Invalid Claude result frame")
    session = _session(result.get("session_id"))
    at = timestamp(captured_at)
    model_usage = _model_usage(result["modelUsage"])
    return {
        "schema": FRAME_SCHEMA,
        "captured_at": at,
        "result": {"type": "result", "session_id": session, "modelUsage": model_usage},
    }


def _aliases(source, project):
    try:
        return label(source, "source"), label(project, "project")
    except ValueError:
        raise ValueError("Invalid Claude result aliases") from None


def claude_results(frames, source, project):
    """Convert complete ordered result-frame history into normalized usage deltas."""
    source, project = _aliases(source, project)
    if isinstance(frames, (str, bytes, dict)):
        raise ValueError("Invalid Claude result frames")
    try:
        iterator = iter(frames)
    except TypeError:
        raise ValueError("Invalid Claude result frames") from None

    # Native session -> per-model cumulative state and most recent capture time.
    sessions = {}
    output = []
    try:
        for raw in iterator:
            if (not isinstance(raw, dict) or set(raw) != {"schema", "captured_at", "result"}
                    or raw.get("schema") != FRAME_SCHEMA):
                raise ValueError("Invalid Claude result frames")
            at_hint = raw.get("captured_at") if isinstance(raw, dict) else None
            clean = capture_result(raw, at_hint)
            result = clean["result"]
            native_session = result["session_id"]
            at = clean["captured_at"]
            session_state = sessions.setdefault(native_session, {"at": None, "models": {}})
            if session_state["at"] is not None and at < session_state["at"]:
                raise ValueError("Claude result timestamps must be nondecreasing per session")
            session_state["at"] = at
            current_models = result["modelUsage"]
            previous_models = session_state["models"]
            if set(previous_models) - set(current_models):
                raise ValueError("Claude result model totals are incomplete")

            for raw_model, current in current_models.items():
                previous = previous_models.get(raw_model)
                totals = tuple(current[key] for key, _ in _COUNTERS)
                cost = current["costUSD"]
                if previous is None:
                    old_totals = (0, 0, 0, 0)
                    old_cost = None
                    cost_known = cost is not None
                    provider = "anthropic" if current.get("provider") == "firstParty" else "unknown"
                else:
                    old_totals = previous["totals"]
                    old_cost = previous["cost"]
                    cost_known = previous["cost_known"]
                    provider = "anthropic" if current.get("provider") == "firstParty" else "unknown"
                    if provider != previous["provider"]:
                        raise ValueError("Claude result provider changed across cumulative history")
                    if cost_known != (cost is not None):
                        raise ValueError("Claude result cost coverage changed ambiguously")
                if any(now < before for now, before in zip(totals, old_totals)):
                    raise ValueError("Claude result token totals decreased")
                if cost_known and previous is not None and Decimal(cost) < Decimal(old_cost):
                    raise ValueError("Claude result cost total decreased")

                token_delta = tuple(now - before for now, before in zip(totals, old_totals))
                cost_delta = None
                if cost_known:
                    cost_delta = Decimal(cost) - (Decimal(old_cost) if previous is not None else Decimal(0))
                changed = any(token_delta) or (cost_delta is not None and cost_delta != 0)
                ordinal = (previous or {}).get("event_ordinal", -1)
                if changed:
                    ordinal += 1
                    event = {
                        "schema": SCHEMA,
                        "id": "cr-" + stable_id(
                            source, native_session, raw_model,
                            "initial" if ordinal == 0 else ordinal,
                        ),
                        "source": source,
                        "session": "s-" + stable_id(native_session),
                        "entity": "s-" + stable_id(native_session),
                        "parent": None,
                        "entity_type": "session",
                        "timestamp": at,
                        "kind": "usage",
                        "provider": provider,
                        "model": adapters.safe_label(raw_model),
                        "surface": "claude-code-result",
                        "project": project,
                        "tokens": {bucket: value for (_, bucket), value in zip(_COUNTERS, token_delta)},
                    }
                    if cost_delta is not None:
                        event["cost"] = {
                            "amount": format(cost_delta, "f"),
                            "currency": "USD",
                            "basis": "estimated",
                            "provenance": "claude-cli-client-estimate",
                        }
                    output.append(validate(event))
                previous_models[raw_model] = {
                    "totals": totals,
                    "cost": cost,
                    "cost_known": cost_known,
                    "provider": provider,
                    "event_ordinal": ordinal,
                }
    except ValueError as exc:
        # Public failures remain generic and do not include native frame content.
        message = str(exc)
        if message.startswith("Claude result ") or message == "Invalid Claude result aliases":
            raise
        raise ValueError("Invalid Claude result frames") from None
    except (TypeError, KeyError):
        raise ValueError("Invalid Claude result frames") from None
    return output

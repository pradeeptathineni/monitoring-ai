"""Project one explicitly selected completed Responses API object into metadata."""

from datetime import datetime, timezone
import json

from .adapters import read_source, unique_keys, reject_constant
from .events import validate
from .producer import EventWriter
from .result_capture import publish_private_json
from .store import stable_id


def _count(value):
    if type(value) is not int or not 0 <= value <= 10**12:
        raise ValueError("Invalid response usage counter")
    return value


def response_event(raw, *, source, project, session):
    if not isinstance(raw, dict) or raw.get("object") != "response" or raw.get("status") != "completed":
        raise ValueError("Select one completed Responses API object")
    ident = raw.get("id")
    if not isinstance(ident, str) or not ident or len(ident) > 1000:
        raise ValueError("Response identity is missing")
    created, completed = raw.get("created_at"), raw.get("completed_at")
    if type(created) is not int or type(completed) is not int or not 0 <= created <= completed <= 253402300799:
        raise ValueError("Original response creation and completion times are required")
    usage = raw.get("usage")
    if not isinstance(usage, dict) or not isinstance(usage.get("input_tokens_details"), dict):
        raise ValueError("Response usage details are missing")
    input_tokens, output_tokens = _count(usage.get("input_tokens")), _count(usage.get("output_tokens"))
    cached = _count(usage["input_tokens_details"].get("cached_tokens"))
    written = _count(usage["input_tokens_details"].get("cache_write_tokens"))
    if cached + written > input_tokens or _count(usage.get("total_tokens")) != input_tokens + output_tokens:
        raise ValueError("Response usage counters are inconsistent")
    output_details = usage.get("output_tokens_details", {})
    if not isinstance(output_details, dict):
        raise ValueError("Unsupported output token details")
    if "reasoning_tokens" in output_details and _count(output_details["reasoning_tokens"]) > output_tokens:
        raise ValueError("Reasoning exceeds reported output tokens")
    return validate(dict(schema="monitoring.event.v1", id="response-" + stable_id("openai-response", ident),
        source=source, project=project, session=session, entity=session, parent=None, entity_type="session",
        timestamp=datetime.fromtimestamp(completed, timezone.utc).isoformat().replace("+00:00", "Z"),
        kind="usage", provider="openai", model=raw.get("model"), surface="openai-response-completed",
        tokens=dict(input=input_tokens - cached - written, cache_read=cached, cache_write=written, output=output_tokens),
        duration_ms=(completed - created) * 1000))


def capture_response(source_file, output, *, source, project, session, append=False):
    raw = json.loads(read_source(source_file), object_pairs_hook=unique_keys, parse_constant=reject_constant)
    event = response_event(raw, source=source, project=project, session=session)
    replayed = False
    if append:
        # Match the response hash across aliases to catch accidental regrouping
        with EventWriter(output, append=True) as writer:
            matching = [row for row in writer.existing if row["id"] == event["id"]]
            if any(row != event for row in matching):
                raise ValueError("Response identity conflicts with an existing receipt; output is unchanged")
            replayed = bool(matching)
            if not replayed:
                writer.write(event)
    else:
        publish_private_json(output, event)
    return dict(schema="monitoring.openai-receipt-capture.v1", events=int(not replayed), replayed=int(replayed),
                timestamp=event["timestamp"], tokens=event["tokens"], cost="unreported")

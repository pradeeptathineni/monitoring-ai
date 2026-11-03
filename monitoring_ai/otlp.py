"""Project explicitly selected OTLP/JSON trace batches into metadata-only events.

This is a deliberately narrow file adapter, not an OTLP receiver. The producer
owns export and authorization; prompt bodies, span names and arbitrary attributes
are dropped before rows can accumulate in the source reader.
"""

from datetime import datetime, timezone, timedelta
import re

from .events import SCHEMA, validate
from .store import stable_id


INFERENCE = frozenset({"chat", "generate_content", "text_completion", "embeddings"})
HEX_TRACE = re.compile(r"[0-9a-fA-F]{32}\Z")
HEX_SPAN = re.compile(r"[0-9a-fA-F]{16}\Z")
LABEL = re.compile(r"[A-Za-z0-9_.:@-]{1,160}\Z")
COUNTERS = {
    "gen_ai.usage.input_tokens": "total_input",
    "gen_ai.usage.output_tokens": "output",
    "gen_ai.usage.cache_read.input_tokens": "cache_read",
    "gen_ai.usage.cache_write.input_tokens": "cache_write",
}
STRINGS = {
    "gen_ai.operation.name", "gen_ai.provider.name", "gen_ai.request.model",
    "gen_ai.response.model", "gen_ai.tool.name",
}
MAX_SPANS = 5000


def _nanos(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"[0-9]{1,21}", str(value)):
        raise ValueError("OTLP span needs a valid nanosecond timestamp")
    number = int(value)
    if not 0 < number <= 253402300799999999999:  # UTC year 9999
        raise ValueError("OTLP span timestamp is out of range")
    return number


def _time(value):
    nanos = _nanos(value)
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=nanos // 1000)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _label(value, fallback="unknown"):
    return value if isinstance(value, str) and LABEL.fullmatch(value) else fallback


def _attributes(raw):
    if not isinstance(raw, list):
        raise ValueError("OTLP attributes must be a list")
    kept = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("OTLP attribute must be an object")
        key = item.get("key")
        if not isinstance(key, str):
            continue
        if key not in COUNTERS and key not in STRINGS:
            continue
        if key in kept:
            raise ValueError("OTLP span repeats a supported attribute")
        value = item.get("value")
        if not isinstance(value, dict):
            raise ValueError("OTLP supported attribute needs a typed value")
        if key in COUNTERS:
            number = value.get("intValue")
            if isinstance(number, bool) or not isinstance(number, (str, int)) or not re.fullmatch(r"[0-9]{1,13}", str(number)):
                raise ValueError("OTLP token counter must be a nonnegative integer")
            kept[key] = int(number)
        else:
            string = value.get("stringValue")
            if not isinstance(string, str):
                raise ValueError("OTLP supported label must be a string")
            kept[key] = string
    return kept


def _status_code(span):
    status = span.get("status", {})
    code = status.get("code", 0) if isinstance(status, dict) else None
    if type(code) is not int or code not in (0, 1, 2):
        raise ValueError("OTLP span status code must be 0, 1 or 2")
    return code


def project_request(request, source, project):
    """Return validated events for one ExportTraceServiceRequest JSON object."""
    if not isinstance(request, dict) or not isinstance(request.get("resourceSpans"), list):
        raise ValueError("Expected an OTLP ExportTraceServiceRequest")
    events, count = [], 0
    for resource in request["resourceSpans"]:
        if not isinstance(resource, dict) or not isinstance(resource.get("scopeSpans"), list):
            raise ValueError("Invalid OTLP resource spans")
        for scope in resource["scopeSpans"]:
            if not isinstance(scope, dict) or not isinstance(scope.get("spans"), list):
                raise ValueError("Invalid OTLP scope spans")
            for span in scope["spans"]:
                count += 1
                if count > MAX_SPANS:
                    raise ValueError("OTLP batch exceeds 5,000 spans")
                if not isinstance(span, dict):
                    raise ValueError("OTLP span must be an object")
                attrs = _attributes(span.get("attributes", []))
                operation = attrs.get("gen_ai.operation.name")
                if operation not in INFERENCE | {"invoke_agent", "execute_tool"}:
                    continue
                trace, identity = span.get("traceId"), span.get("spanId")
                if not isinstance(trace, str) or not HEX_TRACE.fullmatch(trace) or int(trace, 16) == 0:
                    raise ValueError("OTLP trace ID must be 16 hex bytes")
                if not isinstance(identity, str) or not HEX_SPAN.fullmatch(identity) or int(identity, 16) == 0:
                    raise ValueError("OTLP span ID must be 8 hex bytes")
                trace, identity = trace.lower(), identity.lower()
                start, end = _nanos(span.get("startTimeUnixNano")), _nanos(span.get("endTimeUnixNano"))
                if end < start:
                    raise ValueError("OTLP span ends before it starts")
                session = "s-" + stable_id(("otlp-trace", trace))
                provider = _label(attrs.get("gen_ai.provider.name"))
                model = _label(attrs.get("gen_ai.request.model") or attrs.get("gen_ai.response.model"))
                base = dict(schema=SCHEMA, source=source, session=session, entity=session,
                            parent=None, entity_type="session", timestamp=_time(end),
                            provider=provider, model=model, surface="otlp", project=project,
                            duration_ms=(end - start) // 1_000_000)
                if operation in INFERENCE:
                    if not any(key in attrs for key in COUNTERS):
                        continue
                    total = attrs.get("gen_ai.usage.input_tokens", 0)
                    read = attrs.get("gen_ai.usage.cache_read.input_tokens", 0)
                    write = attrs.get("gen_ai.usage.cache_write.input_tokens", 0)
                    if read + write > total:
                        raise ValueError("OTLP cached input exceeds total input")
                    event = dict(base, id=stable_id(("otlp", trace, identity, "usage")), kind="usage",
                                 tokens={"input": total - read - write, "cache_read": read,
                                         "cache_write": write,
                                         "output": attrs.get("gen_ai.usage.output_tokens", 0)})
                elif operation == "invoke_agent":
                    code = _status_code(span)
                    event = dict(base, id=stable_id(("otlp", trace, identity, "agent")),
                                 entity="a-" + stable_id((trace, identity)), parent=session,
                                 entity_type="agent", kind="status",
                                 state="failed" if code == 2 else "completed")
                else:
                    code = _status_code(span)
                    event = dict(base, id=stable_id(("otlp", trace, identity, "tool")), kind="tool",
                                 tool=_label(attrs.get("gen_ai.tool.name"), "tool"),
                                 outcome={0: "unknown", 1: "ok", 2: "error"}[code],
                                 tool_category="other")
                events.append(validate(event))
    return events

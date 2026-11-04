"""Pure, stdlib-only next-step guidance after `sources check`.

No I/O, no command generation, no persistent state. Free-text errors and
summaries are never echoed; only exact known messages are recognised.
"""
import re

INPUT_SCHEMA = "monitoring.source-check.v1"
OUTPUT_SCHEMA = "monitoring.source-actions.v1"
MAX_INPUTS = 32
ERROR_MESSAGE = "Invalid source check"

_ALIAS = re.compile(r"[A-Za-z0-9_.:@-]{1,160}")
_FORMATS = frozenset({"normalized", "claude", "codex", "claude-results", "otlp-jsonl"})
_STATES = frozenset({"readable", "invalid", "unavailable"})
_TOP_KEYS = frozenset({"schema", "checks", "all_readable"})
_METADATA_KEYS = frozenset({"observed_at", "read_only", "scope", "note"})
_CHECK_KEYS = frozenset({"id", "format", "state", "error", "summary"})

_RETAIN = "Retain the existing ledger"
_KNOWN_ERRORS = {
    "Declared Claude fork history requires separate accounting qualification. "
    "This input was not imported; accepted history is retained.": (
        "claude_fork_history_unqualified",
        _RETAIN + ". Choose an independent supported source for this history.",
    ),
    "Declared Codex history metadata requires separate accounting qualification. "
    "This input was not imported; accepted history is retained.": (
        "codex_history_metadata_unqualified",
        _RETAIN + ". Choose an independent supported source for this history.",
    ),
    "Declared Codex parent history requires replay reconciliation. "
    "This input was not imported; accepted history is retained.": (
        "codex_parent_history_unreconciled",
        _RETAIN + ". Complete the selected Codex family export only for "
        "qualified legacy prefixes, otherwise choose an independent supported source.",
    ),
    "Overlapping native records in another selected source. "
    "Accepted history is retained.": (
        "overlapping_native_records",
        _RETAIN + ". Review the selection of inputs so overlapping records "
        "are covered by only one selected source.",
    ),
}
_UNKNOWN = (
    "unrecognized_error",
    "Inspect the input against the documented format for its declared format.",
)
_UNAVAILABLE = (
    "unavailable",
    "Verify that the file selected in this manifest exists and is readable.",
)
_READABLE = (
    "readable",
    "Include in a preview of the existing sources.",
)
_READY_ADVICE = "Preview the existing sources before a deliberate owner restart."


def _fail():
    """Reject malformed projections without echoing their contents."""
    raise ValueError(ERROR_MESSAGE)


def _validate(check):
    """Check the source-check envelope while excluding free-text metadata from output."""
    if not isinstance(check, dict) or not _TOP_KEYS <= set(check) or set(check) - (_TOP_KEYS | _METADATA_KEYS):
        _fail()
    if check["schema"] != INPUT_SCHEMA:
        _fail()
    checks = check["checks"]
    if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_INPUTS:
        _fail()
    if not isinstance(check["all_readable"], bool):
        _fail()
    seen = set()
    for c in checks:
        if not isinstance(c, dict) or set(c) != _CHECK_KEYS:
            _fail()
        alias, fmt, state = c["id"], c["format"], c["state"]
        if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
            _fail()
        if not isinstance(fmt, str) or fmt not in _FORMATS:
            _fail()
        if not isinstance(state, str) or state not in _STATES:
            _fail()
        if c["error"] is not None and not isinstance(c["error"], str):
            _fail()
        if c["summary"] is not None and not isinstance(c["summary"], dict):
            _fail()
        if state == "readable" and c["error"] is not None:
            _fail()
        if alias in seen:
            _fail()
        seen.add(alias)
    computed = all(c["state"] == "readable" for c in checks)
    if check["all_readable"] != computed:
        _fail()
    return checks, computed


def source_actions(check):
    """Return closed monitoring.source-actions.v1 guidance. Raises ValueError
    with a constant message on any malformed input."""
    checks, ready = _validate(check)
    items = []
    for c in checks:
        if c["state"] == "readable":
            code, action = _READABLE
        elif c["state"] == "unavailable":
            code, action = _UNAVAILABLE
        else:
            code, action = _KNOWN_ERRORS.get(c["error"], _UNKNOWN)
        items.append({"id": c["id"], "format": c["format"],
                      "state": c["state"], "code": code, "action": action})
    if ready:
        for item in items:
            item["action"] = _READY_ADVICE
    return {
        "schema": OUTPUT_SCHEMA,
        "scope": "explicit-selected-inputs",
        "complete_coverage": False,
        "items": items,
        "ready_for_preview": ready,
    }

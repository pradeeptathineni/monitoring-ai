"""Pure consistency check between an operator-supplied Codex replay-export
receipt and caller-computed facts about the selected output.

No file, network, or environment access. The caller supplies the observed
SHA-256, byte length, and per-alias event counts.

Limits: a match only binds the supplied receipt to the selected bytes. It does
not authenticate upstream data, show fork compatibility, or establish complete
account coverage. Costs, lifecycle, and compaction are not assessed.
"""

import re
from datetime import datetime

RECEIPT_SCHEMA = "monitoring.codex-replay-export.v1"
EVIDENCE_SCHEMA = "monitoring.receipt-evidence.v1"
MAX_BYTES = 33554432
MAX_COUNT = 50000
MAX_SOURCES = 32

_ERROR = "Invalid receipt evidence input"
_ALIAS = re.compile(r"[A-Za-z0-9_.:@-]{1,160}")
_SHA = re.compile(r"[0-9a-f]{64}")
_RECEIPT_KEYS = {
    "schema", "observed_at", "events", "bytes", "sha256", "sources",
    "complete_coverage", "coverage", "costs", "lifecycle", "compaction",
}
_SOURCE_KEYS = {
    "source", "copied_usage_records", "exported_usage_records",
    "ignored_response_records",
}
_FIXED = {
    "coverage": "selected-proven-cumulative-prefixes",
    "costs": "unknown",
    "lifecycle": "not-exported",
    "compaction": "unsupported",
}
_MESSAGES = {
    "matching": ("Observed digest, byte length, and per-source event counts "
                 "match the supplied receipt. This binds the receipt to the "
                 "selected bytes only. It does not authenticate the producer "
                 "or establish complete coverage."),
    "mismatch": ("Observed digest, byte length, or per-source event counts "
                 "differ from the supplied receipt."),
}


def _fail():
    """Reject malformed evidence without echoing supplied content."""
    raise ValueError(_ERROR) from None


def _int(value, hi):
    """Bound plain counts without accepting booleans."""
    if type(value) is not int or not 0 <= value <= hi:
        _fail()
    return value


def _alias(value):
    """Accept only source identifiers, never paths or free text."""
    if type(value) is not str or not _ALIAS.fullmatch(value):
        _fail()
    return value


def _sha(value):
    """Require the exact lowercase SHA-256 representation."""
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail()
    return value


def _timestamp(value):
    """Require a real timezone-qualified receipt timestamp."""
    if type(value) is not str or not value.isascii() or len(value) > 64 \
            or "T" not in value:
        _fail()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail()


def _validate_receipt(receipt):
    """Validate the bounded supported export receipt."""
    if type(receipt) is not dict or set(receipt) != _RECEIPT_KEYS:
        _fail()
    if receipt["schema"] != RECEIPT_SCHEMA:
        _fail()
    if receipt["complete_coverage"] is not False:
        _fail()
    for key, expected in _FIXED.items():
        if receipt[key] != expected or type(receipt[key]) is not str:
            _fail()
    _timestamp(receipt["observed_at"])
    events = _int(receipt["events"], MAX_COUNT)
    size = _int(receipt["bytes"], MAX_BYTES)
    digest = _sha(receipt["sha256"])
    sources = receipt["sources"]
    if type(sources) is not list or not 1 <= len(sources) <= MAX_SOURCES:
        _fail()
    exported = {}
    copied_total = ignored_total = 0
    for item in sources:
        if type(item) is not dict or set(item) != _SOURCE_KEYS:
            _fail()
        alias = _alias(item["source"])
        if alias in exported:
            _fail()
        copied = _int(item["copied_usage_records"], MAX_COUNT)
        exported[alias] = _int(item["exported_usage_records"], MAX_COUNT)
        ignored_total += _int(item["ignored_response_records"], MAX_COUNT)
        copied_total += copied
    if events != sum(exported.values()) or events + copied_total + ignored_total > MAX_COUNT:
        _fail()
    return digest, size, exported, copied_total, events


def _validate_observed_counts(counts):
    """Validate caller-computed counts independently of receipt claims."""
    if type(counts) is not dict or len(counts) > MAX_SOURCES:
        _fail()
    for alias, count in counts.items():
        _alias(alias)
        _int(count, MAX_COUNT)
    if sum(counts.values()) > MAX_COUNT:
        _fail()
    return counts


def inspect_receipt(receipt, observed_sha256, observed_bytes,
                    observed_source_counts):
    """Return closed, path-free consistency evidence.

    receipt: dict following monitoring.codex-replay-export.v1.
    observed_sha256: 64 lowercase hex characters computed by the caller.
    observed_bytes: integer byte length of the selected output.
    observed_source_counts: exact alias -> event count map. An alias may be
        absent only when the receipt exports zero events for it. An alias not
        in the receipt is a mismatch.

    Raises ValueError with a constant message for any malformed input. Valid
    inputs that disagree return consistency "mismatch" (copied_records None).
    """
    digest, size, exported, copied, events = _validate_receipt(receipt)
    obs_digest = _sha(observed_sha256)
    obs_size = _int(observed_bytes, MAX_BYTES)
    observed = _validate_observed_counts(observed_source_counts)

    counts_match = (
        set(observed) <= set(exported)
        and all(observed.get(a, 0) == n for a, n in exported.items())
    )
    matching = counts_match and obs_digest == digest and obs_size == size
    state = "matching" if matching else "mismatch"
    return {
        "schema": EVIDENCE_SCHEMA,
        "consistency": state,
        "scope": "operator-supplied-receipt-and-selected-output",
        "complete_coverage": False,
        "producer_authenticity": "unverified",
        "costs": "unknown",
        "lifecycle": "not-established",
        "compaction": "unsupported",
        "exported_records": events,
        "copied_records": copied if matching else None,
        "message": _MESSAGES[state],
    }


def inspect_export(receipt_path, output_path):
    """Read explicitly selected receipt/output bytes without changing a ledger."""
    import hashlib
    import json
    from .adapters import read_source, parse_jsonl, unique_keys, reject_constant
    from .events import validate

    raw_receipt = read_source(receipt_path)
    if len(raw_receipt) > 65536:
        _fail()
    receipt = json.loads(raw_receipt, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    content = read_source(output_path)
    if content and not content.endswith(b"\n"):
        _fail()
    counts = {}
    for row in parse_jsonl(content, strict=True):
        event = validate(row)
        if event["kind"] != "usage" or event["provider"] != "openai" or event["surface"] != "codex":
            _fail()
        counts[event["source"]] = counts.get(event["source"], 0) + 1
    return inspect_receipt(receipt, hashlib.sha256(content).hexdigest(), len(content), counts)

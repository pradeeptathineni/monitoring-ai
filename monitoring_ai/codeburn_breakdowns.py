"""Project per-day model and provider breakdowns from one CodeBurn v74 cache row.

The daily reader in ``codeburn_cache`` owns file, version and JSON validation,
daily totals and time fields. This module only projects the two breakdown maps
of a single already-parsed day row into bounded metadata rows. CodeBurn rollups
have an unknown billing basis, so these rows stay informational and never become
ledger events.

The model map and the provider map are independent marginals of the same day.
They are returned side by side and are never intersected, merged, or used to
infer each other. Nested provider models, categories, projects, savings and any
other native detail are dropped.
"""

import hashlib
import re

from .events import amount

MAX_ENTRIES = 128
MAX_KEY_CHARS = 1000
MAX_COUNT = 10**15

BREAKDOWN_ERROR = "CodeBurn daily breakdown could not be validated"
TOO_MANY_ERROR = "CodeBurn daily breakdown has too many entries"
COLLISION_ERROR = "CodeBurn daily breakdown has colliding identifiers"

# Native keys that already look like short identifiers are kept verbatim.
SAFE_ID = re.compile(r"[A-Za-z0-9_.:@-]{1,120}")
OPAQUE_PREFIX = "opaque-"

# (projected bucket, CodeBurn field). Buckets are copied exactly as reported;
# any overlap between them is unknown and is not corrected or guessed here.
TOKEN_FIELDS = (
    ("input", "inputTokens"),
    ("output", "outputTokens"),
    ("cache_read", "cacheReadTokens"),
    ("cache_write", "cacheWriteTokens"),
)


def project_breakdowns(day):
    """Return {"models": rows|None, "providers": rows|None} for one day row.

    None means the map is absent, so coverage is unknown. An empty map gives an
    empty list: no represented rows, which is not proof of complete coverage.
    Malformed input raises ValueError with a fixed message.
    """
    if not isinstance(day, dict):
        raise ValueError(BREAKDOWN_ERROR)
    return {
        "models": _project_map(day, "models", _model_row),
        "providers": _project_map(day, "providers", _provider_row),
    }


def _project_map(day, field, project_row):
    if field not in day:
        return None
    entries = day[field]
    if not isinstance(entries, dict):
        raise ValueError(BREAKDOWN_ERROR)
    if len(entries) > MAX_ENTRIES:
        raise ValueError(TOO_MANY_ERROR)
    rows = {}
    for key, entry in entries.items():
        row_id = _row_id(key, model=field == "models")
        if not isinstance(entry, dict):
            raise ValueError(BREAKDOWN_ERROR)
        # Two native keys mapping to one id would silently merge usage; refuse.
        if row_id in rows:
            raise ValueError(COLLISION_ERROR)
        rows[row_id] = {"id": row_id, **project_row(entry)}
    return [rows[row_id] for row_id in sorted(rows)]


def _model_row(entry):
    """ModelDayStats: calls, cost and all four token counts are required.

    CodeBurn does not track sessions per model, so sessions are always unknown.
    """
    return {
        "calls": _count(entry.get("calls")),
        "source_cost_usd": _cost(entry),
        "sessions": None,
        "tokens": {bucket: _count(entry.get(name)) for bucket, name in TOKEN_FIELDS},
    }


def _provider_row(entry):
    """ProviderDaySlice: calls and cost are required.

    Sessions and token counts may be missing on carried legacy records. Missing
    stays None (unknown), never zero; a present value must be a valid count.
    """
    return {
        "calls": _count(entry.get("calls")),
        "source_cost_usd": _cost(entry),
        "sessions": _optional_count(entry, "sessions"),
        "tokens": {bucket: _optional_count(entry, name) for bucket, name in TOKEN_FIELDS},
    }


def _row_id(key, *, model=False):
    """Keep short identifiers and readable model names without exposing paths."""
    if not isinstance(key, str) or not 0 < len(key) <= MAX_KEY_CHARS:
        raise ValueError(BREAKDOWN_ERROR)
    if SAFE_ID.fullmatch(key) and ".." not in key:
        return key
    try:
        encoded = key.encode("utf-8")
    except UnicodeEncodeError:
        # Lone surrogates have no UTF-8 form to hash; refuse rather than guess.
        raise ValueError(BREAKDOWN_ERROR) from None
    if model and ".." not in key and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.():@-]{0,119}", key):
        readable = re.sub(r"[ ()]+", "-", key).strip("-")[:90].rstrip("-")
        if readable:
            return readable + "-" + hashlib.sha256(encoded).hexdigest()[:8]
    return OPAQUE_PREFIX + hashlib.sha256(encoded).hexdigest()[:24]


def _count(value):
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise ValueError(BREAKDOWN_ERROR)
    return value


def _optional_count(entry, name):
    if name not in entry:
        return None
    return _count(entry[name])


def _cost(entry):
    if "cost" not in entry:
        raise ValueError(BREAKDOWN_ERROR)
    try:
        return amount(entry["cost"])
    except (TypeError, ValueError, ArithmeticError):
        raise ValueError(BREAKDOWN_ERROR) from None

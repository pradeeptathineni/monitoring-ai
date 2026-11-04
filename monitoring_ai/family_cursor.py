"""Pure follower cursor for one explicitly selected Codex family snapshot stream."""

import json

ERROR_MESSAGE = "Family snapshot does not preserve the accepted output history"
MAX_EVENTS = 50000
MAX_EVENT_BYTES = 1 << 20
MAX_DEPTH = 32


def _fail():
    """Raise the single content-free error."""
    raise ValueError(ERROR_MESSAGE) from None


def _check_json(value, depth=0):
    """Verify strict JSON-compatible data: str keys, finite numbers, list/dict only."""
    if depth > MAX_DEPTH:
        _fail()
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            _fail()
        return
    if isinstance(value, list):
        for item in value:
            _check_json(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail()
            _check_json(item, depth + 1)
        return
    _fail()


def _normalize(row):
    """Validate one event dict; return (identity, canonical JSON text)."""
    try:
        if type(row) is not dict:
            _fail()
        for name in ("source", "id", "timestamp"):
            if not isinstance(row.get(name), str):
                _fail()
        if not row["source"] or not row["id"]:
            _fail()
        _check_json(row)
        text = json.dumps(row, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)
        if len(text) > MAX_EVENT_BYTES:
            _fail()
        return (row["source"], row["id"]), text
    except Exception:
        _fail()


def _normalize_all(rows):
    """Normalize a bounded iterable of events into an ordered list; refuse duplicates."""
    out = []
    seen = set()
    try:
        for row in rows:
            if len(out) >= MAX_EVENTS:
                _fail()
            ident, text = _normalize(row)
            if ident in seen:
                _fail()
            seen.add(ident)
            out.append((ident, text))
    except Exception:
        _fail()
    return out


class FamilyCursor:
    """Tracks committed event identities and stages verified, append-only snapshots."""

    def __init__(self, existing=()):
        """Seed committed state from already-written events (duplicates refuse)."""
        self._committed = dict(_normalize_all(existing))
        self._pending = None

    @property
    def count(self):
        """Number of committed identities."""
        return len(self._committed)

    def stage(self, rows):
        """Verify a full snapshot against committed state; return copies of new rows.

        Any previous pending proposal is cleared first, even if this call fails.
        Committed state is never changed here.
        """
        self._pending = None
        items = _normalize_all(rows)
        present = {}
        for ident, text in items:
            present[ident] = text
        for ident, text in self._committed.items():
            if present.get(ident) != text:
                _fail()
        new = [(i, t) for i, t in items if i not in self._committed]
        if len(self._committed) + len(new) > MAX_EVENTS:
            _fail()
        self._pending = new
        return [json.loads(t) for _, t in new]

    def commit(self):
        """Promote the pending proposal after the caller durably wrote it."""
        if self._pending is None:
            _fail()
        self._committed.update(self._pending)
        self._pending = None

    def discard(self):
        """Drop only the pending proposal."""
        self._pending = None

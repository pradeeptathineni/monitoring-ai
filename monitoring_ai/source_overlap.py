"""Identify repeated native observations across separately selected files.

Native event IDs derive from provider message/counter identity and are stable
across source aliases. Normalized producer IDs have their own namespaces and
cannot be compared this way. This checks overlap, not complete source coverage.
"""

NATIVE_FORMATS = frozenset({"claude", "codex"})
MESSAGE = "Overlapping native records in another selected source. Accepted history is retained."


class NativeOverlapError(ValueError):
    """A path-free, content-free accounting rejection."""


def identity(event):
    return event["session"], event["kind"], event["id"]


def identities(rows):
    return {identity(row) for row in rows}

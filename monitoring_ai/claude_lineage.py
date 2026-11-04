"""Refuse explicit copied Claude fork histories before independent accounting."""

MESSAGE = (
    "Declared Claude fork history requires separate accounting qualification. "
    "This input was not imported; accepted history is retained."
)


class ClaudeForkHistoryError(ValueError):
    """A content-free refusal without retaining the supplied parent identity."""


def declared_fork(row):
    """Recognize only an explicit fork declaration, including malformed non-null values."""
    return row.get("fork_declared") is True or row.get("forkedFrom") is not None


def require_independent(rows):
    """Reject the whole input, including declarations on non-usage rows."""
    if any(declared_fork(row) for row in rows):
        raise ClaudeForkHistoryError(MESSAGE)

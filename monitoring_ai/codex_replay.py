"""Pure prefix reconciliation for selected Codex session families.

prefix_lengths() works out, for each session hash, how many cumulative usage
points at the start of that session were copied from its declared parent when
it was forked. The caller drops those points so copied usage is counted once.
A successful result is an exact cumulative-prefix match across the whole
family. It is not a billing figure, a live status or a cost. The integrator
handles native parsing, publication, the CLI and ingestion.
"""

import re
from datetime import datetime

__all__ = ["ERROR_MESSAGE", "MAX_SESSIONS", "MAX_USAGE_POINTS", "prefix_lengths"]

# Public limits and the single error message. The message never contains IDs,
# paths or any text taken from the input.
ERROR_MESSAGE = "Selected Codex histories do not prove a complete, unambiguous copied prefix"
MAX_SESSIONS = 32
MAX_USAGE_POINTS = 50000

_SESSION_KEYS = frozenset(("session", "parent", "fork_at", "usage"))
_POINT_KEYS = frozenset(("at", "total"))
# Canonical UTC with milliseconds. ASCII digits only. Fixed width, so string
# order matches time order once the calendar values are checked.
_TIME_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{3})Z", re.ASCII
)


class _Refuse(Exception):
    """Internal refusal signal, turned into the constant public ValueError."""


# ---- Field validators: strict types, no coercion, no guessing ----

def _check_time(value):
    """Return a canonical UTC timestamp string, or refuse."""
    if type(value) is not str:
        raise _Refuse
    match = _TIME_RE.fullmatch(value)
    if match is None:
        raise _Refuse
    y, mo, d, h, mi, s, ms = (int(g) for g in match.groups())
    try:
        datetime(y, mo, d, h, mi, s, ms * 1000)
    except ValueError:
        raise _Refuse from None
    return value


def _check_total(value):
    """Return (input, cache_read, output) as a tuple of plain non-negative ints, or refuse."""
    if type(value) not in (tuple, list) or len(value) != 3:
        raise _Refuse
    for item in value:
        if type(item) is not int or item < 0:
            raise _Refuse
    total = tuple(value)
    if total[1] > total[0]:  # cache read cannot exceed input that includes cache
        raise _Refuse
    return total


def _check_usage(usage, budget):
    """Check one session's ordered cumulative points and return (at, total) pairs."""
    if type(usage) not in (list, tuple) or len(usage) > budget:
        raise _Refuse
    points = []
    prev_at = prev_total = None
    for point in usage:
        if not isinstance(point, dict) or set(point) != _POINT_KEYS:
            raise _Refuse
        at = _check_time(point["at"])
        total = _check_total(point["total"])
        if prev_total is not None:
            if at < prev_at or total == prev_total:
                raise _Refuse
            if any(cur < old for cur, old in zip(total, prev_total)):
                raise _Refuse
        points.append((at, total))
        prev_at, prev_total = at, total
    return points


# ---- Family structure: records, parent links, cycles ----

def _validate(sessions):
    """Build hash -> (parent, fork_at, points), enforcing the size limits as it goes."""
    if type(sessions) not in (list, tuple) or len(sessions) > MAX_SESSIONS:
        raise _Refuse
    records = {}
    budget = MAX_USAGE_POINTS
    for item in sessions:
        if not isinstance(item, dict) or set(item) != _SESSION_KEYS:
            raise _Refuse
        name, parent, fork_at = item["session"], item["parent"], item["fork_at"]
        if type(name) is not str or not name or name in records:
            raise _Refuse
        if parent is None:
            if fork_at is not None:  # a fork point without a parent is meaningless
                raise _Refuse
        else:
            if type(parent) is not str or not parent or parent == name:
                raise _Refuse
            if fork_at is None:
                raise _Refuse
            fork_at = _check_time(fork_at)
        points = _check_usage(item["usage"], budget)
        budget -= len(points)
        records[name] = (parent, fork_at, points)
    return records


def _check_graph(records):
    """Refuse cycles and missing parents. Each walk is bounded by the session count."""
    limit = len(records)
    for name in records:
        parent, fork_at, _ = records[name]
        if parent in records and records[parent][1] is not None and fork_at <= records[parent][1]:
            raise _Refuse
        current, steps = name, 0
        while records[current][0] is not None:
            current = records[current][0]
            steps += 1
            if current not in records or steps > limit:
                raise _Refuse


# ---- Per-child exact prefix proof ----

def _child_prefix(parent_points, fork_at, child_points):
    """Return how many parent raw snapshots strictly before fork_at the child copied exactly."""
    eligible = 0
    for at, _ in parent_points:
        if at == fork_at:  # tie at the fork time: copied or not is ambiguous
            raise _Refuse
        if at > fork_at:  # later parent activity is never used
            break
        eligible += 1
    if eligible == 0 or len(child_points) < eligible:
        raise _Refuse
    # Copied timestamps are ignored because native forks may rewrite them.
    for i in range(eligible):
        if child_points[i][1] != parent_points[i][1]:
            raise _Refuse
    for at, _ in child_points[eligible:]:
        if at <= fork_at:
            raise _Refuse
    return eligible


def prefix_lengths(sessions):
    """Map each session hash to the number of copied leading usage points to omit."""
    try:
        records = _validate(sessions)
        _check_graph(records)
        result = {}
        for name in sorted(records):
            parent, fork_at, points = records[name]
            if parent is None:
                result[name] = 0
            else:
                # Match against the parent's raw points, including its own copied prefix.
                result[name] = _child_prefix(records[parent][2], fork_at, points)
        return result
    except _Refuse:
        raise ValueError(ERROR_MESSAGE) from None

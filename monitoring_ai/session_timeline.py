"""Bounded exact-source/session timeline over normalized monitoring events.

The result describes selected recorded evidence only. It does not compute or
emit cost, does not infer process/app/native-worker links, and does not claim
complete coverage of a source. Status segments are bounded windows of reported
status evidence, not measurements of continuous work or inference duration.
"""

from __future__ import annotations

import copy
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta, timezone

from .events import BUCKETS, timestamp
from .metrics import filters, now_iso, seconds, selected

SCHEMA = "monitoring.session-timeline.v1"
SCOPE = "exact-source-session-recorded-evidence"
MAX_EVENTS = 100_000
MAX_LIMIT = 500
MAX_LANES = 100
MAX_DEPTH = 7
MAX_SEGMENTS = MAX_LIMIT + MAX_LANES
FRESH_MS = 60_000

KINDS = frozenset({"status", "usage", "tool"})
NONTERMINAL = frozenset({"running", "waiting", "blocked"})
TERMINAL = frozenset({"completed", "failed", "cancelled"})
ENTITY_TYPES = frozenset({"session", "agent", "worker"})
OUTCOMES = frozenset({"ok", "error", "unknown"})

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _strict_int(value, name, low, high=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < low or (high is not None and value > high):
        bound = f"{low}..{high}" if high is not None else f">= {low}"
        raise ValueError(f"{name} must be {bound}")
    return value


def _ms(iso):
    """Integer epoch milliseconds for a canonical ISO timestamp."""
    return int(round(seconds(iso) * 1000))


def _iso(ms):
    dt = _EPOCH + timedelta(milliseconds=ms)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _state(value):
    if value in NONTERMINAL or value in TERMINAL or value == "unknown":
        return value
    return "unknown"


def _tokens(value):
    """Keep canonical token buckets intact and refuse malformed stored values."""
    if not isinstance(value, dict) or set(value) != set(BUCKETS):
        raise ValueError("Timeline usage requires normalized token buckets")
    for count in value.values():
        _strict_int(count, "tokens", 0, 10**12)
    return dict(value)


def _duration(value):
    """Expose only an explicitly reported normalized duration."""
    if value is None:
        return None
    return _strict_int(value, "duration_ms", 0, 10**12)


def _marker(event, entity, iso):
    """Whitelisted marker fields only; cost and other fields are never copied."""
    kind = event.get("kind")
    marker = {"id": event.get("id"), "entity": entity, "kind": kind, "timestamp": iso}
    if kind == "status":
        marker["state"] = _state(event.get("state"))
    elif kind == "usage":
        marker["tokens"] = _tokens(event.get("tokens"))
    elif kind == "tool":
        tool = event.get("tool")
        marker["tool"] = tool if isinstance(tool, str) else None
        outcome = event.get("outcome")
        marker["outcome"] = outcome if outcome in OUTCOMES else "unknown"
        category = event.get("tool_category")
        if isinstance(category, str) and category:
            marker["tool_category"] = category
    if kind in ("tool", "usage"):
        duration = _duration(event.get("duration_ms"))
        if duration is not None:
            # Reported metadata only; never used to derive a start time.
            marker["duration_ms"] = duration
    return marker


def _group_statuses(statuses):
    """Per entity: equal-time records coalesced into one group with a state set."""
    grouped = {}
    for entity, items in statuses.items():
        items.sort(key=lambda item: (item[0], item[1]))
        groups = []
        for ms, rid, iso, state in items:
            if groups and groups[-1]["ms"] == ms:
                groups[-1]["states"].add(state)
                groups[-1]["ids"].append(rid)
            else:
                groups.append({"ms": ms, "iso": iso, "states": {state}, "ids": [rid]})
        grouped[entity] = (groups, [g["ms"] for g in groups])
    return grouped


def _latest_status(groups, now_ms):
    if not groups:
        return {"reported_state": None, "status_at": None, "state": "unknown",
                "stale": False, "conflicting": False}
    last = groups[-1]
    if len(last["states"]) > 1:
        # Equal-time different states: no lexical choice.
        return {"reported_state": None, "status_at": last["iso"], "state": "unknown",
                "stale": False, "conflicting": True}
    reported = next(iter(last["states"]))
    stale = False
    if reported in TERMINAL:
        state = reported  # recorded outcome; not task acceptance or closure
    elif reported in NONTERMINAL:
        stale = now_ms - last["ms"] > FRESH_MS
        state = "unknown" if stale else reported
    else:
        state = "unknown"
    return {"reported_state": reported, "status_at": last["iso"], "state": state,
            "stale": stale, "conflicting": False}


def _segment(entity, groups, idx, from_ms, until_ms, context):
    group = groups[idx]
    if len(group["states"]) != 1:
        return None
    state = next(iter(group["states"]))
    if state not in NONTERMINAL:
        return None
    start = max(group["ms"], from_ms)
    end = min(until_ms, group["ms"] + FRESH_MS)
    if idx + 1 < len(groups):
        end = min(end, groups[idx + 1]["ms"])
    if end <= start:
        return None
    return {"entity": entity, "state": state, "from": _iso(start), "until": _iso(end),
            "source_at": group["iso"], "context": context, "_sort": (start, entity, group["ms"])}


def _lane_order(info):
    """Parent-before-child preorder with alias tie breaks; cycles cut at min alias."""
    children = {}
    roots = []
    for entity, data in info.items():
        if data["parent_state"] == "in-view":
            children.setdefault(data["parent"], []).append(entity)
        else:
            roots.append(entity)
    order, depth, visited, cycle = [], {}, set(), set()

    def walk(start):
        stack = [(start, 0)]
        while stack:
            node, d = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            order.append(node)
            depth[node] = min(d, MAX_DEPTH)
            for child in sorted(children.get(node, ()), reverse=True):
                if child not in visited:
                    stack.append((child, d + 1))

    for root in sorted(roots):
        walk(root)
    # Anything left is on, or hangs below, an in-view parent cycle (<= 100 lanes).
    while len(visited) < len(info):
        start = min(e for e in info if e not in visited)
        path, pos, node = [], {}, start
        while node in info and node not in pos and node not in visited:
            pos[node] = len(path)
            path.append(node)
            node = info[node]["parent"]
        if node in pos:
            members = path[pos[node]:]
            cycle.update(members)
            walk(min(members))
        else:
            walk(start)
    return order, depth, cycle


def session_timeline(revision, events, query, *, limit=500, now=None):
    """Return a JSON-safe bounded timeline for one exact source/session."""
    _strict_int(revision, "revision", 0)
    _strict_int(limit, "limit", 1, MAX_LIMIT)
    if not isinstance(query, dict):
        raise TypeError("query must be a dict")
    if "state" in query:
        raise ValueError("state filter is not supported by session timeline")
    canonical = filters(copy.deepcopy(query))
    if not isinstance(canonical, dict):
        raise TypeError("filters() must return a dict")
    if canonical.get("state") not in (None, "", [], ()):
        raise ValueError("state filter is not supported by session timeline")
    source, session = canonical.get("source"), canonical.get("session")
    if not isinstance(source, str) or not source or not isinstance(session, str) or not session:
        raise ValueError("query must name an exact source and session")
    if isinstance(events, (str, bytes, dict)):
        raise TypeError("events must be an iterable of event dicts")

    observed_at = timestamp(now if now is not None else now_iso())
    now_ms = _ms(observed_at)
    until_iso = observed_at
    if canonical.get("until"):
        requested = timestamp(canonical["until"])
        if _ms(requested) < now_ms:
            until_iso = requested
    until_ms = _ms(until_iso)
    since_iso = timestamp(canonical["since"]) if canonical.get("since") else None
    since_ms = _ms(since_iso) if since_iso is not None else None
    if since_ms is not None and since_ms > until_ms:
        raise ValueError("since is later than until")

    # Metadata matching via the shared selector; time bounds are applied here.
    untimed = dict(canonical)
    for key in ("since", "until"):
        if key in untimed:
            untimed.pop(key)

    matching = []      # (ms, id, iso, entity, event)
    statuses = {}      # entity -> [(ms, id, iso, state)] exact source/session, <= until
    types, parents = {}, {}
    future = 0
    for count, event in enumerate(events, 1):
        if count > MAX_EVENTS:
            raise ValueError(f"more than {MAX_EVENTS} events supplied")
        if not isinstance(event, dict):
            raise TypeError("each event must be a dict")
        if event.get("source") != source or event.get("session") != session:
            continue
        kind = event.get("kind")
        if kind not in KINDS:
            continue
        entity = event.get("entity")
        if not isinstance(entity, str) or not entity:
            raise ValueError("event entity must be a non-empty alias")
        iso = timestamp(event.get("timestamp"))
        ms = _ms(iso)
        if ms > now_ms:
            if selected(event, canonical):
                future += 1
            continue
        if ms > until_ms:
            continue
        etype = event.get("entity_type")
        types.setdefault(entity, set()).add(etype if etype in ENTITY_TYPES else "unknown")
        parent = event.get("parent")
        parents.setdefault(entity, set()).add(parent if isinstance(parent, str) and parent else None)
        rid = str(event.get("id"))
        if kind == "status":
            statuses.setdefault(entity, []).append((ms, rid, iso, _state(event.get("state"))))
        if (since_ms is None or ms >= since_ms) and selected(event, untimed):
            matching.append((ms, rid, iso, entity, event))

    groups = _group_statuses(statuses)
    matching.sort(key=lambda r: (r[0], r[1]))
    lane_matching = Counter(r[3] for r in matching)
    retained = matching[-limit:]

    lanes_seen = set()
    for record in reversed(retained):  # newest first selects lanes
        if record[3] in lanes_seen:
            continue
        if len(lanes_seen) >= MAX_LANES:
            break
        lanes_seen.add(record[3])
    shown = [r for r in retained if r[3] in lanes_seen]
    shown_status = {(r[3], r[1]) for r in shown if r[4].get("kind") == "status"}

    range_from = since_iso if since_iso is not None else (shown[0][2] if shown else None)
    from_ms = _ms(range_from) if range_from is not None else None

    info = {}
    for entity in lanes_seen:
        tset, pset = types.get(entity, set()), parents.get(entity, set())
        conflicts = []
        if len(tset) == 1:
            entity_type = next(iter(tset))
        else:
            entity_type = "unknown"
            conflicts.append("entity_type")
        if len(pset) > 1:
            conflicts.append("parent")
            parent, parent_state = None, "ambiguous"
        else:
            parent = next(iter(pset), None)
            if parent is None:
                parent_state = "none"
            elif parent in lanes_seen:
                parent_state = "in-view"
            elif parent in types:
                parent_state = "outside-view"
            else:
                parent_state = "unobserved"
        info[entity] = {"parent": parent, "parent_state": parent_state,
                        "entity_type": entity_type, "conflicts": conflicts}
    order, depth, cycle = _lane_order(info)

    lane_shown, lane_tools = Counter(), Counter()
    lane_tokens = {e: {b: 0 for b in BUCKETS} for e in lanes_seen}
    markers = []
    for _ms_, _rid, iso, entity, event in shown:
        marker = _marker(event, entity, iso)
        markers.append(marker)
        lane_shown[entity] += 1
        if marker["kind"] == "usage":
            for bucket in BUCKETS:
                lane_tokens[entity][bucket] += marker["tokens"][bucket]
        elif marker["kind"] == "tool":
            lane_tools[entity] += 1

    context_ids = set()
    segments = []
    if from_ms is not None:
        for entity in order:
            glist, gms = groups.get(entity, ([], []))
            idx = bisect_left(gms, from_ms) - 1
            if idx >= 0:
                seg = _segment(entity, glist, idx, from_ms, until_ms, True)
                if seg is not None:
                    segments.append(seg)
                    context_ids.update((entity, rid) for rid in glist[idx]["ids"])
        done = set()
        for ms, _rid, _iso_, entity, event in shown:
            if event.get("kind") != "status" or (entity, ms) in done:
                continue
            done.add((entity, ms))
            glist, gms = groups[entity]
            seg = _segment(entity, glist, bisect_left(gms, ms), from_ms, until_ms, False)
            if seg is not None:
                segments.append(seg)
    segments.sort(key=lambda s: s["_sort"])
    segments = segments[:MAX_SEGMENTS]
    for seg in segments:
        del seg["_sort"]

    lanes = []
    for entity in order:
        glist = groups.get(entity, ([], []))[0]
        if glist:
            context_ids.update((entity, rid) for rid in glist[-1]["ids"]
                               if (entity, rid) not in shown_status)
        data = info[entity]
        lanes.append({
            "entity": entity,
            "parent": data["parent"],
            "entity_type": data["entity_type"],
            "depth": depth[entity],
            "parent_state": data["parent_state"],
            "cycle": entity in cycle,
            "conflicts": data["conflicts"],  # diagnostic: explicit parent/type disagreement
            "shown_records": lane_shown[entity],
            "matching_records": lane_matching[entity],
            "tokens": lane_tokens[entity],
            "tool_records": lane_tools[entity],
            "latest_status": _latest_status(glist, now_ms),
        })

    omitted_records = len(matching) - len(shown)
    omitted_lanes = len(lane_matching) - len(lanes)
    return {
        "schema": SCHEMA,
        "revision": revision,
        "observed_at": observed_at,
        "filters": copy.deepcopy(canonical),
        "scope": SCOPE,
        "complete_coverage": False,
        "source": source,
        "session": session,
        "range": {"from": range_from, "until": until_iso},
        "coverage": {
            "matching_records": len(matching),
            "shown_records": len(shown),
            "omitted_records": omitted_records,
            "matching_lanes": len(lane_matching),
            "shown_lanes": len(lanes),
            "omitted_lanes": omitted_lanes,
            "context_status_records": len(context_ids),
            "future_records_excluded": future,
        },
        "truncated": omitted_records > 0 or omitted_lanes > 0,
        "lanes": lanes,
        "markers": markers,
        "segments": segments,
    }

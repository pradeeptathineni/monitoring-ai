"""Scenario tests for monitoring_ai.session_timeline using synthetic fixtures."""

import copy
import itertools
import json
import unittest

from monitoring_ai.session_timeline import session_timeline

# Mirrors fixtures.json (synthetic.timeline-fixtures.v1).
FIXTURE = {
    "now": "2026-11-04T01:00:00.000Z",
    "base": {
        "schema": "monitoring.event.v1", "source": "synthetic-timeline",
        "session": "fixture-session", "provider": "example", "model": "fixture-model",
        "surface": "fixture", "project": "fixture-project", "parent": None,
        "entity_type": "session",
    },
    "records": [
        {"id": "start", "entity": "root", "kind": "status",
         "timestamp": "2026-11-04T00:55:00.000Z", "state": "running"},
        {"id": "child-start", "entity": "child", "parent": "root", "entity_type": "worker",
         "kind": "status", "timestamp": "2026-11-04T00:55:10.000Z", "state": "running"},
        {"id": "tool", "entity": "child", "parent": "root", "entity_type": "worker",
         "kind": "tool", "timestamp": "2026-11-04T00:55:20.000Z", "tool": "Read",
         "outcome": "ok", "tool_category": "core", "duration_ms": 4},
        {"id": "usage", "entity": "root", "kind": "usage",
         "timestamp": "2026-11-04T00:58:00.000Z",
         "tokens": {"input": 100, "cache_read": 200, "cache_write": 0, "output": 40}},
        {"id": "end", "entity": "root", "kind": "status",
         "timestamp": "2026-11-04T00:59:00.000Z", "state": "completed"},
    ],
}
NOW = FIXTURE["now"]
Q = {"source": "synthetic-timeline", "session": "fixture-session"}
ZERO = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}


def t(hms):
    return f"2026-11-04T{hms}.000Z"


def ev(**fields):
    event = dict(FIXTURE["base"])
    event.update(fields)
    return event


def baseline():
    return [ev(**r) for r in copy.deepcopy(FIXTURE["records"])]


def usage(id_, entity, ts, tokens=None, **extra):
    return ev(id=id_, entity=entity, kind="usage", timestamp=ts,
              tokens={**ZERO, **(tokens or {})}, **extra)


def run(events, query=None, **kw):
    return session_timeline(1, events, dict(Q) if query is None else query, now=NOW, **kw)


class SessionTimelineTests(unittest.TestCase):
    def test_fixture_bounded_segments_and_inputs_unchanged(self):
        events = baseline()
        events[3]["cost"] = {"usd": 0.5}  # recorded cost must not be copied
        query = dict(Q)
        before_events, before_query = copy.deepcopy(events), copy.deepcopy(query)
        out = run(events, query)
        self.assertEqual(events, before_events)
        self.assertEqual(query, before_query)
        text = json.dumps(out)
        self.assertNotIn("cost", text)

        self.assertEqual(out["range"], {"from": t("00:55:00"), "until": NOW})
        segs = {(s["entity"], s["state"], s["from"], s["until"], s["context"])
                for s in out["segments"]}
        self.assertEqual(segs, {
            ("root", "running", t("00:55:00"), t("00:56:00"), False),   # not 00:59
            ("child", "running", t("00:55:10"), t("00:56:10"), False),
        })
        self.assertEqual([l["entity"] for l in out["lanes"]], ["root", "child"])
        root, child = out["lanes"]
        self.assertEqual((child["depth"], child["parent_state"], child["entity_type"]),
                         (1, "in-view", "worker"))
        self.assertEqual(root["latest_status"]["state"], "completed")
        self.assertEqual(child["latest_status"],
                         {"reported_state": "running", "status_at": t("00:55:10"),
                          "state": "unknown", "stale": True, "conflicting": False})
        self.assertEqual(root["tokens"],
                         {"input": 100, "cache_read": 200, "cache_write": 0, "output": 40})
        self.assertEqual(child["tokens"], ZERO)
        self.assertEqual((child["tool_records"], root["tool_records"]), (1, 0))
        tool = next(m for m in out["markers"] if m["kind"] == "tool")
        self.assertEqual((tool["duration_ms"], tool["outcome"], tool["tool"]), (4, "ok", "Read"))
        self.assertEqual(out["coverage"], {
            "matching_records": 5, "shown_records": 5, "omitted_records": 0,
            "matching_lanes": 2, "shown_lanes": 2, "omitted_lanes": 0,
            "context_status_records": 0, "future_records_excluded": 0})
        self.assertFalse(out["truncated"])
        self.assertFalse(out["complete_coverage"])

    def test_same_time_states_conflict_and_equal_states_coalesce(self):
        events = [
            ev(id="x1", entity="x", kind="status", timestamp=t("00:59:30"), state="running"),
            ev(id="x2", entity="x", kind="status", timestamp=t("00:59:30"), state="completed"),
            ev(id="y1", entity="y", kind="status", timestamp=t("00:59:40"), state="running"),
            ev(id="y2", entity="y", kind="status", timestamp=t("00:59:40"), state="running"),
        ]
        out = run(events)
        lanes = {l["entity"]: l for l in out["lanes"]}
        self.assertEqual(lanes["x"]["latest_status"],
                         {"reported_state": None, "status_at": t("00:59:30"),
                          "state": "unknown", "stale": False, "conflicting": True})
        self.assertEqual(lanes["y"]["latest_status"]["state"], "running")
        self.assertEqual(
            [(s["entity"], s["from"], s["until"]) for s in out["segments"]],
            [("y", t("00:59:40"), NOW)])
        self.assertEqual(len(out["markers"]), 4)

    def test_metadata_and_date_filters_keep_status_context_only(self):
        events = [
            ev(id="r1", entity="root", kind="status", timestamp=t("00:56:30"),
               state="running", model="m-a"),
            usage("u1", "root", t("00:57:10"), {"input": 100}, model="m-a"),
            usage("u2", "root", t("00:58:00"), {"input": 999}, model="m-b"),
            ev(id="e1", entity="root", kind="status", timestamp=t("00:59:00"),
               state="completed", model="m-b"),
        ]
        out = run(events, dict(Q, model="m-a", since=t("00:57:00")))
        self.assertEqual([m["id"] for m in out["markers"]], ["u1"])
        self.assertEqual(out["range"]["from"], t("00:57:00"))
        lane = out["lanes"][0]
        self.assertEqual(lane["latest_status"]["state"], "completed")  # hidden terminal used
        self.assertEqual(lane["tokens"]["input"], 100)  # hidden usage not copied
        self.assertEqual(out["segments"], [{
            "entity": "root", "state": "running", "from": t("00:57:00"),
            "until": t("00:57:30"), "source_at": t("00:56:30"), "context": True}])
        self.assertEqual(out["coverage"]["context_status_records"], 2)

        out = run(events, dict(Q, model="m-a"))
        self.assertEqual([m["id"] for m in out["markers"]], ["r1", "u1"])
        self.assertEqual(out["lanes"][0]["latest_status"]["state"], "completed")
        self.assertEqual([(s["from"], s["until"], s["context"]) for s in out["segments"]],
                         [(t("00:56:30"), t("00:57:30"), False)])
        self.assertEqual(out["coverage"]["context_status_records"], 1)

        # Historical until: fresh at until, but stale relative to now.
        out = run(events, dict(Q, until=t("00:56:45")))
        self.assertEqual(out["range"]["until"], t("00:56:45"))
        self.assertEqual([m["id"] for m in out["markers"]], ["r1"])
        status = out["lanes"][0]["latest_status"]
        self.assertEqual((status["reported_state"], status["state"], status["stale"]),
                         ("running", "unknown", True))
        self.assertEqual([(s["from"], s["until"]) for s in out["segments"]],
                         [(t("00:56:30"), t("00:56:45"))])
        self.assertEqual(out["coverage"]["future_records_excluded"], 0)

    def test_parent_states_cycles_limits_and_future(self):
        events = [
            usage("o1", "old", t("00:50:00"), entity_type="agent"),
            usage("r1", "root", t("00:51:00")),
            usage("a1", "a", t("00:51:10"), parent="root", entity_type="agent"),
            usage("b1", "b", t("00:51:20"), parent="a", entity_type="worker"),
            usage("g1", "orphan", t("00:51:30"), parent="ghost", entity_type="worker"),
            usage("p1", "p", t("00:51:40"), parent="q", entity_type="agent"),
            usage("q1", "q", t("00:51:50"), parent="p", entity_type="agent"),
            usage("m1", "m", t("00:52:00"), parent="root", entity_type="agent"),
            usage("m2", "m", t("00:52:10"), parent="a", entity_type="worker"),
            usage("x1", "outside", t("00:52:20"), parent="old", entity_type="worker"),
            usage("f1", "root", t("01:05:00")),
        ]
        out = run(events, limit=9)
        lanes = {l["entity"]: l for l in out["lanes"]}
        self.assertEqual([l["entity"] for l in out["lanes"]],
                         ["m", "orphan", "outside", "root", "a", "b", "p", "q"])
        self.assertEqual({e: l["depth"] for e, l in lanes.items()},
                         {"m": 0, "orphan": 0, "outside": 0, "root": 0,
                          "a": 1, "b": 2, "p": 0, "q": 1})
        self.assertEqual({e for e, l in lanes.items() if l["cycle"]}, {"p", "q"})
        self.assertEqual((lanes["m"]["parent"], lanes["m"]["parent_state"],
                          lanes["m"]["entity_type"], lanes["m"]["conflicts"]),
                         (None, "ambiguous", "unknown", ["entity_type", "parent"]))
        self.assertEqual((lanes["orphan"]["parent"], lanes["orphan"]["parent_state"]),
                         ("ghost", "unobserved"))
        self.assertEqual((lanes["outside"]["parent"], lanes["outside"]["parent_state"]),
                         ("old", "outside-view"))
        self.assertEqual(lanes["root"]["parent_state"], "none")
        self.assertNotIn("f1", [m["id"] for m in out["markers"]])
        cov = out["coverage"]
        self.assertEqual((cov["matching_records"], cov["shown_records"], cov["omitted_records"],
                          cov["matching_lanes"], cov["shown_lanes"], cov["omitted_lanes"],
                          cov["future_records_excluded"]), (10, 9, 1, 9, 8, 1, 1))
        self.assertTrue(out["truncated"])

        with self.subTest("lane cap keeps newest 100 entities"):
            many = [usage(f"u{i:03d}", f"e{i:03d}", t(f"00:40:{0:02d}").replace(
                "00:40:00", f"00:{40 + i // 60:02d}:{i % 60:02d}")) for i in range(105)]
            out = run(many)
            self.assertEqual({l["entity"] for l in out["lanes"]},
                             {f"e{i:03d}" for i in range(5, 105)})
            cov = out["coverage"]
            self.assertEqual((cov["shown_records"], cov["omitted_records"],
                              cov["matching_lanes"], cov["omitted_lanes"]), (100, 5, 105, 5))

    def test_missing_lifecycle_empty_scope_and_validation(self):
        out = run([usage("u", "root", t("00:59:00"), {"input": 3})])
        self.assertEqual(out["lanes"][0]["latest_status"],
                         {"reported_state": None, "status_at": None, "state": "unknown",
                          "stale": False, "conflicting": False})
        self.assertEqual(out["segments"], [])

        for events, query in (([], dict(Q)), (baseline(), dict(Q, session="other-session"))):
            out = run(events, query)
            self.assertEqual((out["lanes"], out["markers"], out["segments"]), ([], [], []))
            self.assertEqual(out["range"], {"from": None, "until": NOW})
            self.assertEqual((out["source"], out["session"]),
                             (query["source"], query["session"]))
            self.assertFalse(out["truncated"])

        bad_calls = [
            lambda: run([], dict(Q, state="running")),
            lambda: run([], {"source": Q["source"]}),
            lambda: run([], limit=True),
            lambda: run([], limit=0),
            lambda: run([], limit=501),
            lambda: run([usage("invalid", "root", t("00:59:00"), {"input": -1})]),
            lambda: session_timeline(-1, [], dict(Q), now=NOW),
            lambda: session_timeline(True, [], dict(Q), now=NOW),
            lambda: run([], dict(Q, since=t("00:59:00"), until=t("00:58:00"))),
            lambda: run(itertools.repeat({"source": "other"}, 100_001)),
        ]
        for call in bad_calls:
            with self.assertRaises((ValueError, TypeError)):
                call()


if __name__ == "__main__":
    unittest.main()

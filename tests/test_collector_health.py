import unittest
from monitoring_ai.collector_health import project_checkpoints

S = "monitoring.codex-family-follow.v1"


def ordinary(at, state="following", success=None, event=None, acc=1, error=None):
    return {"schema": S, "state": state, "observed_at": at, "last_success_at": success,
            "latest_event_at": event, "added": 1, "accepted_records": acc,
            "complete_coverage": False, "error": error}


def stopped(at, acc=3, **kw):
    row = {"schema": S, "state": "stopped", "last_source_state": "unavailable",
           "observed_at": at, "added_total": 3, "accepted_records": acc, "polls": 5,
           "full_reads": 2, "failed_polls": 1, "complete_coverage": False, "finite": True}
    row.update(kw)
    return row


class WorkflowTests(unittest.TestCase):
    def test_success_unavailable_stopped_keeps_separate_times(self):
        rows = [
            ordinary("2026-01-01T00:00:00Z", success="2026-01-01T00:00:00Z",
                     event="2025-12-31T23:59:00Z", acc=3),
            ordinary("2026-01-01T00:01:00Z", "unavailable", acc=3, error="/secret/path"),
        ]
        r = project_checkpoints(rows, "2026-01-01T00:01:30Z")
        self.assertEqual((r["state"], r["freshness"]), ("unavailable", "current"))
        self.assertEqual(r["reason"], "collection-source-unavailable")
        self.assertEqual(r["last_success_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(r["latest_event_at"], "2025-12-31T23:59:00+00:00")
        self.assertNotIn("secret", repr(r))
        rows.append(stopped("2026-01-01T00:02:00Z"))
        r = project_checkpoints(rows, "2026-01-01T00:02:10Z")
        self.assertEqual((r["state"], r["reason"]), ("stopped", "collector-stopped"))
        self.assertEqual(r["last_source_state"], "unavailable")
        self.assertEqual(r["last_checkpoint_at"], "2026-01-01T00:02:00+00:00")
        self.assertEqual(r["last_success_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(r["latest_event_at"], "2025-12-31T23:59:00+00:00")
        self.assertIs(r["complete_coverage"], False)

    def test_stale_future_newrun_empty(self):
        row = ordinary("2026-01-01T00:00:00Z", success="2026-01-01T00:00:00Z", acc=2)
        stale = project_checkpoints([row], "2026-01-01T00:02:06Z")
        self.assertEqual((stale["state"], stale["reported_state"], stale["freshness"]),
                         ("unknown", "following", "stale"))
        self.assertEqual(project_checkpoints([row], "2026-01-01T00:02:05Z")["state"], "following")
        fut = project_checkpoints([row], "2025-12-31T23:59:59Z")
        self.assertEqual((fut["state"], fut["freshness"], fut["reason"]),
                         ("unknown", "future", "checkpoint-in-future"))
        rows = [ordinary("2026-01-01T00:00:00Z", success="2026-01-01T00:00:00Z",
                         event="2026-01-01T00:00:00Z"),
                stopped("2026-01-01T00:01:00Z"),
                ordinary("2026-01-01T00:02:00Z", acc=1)]
        r = project_checkpoints(rows, "2026-01-01T00:02:10Z")
        self.assertEqual(r["state"], "following")
        self.assertIsNone(r["last_success_at"])
        self.assertIsNone(r["latest_event_at"])
        e = project_checkpoints([], "2026-01-01T00:00:00Z")
        self.assertEqual((e["state"], e["freshness"], e["accepted_records"], e["reported_state"]),
                         ("unknown", "empty", None, None))
        self.assertEqual(e["reason"], "no-checkpoints")

    def test_malformed_privacy_and_bounds(self):
        now = "2026-01-01T00:10:00Z"
        good = ordinary("2026-01-01T00:09:00Z")

        def bad(rows, **kw):
            with self.assertRaises(ValueError) as c:
                project_checkpoints(rows, kw.pop("now", now), **kw)
            self.assertIsNone(c.exception.__cause__)
            self.assertTrue(c.exception.__suppress_context__)
            self.assertEqual(str(c.exception), "malformed collector checkpoints")

        bad([dict(good, extra=1)])
        bad([dict(good, complete_coverage=True)])
        bad([dict(good, accepted_records=True)])
        bad([dict(good, accepted_records=50001)])
        bad([dict(good, error=5)])
        bad([dict(good, observed_at="2026-01-01T00:09:00")])  # naive
        bad([dict(good, state="paused")])
        bad([good, ordinary("2026-01-01T00:08:00Z")])  # time goes backwards
        bad([stopped("2026-01-01T00:09:00Z", full_reads=6)])
        bad([stopped("2026-01-01T00:09:00Z", failed_polls=6)])
        bad([stopped("2026-01-01T00:09:00Z", added_total=4, accepted_records=3)])
        bad([stopped("2026-01-01T00:09:00Z", finite=False)])
        bad([stopped("2026-01-01T00:09:00Z", last_source_state="done")])
        bad([good], max_age_seconds=0)
        bad([good], max_age_seconds=3601)
        bad([good] * 5001)
        bad([good], now="nonsense")
        bad("not rows")
        # boundaries accepted
        project_checkpoints([dict(good, accepted_records=50000, added=50000)], now)
        project_checkpoints([stopped("2026-01-01T00:09:00Z", polls=100000, full_reads=100000,
                                     failed_polls=100000, added_total=3)], now)
        bad([stopped("2026-01-01T00:09:00Z", polls=100001)])
        project_checkpoints([good] * 5000, now, max_age_seconds=3600)


if __name__ == "__main__":
    unittest.main()

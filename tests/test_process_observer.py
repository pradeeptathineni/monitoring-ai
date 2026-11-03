import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.process_observer import HISTORY_SAMPLES, ProcessObserver, parse_ps
from monitoring_ai.server import Observatory
from monitoring_ai.store import Store


FIRST = "  401   10  12.5  4096 Mon Nov  2 14:32:50 2026 /Applications/Claude.app/Contents/MacOS/Claude\n"
SECOND = "  402  401   0.2  2048 Mon Nov  2 14:33:50 2026 /private/CodexCLI.app/Contents/MacOS/codex\n"
UNRELATED = "  403    1   9.1  1000 Mon Nov  2 14:34:50 2026 /private/Secret.app/Contents/MacOS/node\n"


class ProcessObserverTests(unittest.TestCase):
    def test_projection_excludes_paths_unrelated_names_and_args(self):
        rows = parse_ps(FIRST + SECOND + UNRELATED)
        self.assertEqual([row["executable"] for row in rows], ["Claude", "codex"])
        self.assertEqual(rows[0]["parent_pid"], 10)
        self.assertEqual(rows[0]["rss_bytes"], 4096 * 1024)
        self.assertEqual(rows[0]["cpu_percent"], 12.5)
        self.assertIsNone(rows[0]["session"])
        encoded = json.dumps(rows)
        self.assertNotIn("/Applications", encoded)
        self.assertNotIn("/private", encoded)
        self.assertNotIn("Secret", encoded)

    def test_fresh_samples_and_disappearance_are_not_session_events(self):
        frames = iter([FIRST + SECOND, SECOND, SECOND.replace("0.2", "1.2"),
                       OSError("private path"), SECOND])
        def read():
            item = next(frames)
            if isinstance(item, Exception):
                raise item
            return item
        observer = ProcessObserver(read)
        first = observer.refresh()
        self.assertEqual(first["state"], "following")
        self.assertEqual(first["interval_seconds"], 5)
        self.assertEqual(len(first["processes"]), 2)
        self.assertEqual(first["history"][0]["count"], 2)
        self.assertEqual(first["history"][0]["cpu_percent"], 12.7)
        self.assertEqual(first["history"][0]["rss_bytes"], 6144 * 1024)
        self.assertNotIn("pid", json.dumps(first["history"]))
        second = observer.refresh()
        self.assertEqual([row["executable"] for row in second["processes"]], ["codex"])
        self.assertEqual(second["no_longer_observed"][0]["pid"], 401)
        self.assertNotEqual(first["sampled_at"], None)
        changed = observer.refresh()
        self.assertEqual(changed["changed_count"], 1)
        self.assertEqual(changed["no_longer_observed"], [])
        failed = observer.refresh()
        self.assertEqual(failed["state"], "unavailable")
        self.assertEqual(failed["processes"], [])
        self.assertEqual(failed["no_longer_observed"], [])
        self.assertEqual(len(failed["history"]), 3)
        resumed = observer.refresh()
        self.assertEqual(resumed["history"][-1]["segment"], 1)
        self.assertEqual(resumed["history"][-2]["segment"], 0)
        self.assertEqual(resumed["no_longer_observed"], [])
        copy = observer.snapshot()
        copy["history"][0]["count"] = 99
        self.assertEqual(observer.snapshot()["history"][0]["count"], 2)
        self.assertNotIn("private path", json.dumps(failed))
        self.assertRaises(ValueError, ProcessObserver, interval_seconds=1)
        self.assertRaises(ValueError, ProcessObserver, interval_seconds=61)
        self.assertEqual(ProcessObserver(interval_seconds=7).snapshot()["interval_seconds"], 7)

    def test_bounded_successful_history_keeps_latest_samples_only(self):
        observer = ProcessObserver(lambda: FIRST)
        for _ in range(HISTORY_SAMPLES + 1):
            observer.refresh()
        value = observer.snapshot()
        self.assertEqual(len(value["history"]), HISTORY_SAMPLES)
        self.assertTrue(value["history_truncated"])
        self.assertEqual({row["segment"] for row in value["history"]}, {0})

    def test_missed_sample_interval_starts_a_new_trend_segment(self):
        observer = ProcessObserver(lambda: FIRST, interval_seconds=5)
        observer.refresh()
        observer._history[-1]["sampled_at"] = "2026-11-02T00:00:00Z"
        later = observer.refresh()
        self.assertEqual(later["history"][-1]["segment"], 1)

    def test_snapshot_is_separate_from_event_accounting(self):
        with tempfile.TemporaryDirectory() as temp:
            observer = ProcessObserver(lambda: FIRST)
            observer.refresh()
            owner = Observatory(Store(Path(temp) / "events.sqlite"), Pricing(), Exchange(),
                                process_observer=observer)
            value = owner.read({})
            self.assertEqual(value["os_processes"]["processes"][0]["executable"], "Claude")
            self.assertEqual(value["revision"], 0)
            self.assertEqual(value["summary"]["sessions"], 0)


if __name__ == "__main__":
    unittest.main()

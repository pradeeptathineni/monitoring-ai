"""Keep replaceable cached aggregates separate from immutable event accounting."""

import tempfile
import threading
import unittest
from pathlib import Path

from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory
from monitoring_ai.store import Store


class CachedUsageObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "events.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def test_revised_totals_replace_cache_and_failure_retains_last_read_without_ledger_writes(self):
        current = {"schema": "monitoring.cached-usage.v1", "days": [{"source_cost_usd": "10"}]}

        def read():
            if current is None:
                raise ValueError("private source details")
            return current

        owner = Observatory(self.store, Pricing(), Exchange(), cached_usage_loader=read)
        owner.refresh_cached_usage()
        first = owner.read({})
        current = {"schema": "monitoring.cached-usage.v1", "days": [{"source_cost_usd": "7"}]}
        owner.refresh_cached_usage()
        revised = owner.read({"provider": "unmatched"})
        self.assertEqual(revised["cached_usage"]["days"][0]["source_cost_usd"], "7")
        self.assertEqual(first["cached_usage"]["days"][0]["source_cost_usd"], "10")
        self.assertEqual(revised["summary"]["total_tokens"], 0)
        self.assertIsNone(revised["summary"]["costs"]["estimated"]["amount"])
        current = None
        owner.refresh_cached_usage()
        retained = owner.read({})
        self.assertTrue(retained["cached_usage"]["retained"])
        self.assertEqual(retained["cached_usage_status"]["state"], "unavailable")
        self.assertEqual(retained["cached_usage"]["days"], revised["cached_usage"]["days"])
        self.assertNotIn("private source details", str(retained))
        self.assertEqual(self.store.read(), (0, []))

    def test_initial_failure_is_unknown_and_slow_cache_io_does_not_hold_observation_lock(self):
        started, release = threading.Event(), threading.Event()

        def read():
            started.set()
            release.wait(3)
            raise OSError("sensitive filename")

        owner = Observatory(self.store, Pricing(), Exchange(), cached_usage_loader=read)
        worker = threading.Thread(target=owner.refresh_cached_usage)
        worker.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertTrue(owner.lock.acquire(timeout=0.1))
            owner.lock.release()
            self.assertIsNone(owner.read({})["cached_usage"])
        finally:
            release.set()
            worker.join(3)
        failed = owner.read({})
        self.assertIsNone(failed["cached_usage"])
        self.assertEqual(failed["cached_usage_status"]["state"], "unavailable")
        self.assertIsNone(failed["cached_usage_status"]["last_success_at"])


if __name__ == "__main__":
    unittest.main()

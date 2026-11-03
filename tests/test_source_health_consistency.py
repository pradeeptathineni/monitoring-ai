"""Keep response source health consistent with its copied input inventory."""

from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory, handler
from monitoring_ai.source_observer import SourceObserver
from monitoring_ai.store import Store

NOW = "2026-11-02T12:00:00Z"


def event(ident, source):
    return {
        "schema": "monitoring.event.v1", "id": ident, "source": source,
        "session": "session-a", "entity": "session-a", "parent": None,
        "entity_type": "session", "timestamp": NOW, "kind": "usage",
        "provider": "synthetic", "model": "fixture", "surface": "runtime",
        "project": "demo", "tokens": {"input": 1},
    }


class FakeSource:
    def __init__(self, source_id, row):
        self.id = source_id
        self.format = "normalized"
        self.project = None
        self.rows = [row]
        self.failure = False

    def load(self, live=True):
        if self.failure:
            raise OSError("fixture failure")
        return list(self.rows)


class SourceHealthConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.store = Store(self.path)
        self.a = FakeSource("input-a", event("event-a", "event-source-a"))
        self.b = FakeSource("input-b", event("event-b", "event-source-b"))
        self.observer = SourceObserver([self.a, self.b])
        self.owner = Observatory(self.store, Pricing(), Exchange(), source_observer=self.observer)

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_report_and_health_derive_status_from_same_input_copy(self):
        self.b.failure = True
        self.owner.refresh()
        self.assertEqual(self.owner.source_status["state"], "unavailable")

        # Reproduce the gap: per-input refresh completes before aggregate cache update.
        self.b.failure = False
        self.observer.refresh(self.store)
        latest_success = max(row["last_success_at"] for row in self.observer.snapshot()["inputs"])

        snapshot = self.owner.read({})
        report = self.owner.report({})
        for response in (snapshot, report):
            self.assertEqual(response["inputs"]["healthy_inputs"], 2)
            self.assertEqual(response["source_status"]["state"], "following")
            self.assertFalse(response["source_status"]["degraded"])
            self.assertIsNone(response["source_status"]["error"])
            self.assertEqual(response["source_status"]["last_success_at"], latest_success)

        handler_type = handler(self.owner)
        health_handler = handler_type.__new__(handler_type)
        health_handler.server = SimpleNamespace(server_port=5274)
        health_handler.headers = {"Host": "127.0.0.1:5274"}
        health_handler.path = "/health"
        health_handler.wfile = BytesIO()
        health_handler.send_response = lambda status: None
        health_handler.send_header = lambda name, value: None
        health_handler.end_headers = lambda: None
        health_handler.do_GET()
        health = json.loads(health_handler.wfile.getvalue())
        self.assertEqual(health["inputs"]["healthy_inputs"], 2)
        self.assertEqual(health["source_status"]["state"], "following")
        self.assertEqual(health["source_status"]["last_success_at"], latest_success)

        # A later failure is reported against its current inventory and retains global success.
        self.b.failure = True
        self.observer.refresh(self.store)
        failed = self.owner.read({})
        self.assertEqual(failed["inputs"]["healthy_inputs"], 1)
        self.assertEqual(failed["source_status"]["state"], "unavailable")
        self.assertTrue(failed["source_status"]["degraded"])
        self.assertEqual(failed["source_status"]["last_success_at"], latest_success)


if __name__ == "__main__":
    unittest.main()

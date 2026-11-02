from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory, handler
from monitoring_ai.store import Store

BASE = datetime(2026, 10, 20, tzinfo=timezone.utc)


def event(ident, *, source="source-a", session="session-a", entity="target", at=BASE):
    timestamp = (at if isinstance(at, datetime) else BASE + timedelta(seconds=at))
    return {"schema": "monitoring.event.v1", "id": ident, "source": source,
            "session": session, "entity": entity, "parent": None,
            "entity_type": "session", "timestamp": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "kind": "usage", "provider": "test", "model": "test-model",
            "surface": "test", "project": "test-app", "tokens": {"input": 1}}


class EntityEventsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "events.sqlite")
        rows = [event(f"old-{i:03}", at=BASE + timedelta(seconds=i)) for i in range(125)]
        rows.extend([
            event("other-session", session="session-b", at=BASE + timedelta(seconds=126)),
            event("other-source", source="source-b", at=BASE + timedelta(seconds=127)),
            event("other-entity", entity="different-entity", at=BASE + timedelta(seconds=128)),
        ])
        self.store.ingest(rows)
        owner = Observatory(self.store, Pricing(), Exchange())
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(owner))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get(self, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        result = response.status, json.loads(body)
        connection.close()
        return result

    def test_scoped_entity_filter_finds_older_rows_and_pages_in_ledger_order(self):
        query = "source=source-a&session=session-a&entity=target"
        status, page = self.get(f"/api/v1/events?{query}&limit=20&after=100")
        self.assertEqual(status, 200)
        self.assertEqual(page["total"], 125)
        self.assertEqual([row["id"] for row in page["events"]], [f"old-{i:03}" for i in range(100, 120)])
        self.assertEqual(page["next_offset"], 120)
        self.assertEqual(page["events"][0]["entity"], "target")
        self.assertIn("tokens", page["events"][0])
        self.assertNotIn("matched_entity", page["events"][0])

        status, last = self.get(f"/api/v1/events?{query}&limit=20&after=120")
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in last["events"]], [f"old-{i:03}" for i in range(120, 125)])
        self.assertIsNone(last["next_offset"])

        previous_revision = page["revision"]
        self.store.ingest([event("new-later", at=BASE + timedelta(seconds=130))])
        status, changed = self.get(f"/api/v1/events?{query}&expected_revision={previous_revision}")
        self.assertEqual(status, 409)
        self.assertEqual(changed["revision"], previous_revision + 1)

    def test_entity_needs_both_scope_aliases_and_does_not_change_snapshot_or_report_filters(self):
        for query in (
            "session=session-a&entity=target",
            "source=source-a&entity=target",
            "source=source-a&session=session-a&entity=../private",
            "source=source-a&session=session-a&entity=target&entity=target",
            "source=source-a&session=session-a&entity=target&state=running",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.get(f"/api/v1/events?{query}")[0], 400)
        self.assertEqual(self.get("/api/v1/snapshot?entity=target")[0], 400)
        self.assertEqual(self.get("/api/v1/report?entity=target")[0], 400)

    def test_capabilities_distinguish_snapshot_filters_from_event_filters(self):
        status, value = self.get("/api/v1/capabilities")
        self.assertEqual(status, 200)
        self.assertNotIn("entity", value["filters"])
        self.assertEqual(value["event_filters"], [
            "provider", "model", "surface", "project", "source", "session", "since", "until", "entity"])


if __name__ == "__main__":
    unittest.main()

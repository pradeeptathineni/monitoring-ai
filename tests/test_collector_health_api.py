"""Opt-in collector evidence stays separate from accepted event accounting."""

import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from monitoring_ai.metrics import Pricing, Exchange
from monitoring_ai.server import Observatory, handler
from monitoring_ai.store import Store
from test_collector_health import ordinary


class CollectorReadTests(unittest.TestCase):
    def test_opt_in_identity_failure_retention_and_no_ledger_change(self):
        """A selected read can fail without erasing its dated prior evidence."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "checkpoints.jsonl"
            path.write_text(json.dumps(ordinary("2026-01-01T00:00:00Z", "unavailable", acc=3)) + "\n")
            store = Store(Path(folder) / "events.sqlite")
            owner = Observatory(store, Pricing(), Exchange(), collector_checkpoint_file=path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler(owner))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def read(query):
                """Use the same loopback route as the optional panel."""
                client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                client.request("GET", "/api/v1/collector-health" + query)
                response = client.getresponse()
                result = response.status, json.loads(response.read())
                client.close()
                return result

            try:
                self.assertEqual(read("")[0], 409)
                status, first = read("?expected_identity=" + owner.identity)
                self.assertEqual(status, 200)
                self.assertEqual(first["read_status"], "available")
                self.assertEqual(first["health"]["last_source_state"], "unavailable")
                self.assertEqual(first["health"]["freshness"], "stale")
                path.write_text("not-json\n")
                status, failed = read("?expected_identity=" + owner.identity)
                self.assertEqual(failed["read_status"], "unavailable")
                self.assertEqual(failed["health"], first["health"])
                self.assertNotIn(str(path), json.dumps(failed))
                self.assertEqual(store.read(), (0, []))
                owner.collector_checkpoint_file = None
                self.assertEqual(read("?expected_identity=" + owner.identity)[0], 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

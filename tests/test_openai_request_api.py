"""Selected request receipts never enter ledger accounting or provider execution."""

import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from monitoring_ai.metrics import Pricing, Exchange
from monitoring_ai.openai_observations import record_observation
from monitoring_ai.server import Observatory, handler
from monitoring_ai.store import Store


class RequestReadTests(unittest.TestCase):
    """Exercise owner pins, file failures and repeated reads through HTTP."""

    def test_selected_receipts_retain_dates_and_leave_usage_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'requests.jsonl'
            store = Store(Path(folder) / 'events.sqlite')
            owner = Observatory(store, Pricing(), Exchange(), openai_observation_file=path)
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(owner))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            # Read through the same loopback boundary as the browser
            def read(query=None, **headers):
                query = '?expected_identity=' + owner.identity if query is None else query
                client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
                client.request('GET', '/api/v1/openai-requests' + query, headers=headers)
                response = client.getresponse()
                result = response.status, json.loads(response.read())
                client.close()
                return result

            try:
                for query in ('', '?expected_identity=old', '?expected_identity=' + owner.identity + '&extra=1'):
                    self.assertEqual(read(query)[0], 409)
                self.assertEqual(read(Origin='https://example.invalid')[0], 403)
                status, missing = read()
                self.assertEqual((status, missing['read_status'], missing['book']), (200, 'unavailable', None))
                raw = dict(object='response', id='PRIVATE-RESPONSE-ID', status='in_progress', output='PRIVATE-CONTENT')
                aliases = dict(source='caller', project='fixture', session='group')
                record_observation(raw, path, observed_at='2026-01-01T00:00:00Z', **aliases)
                before = path.read_bytes()
                for _ in range(2):
                    status, first = read()
                    self.assertEqual(status, 200)
                    self.assertEqual(first['read_status'], 'available')
                    row, = first['book']['rows']
                    self.assertEqual(row['observed_at'], '2026-01-01T00:00:00.000Z')
                    self.assertEqual(row['freshness'], 'stale')
                    self.assertEqual(row['reported_status'], 'in_progress')
                self.assertEqual(path.read_bytes(), before)
                raw['status'] = 'completed'
                record_observation(raw, path, observed_at='2026-01-01T00:01:00Z', append=True, **aliases)
                _, completed = read()
                self.assertEqual(len(completed['book']['rows']), 1)
                self.assertEqual(completed['book']['rows'][0]['reported_status'], 'completed')
                for invalid in ('not-json\n', before.decode().rstrip('\n')):
                    path.write_text(invalid)
                    _, failed = read()
                    self.assertEqual(failed['read_status'], 'unavailable')
                    self.assertEqual(failed['book'], completed['book'])
                    for secret in (str(path), 'PRIVATE-RESPONSE-ID', 'PRIVATE-CONTENT', 'not-json'):
                        self.assertNotIn(secret, json.dumps(failed))
                path.write_text('')
                _, empty = read()
                self.assertEqual((empty['read_status'], empty['book']['rows']), ('available', []))
                self.assertEqual(store.read(), (0, []))
                owner.openai_observation_file = None
                self.assertEqual(read()[0], 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.adapters import load
from monitoring_ai.openai_receipt import capture_response, response_event
from monitoring_ai.store import Store


def response():
    return dict(object="response", id="private-response-id", status="completed", model="fixture-model",
                created_at=1793732400, completed_at=1793732402, instructions="private prompt",
                output=[{"text": "private answer"}], metadata={"secret": "private metadata"},
                usage=dict(input_tokens=100, input_tokens_details=dict(cached_tokens=20, cache_write_tokens=30),
                           output_tokens=40, output_tokens_details=dict(reasoning_tokens=25), total_tokens=140))


class OpenAiReceiptTests(unittest.TestCase):
    def test_disjoint_usage_drops_content_and_replays_as_one_request(self):
        raw = response()
        row = response_event(raw, source="api-fixture", project="project", session="application-group")
        self.assertEqual(row["tokens"], dict(input=50, cache_read=20, cache_write=30, output=40))
        self.assertEqual(row["duration_ms"], 2000)
        self.assertEqual(row["timestamp"], "2026-11-03T19:00:02.000Z")
        self.assertNotIn("cost", row)
        self.assertNotIn("state", row)
        for private in ("private-response-id", "private prompt", "private answer", "private metadata"):
            self.assertNotIn(private, json.dumps(row))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "raw.json"; output = root / "metadata.jsonl"
            source.write_text(json.dumps(raw))
            capture_response(source, output, source="api-fixture", project="project", session="application-group")
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            store = Store(root / "ledger.sqlite")
            rows = load(output, "normalized")
            self.assertEqual(store.ingest(rows)["added"], 1)
            self.assertEqual(store.ingest(rows)["added"], 0)
            with self.assertRaises(FileExistsError):
                capture_response(source, output, source="api-fixture", project="project", session="application-group")

    def test_missing_time_incomplete_or_ambiguous_usage_refuses(self):
        cases = [{**response(), "status": "incomplete"}, {**response(), "completed_at": None}]
        bad = response(); bad["usage"]["input_tokens_details"]["cached_tokens"] = 99; cases.append(bad)
        bad = response(); del bad["usage"]["input_tokens_details"]["cache_write_tokens"]; cases.append(bad)
        bad = response(); bad["usage"]["total_tokens"] = 999; cases.append(bad)
        for raw in cases:
            with self.assertRaises(ValueError):
                response_event(raw, source="fixture", project="project", session="group")

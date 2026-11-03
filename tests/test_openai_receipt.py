import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.adapters import load
from monitoring_ai.openai_receipt import capture_response, record_response, response_event
from monitoring_ai.source_setup import create_selection, check_selection
from monitoring_ai.store import Store


def response():
    return dict(object="response", id="private-response-id", status="completed", model="fixture-model",
                created_at=1793732400, completed_at=1793732402, instructions="private prompt",
                output=[{"text": "private answer"}], metadata={"secret": "private metadata"},
                usage=dict(input_tokens=100, input_tokens_details=dict(cached_tokens=20, cache_write_tokens=30),
                           output_tokens=40, output_tokens_details=dict(reasoning_tokens=25), total_tokens=140))


class OpenAiReceiptTests(unittest.TestCase):
    def test_application_records_received_responses_without_raw_files(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "metadata.jsonl"
            options = dict(source="api-fixture", project="project", session="application-group")
            first = response()
            self.assertEqual(record_response(first, output, **options)["events"], 1)
            second = response(); second.update(id="another-response", completed_at=1793732405)
            self.assertEqual(record_response(second, output, **options)["events"], 1)
            before = output.read_bytes()
            self.assertEqual(record_response(first, output, **options)["replayed"], 1)
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(len(load(output, "normalized")), 2)
            self.assertEqual(list(Path(folder).iterdir()), [output])
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            for private in ("private prompt", "private answer", "private metadata", "another-response"):
                self.assertNotIn(private, output.read_text())
            with self.assertRaises(ValueError):
                record_response(first, output, **{**options, "session": "changed-group"})
            self.assertEqual(output.read_bytes(), before)

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

    def test_explicit_append_replay_conflict_and_selected_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "raw.json"; output = root / "metadata.jsonl"
            options = dict(source="api-fixture", project="project", session="application-group")
            first = response(); source.write_text(json.dumps(first))
            self.assertEqual(capture_response(source, output, **options)["events"], 1)
            original = output.read_bytes()
            replay = capture_response(source, output, append=True, **options)
            self.assertEqual((replay["events"], replay["replayed"]), (0, 1))
            self.assertEqual(output.read_bytes(), original)
            for changed in ({**options, "source": "another-source"}, {**options, "session": "another-group"}):
                with self.assertRaisesRegex(ValueError, "conflicts"):
                    capture_response(source, output, append=True, **changed)
                self.assertEqual(output.read_bytes(), original)
            changed = response(); changed["usage"]["output_tokens"] += 1; changed["usage"]["total_tokens"] += 1
            source.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "conflicts"):
                capture_response(source, output, append=True, **options)
            self.assertEqual(output.read_bytes(), original)
            second = response(); second.update(id="second-private-id", completed_at=1793732403)
            source.write_text(json.dumps(second))
            self.assertEqual(capture_response(source, output, append=True, **options)["events"], 1)
            manifest = root / "selection.json"
            create_selection(manifest, file=output, id="api-input")
            self.assertEqual(check_selection(manifest)["checks"][0]["state"], "readable")
            store = Store(root / "ledger.sqlite")
            self.assertEqual(store.ingest(load(output, "normalized"))["added"], 2)
            self.assertEqual(store.ingest(load(output, "normalized"))["added"], 0)

    def test_append_refuses_missing_public_or_partial_file_without_changing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "raw.json"; output = root / "metadata.jsonl"
            source.write_text(json.dumps(response()))
            options = dict(source="fixture", project="project", session="group", append=True)
            with self.assertRaises(ValueError):
                capture_response(source, output, **options)
            self.assertFalse(output.exists())
            for content, mode in ((b"", 0o644), (b'{"partial":', 0o600)):
                output.write_bytes(content); output.chmod(mode)
                with self.assertRaises(ValueError):
                    capture_response(source, output, **options)
                self.assertEqual(output.read_bytes(), content)

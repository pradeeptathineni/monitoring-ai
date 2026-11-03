"""The terminal can own selected input reads without a web server."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from monitoring_ai.cli import terminal, watch_changes
from monitoring_ai.metrics import snapshot


def event(ident):
    return {"schema": "monitoring.event.v1", "id": ident, "source": "fixture-source",
            "session": "fixture-session", "entity": "fixture-session", "parent": None,
            "entity_type": "session", "timestamp": "2026-11-03T09:00:00Z",
            "kind": "usage", "provider": "example", "model": "example-model",
            "surface": "test", "project": "example-project", "tokens": {"input": 4}}


class TopSourcesTests(unittest.TestCase):
    def test_watch_change_summary_keeps_source_and_observation_times_distinct(self):
        first, second = event("first"), event("second")
        baseline = snapshot(1, [first], now="2026-11-03T09:00:01Z")
        self.assertEqual(watch_changes(None, baseline)[0], [])
        added = snapshot(2, [first, second], now="2026-11-03T09:00:03Z")
        rows, note = watch_changes(baseline, added)
        self.assertEqual(len(rows), 1)
        self.assertIn("Added usage", rows[0])
        self.assertIn("Source 2026-11-03T09:00:00Z · observed 2026-11-03T09:00:03Z", rows[0])
        self.assertIn("1 changed record", note)
        self.assertEqual(watch_changes(added, added)[0], [])
        revised = snapshot(3, [first, {**second, "tokens": {"input": 9}}], now="2026-11-03T09:00:05Z")
        rows, _ = watch_changes(added, revised)
        self.assertEqual(len(rows), 1)
        self.assertIn("Revised usage", rows[0])
        self.assertIn("9 recorded tokens", rows[0])
        self.assertEqual(watch_changes(added, {**added, "revision": 3})[0], [])

    def test_selected_input_recovery_and_replay_in_one_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first, later = root / "first.jsonl", root / "later.jsonl"
            first.write_text(json.dumps(event("first")) + "\n")
            manifest = root / "private-selection.json"
            manifest.write_text(json.dumps({"schema": "monitoring.sources.v1", "sources": [
                {"id": "first", "format": "normalized", "path": str(first)},
                {"id": "later", "format": "normalized", "path": str(later)},
            ]}))
            db = root / "ledger.sqlite"

            def top():
                result = subprocess.run([sys.executable, "-m", "monitoring_ai", "top",
                    "--sources", str(manifest), "--db", str(db), "--json"],
                    capture_output=True, text=True, check=True)
                self.assertNotIn(str(root), result.stdout)
                return json.loads(result.stdout)

            initial = top()
            self.assertEqual(initial["revision"], 1)
            self.assertEqual(initial["summary"]["total_tokens"], 4)
            self.assertEqual(initial["inputs"]["healthy_inputs"], 1)
            self.assertEqual(initial["inputs"]["unavailable_inputs"], 1)
            self.assertEqual(initial["source_coverage"]["inputs"][1]["event_freshness"], "unread")
            later.write_text(json.dumps(event("second")) + "\n")
            recovered = top()
            self.assertEqual(recovered["revision"], 2)
            self.assertEqual(recovered["summary"]["total_tokens"], 8)
            self.assertEqual(recovered["inputs"]["healthy_inputs"], 2)
            self.assertEqual(top()["revision"], 2)

    def test_terminal_separates_selected_source_and_process_evidence(self):
        value = snapshot(0, [], now="2026-11-03T09:00:00Z")
        value["inputs"] = {"healthy_inputs": 0, "inputs": [{"id": "selected"}]}
        value["source_coverage"] = {"inputs": [{"id": "selected", "format": "claude",
            "read_state": "unavailable", "records": None, "event_age_seconds": None,
            "event_freshness": "unread", "retained": False}]}
        value["os_processes"] = {"state": "following", "sampled_at": "2026-11-03T09:00:00Z",
            "processes": [{"executable": "claude", "pid": 123, "cpu_percent": 2.5,
                           "rss_bytes": 1048576}], "truncated": False}
        rendered = terminal(value)
        self.assertIn("Selected inputs: 0/1 readable", rendered)
        self.assertIn("selected (claude): unavailable · unknown records · unread", rendered)
        self.assertIn("OS sample: 1 matching executables", rendered)
        self.assertIn("no verified session or worker links", rendered)


if __name__ == "__main__":
    unittest.main()

"""Explicit result capture and ingestion using fictional CLI metadata."""

import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from monitoring_ai.adapters import load
from monitoring_ai.result_capture import capture_cli_result, publish_private_json
from monitoring_ai.source_readiness import preview_selection
from monitoring_ai.source_setup import check_selection, create_selection
from monitoring_ai.store import Store

AT = "2026-11-03T00:00:00Z"


def native_result():
    return {"type": "result", "session_id": "private-native-session", "result": "PRIVATE_ANSWER",
            "cwd": "/private/PRIVATE_PATH", "usage": {"input_tokens": 99999}, "total_cost_usd": 99999,
            "modelUsage": {"example-model": {"inputTokens": 12, "outputTokens": 7,
                "cacheReadInputTokens": 3, "cacheCreationInputTokens": 2,
                "costUSD": 0.0042, "provider": "firstParty", "thinkingTokens": 99999}}}


class ResultCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.raw = self.root / "raw.json"
        self.raw.write_text(json.dumps(native_result(), indent=2))

    def test_private_capture_checks_preview_import_and_replay_preserve_accounting(self):
        output = self.root / "captured.jsonl"
        captured = capture_cli_result(self.raw, output, captured_at=AT)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(json.loads(output.read_text()), captured)
        self.assertNotIn("PRIVATE_", output.read_text())
        self.assertNotIn("thinkingTokens", output.read_text())
        self.assertNotIn("total_cost_usd", output.read_text())
        manifest = self.root / "sources.json"
        create_selection(manifest, file=output, id="review-results", format="claude-results", project="example")
        checked = check_selection(manifest)
        self.assertTrue(checked["all_readable"])
        self.assertEqual(checked["checks"][0]["summary"]["records"]["status"], 0)
        preview = preview_selection(manifest)
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["inputs"][0]["proposed"]["added"], 1)
        events = load(output, "claude-results", "review-results", "example")
        self.assertEqual(events[0]["tokens"], dict(input=12, cache_read=3, cache_write=2, output=7))
        self.assertEqual(events[0]["cost"]["basis"], "estimated")
        self.assertNotIn("private-native-session", json.dumps(events))
        store = Store(self.root / "ledger.sqlite")
        self.assertEqual(store.ingest(events)["added"], 1)
        self.assertEqual(store.ingest(events)["replayed"], 1)

    def test_capture_refuses_existing_outputs_symlinks_and_ambiguous_json_without_damage(self):
        output = self.root / "existing.jsonl"
        output.write_text("original\n")
        with self.assertRaises(FileExistsError):
            capture_cli_result(self.raw, output, captured_at=AT)
        self.assertEqual(output.read_text(), "original\n")
        link = self.root / "link.jsonl"
        link.symlink_to(output)
        with self.assertRaises(FileExistsError):
            capture_cli_result(self.raw, link, captured_at=AT)
        self.assertTrue(link.is_symlink())
        native_link = self.root / "raw-link.json"
        native_link.symlink_to(self.raw)
        with self.assertRaises(ValueError):
            capture_cli_result(native_link, self.root / "new.jsonl", captured_at=AT)
        self.raw.write_text('{"type":"result","type":"result"}')
        with self.assertRaises(ValueError):
            capture_cli_result(self.raw, self.root / "new.jsonl", captured_at=AT)
        self.assertFalse((self.root / "new.jsonl").exists())
        self.assertEqual(list(self.root.glob(".monitoring-private-*")), [])

    def test_private_json_publication_refuses_nonfinite_or_nonobject_values(self):
        target = self.root / "quota.json"
        with self.assertRaises(ValueError):
            publish_private_json(target, {"used": float("nan")})
        with self.assertRaises(ValueError):
            publish_private_json(target, ["not-an-object"])
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob(".monitoring-private-*")), [])

    def test_cli_creates_no_ledger_and_rejects_missing_receipt_time_without_reading(self):
        output = self.root / "capture.jsonl"
        command = [sys.executable, "-m", "monitoring_ai", "sources", "capture-claude",
                   "--file", str(self.raw), "--output", str(output)]
        missing = subprocess.run(command, text=True, capture_output=True, timeout=5)
        self.assertEqual(missing.returncode, 2)
        self.assertFalse(output.exists())
        result = subprocess.run(command + ["--captured-at", AT], text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not live worker status", result.stdout)
        self.assertNotIn("PRIVATE_", result.stdout + result.stderr)
        self.assertEqual(list(self.root.glob("*.sqlite*")), [])
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_import_and_capture_reject_ambiguous_json_and_recapture_preserves_original_time(self):
        output = self.root / "capture.jsonl"
        capture_cli_result(self.raw, output, captured_at=AT)
        before = output.read_bytes()
        with self.assertRaises(ValueError):
            capture_cli_result(output, self.root / "recaptured.jsonl", captured_at="2026-11-03T01:00:00Z")
        self.assertEqual(output.read_bytes(), before)
        self.assertFalse((self.root / "recaptured.jsonl").exists())
        for content in [before.decode().replace('"session_id":', '"session_id":"duplicate", "session_id":'),
                        before.decode().replace('"inputTokens":12', '"inputTokens":NaN')]:
            output.write_text(content)
            with self.assertRaises(ValueError):
                load(output, "claude-results", "review-results", "example")


if __name__ == "__main__":
    unittest.main()

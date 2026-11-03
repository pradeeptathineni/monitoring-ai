import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.app_threads import dated_app_snapshot, read_app_threads, capture_app_threads
from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory
from monitoring_ai.store import Store


def inventory():
    return dict(schema="monitoring.app-threads.v1", captured_at="2026-11-03T19:00:00Z",
                source="codex-app-task-inventory", scope="recent-app-page", complete_coverage=False,
                threads=[dict(alias="task-fixture", kind="codex", status="active", host="local",
                              updated_at="2026-11-03T18:59:00Z")])


class AppThreadTests(unittest.TestCase):
    def test_capture_hashes_ids_refuses_titles_and_replaces_only_newer_private_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture.json"
            row = dict(id="private-native-id", kind="chatgpt", status="idle", host="unknown", updated_at="2026-11-03T19:00:00Z")
            def stream(value): return io.BytesIO(json.dumps(value).encode())
            capture_app_threads(stream([row]), path, "2026-11-03T19:00:01Z")
            self.assertNotIn(row["id"], path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            capture_app_threads(stream([{**row, "status": "active"}]), path, "2026-11-03T19:00:02Z", replace=True)
            self.assertEqual(read_app_threads(path)["threads"][0]["status"], "active")
            with self.assertRaises(ValueError):
                capture_app_threads(stream([row]), path, "2026-11-03T19:00:01Z", replace=True)
            with self.assertRaises(ValueError):
                capture_app_threads(stream([{**row, "title": "private"}]), path, "2026-11-03T19:00:03Z", replace=True)

    def test_strict_metadata_boundary_and_source_age(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture.json"
            path.write_text(json.dumps(inventory()))
            with patch("monitoring_ai.app_threads.now_iso", return_value="2026-11-03T19:00:10Z"):
                value = read_app_threads(path)
            self.assertEqual(value["freshness"], "recent")
            self.assertEqual(value["age_seconds"], 10)
            with patch("monitoring_ai.app_threads.now_iso", return_value="2026-11-03T19:01:00Z"):
                self.assertEqual(read_app_threads(path)["freshness"], "stale")
            leaked = inventory(); leaked["threads"][0]["title"] = "private title"
            path.write_text(json.dumps(leaked))
            with self.assertRaises(ValueError): read_app_threads(path)
            path.write_text('{"schema":"one","schema":"two"}')
            with self.assertRaises(ValueError): read_app_threads(path)

    def test_followed_capture_never_becomes_ledger_lifecycle_or_report_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture.json"
            path.write_text(json.dumps(inventory()))
            owner = Observatory(Store(Path(folder) / "ledger.sqlite"), Pricing(), Exchange(),
                                app_threads_loader=lambda: read_app_threads(path))
            value = owner.read({})
            self.assertEqual(value["revision"], 0)
            self.assertEqual(value["summary"]["active"], 0)
            self.assertEqual(value["app_threads"]["threads"][0]["status"], "active")
            self.assertNotIn("app_threads", owner.report({}))
            path.unlink()
            retained = owner.read({})
            self.assertEqual(retained["app_threads"]["read_state"], "unavailable")
            self.assertEqual(len(retained["app_threads"]["threads"]), 1)


class AppHistoryTests(unittest.TestCase):
    def test_missing_capture_recovers_and_failed_reads_age_retained_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture.json"
            loader = lambda: read_app_threads(path)
            initial = dated_app_snapshot(loader, None, "2026-11-03T19:00:01Z")
            self.assertEqual(initial["read_state"], "unavailable")
            self.assertEqual(initial["threads"], [])
            path.write_text(json.dumps(inventory()))
            recovered = dated_app_snapshot(loader, initial, "2026-11-03T19:00:02Z")
            self.assertEqual(recovered["read_state"], "following")
            self.assertEqual(recovered["freshness"], "recent")
            path.write_text('{"unsupported":"capture"}')
            failed = dated_app_snapshot(loader, recovered, "2026-11-03T19:01:00Z")
            self.assertEqual(failed["read_state"], "unavailable")
            self.assertEqual(failed["captured_at"], recovered["captured_at"])
            self.assertEqual(failed["threads"], recovered["threads"])
            self.assertEqual(failed["freshness"], "stale")
            self.assertEqual(failed["age_seconds"], 60)
            path.write_text(json.dumps(inventory()))
            reread = dated_app_snapshot(loader, failed, "2026-11-03T19:01:02Z")
            self.assertEqual(reread["read_state"], "following")
            self.assertEqual(reread["freshness"], "stale")

    def test_history_profile_keeps_alias_family_and_refuses_wrong_source_or_kind(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); recent = root / "recent.json"; history = root / "history.json"
            row = dict(id="fixture-native-task", kind="codex", status="active", host="local", updated_at="2026-11-03T19:00:00Z")
            def stream(rows): return io.BytesIO(json.dumps(rows).encode())
            capture_app_threads(stream([row]), recent, "2026-11-03T19:00:01Z")
            capture_app_threads(stream([row]), history, "2026-11-03T19:00:01Z", source="codex-app-task-history")
            a, b = read_app_threads(recent), read_app_threads(history)
            self.assertEqual(a["threads"][0]["alias"], b["threads"][0]["alias"])
            self.assertEqual(b["scope"], "codex-history-page")
            self.assertFalse(b["complete_coverage"])
            before = history.read_bytes()
            with self.assertRaises(ValueError):
                capture_app_threads(stream([row]), history, "2026-11-03T19:00:02Z", replace=True)
            with self.assertRaises(ValueError):
                capture_app_threads(stream([{**row,"kind":"chatgpt"}]), history, "2026-11-03T19:00:02Z", source="codex-app-task-history", replace=True)
            with self.assertRaises(ValueError):
                capture_app_threads(stream([{**row,"id":f"fixture-{i}"} for i in range(51)]), history, "2026-11-03T19:00:02Z", source="codex-app-task-history", replace=True)
            self.assertEqual(history.read_bytes(), before)

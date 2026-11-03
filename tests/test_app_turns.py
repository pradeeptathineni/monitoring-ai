import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.app_threads import capture_app_threads
from monitoring_ai.app_turns import capture_app_turns, read_app_turns
from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory
from monitoring_ai.store import Store


def task():
    return dict(id="fixture-thread", host="local", updated_at="2026-11-03T23:00:00Z",
                observed_at="2026-11-03T23:00:02Z", has_more=True,
                turns=[dict(id="fixture-turn", status="inProgress", started_at="2026-11-03T23:00:01Z",
                            completed_at=None, duration_ms=None, error_present=False)])


def stream(rows):
    return io.BytesIO(json.dumps(rows).encode())


class AppTurnTests(unittest.TestCase):
    def test_private_projection_preserves_times_and_authoritative_app_alias(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "turns.json"
            capture_app_turns(stream([task()]), path, "2026-11-03T23:00:03Z")
            value = read_app_turns(path)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertNotIn(b"fixture-thread", path.read_bytes())
            self.assertNotIn(b"fixture-turn", path.read_bytes())
            turn = value["tasks"][0]["turns"][0]
            self.assertEqual(turn["started_at"], "2026-11-03T23:00:01.000Z")
            self.assertEqual(turn["status"], "inProgress")
            self.assertIsNone(turn["completed_at"])
            capture_app_threads(stream([dict(id="fixture-thread", kind="codex", host="local", status="active",
                                             updated_at="2026-11-03T23:00:00Z")]), root / "app.json", "2026-11-03T23:00:03Z")
            self.assertEqual(json.loads((root / "app.json").read_bytes())["threads"][0]["alias"], value["tasks"][0]["alias"])

    def test_boundaries_and_replacement_refuse_without_changing_prior_export(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "turns.json"
            capture_app_turns(stream([task()]), path, "2026-11-03T23:00:03Z")
            original = path.read_bytes()
            bad = [dict(task(), title="private title"), dict(task(), host={}), dict(task(), turns=task()["turns"] * 5),
                   dict(task(), turns=[dict(task()["turns"][0], duration_ms=True)]), dict(task(), turns=[dict(task()["turns"][0], output="private output")])]
            for row in bad:
                with self.subTest(row_keys=list(row)):
                    with self.assertRaises(ValueError):capture_app_turns(stream([row]), path, "2026-11-03T23:00:04Z", replace=True)
                    self.assertEqual(path.read_bytes(), original)
            for rows, at in [([dict(task(), id="another-task")], "2026-11-03T23:00:04Z"),
                             ([task()], "2026-11-03T23:00:02Z"), ([task()] * 5, "2026-11-03T23:00:04Z")]:
                with self.assertRaises(ValueError):capture_app_turns(stream(rows), path, at, replace=True)
                self.assertEqual(path.read_bytes(), original)
            updated = dict(task(), turns=[dict(task()["turns"][0], status="new-source-state")])
            capture_app_turns(stream([updated]), path, "2026-11-03T23:00:04Z", replace=True)
            self.assertEqual(read_app_turns(path)["tasks"][0]["turns"][0]["status"], "unknown")

    def test_optional_owner_read_retains_stale_turns_outside_ledger_and_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "turns.json"
            owner = Observatory(Store(root / "ledger.sqlite"), Pricing(), Exchange(), app_turns_loader=lambda: read_app_turns(path))
            self.assertEqual(owner.read({})["codex_app_turns"]["read_state"], "unavailable")
            capture_app_turns(stream([task()]), path, "2026-11-03T23:00:03Z")
            with patch("monitoring_ai.metrics.now_iso", return_value="2026-11-03T23:00:04Z"):
                value = owner.read({})
            self.assertEqual(value["codex_app_turns"]["freshness"], "recent")
            self.assertEqual(value["summary"]["active"], 0)
            self.assertEqual(value["revision"], 0)
            self.assertNotIn("codex_app_turns", owner.report({}))
            path.unlink()
            with patch("monitoring_ai.metrics.now_iso", return_value="2026-11-03T23:01:04Z"):
                retained = owner.read({})["codex_app_turns"]
            self.assertEqual(retained["read_state"], "unavailable")
            self.assertEqual(retained["freshness"], "stale")
            self.assertEqual(retained["tasks"], value["codex_app_turns"]["tasks"])

    def test_explicit_cli_capture_and_top_use_the_optional_metadata_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "turns.json"
            subprocess.run([sys.executable, "-m", "monitoring_ai", "sources", "capture-codex-app-turns",
                            "--output", str(path), "--captured-at", "2026-11-03T23:00:03Z"],
                           input=json.dumps([task()]), text=True, capture_output=True, check=True)
            value = json.loads(subprocess.run([sys.executable, "-m", "monitoring_ai", "top", "--db", str(root / "ledger.sqlite"),
                                              "--app-turns", str(path), "--json"], text=True, capture_output=True, check=True).stdout)
            self.assertIn("codex_app_turns", value)
            self.assertEqual(value["summary"]["calls"], 0)
            self.assertEqual(value["summary"]["active"], 0)


if __name__ == "__main__":
    unittest.main()

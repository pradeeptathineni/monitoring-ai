import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from monitoring_ai.source_setup import create_selection, check_selection
from test_multi_sources import event


class SourceSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.file = self.root / "private-export.jsonl"
        self.manifest = self.root / "private-selection.json"

    def create(self, **changes):
        args = dict(file=self.file, id="selected-input")
        args.update(changes)
        return create_selection(self.manifest, **args)

    def test_create_validates_metadata_only_and_never_overwrites(self):
        self.file.write_text("private malformed content\n")
        self.create()
        self.assertEqual(stat.S_IMODE(self.manifest.stat().st_mode), 0o600)
        original = self.manifest.read_bytes()
        with self.assertRaises(FileExistsError):
            self.create(id="replacement")
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         ["private-export.jsonl", "private-selection.json"])
        self.assertFalse(check_selection(self.manifest)["all_readable"])

    def test_existing_symlink_and_invalid_native_selection_are_preserved(self):
        target = self.root / "retained.json"
        target.write_text("keep this")
        self.manifest.symlink_to(target)
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(target.read_text(), "keep this")
        self.manifest.unlink()
        with self.assertRaises(ValueError):
            self.create(format="codex")
        self.assertFalse(self.manifest.exists())
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["retained.json"])

    def test_read_only_check_preserves_files_and_counts_unique_evidence(self):
        rows = [event("first"), event("first")]
        self.file.write_text("".join(json.dumps(row) + "\n" for row in rows))
        self.create()
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        value = check_selection(self.manifest)
        self.assertTrue(value["all_readable"])
        self.assertTrue(value["read_only"])
        summary = value["checks"][0]["summary"]
        self.assertEqual(summary["records"]["total"], 1)
        self.assertEqual(summary["records"]["status"], 0)
        self.assertNotIn(str(self.root), json.dumps(value))
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()}, before)

    def test_missing_invalid_and_incomplete_sources_have_distinct_outcomes(self):
        self.create()
        missing = check_selection(self.manifest)["checks"][0]
        self.assertEqual(missing["state"], "unavailable")
        self.file.write_text(json.dumps(event("unfinished")))
        empty = check_selection(self.manifest)["checks"][0]
        self.assertEqual(empty["state"], "readable")
        self.assertEqual(empty["summary"]["records"]["total"], 0)
        self.file.write_text('{"SECRET":' + '[' * 2000 + '0' + ']' * 2000 + '}\n')
        invalid = check_selection(self.manifest)["checks"][0]
        self.assertEqual(invalid["state"], "invalid")
        self.assertNotIn("SECRET", json.dumps(invalid))
        self.assertNotIn(str(self.root), json.dumps(invalid))

    def test_actual_cli_create_and_check_never_create_a_ledger(self):
        project = str(Path(__file__).resolve().parents[1])
        environment = dict(os.environ, PYTHONPATH=project)
        def invoke(*args):
            return subprocess.run([sys.executable, "-m", "monitoring_ai", "sources", *args],
                                  cwd=self.root, env=environment, capture_output=True, text=True, timeout=5)
        created = invoke("create", "--output", str(self.manifest), "--file", str(self.file), "--id", "my-input")
        self.assertEqual(created.returncode, 0)
        missing = invoke("check", "--sources", str(self.manifest), "--json")
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(json.loads(missing.stdout)["checks"][0]["state"], "unavailable")
        self.file.write_text(json.dumps(event("ready")) + "\n")
        ready = invoke("check", "--sources", str(self.manifest))
        self.assertEqual(ready.returncode, 0)
        self.assertIn("No lifecycle events supplied", ready.stdout)
        self.assertNotIn(str(self.root), ready.stdout)
        self.assertFalse((self.root / ".monitoring-ai").exists())


if __name__ == "__main__":
    unittest.main()

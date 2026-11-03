import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from monitoring_ai.source_setup import add_selection, create_selection, check_selection, select_claude_stream
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

    def test_claude_stream_selection_plans_missing_outputs_then_checks_matching_capture(self):
        from monitoring_ai.claude_stream import capture_stream
        from test_claude_stream import INIT, SUCCESS, stream
        status = self.root / "status.jsonl"
        result = self.root / "result.jsonl"
        selected = select_claude_stream(self.manifest, status_file=status, result_file=result,
                                        source_id="owned-cli", project="example")
        self.assertEqual([row.format for row in selected], ["normalized", "claude-results"])
        self.assertEqual([row.id for row in selected], ["owned-cli-status", "owned-cli"])
        self.assertFalse(check_selection(self.manifest)["all_readable"])
        receipt = {**SUCCESS, "modelUsage": {"claude-example": {
            "inputTokens": 7, "cacheReadInputTokens": 2,
            "cacheCreationInputTokens": 1, "outputTokens": 3,
            "costUSD": 0.04, "provider": "firstParty"}}}
        use = {"type": "assistant", "session_id": INIT["session_id"],
               "message": {"model": "claude-example", "content": [
                   {"type": "tool_use", "id": "call-one", "name": "Read",
                    "input": {"file_path": "/private/example"}}]}}
        answer = {"type": "user", "session_id": INIT["session_id"],
                  "message": {"content": [
                      {"type": "tool_result", "tool_use_id": "call-one", "is_error": False,
                       "content": "private file text"}]}}
        capture_stream(stream(INIT, use, answer, receipt), status, source="owned-cli", project="example",
                       clock=lambda: "2026-11-03T09:00:00.000Z", result_output=result)
        checked = check_selection(self.manifest)
        self.assertTrue(checked["all_readable"])
        self.assertEqual([row["summary"]["records"]["total"] for row in checked["checks"]], [3, 1])
        self.assertEqual(checked["checks"][0]["summary"]["records"]["tool"], 1)
        self.assertNotIn(str(self.root), json.dumps(checked))
        self.assertEqual(stat.S_IMODE(self.manifest.stat().st_mode), 0o600)

    def test_claude_stream_selection_refuses_mismatched_existing_content_before_publication(self):
        from monitoring_ai.claude_stream import capture_stream
        from test_claude_stream import INIT, SUCCESS, stream
        status = self.root / "status.jsonl"
        result = self.root / "result.jsonl"
        receipt = {**SUCCESS, "modelUsage": {"claude-example": {
            "inputTokens": 7, "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0, "outputTokens": 3,
            "costUSD": 0.04, "provider": "firstParty"}}}
        capture_stream(stream(INIT, receipt), status, source="actual-alias", project="example",
                       clock=lambda: "2026-11-03T09:00:00.000Z", result_output=result)
        with self.assertRaisesRegex(ValueError, "does not match"):
            select_claude_stream(self.manifest, status_file=status, result_file=result,
                                 source_id="wrong-alias", project="example")
        self.assertFalse(self.manifest.exists())
        with self.assertRaisesRegex(ValueError, "must differ"):
            select_claude_stream(status, status_file=status, result_file=result,
                                 source_id="actual-alias", project="example")

    def test_planned_claude_selection_detects_later_alias_mismatch(self):
        from monitoring_ai.claude_stream import capture_stream
        from test_claude_stream import INIT, SUCCESS, stream
        status = self.root / "status.jsonl"
        result = self.root / "result.jsonl"
        select_claude_stream(self.manifest, status_file=status, result_file=result,
                             source_id="planned-alias", project="example")
        receipt = {**SUCCESS, "modelUsage": {"claude-example": {
            "inputTokens": 7, "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0, "outputTokens": 3,
            "costUSD": 0.04, "provider": "firstParty"}}}
        capture_stream(stream(INIT, receipt), status, source="different-alias", project="example",
                       clock=lambda: "2026-11-03T09:00:00.000Z", result_output=result)
        checked = check_selection(self.manifest)
        self.assertFalse(checked["all_readable"])
        self.assertEqual([row["state"] for row in checked["checks"]], ["invalid", "readable"])
        self.assertNotIn(str(self.root), json.dumps(checked))
    def test_add_creates_a_private_new_manifest_without_reading_or_replacing_sources(self):
        self.file.write_text("private malformed content\n")
        self.create()
        before = self.manifest.read_bytes()
        second = self.root / "second.jsonl"
        second.write_text("another private malformed source\n")
        output = self.root / "two-sources.json"
        entries = add_selection(self.manifest, output, file=second, id="second", format="claude", project="project")
        self.assertEqual([row.id for row in entries], ["selected-input", "second"])
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertFalse(check_selection(output)["all_readable"])
        retained = output.read_bytes()
        with self.assertRaises(FileExistsError):
            add_selection(self.manifest, output, file=second, id="different")
        self.assertEqual(output.read_bytes(), retained)
        with self.assertRaises(ValueError):
            add_selection(self.manifest, self.root / "duplicate.json", file=self.file, id="other")
        with self.assertRaises(ValueError):
            add_selection(self.manifest, self.root / "duplicate.json", file=second, id="selected-input")
        self.assertFalse((self.root / "duplicate.json").exists())

    def test_cli_add_then_check_uses_only_explicit_files(self):
        self.file.write_text(json.dumps(event("first")) + "\n")
        second = self.root / "second.jsonl"
        second.write_text(json.dumps(event("second")) + "\n")
        self.create()
        output = self.root / "two-sources.json"
        project = str(Path(__file__).resolve().parents[1])
        environment = dict(os.environ, PYTHONPATH=project)
        def invoke(*args):
            return subprocess.run([sys.executable, "-m", "monitoring_ai", "sources", *args],
                                  cwd=self.root, env=environment, capture_output=True, text=True, timeout=5)
        added = invoke("add", "--sources", str(self.manifest), "--output", str(output),
                       "--file", str(second), "--id", "second")
        self.assertEqual(added.returncode, 0)
        self.assertNotIn(str(self.root), added.stdout)
        checked = invoke("check", "--sources", str(output), "--json")
        self.assertEqual(checked.returncode, 0)
        self.assertEqual([row["id"] for row in json.loads(checked.stdout)["checks"]],
                         ["selected-input", "second"])
        self.assertEqual(len(json.loads(self.manifest.read_text())["sources"]), 1)

    def test_guided_setup_creates_private_selection_and_reports_coverage(self):
        self.file.write_text(json.dumps(event("first")) + "\n")
        project = str(Path(__file__).resolve().parents[1])
        result = subprocess.run([sys.executable, "-m", "monitoring_ai", "setup"],
                                input=f"{self.file}\nnormalized\nmy-input\n", cwd=self.root,
                                env=dict(os.environ, PYTHONPATH=project,
                                         XDG_CONFIG_HOME=str(self.root / "config")), capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = self.root / "config" / "monitoring-ai" / "sources.json"
        self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(saved.parent.stat().st_mode), 0o700)
        self.assertEqual(json.loads(saved.read_text())["sources"][0]["path"], str(self.file))
        self.assertIn("No lifecycle events supplied", result.stdout)
        self.assertIn("Missing evidence means coverage is unknown", result.stdout)
        self.assertFalse((self.root / ".monitoring-ai" / "events.sqlite").exists())
        original = saved.read_bytes()
        repeated = subprocess.run([sys.executable, "-m", "monitoring_ai", "setup"],
                                  input=f"{self.file}\nnormalized\nother\n", cwd=self.root,
                                  env=dict(os.environ, PYTHONPATH=project,
                                           XDG_CONFIG_HOME=str(self.root / "config")), capture_output=True,
                                  text=True, timeout=5)
        self.assertEqual(repeated.returncode, 2)
        self.assertEqual(saved.read_bytes(), original)

    def test_guided_setup_adds_to_existing_selection_without_replacing_it(self):
        self.file.write_text(json.dumps(event("first")) + "\n")
        self.create()
        before = self.manifest.read_bytes()
        second = self.root / "second.jsonl"
        second.write_text(json.dumps(event("second")) + "\n")
        project = str(Path(__file__).resolve().parents[1])
        result = subprocess.run([sys.executable, "-m", "monitoring_ai", "setup",
                                 "--from-selection", str(self.manifest)],
                                input=f"{second}\nnormalized\nsecond\n", cwd=self.root,
                                env=dict(os.environ, PYTHONPATH=project,
                                         XDG_CONFIG_HOME=str(self.root / "config")), capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = self.root / "config" / "monitoring-ai" / "sources-next.json"
        self.assertEqual(len(json.loads(saved.read_text())["sources"]), 2)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertNotIn(str(second), result.stdout)

    def test_guided_setup_quotes_private_output_in_suggested_commands(self):
        self.file.write_text(json.dumps(event("first")) + "\n")
        output = self.root / "private selection.json"
        project = str(Path(__file__).resolve().parents[1])
        result = subprocess.run([sys.executable, "-m", "monitoring_ai", "setup", "--output", str(output)],
                                input=f"{self.file}\nnormalized\nfirst\n", cwd=self.root,
                                env=dict(os.environ, PYTHONPATH=project), capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--sources '" + str(output) + "'", result.stdout)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()

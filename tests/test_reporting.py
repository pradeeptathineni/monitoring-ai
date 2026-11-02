import unittest
import json
from pathlib import Path
import tempfile

from monitoring_ai.adapters import load
from monitoring_ai.events import validate
from monitoring_ai.metrics import Exchange, Pricing, snapshot
from monitoring_ai.reporting import detailed_report

NOW = "2026-11-02T20:00:00.000Z"


def usage(identity, at, **extra):
    return validate(dict(schema="monitoring.event.v1", source="test", id=identity, session="s", entity="s",
        parent=None, entity_type="session", provider="example", model="model", surface="api", project="app",
        timestamp=at, kind="usage", tokens={"input": 10, "output": 2}, **extra))


class ReportingTests(unittest.TestCase):
    def report(self, rows, query=None):
        return detailed_report(3, rows, query or {}, Pricing(), Exchange(), NOW)

    def test_inclusive_bounds_comparison_is_adjacent_and_equal_elapsed_length(self):
        rows = [usage("before", "2026-11-02T19:57:59.999Z"), usage("prior", "2026-11-02T19:58:00Z"),
                usage("prior-end", "2026-11-02T19:58:59.999Z"), usage("start", "2026-11-02T19:59:00Z"),
                usage("end", "2026-11-02T19:59:59.999Z"), usage("after", NOW)]
        report = self.report(rows, {"since": "2026-11-02T19:59:00Z", "until": "2026-11-02T19:59:59.999Z"})
        self.assertEqual(report["summary"]["calls"], 2)
        self.assertEqual(report["comparisons"]["records"]["previous"], 2)
        self.assertEqual(report["previous_range"], {"from": "2026-11-02T19:58:00.000Z", "until": "2026-11-02T19:58:59.999Z"})

    def test_future_usage_is_retained_in_ledger_but_excluded_from_report(self):
        rows = [usage("current", NOW), usage("future", "2026-11-02T20:00:00.001Z")]
        report = self.report(rows, {"since": "2026-11-02", "until": "2026-11-02"})
        self.assertEqual(report["summary"]["calls"], 1)
        self.assertEqual(report["coverage"]["future_events_excluded"], 1)
        self.assertTrue(report["coverage"]["until_clamped_to_observation"])
        self.assertEqual(report["range"]["until"], NOW)

    def test_all_recorded_does_not_invent_previous_period_and_empty_cost_is_unknown(self):
        report = self.report([])
        self.assertIsNone(report["previous_range"])
        self.assertIsNone(report["summary"]["costs"]["estimated"]["amount"])
        self.assertIsNone(report["comparisons"]["records"]["previous"])

    def test_state_selection_uses_same_current_evidence_as_snapshot(self):
        row = usage("usage", "2026-11-02T19:00:00Z")
        status = dict(row, id="status", kind="status", state="completed", timestamp=NOW)
        status.pop("tokens")
        rows = [row, status]
        query = {"state": "completed", "since": "2026-11-02T18:00:00Z", "until": "2026-11-02T19:30:00Z"}
        report = self.report(rows, query)
        base = snapshot(3, rows, query, now=NOW)
        self.assertEqual(report["summary"]["calls"], base["summary"]["calls"])
        self.assertEqual(report["summary"]["observed_sessions"], base["summary"]["sessions"])

    def test_tool_metadata_is_explicit_and_argument_fields_still_refuse(self):
        row = usage("tool", NOW)
        row.pop("tokens")
        row.update(kind="tool", tool="search", tool_category="mcp", tool_server="docs", outcome="ok")
        self.assertEqual(validate(row)["tool_server"], "docs")
        for extra in ({"tool_category": "shell"}, {"arguments": "private"}, {"tool_server": "/private/path"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate(dict(row, **extra))

    def test_claude_file_to_report_retains_tool_metadata_without_arguments_or_inferred_time(self):
        rows = [dict(type="assistant", sessionId="native-private-id", timestamp="2026-11-02T19:00:00Z",
            message=dict(role="assistant", id="message-one", model="example-model", usage=dict(input_tokens=10, output_tokens=5),
                content=[dict(type="tool_use", id="call-one", name="Bash", input={"command": "private-command-argument"})])),
            dict(type="user", sessionId="native-private-id", timestamp=NOW,
                 message=dict(role="user", content=[dict(type="tool_result", tool_use_id="call-one", content="private-result")]))]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).resolve() / "authorized.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows))
            normalized = load(path, "claude", "chosen-source", "project-alias")
            report = self.report(normalized)
        self.assertEqual(report["summary"]["calls"], 1)
        self.assertEqual(report["tools"]["records"], 1)
        self.assertEqual(report["tools"]["shell_commands"][0]["key"], "Bash")
        self.assertIsNone(report["tools"]["shell_commands"][0]["total_duration_ms"])
        for secret in ("native-private-id", "private-command-argument", "private-result"):
            self.assertNotIn(secret, json.dumps(report))

    def test_explicit_directory_alias_works_but_file_symlink_refuses(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            actual = root / "actual"
            actual.mkdir()
            path = actual / "events.jsonl"
            path.write_text(json.dumps(usage("one", NOW)) + "\n")
            alias = root / "directory-alias"
            alias.symlink_to(actual, target_is_directory=True)
            self.assertEqual(len(load(alias / path.name)), 1)
            file_link = root / "file-link.jsonl"
            file_link.symlink_to(path)
            with self.assertRaises(ValueError):
                load(file_link)

    def test_invalid_native_model_shape_does_not_serialize_private_object_content(self):
        rows = [dict(type="assistant", sessionId="s", timestamp=NOW,
            message=dict(role="assistant", id="message", model={"private": "do-not-retain-this"}, usage={"input_tokens": 1}))]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).resolve() / "events.jsonl"
            path.write_text(json.dumps(rows[0]) + "\n")
            normalized = load(path, "claude", "source", "project")
        self.assertEqual(normalized[0]["model"], "unknown")
        self.assertNotIn("do-not-retain-this", json.dumps(normalized))


if __name__ == "__main__":
    unittest.main()

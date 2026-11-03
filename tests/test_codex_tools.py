"""Completed Codex items become metadata-only, source-limited tool observations."""

import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.adapters import codex, codex_row
from monitoring_ai.codex_follow import CodexFollower
from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.reports import build_report
from monitoring_ai.source_coverage import source_summary
from monitoring_ai.sources import Source
from monitoring_ai.store import Store


def row(kind, at, payload):
    return {"type": kind, "timestamp": at, "payload": payload}


def item(kind, ident, at, **fields):
    return row("event_msg", at, {"type": "item_completed", "item": {
        "type": kind, "id": ident, "command": "private shell command",
        "arguments": {"secret": "private input"}, "aggregated_output": "private result",
        "cwd": "/private/workspace", **fields}})


META = row("session_meta", "2026-11-03T03:00:00Z", {"id": "fictional-session"})
MODEL = row("turn_context", "2026-11-03T03:00:00Z", {"model": "fictional-model"})


class CodexToolTests(unittest.TestCase):
    def test_projected_completed_items_keep_only_safe_metadata_and_source_outcomes(self):
        native = [META, MODEL,
                  item("CommandExecution", "private-command-id", "2026-11-03T03:00:01Z",
                       status="completed", exit_code=0, duration={"secs": 2, "nanos": 500_000_000}),
                  item("CommandExecution", "failed-id", "2026-11-03T03:00:02Z",
                       status="failed", exit_code=1),
                  item("McpToolCall", "mcp-id", "2026-11-03T03:00:03Z",
                       status="completed", server="fictional-server", tool="search", duration={"secs": 1, "nanos": 0}),
                  item("FileChange", "file-id", "2026-11-03T03:00:04Z", status="completed"),
                  item("Reasoning", "reason-id", "2026-11-03T03:00:05Z"),
                  item({"unexpected": "shape"}, "invalid-type", "2026-11-03T03:00:06Z")]
        projected = [codex_row(value) for value in native]
        encoded = json.dumps(projected)
        for secret in ("private shell command", "private input", "private result", "/private/workspace",
                       "private-command-id"):
            self.assertNotIn(secret, encoded)
        events = codex(projected, "chosen", "fictional-project")
        self.assertEqual([(event["tool_category"], event["outcome"]) for event in events],
                         [("shell", "ok"), ("shell", "error"), ("mcp", "unknown"), ("core", "unknown")])
        self.assertEqual(events[0]["duration_ms"], 2500)
        self.assertEqual(events[2]["tool_server"], "fictional-server")
        self.assertEqual(events[2]["tool"], "search")
        self.assertTrue(all("cost" not in event for event in events))
        self.assertTrue(all("command" not in event and "arguments" not in event for event in events))
        report = build_report(events, pricing=Pricing(), fx=Exchange(), now="2026-11-03T03:01:00Z")
        self.assertEqual(report["tools"]["records"], 4)
        self.assertEqual(report["tools"]["errors"], 1)
        self.assertEqual(report["tools"]["unknown_outcomes"], 2)
        self.assertEqual(report["coverage"]["missing_tool_duration_records"], 2)
        self.assertEqual([(row["key"], row["records"]) for row in report["tools"]["categories"]],
                         [("shell", 2), ("core", 1), ("mcp", 1)])

    def test_tool_events_keep_source_order_and_exact_prefix_across_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selected.jsonl"
            store = Store(Path(directory) / "ledger.sqlite")
            follower = CodexFollower(path, "chosen", "fictional-project")
            first = item("CommandExecution", "command-1", "2026-11-03T03:00:01Z", status="completed", exit_code=0)
            second = item("FileChange", "change-1", "2026-11-03T03:00:02Z", status="completed")
            lines = [META, MODEL, first]
            path.write_text("".join(json.dumps(value) + "\n" for value in lines))
            def accept():
                result = store.ingest(follower(), accepted_rows=True)
                follower.commit()
                return result
            self.assertEqual(accept()["added"], 1)
            self.assertEqual(accept()["added"], 0)
            with path.open("a") as stream:
                stream.write(json.dumps(second) + "\n")
            self.assertEqual(accept()["added"], 1)
            self.assertEqual([row["tool"] for row in store.read()[1]], ["CommandExecution", "FileChange"])
            replacement = Path(directory) / "replacement"
            replacement.write_text("".join(json.dumps(value) + "\n" for value in [*lines, second]))
            replacement.replace(path)
            self.assertEqual(accept()["added"], 0)
            altered = item("CommandExecution", "command-1", "2026-11-03T03:00:01Z", status="failed", exit_code=1)
            replacement.write_text("".join(json.dumps(value) + "\n" for value in [META, MODEL, altered, second]))
            replacement.replace(path)
            with self.assertRaises(ValueError):
                follower()
            follower.discard()
            self.assertEqual(store.read()[0], 2)
            summary = source_summary(Source("chosen", "codex", path, "fictional-project"), store.read()[1],
                                     sampled_at="2026-11-03T03:01:00Z")
            self.assertEqual(summary["records"]["tool"], 2)
            self.assertEqual(summary["capabilities"]["tools"], "completed-items")


if __name__ == "__main__":
    unittest.main()

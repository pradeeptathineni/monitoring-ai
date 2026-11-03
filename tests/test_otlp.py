"""Bounded OTLP/JSON file projection using fictional trace batches."""

import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.adapters import load
from monitoring_ai.metrics import snapshot
from monitoring_ai.otlp import project_request
from monitoring_ai.source_observer import SourceObserver
from monitoring_ai.source_readiness import preview_selection
from monitoring_ai.source_setup import check_selection, create_selection
from monitoring_ai.sources import load_sources
from monitoring_ai.store import Store


TRACE = "1234567890abcdef1234567890abcdef"
NANO = "1793664000000000000"


def attr(key, value):
    return {"key": key, "value": {"intValue" if isinstance(value, int) else "stringValue": value}}


def span(identity, operation, *, attributes=(), status=0):
    return {"traceId": TRACE, "spanId": identity, "name": "PRIVATE PROMPT BODY",
            "startTimeUnixNano": NANO, "endTimeUnixNano": "1793664000500000000",
            "status": {"code": status},
            "attributes": [attr("gen_ai.operation.name", operation),
                           attr("gen_ai.provider.name", "openai"),
                           attr("gen_ai.request.model", "gpt-6"),
                           attr("gen_ai.input.messages", "PRIVATE PROMPT BODY"), *attributes]}


def batch(*spans):
    return {"resourceSpans": [{"resource": {"attributes": [attr("service.name", "PRIVATE SERVICE")]},
                               "scopeSpans": [{"spans": list(spans)}]}]}


class OtlpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.file = self.root / "export.jsonl"
        self.manifest = self.root / "selection.json"

    def fixture(self):
        return batch(
            span("0000000000000001", "invoke_agent", status=1),
            span("0000000000000002", "chat", attributes=[
                attr("gen_ai.usage.input_tokens", 100),
                attr("gen_ai.usage.cache_read.input_tokens", 20),
                attr("gen_ai.usage.cache_write.input_tokens", 10),
                attr("gen_ai.usage.output_tokens", 30)]),
            span("0000000000000003", "execute_tool", status=2,
                 attributes=[attr("gen_ai.tool.name", "file_search")]),
            span("0000000000000004", "invoke_workflow", attributes=[
                attr("gen_ai.usage.input_tokens", 100)]),
        )

    def test_project_disjoint_tokens_and_distinct_evidence(self):
        events = project_request(self.fixture(), "selected", "project")
        self.assertEqual([row["kind"] for row in events], ["status", "usage", "tool"])
        self.assertEqual(events[1]["tokens"], {"input": 70, "cache_read": 20,
                                                  "cache_write": 10, "output": 30})
        self.assertEqual(events[0]["state"], "completed")
        self.assertEqual(events[0]["entity_type"], "agent")
        self.assertEqual(events[0]["parent"], events[1]["session"])
        self.assertEqual(events[2]["outcome"], "error")
        self.assertEqual(events[2]["tool"], "file_search")
        self.assertEqual(events[1]["duration_ms"], 500)
        self.assertFalse(any("cost" in row for row in events))
        self.assertNotIn("PRIVATE", json.dumps(events))
        self.assertNotIn(TRACE, json.dumps(events))

    def test_selected_file_check_import_and_replay(self):
        self.file.write_text(json.dumps(self.fixture()) + "\n")
        create_selection(self.manifest, file=self.file, id="selected", format="otlp-jsonl", project="project")
        self.assertTrue(check_selection(self.manifest)["all_readable"])
        (source,) = load_sources(self.manifest)
        preview = preview_selection(self.manifest)
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["inputs"][0]["proposed"]["added"], 3)
        rows = source.load()
        store = Store(self.root / "ledger.sqlite")
        first = store.ingest(rows)
        second = store.ingest(source.load())
        self.assertEqual((first["added"], second["replayed"]), (3, 3))
        self.assertEqual(check_selection(self.manifest)["checks"][0]["summary"]["records"]["total"], 3)
        revision, accepted = store.read()
        view = snapshot(revision, accepted, now="2026-11-03T00:00:01Z")
        self.assertEqual(view["summary"]["total_tokens"], 130)
        self.assertEqual(view["summary"]["active"], 0)
        self.assertEqual(view["summary"]["unknown_cost_events"], 1)
        self.assertEqual(view["groups"]["surface"][0]["key"], "otlp")
        observer = SourceObserver((source,))
        status = observer.refresh(store)
        self.assertEqual(status["inputs"][0]["state"], "following")
        self.assertEqual(status["inputs"][0]["replayed"], 3)
        self.assertEqual(observer.coverage(now="2026-11-03T00:00:01Z")["inputs"][0]["records"]["tool"], 1)

    def test_partial_line_is_ignored_only_in_live_mode(self):
        self.file.write_text(json.dumps(self.fixture()))
        self.assertEqual(load(self.file, "otlp-jsonl", "selected", "project", live=True), [])
        self.assertEqual(len(load(self.file, "otlp-jsonl", "selected", "project")), 3)

    def test_refuses_ambiguous_counters_and_identity(self):
        broken = self.fixture()
        usage = broken["resourceSpans"][0]["scopeSpans"][0]["spans"][1]
        usage["attributes"].append(attr("gen_ai.usage.cache_read.input_tokens", 99))
        with self.assertRaisesRegex(ValueError, "repeats"):
            project_request(broken, "selected", "project")
        usage["attributes"].pop()
        usage["attributes"][5] = attr("gen_ai.usage.cache_read.input_tokens", 101)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            project_request(broken, "selected", "project")
        usage["attributes"][5] = attr("gen_ai.usage.cache_read.input_tokens", 20)
        usage["traceId"] = "PRIVATE"
        with self.assertRaisesRegex(ValueError, "trace ID"):
            project_request(broken, "selected", "project")
        agent = self.fixture()["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        agent["status"] = {"code": True}
        with self.assertRaisesRegex(ValueError, "status code"):
            project_request(batch(agent), "selected", "project")

    def test_no_agent_or_usage_claim_from_unrelated_spans(self):
        self.assertEqual(project_request(batch(span("0000000000000001", "invoke_workflow")),
                                         "selected", "project"), [])
        no_usage = batch(span("0000000000000001", "chat"))
        self.assertEqual(project_request(no_usage, "selected", "project"), [])


if __name__ == "__main__":
    unittest.main()

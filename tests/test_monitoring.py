from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import tempfile
import threading
import subprocess
import sys
import time
import unittest
from urllib.request import urlopen
from http.server import ThreadingHTTPServer

from monitoring_ai.adapters import load
from monitoring_ai.demo import demo_events
from monitoring_ai.events import validate
from monitoring_ai.metrics import Exchange, Pricing, snapshot
from monitoring_ai.server import Observatory, handler
from monitoring_ai.store import Store

NOW = "2026-11-02T20:00:00.000Z"


def event(**changes):
    return dict(schema="monitoring.event.v1", id="one", source="test", session="s1", entity="s1",
                parent=None, entity_type="session", timestamp=NOW, kind="usage", provider="openai",
                model="test-model", surface="codex", project="test-app",
                tokens={"input": 100, "output": 20}, **changes)


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root / "events.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        path = self.root / name
        path.write_text(value)
        return path

    def test_replay_and_conflicting_import_are_atomic(self):
        row = event()
        self.assertEqual(self.store.ingest([row])["added"], 1)
        self.assertEqual(self.store.ingest([row])["replayed"], 1)
        altered = dict(row, model="changed")
        with self.assertRaises(ValueError):
            self.store.ingest([dict(row, id="new"), altered])
        self.assertEqual(len(self.store.read()[1]), 1)

    def test_rejects_cycles_and_parent_mutations(self):
        row = event()
        self.store.ingest([row])
        with self.assertRaises(ValueError):
            self.store.ingest([dict(row, id="changed", parent="other")])
        with self.assertRaises(ValueError):
            self.store.ingest([dict(row, id="a", entity="a", parent="b", entity_type="agent"),
                               dict(row, id="b", entity="b", parent="a", entity_type="worker")])
        self.assertEqual(len(self.store.read()[1]), 1)

    def test_no_private_text_or_invalid_numbers_enter_normalized_store(self):
        for changes in ({"prompt": "private"}, {"project": "/private/path"},
                        {"tokens": {"input": True}}, {"timestamp": "2026-11-02"},
                        {"tokens": {"input": -1}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(dict(event(), **changes))

    def test_codex_cumulative_delta_cache_and_reasoning_are_not_double_counted(self):
        rows = [
            {"type": "session_meta", "payload": {"id": "private-native-id", "cwd": "/secret/path"}},
            {"type": "turn_context", "payload": {"model": "test-model"}},
        ]
        for total in [dict(input_tokens=100, cached_input_tokens=40, output_tokens=20, reasoning_output_tokens=10),
                      dict(input_tokens=100, cached_input_tokens=40, output_tokens=20, reasoning_output_tokens=10),
                      dict(input_tokens=160, cached_input_tokens=60, output_tokens=30, reasoning_output_tokens=12)]:
            rows.append(dict(type="event_msg", timestamp=NOW,
                payload=dict(type="token_count", info=dict(total_token_usage=total))))
        path = self.write("native.jsonl", "\n".join(json.dumps(r) for r in rows))
        events = load(path, "codex", "chosen-source", "public-alias")
        self.assertEqual(len(events), 2)
        self.store.ingest(events)
        value = snapshot(*self.store.read(), now=NOW)
        self.assertEqual(value["summary"]["tokens"], dict(input=100, cache_read=60, cache_write=0, output=30))
        self.assertEqual(value["summary"]["total_tokens"], 190)
        self.assertEqual(value["summary"]["active"], 0)
        self.assertNotIn("private-native-id", json.dumps(value))
        self.assertNotIn("/secret/path", json.dumps(value))
        self.assertEqual(self.store.ingest(load(path, "codex", "chosen-source", "public-alias"))["added"], 0)

    def test_codex_reset_refuses_instead_of_inventing_usage(self):
        rows = [{"type": "session_meta", "payload": {"id": "s"}}]
        for count in [100, 20]:
            rows.append(dict(type="event_msg", timestamp=NOW,
                payload=dict(type="token_count", info=dict(total_token_usage={"input_tokens": count}))))
        path = self.write("reset.jsonl", "\n".join(json.dumps(r) for r in rows))
        with self.assertRaises(ValueError):
            load(path, "codex", "source", "project")

    def test_claude_message_replay_discards_text_and_paths(self):
        row = dict(type="assistant", timestamp=NOW, sessionId="private-session", cwd="/secret/path",
                   message=dict(role="assistant", id="message1", model="demo-model", content="private prompt",
                                usage=dict(input_tokens=10, cache_read_input_tokens=30, cache_creation_input_tokens=5, output_tokens=7)))
        path = self.write("claude.jsonl", json.dumps(row) + "\n" + json.dumps(row))
        events = load(path, "claude", "chosen-source", "alias")
        self.assertEqual(len(events), 1)
        self.assertEqual(sum(events[0]["tokens"].values()), 52)
        self.assertNotIn("private", json.dumps(events))
        self.assertNotIn("secret", json.dumps(events))

    def test_claude_message_replay_keeps_first_timestamp_but_refuses_changed_usage(self):
        row = dict(type="assistant", timestamp=NOW, sessionId="session", cwd="/discarded",
                   message=dict(role="assistant", id="stable-message", model="demo-model", stop_reason="end_turn",
                                usage=dict(input_tokens=10, cache_read_input_tokens=2,
                                           cache_creation_input_tokens=1, output_tokens=7)))
        replay = json.loads(json.dumps(row))
        replay["timestamp"] = "2026-11-02T20:00:01Z"
        path = self.write("claude-timestamp-replay.jsonl", json.dumps(row) + "\n" + json.dumps(replay))

        events = load(path, "claude", "chosen-source", "alias")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["timestamp"], NOW)
        self.assertEqual(events[0]["tokens"], {"input": 10, "cache_read": 2, "cache_write": 1, "output": 7})

        for changes in ({"input_tokens": 11}, {"cache_read_input_tokens": 3}):
            changed = json.loads(json.dumps(replay))
            changed["message"]["usage"].update(changes)
            changed_path = self.write("claude-changed-usage.jsonl", json.dumps(row) + "\n" + json.dumps(changed))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                load(changed_path, "claude", "chosen-source", "alias")

        changed_model = json.loads(json.dumps(replay))
        changed_model["message"]["model"] = "other-model"
        changed_path = self.write("claude-changed-model.jsonl", json.dumps(row) + "\n" + json.dumps(changed_model))
        with self.assertRaises(ValueError):
            load(changed_path, "claude", "chosen-source", "alias")

    def test_unknown_cost_is_not_zero_and_billed_is_not_estimated(self):
        rows = [event(), dict(event(), id="two", cost=dict(amount="1.20", currency="USD", basis="billed", provenance="invoice")),
                dict(event(), id="three", cost=dict(amount="0.25", currency="EUR", basis="estimated", provenance="estimate"))]
        self.store.ingest(rows)
        value = snapshot(*self.store.read(), now=NOW)["summary"]
        self.assertEqual(value["costs"]["billed"]["amount"], "1.20")
        self.assertIsNone(value["costs"]["estimated"]["amount"])
        self.assertEqual(value["unknown_cost_events"], 1)
        self.assertEqual(value["unconverted_cost_events"], 1)

    def test_dated_prices_and_explicit_fx_preserve_provenance(self):
        rate = dict(provider="openai", model="test-model", currency="USD", provenance="operator-price",
                    **{"from": "2026-11-01T00:00:00Z", "until": "2026-12-01T00:00:00Z"},
                    input="10", cache_read="1", cache_write="12", output="30")
        pricing_path = self.write("prices.json", json.dumps(dict(schema="monitoring.pricing.v1", rates=[rate])))
        fx_path = self.write("fx.json", json.dumps(dict(schema="monitoring.fx.v1", base="USD", as_of=NOW,
                            provenance="test-only-rate", rates={"EUR": "0.8"})))
        self.store.ingest([event()])
        value = snapshot(*self.store.read(), pricing=Pricing(pricing_path), fx=Exchange("EUR", fx_path), now=NOW)
        self.assertEqual(value["summary"]["costs"]["estimated"]["amount"], "0.00128")
        self.assertEqual(value["fx"]["provenance"], "test-only-rate")
        with self.assertRaises(ValueError):
            Pricing(self.write("overlap.json", json.dumps(dict(schema="monitoring.pricing.v1", rates=[rate, rate]))))

    def test_stale_lifecycle_is_unknown_even_after_new_usage(self):
        status = dict(event(), id="status", kind="status", state="running", timestamp="2026-11-02T19:58:59Z")
        status.pop("tokens")
        self.store.ingest([status, event()])
        value = snapshot(*self.store.read(), now=NOW)
        self.assertEqual(value["entities"][0]["state"], "unknown")
        self.assertEqual(value["entities"][0]["reported_state"], "running")
        self.assertEqual(value["summary"]["active"], 0)

    def test_entity_evidence_and_usage_filters_are_separate(self):
        row = event()
        status = dict(row, id="status", kind="status", state="completed", model="different-model")
        status.pop("tokens")
        self.store.ingest([row, status])
        value = snapshot(*self.store.read(), query={"model": "test-model", "since": "2026-11-02", "until": "2026-11-02"}, now=NOW)
        self.assertEqual(value["summary"]["calls"], 1)
        self.assertEqual(value["entities"][0]["state"], "completed")
        self.assertEqual(value["groups"]["model"][0]["key"], "test-model")

    def test_live_partial_line_waits_and_invalid_complete_line_retains_ledger(self):
        path = self.write("live.jsonl", json.dumps(event()) + "\n{\"unfinished\"")
        owner = Observatory(self.store, Pricing(), Exchange(), lambda: load(path, live=True))
        owner.refresh()
        self.assertEqual(owner.source_status["state"], "following")
        self.assertEqual(self.store.read()[0], 1)
        path.write_text(path.read_text() + "\n")
        owner.refresh()
        self.assertEqual(owner.source_status["state"], "unavailable")
        self.assertEqual(len(self.store.read()[1]), 1)

    def test_api_has_bounded_pages_revision_guard_and_origin_boundary(self):
        self.store.ingest(demo_events(datetime(2026, 11, 2, 20, tzinfo=timezone.utc)))
        owner = Observatory(self.store, Pricing(), Exchange())
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(owner))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def get(path, headers=None):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request("GET", path, headers=headers or {})
                response = connection.getresponse()
                body = response.read()
                status = response.status
                connection.close()
                return status, json.loads(body)
            status, value = get("/api/v1/snapshot?provider=openai")
            self.assertEqual(status, 200)
            self.assertEqual(value["schema"], "monitoring.snapshot.v1")
            status, page = get("/api/v1/events?limit=2")
            self.assertEqual(status, 200)
            self.assertEqual(len(page["events"]), 2)
            self.assertEqual(page["next_offset"], 2)
            self.assertEqual(get("/api/v1/events?expected_revision=0")[0], 409)
            self.assertEqual(get("/api/v1/events?limit=501")[0], 400)
            self.assertEqual(get("/api/v1/snapshot?provider=openai&provider=local")[0], 400)
            self.assertEqual(get("/health", {"Host": "attacker.invalid"})[0], 403)
            self.assertEqual(get("/health", {"Origin": "https://attacker.invalid"})[0], 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_standalone_process_follows_appends_without_recounting_and_retains_history(self):
        source = self.write("stream.jsonl", json.dumps(event()) + "\n")
        process = subprocess.Popen([
            sys.executable, "-m", "monitoring_ai", "serve", "--file", str(source),
            "--db", str(self.root / "standalone.sqlite"), "--port", "0",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = process.stdout.readline().strip()
            self.assertIn("http://127.0.0.1:", line)
            origin = line.split("→ ")[1]

            def read():
                with urlopen(origin + "/api/v1/snapshot", timeout=3) as response:
                    return json.load(response)

            first = read()
            self.assertEqual(first["summary"]["calls"], 1)
            with source.open("a") as stream:
                stream.write(json.dumps(dict(event(), id="second")) + "\n")
            deadline = time.monotonic() + 7
            while time.monotonic() < deadline:
                second = read()
                if second["summary"]["calls"] == 2:
                    break
                time.sleep(.1)
            self.assertEqual(second["summary"]["calls"], 2)
            self.assertEqual(second["summary"]["total_tokens"], 240)
            source.unlink()
            deadline = time.monotonic() + 7
            while time.monotonic() < deadline:
                retained = read()
                if retained["source_status"]["state"] == "unavailable":
                    break
                time.sleep(.1)
            self.assertEqual(retained["source_status"]["state"], "unavailable")
            self.assertEqual(retained["summary"]["calls"], 2)
            self.assertEqual(retained["revision"], second["revision"])
        finally:
            process.terminate()
            process.communicate(timeout=5)

    def test_standalone_claude_source_accepts_new_messages_but_not_replays_or_partial_lines(self):
        def message(identity, at, tokens):
            return json.dumps(dict(type="assistant", timestamp=at, sessionId="fictional-session", cwd="/discarded",
                                   message=dict(role="assistant", id=identity, model="demo-model",
                                                content=[{"type": "text", "text": "discarded"}],
                                                usage=dict(input_tokens=tokens, output_tokens=1)))) + "\n"

        source = self.write("claude-stream.jsonl", message("message-one", NOW, 10))
        process = subprocess.Popen([
            sys.executable, "-m", "monitoring_ai", "serve", "--file", str(source), "--format", "claude",
            "--source-id", "fictional-source", "--project", "fictional-app",
            "--db", str(self.root / "claude.sqlite"), "--port", "0",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            origin = process.stdout.readline().strip().split("→ ")[1]

            def read(path="/api/v1/snapshot"):
                with urlopen(origin + path, timeout=3) as response:
                    return json.load(response)

            def until(calls):
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline:
                    value = read()
                    if value["summary"]["calls"] == calls:
                        break
                    time.sleep(.1)
                return value

            first = until(1)
            self.assertEqual(first["summary"]["total_tokens"], 11)
            [original] = read("/api/v1/events")["events"]
            third = message("message-three", "2026-11-02T20:00:03Z", 30)
            with source.open("a") as stream:
                stream.write(message("message-two", "2026-11-02T20:00:02Z", 20))
                stream.write(message("message-one", "2026-11-02T20:00:04Z", 10))
                stream.write(third[:len(third) // 2])
            second = until(2)
            self.assertEqual(second["source_status"]["state"], "following")
            self.assertEqual(second["summary"]["total_tokens"], 32)
            self.assertNotEqual(second["revision"], first["revision"])
            events = {row["id"]: row for row in read("/api/v1/events")["events"]}
            self.assertEqual(len(events), 2)
            self.assertEqual(events[original["id"]]["timestamp"], original["timestamp"])
            with source.open("a") as stream:
                stream.write(third[len(third) // 2:])
            self.assertEqual(until(3)["summary"]["total_tokens"], 63)
        finally:
            process.terminate()
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()

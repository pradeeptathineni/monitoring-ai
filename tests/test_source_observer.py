import json
from pathlib import Path
import tempfile
import threading
import unittest

from monitoring_ai.source_observer import SourceObserver
from monitoring_ai.store import Store

NOW = "2026-11-02T20:00:00Z"


def event(source, ident, *, tokens=1, project="synthetic"):
    return {
        "schema": "monitoring.event.v1", "id": ident, "source": source,
        "session": "session-1", "entity": "session-1", "parent": None,
        "entity_type": "session", "timestamp": NOW, "kind": "usage",
        "provider": "test", "model": "model", "surface": "test",
        "project": project, "tokens": {"input": tokens},
    }


class FakeSource:
    def __init__(self, source_id, rows=None, *, format="normalized", project="alias", path="/private/source.jsonl"):
        self.id = source_id
        self.format = format
        self.project = project
        self.path = Path(path)
        self.rows = list(rows or [])
        self.error = None
        self.live_arguments = []

    def load(self, *, live=True):
        self.live_arguments.append(live)
        if self.error:
            raise self.error
        return list(self.rows)


class BlockingSource(FakeSource):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started = threading.Event()
        self.release = threading.Event()

    def load(self, *, live=True):
        self.live_arguments.append(live)
        self.started.set()
        if not self.release.wait(3):
            raise RuntimeError("blocking test timed out")
        return list(self.rows)


class SourceObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "ledger.sqlite")

    def test_independent_sources_replay_and_frozen_source_list(self):
        first = FakeSource("source-a", [event("source-a", "a1")], format="normalized", project=None)
        second = FakeSource("source-b", [event("source-b", "b1")], format="codex")
        configured = [first, second]
        observer = SourceObserver(configured)
        configured.append(FakeSource("later", [event("later", "x")]))

        value = observer.refresh(self.store)
        self.assertEqual(value["schema"], "monitoring.inputs.v1")
        self.assertEqual(value["healthy_inputs"], 2)
        self.assertEqual(value["unavailable_inputs"], 0)
        self.assertEqual(len(value["inputs"]), 2)
        self.assertTrue(all(source.live_arguments == [True] for source in (first, second)))
        self.assertEqual({row["added"] for row in value["inputs"]}, {1})
        repeated = observer.refresh(self.store)
        self.assertEqual({row["added"] for row in repeated["inputs"]}, {0})
        self.assertEqual({row["replayed"] for row in repeated["inputs"]}, {1})

    def test_changed_identity_refuses_only_that_batch_while_other_source_advances(self):
        source_a = FakeSource("source-a", [event("source-a", "original")])
        source_b = FakeSource("source-b", [event("source-b", "first")])
        observer = SourceObserver([source_a, source_b])
        observer.refresh(self.store)

        source_a.rows = [event("source-a", "would-be-added"), event("source-a", "original", tokens=99)]
        source_b.rows = [event("source-b", "first"), event("source-b", "second")]
        value = observer.refresh(self.store)
        by_id = {row["id"]: row for row in value["inputs"]}
        self.assertEqual(by_id["source-a"]["state"], "unavailable")
        self.assertEqual(by_id["source-a"]["records_seen"], 2)
        self.assertEqual(by_id["source-a"]["added"], 0)
        self.assertEqual(by_id["source-a"]["error"], "Input data was rejected.")
        self.assertEqual(by_id["source-b"]["state"], "following")
        self.assertEqual(by_id["source-b"]["added"], 1)
        _, rows = self.store.read()
        stored = {(row["source"], row["id"]) for row in rows}
        self.assertNotIn(("source-a", "would-be-added"), stored)
        self.assertIn(("source-b", "second"), stored)

    def test_source_loss_retains_history_and_status_never_exposes_paths_or_errors(self):
        secret_path = "/private/very-secret-session-name.jsonl"
        source = FakeSource("alias", [event("alias", "kept")], path=secret_path)
        observer = SourceObserver([source])
        observer.refresh(self.store)
        source.error = OSError(f"missing {secret_path}")
        value = observer.refresh(self.store)
        encoded = json.dumps(value)
        self.assertEqual(value["unavailable_inputs"], 1)
        self.assertEqual(value["inputs"][0]["error"], "Input unavailable.")
        self.assertNotIn(secret_path, encoded)
        self.assertNotIn("very-secret-session-name", encoded)
        _, rows = self.store.read()
        self.assertEqual([(row["source"], row["id"]) for row in rows], [("alias", "kept")])

    def test_snapshot_does_not_wait_for_slow_source_load(self):
        source = BlockingSource("slow", [event("slow", "later")])
        observer = SourceObserver([source])
        refresh_thread = threading.Thread(target=observer.refresh, args=(self.store,))
        refresh_thread.start()
        self.assertTrue(source.started.wait(1))
        snapshot_ready = threading.Event()
        snapshot_result = []

        def read_snapshot():
            snapshot_result.append(observer.snapshot())
            snapshot_ready.set()

        snapshot_thread = threading.Thread(target=read_snapshot)
        snapshot_thread.start()
        try:
            self.assertTrue(snapshot_ready.wait(0.5), "status snapshot blocked behind source I/O")
            value = snapshot_result[0]
            self.assertEqual(value["unread_inputs"], 1)
            self.assertIsNotNone(value["inputs"][0]["last_attempt_at"])
        finally:
            source.release.set()
            refresh_thread.join(2)
            snapshot_thread.join(2)
        self.assertFalse(refresh_thread.is_alive())
        self.assertFalse(snapshot_thread.is_alive())


if __name__ == "__main__":
    unittest.main()

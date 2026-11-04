import copy
import unittest

from monitoring_ai.openai_observations import (
    FRAME_SCHEMA, BOOK_SCHEMA, latest_observations, project_response,
)
from monitoring_ai.store import stable_id

T0 = "2026-11-02T10:00:00Z"
T1 = "2026-11-02T10:00:30Z"
T2 = "2026-11-02T10:01:00Z"
NOW = "2026-11-02T10:01:10Z"
ALIAS = dict(source="agent", project="proj", session="sess")


def proj(status, at, rid="resp_1", **extra):
    raw = {"object":"response", "id": rid, "status": status, "model": "gpt-x", "created_at": 1780000000, **extra}
    return project_response(raw, observed_at=at, **ALIAS)


class Workflow(unittest.TestCase):
    def test_queued_in_progress_completed_no_tokens(self):
        frames = [proj("queued", T0), proj("in_progress", T1), proj("completed", T2)]
        for f in frames:
            self.assertEqual(f["schema"], FRAME_SCHEMA)
            self.assertEqual(f["id"], "response-" + stable_id("openai-response", "resp_1"))
            self.assertFalse(f["complete_coverage"])
            self.assertFalse(any(k in f for k in ("usage", "tokens", "cost", "input_tokens")))
        book = latest_observations(frames, NOW)
        self.assertEqual(book["schema"], BOOK_SCHEMA)
        self.assertEqual(book["scope"], "explicit-caller-received-responses")
        self.assertFalse(book["complete_coverage"])
        self.assertFalse(book["truncated"])
        (row,) = book["rows"]
        self.assertEqual(row["reported_status"], "completed")
        self.assertEqual(row["freshness"], "current")
        # reversed input order gives identical result
        self.assertEqual(book, latest_observations(frames[::-1], NOW))

    def test_terminal_statuses_and_privacy(self):
        for st in ("failed", "cancelled", "incomplete"):
            f = proj(st, T0, rid="secret-request-id", error={"message": "BODY-SECRET"},
                     output=[{"text": "CONTENT"}], user="u@example.com",
                     usage={"input_tokens": 5}, metadata={"k": "v"})
            self.assertEqual(f["status"], st)
            self.assertEqual(f["has_error"], True)
            blob = repr(f)
            for s in ("BODY-SECRET", "CONTENT", "u@example.com", "secret-request-id", "input_tokens"):
                self.assertNotIn(s, blob)
            row = latest_observations([f], NOW)["rows"][0]
            self.assertEqual(row["reported_status"], st)
        self.assertFalse(proj("completed", T0)["has_error"])
        self.assertEqual(project_response({"object":"response", "id": "a", "status": "queued"}, observed_at=T0, **ALIAS)["model"], "unknown")
        self.assertIsNone(project_response({"object":"response", "id": "a", "status": "queued"}, observed_at=T0, **ALIAS)["created_at"])

    def test_malformed_projection(self):
        bads = [
            None, {}, {"id": "", "status": "queued"}, {"id": "x" * 1001, "status": "queued"},
            {"id": 5, "status": "queued"}, {"id": "a"}, {"id": "a", "status": "weird"},
            {"id": "a", "status": "queued", "model": 5},
            {"id": "a", "status": "queued", "created_at": float("nan")},
            {"id": "a", "status": "queued", "created_at": True},
        ]
        for raw in bads:
            if isinstance(raw, dict):
                raw = {"object": "response", **raw}
            with self.assertRaises(ValueError) as c:
                project_response(raw, observed_at=T0, **ALIAS)
            self.assertIsNone(c.exception.__cause__)
        with self.assertRaises(ValueError):
            project_response({"object":"response", "id": "a", "status": "queued"}, observed_at="2026-11-02T10:00:00", **ALIAS)

    def test_replay_conflict_binding_freshness_bounds(self):
        a = proj("queued", T0)
        self.assertEqual(len(latest_observations([a, copy.deepcopy(a)], NOW)["rows"]), 1)
        conflict = dict(a, status="completed")
        with self.assertRaises(ValueError):
            latest_observations([a, conflict], NOW)
        # alias / model / created_at binding across times
        for k, v in (("project", "other"), ("session", "other"), ("model", "other-model"),
                     ("created_at", "2030-01-01T00:00:00Z")):
            with self.assertRaises(ValueError):
                latest_observations([a, dict(proj("in_progress", T1), **{k: v})], NOW)
        # freshness
        self.assertEqual(latest_observations([a], "2026-11-02T10:02:05Z")["rows"][0]["freshness"], "current")
        self.assertEqual(latest_observations([a], "2026-11-02T10:02:06Z")["rows"][0]["freshness"], "stale")
        self.assertEqual(latest_observations([a], "2026-11-02T09:59:00Z")["rows"][0]["freshness"], "future")
        stale = latest_observations([proj("completed", T0)], "2026-11-02T12:00:00Z")["rows"][0]
        self.assertEqual((stale["reported_status"], stale["freshness"]), ("completed", "stale"))
        # bounds
        with self.assertRaises(ValueError):
            latest_observations([a] * 5001, NOW)
        many = [proj("queued", T0, rid="r%d" % i) for i in range(101)]
        with self.assertRaises(ValueError):
            latest_observations(many, NOW)
        self.assertEqual(len(latest_observations(many[:100], NOW)["rows"]), 100)
        # malformed frames / args
        for bad in ("x", [None], [dict(a, extra=1)], [dict(a, complete_coverage=True)],
                    [dict(a, provider="other")], [dict(a, status="weird")],
                    [dict(a, id="plain")], [dict(a, observed_at="nope")]):
            with self.assertRaises(ValueError):
                latest_observations(bad, NOW)
        for age in (-1, float("inf"), True, "5"):
            with self.assertRaises(ValueError):
                latest_observations([a], NOW, max_age_seconds=age)
        with self.assertRaises(ValueError):
            latest_observations([a], "bad")


if __name__ == "__main__":
    unittest.main()


class CaptureWorkflow(unittest.TestCase):
    def test_capture_retrieve_replay_and_conflict_without_usage(self):
        """Repeated retrieval is a dated observation, never another inference receipt."""
        import json
        import tempfile
        from pathlib import Path
        from monitoring_ai.openai_observations import record_observation, read_observations
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "status.jsonl"
            raw = dict(object="response", id="fixture-response", status="in_progress", model="fixture-model",
                       created_at=1780000000.125, _request_id="PRIVATE-HTTP-ID", output="PRIVATE-CONTENT")
            first = record_observation(raw, output, observed_at=T0, **ALIAS)
            self.assertEqual(first["added"], 1)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            before = output.read_bytes()
            self.assertEqual(record_observation(raw, output, observed_at=T0, append=True, **ALIAS)["replayed"], 1)
            self.assertEqual(output.read_bytes(), before)
            raw["status"] = "completed"
            self.assertEqual(record_observation(raw, output, observed_at=T1, append=True, **ALIAS)["added"], 1)
            accepted = output.read_bytes()
            raw["status"] = "failed"
            with self.assertRaises(ValueError):
                record_observation(raw, output, observed_at=T1, append=True, **ALIAS)
            self.assertEqual(output.read_bytes(), accepted)
            book = read_observations(output, NOW)
            self.assertEqual(len(book["rows"]), 1)
            self.assertEqual(book["rows"][0]["reported_status"], "completed")
            self.assertEqual(len(output.read_text().splitlines()), 2)
            self.assertFalse(any(key in book["rows"][0] for key in ("tokens", "cost", "usage")))
            for secret in ("PRIVATE-HTTP-ID", "PRIVATE-CONTENT", "fixture-response"):
                self.assertNotIn(secret, output.read_text())

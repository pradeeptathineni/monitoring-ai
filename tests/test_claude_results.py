from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from monitoring_ai.claude_results import FRAME_SCHEMA, capture_result, claude_results
from monitoring_ai.store import Store


def model(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0, cost=None, provider="firstParty"):
    value = {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadInputTokens": cache_read,
        "cacheCreationInputTokens": cache_write,
    }
    if cost is not None:
        value["costUSD"] = cost
    if provider is not None:
        value["provider"] = provider
    value["canonicalModel"] = "ignored-canonical-alias"
    value["costBasis"] = "ignored-native-basis"
    return value


def frame(at, session="session-A", usage=None, **native_extras):
    result = {
        "type": "result",
        "session_id": session,
        "modelUsage": usage or {},
        "usage": {"input_tokens": 999999},
        "total_cost_usd": 999999,
        "result": "private prompt and output text",
        **native_extras,
    }
    return {"schema": FRAME_SCHEMA, "captured_at": at, "result": result}


class ClaudeResultTests(unittest.TestCase):
    def test_capture_discards_native_content_and_normalizes_cost_metadata(self):
        raw = frame("ignored", usage={"claude-sonnet-4": model(10, 4, 3, 2, "0.012300")})
        raw["result"]["result"] = "/private/prompt.txt and private answer"
        captured = capture_result(raw, "2026-04-01T10:00:00-04:00")
        self.assertEqual(captured["captured_at"], "2026-04-01T14:00:00.000Z")
        self.assertEqual(set(captured), {"schema", "captured_at", "result"})
        self.assertEqual(captured["result"]["modelUsage"]["claude-sonnet-4"]["costUSD"], "0.012300")
        self.assertNotIn("private", repr(captured))
        self.assertNotIn("canonicalModel", repr(captured))

    def test_cumulative_resumed_frames_emit_per_model_deltas_and_estimated_costs(self):
        frames = [
            frame("2026-04-01T10:00:00Z", usage={
                "claude-sonnet-4": model(100, 20, 30, 4, "0.10"),
                "claude-haiku-4": model(50, 5, 10, 0, "0.01", provider="thirdParty"),
            }),
            frame("2026-04-01T10:02:00Z", usage={
                "claude-sonnet-4": model(160, 32, 50, 9, "0.16"),
                "claude-haiku-4": model(80, 8, 15, 2, "0.02", provider="thirdParty"),
            }, result="changed prose ignored"),
            frame("2026-04-01T10:02:00Z", usage={
                "claude-sonnet-4": model(160, 32, 50, 9, "0.16"),
                "claude-haiku-4": model(80, 8, 15, 2, "0.02", provider="thirdParty"),
            }),
        ]
        events = claude_results(frames, "claude-local", "demo")
        self.assertEqual(len(events), 4)
        self.assertEqual([e["tokens"] for e in events], [
            {"input": 100, "cache_read": 30, "cache_write": 4, "output": 20},
            {"input": 50, "cache_read": 10, "cache_write": 0, "output": 5},
            {"input": 60, "cache_read": 20, "cache_write": 5, "output": 12},
            {"input": 30, "cache_read": 5, "cache_write": 2, "output": 3},
        ])
        self.assertEqual([e["cost"]["amount"] for e in events], ["0.10", "0.01", "0.06", "0.01"])
        self.assertTrue(all(e["cost"]["basis"] == "estimated" for e in events))
        self.assertTrue(all(e["cost"]["provenance"] == "claude-cli-client-estimate" for e in events))
        self.assertEqual([e["provider"] for e in events], ["anthropic", "unknown", "anthropic", "unknown"])
        self.assertEqual([e["surface"] for e in events], ["claude-code-result"] * 4)
        self.assertEqual([e["timestamp"] for e in events], [
            "2026-04-01T10:00:00.000Z", "2026-04-01T10:00:00.000Z",
            "2026-04-01T10:02:00.000Z", "2026-04-01T10:02:00.000Z",
        ])
        self.assertEqual(claude_results(frames, "claude-local", "demo"), events)

    def test_empty_and_zero_baselines_then_session_reset(self):
        frames = [
            frame("2026-04-01T10:00:00Z"),
            frame("2026-04-01T10:01:00Z", usage={"model-x": model(cost="0.5")}),
            frame("2026-04-01T10:02:00Z", usage={"model-x": model(5, 2, 1, 0, "0.6")}),
            frame("2026-04-01T09:00:00Z", session="session-B", usage={"model-x": model(3, 1, cost="0.02")}),
        ]
        events = claude_results(frames, "src", "demo")
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["tokens"], {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0})
        self.assertEqual(events[0]["cost"]["amount"], "0.5")
        self.assertEqual(events[1]["tokens"], {"input": 5, "cache_read": 1, "cache_write": 0, "output": 2})
        self.assertEqual(events[1]["cost"]["amount"], "0.1")
        self.assertNotEqual(events[1]["session"], events[2]["session"])

    def test_first_nonzero_event_uses_fixed_baseline_id_and_truncated_history_conflicts(self):
        first = frame("2026-04-01T10:00:00Z", usage={"model-x": model(10, 2, cost="0.02")})
        second = frame("2026-04-01T10:01:00Z", usage={"model-x": model(20, 4, cost="0.04")})
        third = frame("2026-04-01T10:02:00Z", usage={"model-x": model(30, 6, cost="0.06")})
        accepted = claude_results([first, second], "src", "demo")
        gap_replacement = claude_results([first, third], "src", "demo")
        complete = claude_results([first, second, third], "src", "demo")
        self.assertEqual(accepted[0]["id"], gap_replacement[0]["id"])
        self.assertEqual(accepted[1]["id"], gap_replacement[1]["id"])
        self.assertNotEqual(accepted[1], gap_replacement[1])
        self.assertEqual(complete[:2], accepted)
        self.assertNotEqual(complete[2]["id"], gap_replacement[1]["id"])
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "events.sqlite3")
            store.ingest(accepted)
            with self.assertRaisesRegex(ValueError, "identity"):
                store.ingest(gap_replacement)
            self.assertEqual(store.ingest(complete), {"added": 1, "replayed": 2})
            self.assertEqual(store.ingest(complete), {"added": 0, "replayed": 3})
            _, stored = store.read()
            self.assertEqual(len(stored), len(complete))

    def test_refuses_decreases_disappearing_models_and_cost_coverage_changes(self):
        start = frame("2026-04-01T10:00:00Z", usage={"model-x": model(10, 2, cost="0.02")})
        cases = [
            frame("2026-04-01T10:01:00Z", usage={"model-x": model(9, 2, cost="0.02")}),
            frame("2026-04-01T10:01:00Z"),
            frame("2026-04-01T10:01:00Z", usage={"model-x": model(10, 2)}),
        ]
        for end in cases:
            with self.subTest(end=end):
                with self.assertRaises(ValueError):
                    claude_results([start, end], "src", "demo")
        unknown = frame("2026-04-01T10:00:00Z", usage={"model-x": model(10, 2)})
        later_known = frame("2026-04-01T10:01:00Z", usage={"model-x": model(12, 3, cost="0.04")})
        with self.assertRaises(ValueError):
            claude_results([unknown, later_known], "src", "demo")
        provider_start = frame("2026-04-01T10:00:00Z", usage={"model-x": model(10, 2)})
        provider_change = frame("2026-04-01T10:01:00Z", usage={
            "model-x": model(12, 3, provider="thirdParty"),
        })
        with self.assertRaisesRegex(ValueError, "provider changed"):
            claude_results([provider_start, provider_change], "src", "demo")

    def test_validates_required_counters_types_bounds_and_frame_order(self):
        invalid = frame("2026-04-01T10:00:00Z", usage={"model-x": model(1, 1)})
        del invalid["result"]["modelUsage"]["model-x"]["outputTokens"]
        with self.assertRaisesRegex(ValueError, "Invalid Claude result frame"):
            capture_result(invalid, "2026-04-01T10:00:00Z")
        invalid = frame("2026-04-01T10:00:00Z", usage={"model-x": model(True, 1)})
        with self.assertRaises(ValueError):
            capture_result(invalid, "2026-04-01T10:00:00Z")
        invalid = frame("2026-04-01T10:00:00Z", usage={"model-x": model(cost="NaN")})
        with self.assertRaises(ValueError):
            capture_result(invalid, "2026-04-01T10:00:00Z")
        ordered = [
            frame("2026-04-01T10:01:00Z", usage={"model-x": model(1)}),
            frame("2026-04-01T10:00:00Z", usage={"model-x": model(2)}),
        ]
        with self.assertRaisesRegex(ValueError, "nondecreasing"):
            claude_results(ordered, "src", "demo")
        with self.assertRaises(ValueError):
            capture_result(frame("", usage={}), "2026-04-01T10:00:00")

    def test_capture_accepts_native_result_but_parser_requires_closed_wrapper(self):
        wrapped = frame("2026-04-01T10:00:00Z", usage={"model-x": model(2, 1)})
        self.assertEqual(capture_result(wrapped["result"], wrapped["captured_at"])["schema"], FRAME_SCHEMA)
        bare = dict(wrapped["result"], captured_at=wrapped["captured_at"])
        with self.assertRaisesRegex(ValueError, "Invalid Claude result frames"):
            claude_results([bare], "src", "demo")
        extra = dict(wrapped, unexpected="discarded by sanitizer")
        with self.assertRaisesRegex(ValueError, "Invalid Claude result frames"):
            claude_results([extra], "src", "demo")
        missing = {key: value for key, value in wrapped.items() if key != "captured_at"}
        with self.assertRaisesRegex(ValueError, "Invalid Claude result frames"):
            claude_results([missing], "src", "demo")

    def test_model_usage_must_be_explicit_even_when_empty_is_valid(self):
        empty = frame("2026-04-01T10:00:00Z", usage={})
        self.assertEqual(claude_results([empty], "src", "demo"), [])
        for absent in ("missing", None):
            malformed = frame("2026-04-01T10:00:00Z", usage={})
            if absent == "missing":
                del malformed["result"]["modelUsage"]
            else:
                malformed["result"]["modelUsage"] = None
            with self.subTest(absent=absent), self.assertRaisesRegex(ValueError, "Invalid Claude result frame"):
                capture_result(malformed, malformed["captured_at"])

    def test_native_extras_and_changed_prose_do_not_enter_events(self):
        a = frame("2026-04-01T10:00:00Z", usage={"model-x": model(5, 1)}, result="first private text")
        b = deepcopy(a)
        b["result"]["result"] = "/some/private/path second text"
        b["result"]["unrelated"] = {"nested": "secret"}
        self.assertEqual(claude_results([a], "src", "demo"), claude_results([b], "src", "demo"))
        self.assertNotIn("private", repr(claude_results([a], "src", "demo")))
        self.assertNotIn("secret", repr(capture_result(b, b["captured_at"])))


if __name__ == "__main__":
    unittest.main()

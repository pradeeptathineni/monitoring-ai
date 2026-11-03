"""Synthetic tests for CodeBurn daily breakdown projection."""

import copy
import hashlib
import json
import unittest

from monitoring_ai.codeburn_breakdowns import (
    BREAKDOWN_ERROR,
    COLLISION_ERROR,
    MAX_ENTRIES,
    TOO_MANY_ERROR,
    project_breakdowns,
)

TOKEN_FIELDS = ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
BUCKETS = ("input", "output", "cache_read", "cache_write")
ROW_KEYS = {"id", "calls", "source_cost_usd", "sessions", "tokens"}


def model_stats(**overrides):
    stats = {
        "calls": 4,
        "cost": "0.2500",
        "inputTokens": 1200,
        "outputTokens": 300,
        "cacheReadTokens": 50,
        "cacheWriteTokens": 0,
        "savingsUSD": "0.05",
    }
    stats.update(overrides)
    return stats


def provider_slice(**overrides):
    # Only the always-written fields; sessions and token counts are optional.
    data = {"calls": 4, "cost": "0.2500", "savingsUSD": "0.05"}
    data.update(overrides)
    return data


def without(entry, field):
    return {key: value for key, value in entry.items() if key != field}


def opaque(key):
    return "opaque-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


class ProjectBreakdownsTest(unittest.TestCase):
    def assert_refuses(self, day, message=BREAKDOWN_ERROR):
        with self.assertRaises(ValueError) as caught:
            project_breakdowns(day)
        self.assertEqual(str(caught.exception), message)
        self.assertIsNone(caught.exception.__cause__)

    def test_projects_rows_with_original_decimal_costs(self):
        day = {
            "models": {
                "model-b": model_stats(cost="0.10", calls=2),
                "model-a": model_stats(cost="12.3400"),
            },
            "providers": {
                "provider-x": provider_slice(
                    cost="1E+2", calls=6, sessions=2,
                    inputTokens=10, outputTokens=20, cacheReadTokens=30, cacheWriteTokens=40,
                ),
                "provider-w": provider_slice(cost=3),
            },
        }
        original = copy.deepcopy(day)
        result = project_breakdowns(day)
        model_tokens = {"input": 1200, "output": 300, "cache_read": 50, "cache_write": 0}
        self.assertEqual(result, {
            "models": [
                {"id": "model-a", "calls": 4, "source_cost_usd": "12.3400", "sessions": None, "tokens": model_tokens},
                {"id": "model-b", "calls": 2, "source_cost_usd": "0.10", "sessions": None, "tokens": model_tokens},
            ],
            "providers": [
                {"id": "provider-w", "calls": 4, "source_cost_usd": "3", "sessions": None,
                 "tokens": dict.fromkeys(BUCKETS)},
                {"id": "provider-x", "calls": 6, "source_cost_usd": "100", "sessions": 2,
                 "tokens": {"input": 10, "output": 20, "cache_read": 30, "cache_write": 40}},
            ],
        })
        self.assertEqual(day, original)

    def test_absent_maps_are_unknown_and_empty_maps_have_no_rows(self):
        self.assertEqual(project_breakdowns({}), {"models": None, "providers": None})
        self.assertEqual(project_breakdowns({"models": {}}), {"models": [], "providers": None})
        self.assertEqual(
            project_breakdowns({"models": {}, "providers": {}}), {"models": [], "providers": []}
        )

    def test_missing_provider_buckets_stay_unknown_not_zero(self):
        result = project_breakdowns({"providers": {
            "legacy": provider_slice(),
            "partial": provider_slice(inputTokens=7),
            "zeros": provider_slice(
                sessions=0, inputTokens=0, outputTokens=0, cacheReadTokens=0, cacheWriteTokens=0
            ),
        }})
        legacy, partial, zeros = result["providers"]
        self.assertIsNone(legacy["sessions"])
        self.assertEqual(legacy["tokens"], dict.fromkeys(BUCKETS))
        self.assertEqual(partial["tokens"], {"input": 7, "output": None, "cache_read": None, "cache_write": None})
        self.assertEqual(zeros["sessions"], 0)
        self.assertEqual(zeros["tokens"], dict.fromkeys(BUCKETS, 0))

    def test_present_provider_optional_fields_must_be_valid_counts(self):
        for field in ("sessions",) + TOKEN_FIELDS:
            for bad in (None, -1, True, 1.0, "3", 10**15 + 1):
                with self.subTest(field=field, bad=bad):
                    self.assert_refuses({"providers": {"p": provider_slice(**{field: bad})}})
            project_breakdowns({"providers": {"p": provider_slice(**{field: 10**15})}})

    def test_required_counts_are_strict_integers(self):
        cases = [("models", model_stats(), field) for field in ("calls",) + TOKEN_FIELDS]
        cases.append(("providers", provider_slice(), "calls"))
        for name, entry, field in cases:
            with self.subTest(name=name, field=field, case="missing"):
                self.assert_refuses({name: {"x": without(entry, field)}})
            for bad in (None, -1, False, 2.0, "5", 10**15 + 1):
                with self.subTest(name=name, field=field, bad=bad):
                    self.assert_refuses({name: {"x": dict(entry, **{field: bad})}})

    def test_model_sessions_are_always_unknown(self):
        row = project_breakdowns({"models": {"m": model_stats(sessions=9, calls=10**15)}})["models"][0]
        self.assertIsNone(row["sessions"])
        self.assertEqual(row["calls"], 10**15)

    def test_cost_is_required_and_validated(self):
        bad_costs = (None, True, "-0.01", "NaN", "Infinity", "1e13", "abc", [], -1)
        for name, entry in (("models", model_stats()), ("providers", provider_slice())):
            with self.subTest(name=name, case="missing"):
                self.assert_refuses({name: {"x": without(entry, "cost")}})
            for bad in bad_costs:
                with self.subTest(name=name, bad=bad):
                    self.assert_refuses({name: {"x": dict(entry, cost=bad)}})

    def test_marginals_stay_separate_and_nested_data_is_dropped(self):
        day = {
            "models": {"model-a": model_stats(calls=5), "model-b": model_stats(calls=2)},
            "providers": {
                "provider-x": provider_slice(
                    calls=4,
                    models={"nested-only-model": model_stats(calls=99)},
                    categories={"secret-category": 1},
                    projects={"secret-project": 1},
                    sessionIds=["native-session-id"],
                ),
            },
            "categories": {"day-category": {"calls": 1}},
            "projects": {"day-project": {"calls": 1}},
            "calls": 7,
            "cost": "9.99",
        }
        result = project_breakdowns(day)
        self.assertEqual(set(result), {"models", "providers"})
        self.assertEqual([row["id"] for row in result["models"]], ["model-a", "model-b"])
        self.assertEqual([row["id"] for row in result["providers"]], ["provider-x"])
        # Each marginal keeps its own reported counts; nothing is split or combined.
        self.assertEqual([row["calls"] for row in result["models"]], [5, 2])
        self.assertEqual(result["providers"][0]["calls"], 4)
        for row in result["models"] + result["providers"]:
            self.assertEqual(set(row), ROW_KEYS)
        text = json.dumps(result)
        for leaked in ("nested-only-model", "secret-category", "secret-project",
                       "native-session-id", "day-category", "day-project", "savings", "0.05", "9.99"):
            self.assertNotIn(leaked, text)

    def test_unsafe_labels_become_opaque_digests(self):
        unsafe = [
            "/Users/someone/private/project",
            "a..b",
            "x" * 121,
            "tab" + chr(9) + "key",
            "bell" + chr(7),
            "ünicode-model",
            "[decorated] model (1)",
            "z" * 1000,
        ]
        safe = ["claude-sonnet-4.5", "gpt:4o@2024_rev", "a.b", "y" * 120]
        result = project_breakdowns({"models": {key: model_stats() for key in unsafe + safe}})
        ids = [row["id"] for row in result["models"]]
        self.assertEqual(ids, sorted([opaque(key) for key in unsafe] + safe))
        text = json.dumps(result, ensure_ascii=False)
        for key in unsafe:
            self.assertNotIn(key, text)

    def test_spaced_model_name_stays_readable_without_raw_label(self):
        key = "Claude Sonnet 4.6 (route)"
        alias = project_breakdowns({"models": {key: model_stats()}})["models"][0]["id"]
        self.assertTrue(alias.startswith("Claude-Sonnet-4.6-route-"))
        self.assertEqual(len(alias.rsplit("-", 1)[-1]), 8)
        self.assertNotIn(key, alias)
        provider = project_breakdowns({"providers": {"my provider": provider_slice()}})["providers"][0]["id"]
        self.assertEqual(provider, opaque("my provider"))

    def test_identifier_collision_is_refused_not_merged(self):
        for name, entry in (("models", model_stats()), ("providers", provider_slice())):
            with self.subTest(name=name):
                native_key = "a/private/path" if name == "models" else "has space"
                self.assert_refuses({name: {native_key: entry, opaque(native_key): entry}}, COLLISION_ERROR)
        # The same id in the two separate maps is not a collision.
        result = project_breakdowns({"models": {"shared": model_stats()}, "providers": {"shared": provider_slice()}})
        self.assertEqual(result["models"][0]["id"], result["providers"][0]["id"])

    def test_native_keys_must_be_nonempty_bounded_strings(self):
        for key in ("", "k" * 1001, 5, None, "bad" + chr(0xD800)):
            with self.subTest(key=repr(key)[:20]):
                self.assert_refuses({"providers": {key: provider_slice()}})

    def test_entry_limit_per_map(self):
        self.assertEqual(MAX_ENTRIES, 128)
        at_limit = {f"model-{index:03d}": model_stats() for index in range(MAX_ENTRIES)}
        self.assertEqual(len(project_breakdowns({"models": at_limit})["models"]), MAX_ENTRIES)
        self.assert_refuses({"models": dict(at_limit, extra=model_stats())}, TOO_MANY_ERROR)
        over = {f"p{index}": provider_slice() for index in range(MAX_ENTRIES + 1)}
        self.assert_refuses({"providers": over}, TOO_MANY_ERROR)

    def test_non_mapping_inputs_are_refused(self):
        for day in (None, [], "day", 7):
            with self.subTest(day=day):
                self.assert_refuses(day)
        for name in ("models", "providers"):
            for bad in (None, [], "value", 0):
                with self.subTest(name=name, bad=bad):
                    self.assert_refuses({name: bad})
                    self.assert_refuses({name: {"ok-id": bad}})

    def test_errors_do_not_echo_source_details(self):
        cases = [
            {"models": {"/secret/path/model": model_stats(cost="SECRET-COST")}},
            {"providers": {"secret-provider": provider_slice(sessions="SECRET-SESSIONS")}},
            {"models": {"secret-model": model_stats(inputTokens=-424242)}},
        ]
        for day in cases:
            with self.assertRaises(ValueError) as caught:
                project_breakdowns(day)
            message = str(caught.exception)
            self.assertEqual(message, BREAKDOWN_ERROR)
            for fragment in ("secret", "SECRET", "424242", "cost", "inputTokens", "sessions"):
                self.assertNotIn(fragment, message)


if __name__ == "__main__":
    unittest.main()

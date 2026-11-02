"""Readiness previews use fictional files and private temporary ledgers only."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from monitoring_ai.source_readiness import preview_selection
from monitoring_ai.store import Store

NOW = "2026-11-02T20:00:00Z"


def event(ident, *, source="producer", session="session", entity="entity", parent=None,
          entity_type="session", tokens=1):
    return {
        "schema": "monitoring.event.v1", "id": ident, "source": source,
        "session": session, "entity": entity, "parent": parent,
        "entity_type": entity_type, "timestamp": NOW, "kind": "usage",
        "provider": "example", "model": "fictional-model", "surface": "fixture",
        "project": "fictional-project", "tokens": {"input": tokens},
    }


class SourceReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self._source_number = 0

    def input_file(self, rows, *, name=None):
        self._source_number += 1
        path = self.root / (name or f"input-{self._source_number}.jsonl")
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return path

    def manifest(self, entries, *, name="selection.json"):
        path = self.root / name
        path.write_text(json.dumps({"schema": "monitoring.sources.v1", "sources": entries}), encoding="utf-8")
        return path

    def entry(self, ident, path):
        return {"id": ident, "format": "normalized", "path": str(path)}

    def ledger(self, rows=()):
        store = Store(self.root / "selected-ledger.sqlite")
        if rows:
            store.ingest(list(rows))
        return store

    def stored_pairs(self, store):
        _, rows = store.read()
        return {(row["source"], row["id"]) for row in rows}

    def test_changed_identity_rolls_back_one_input_and_later_input_can_advance(self):
        base = event("existing", source="producer-a")
        ledger = self.ledger([base])
        conflicting = event("existing", source="producer-a", tokens=99)
        invalid_batch = self.input_file([event("would-be-added", source="producer-a"), conflicting])
        healthy_batch = self.input_file([event("healthy", source="producer-b")])
        selected = self.manifest([
            self.entry("conflict-alias", invalid_batch),
            self.entry("healthy-alias", healthy_batch),
        ])

        result = preview_selection(selected, ledger.path)

        self.assertEqual(result["schema"], "monitoring.source-readiness.v1")
        self.assertEqual(result["scope"], "explicit-inputs-and-ledger-snapshot")
        self.assertTrue(result["read_only"])
        self.assertEqual(result["base"], {"kind": "supplied-ledger", "revision": 1, "events": 1})
        self.assertFalse(result["ready"])
        self.assertEqual([row["state"] for row in result["inputs"]], ["rejected", "ready"])
        self.assertIsNone(result["inputs"][0]["proposed"])
        self.assertIsNotNone(result["inputs"][0]["summary"])
        self.assertEqual(result["inputs"][1]["proposed"], {"added": 1, "replayed": 0})
        # Both the failed first batch and successful later batch affected only the disposable copy.
        self.assertEqual(self.stored_pairs(ledger), {("producer-a", "existing")})

    def test_cycles_and_parent_type_collisions_are_rejected(self):
        base = event("base", source="producer-base", entity="shared", entity_type="session")
        ledger = self.ledger([base])
        cycle_rows = [
            event("cycle-a", source="producer-cycle", entity="a", parent="b"),
            event("cycle-b", source="producer-cycle", entity="b", parent="a", entity_type="worker"),
        ]
        collision_rows = [event("changed-type", source="producer-base", entity="shared", entity_type="agent")]
        cycle_file = self.input_file(cycle_rows)
        collision_file = self.input_file(collision_rows)
        result = preview_selection(self.manifest([
            self.entry("cycle", cycle_file), self.entry("type-change", collision_file),
        ]), ledger.path)

        self.assertEqual([row["state"] for row in result["inputs"]], ["rejected", "rejected"])
        self.assertTrue(all(row["proposed"] is None for row in result["inputs"]))
        self.assertTrue(all(row["summary"] is not None for row in result["inputs"]))
        self.assertEqual(self.stored_pairs(ledger), {("producer-base", "base")})

    def test_missing_input_does_not_stop_later_source(self):
        absent = self.root / "private-missing.jsonl"
        healthy = self.input_file([event("good")])
        result = preview_selection(self.manifest([
            self.entry("missing-alias", absent), self.entry("healthy-alias", healthy),
        ]))

        self.assertEqual([row["state"] for row in result["inputs"]], ["unavailable", "ready"])
        self.assertIsNone(result["inputs"][0]["summary"])
        self.assertEqual(result["inputs"][1]["proposed"], {"added": 1, "replayed": 0})
        self.assertFalse(result["ready"])
        self.assertEqual(result["base"], {"kind": "empty", "revision": 0, "events": 0})
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_summary_counts_unique_pairs_but_proposal_reports_raw_replays(self):
        repeated = event("same", source="producer")
        source = self.input_file([repeated, dict(repeated)])
        result = preview_selection(self.manifest([self.entry("repeat-input", source)]))
        item = result["inputs"][0]

        self.assertEqual(item["summary"]["records"]["total"], 1)
        self.assertEqual(item["proposed"], {"added": 1, "replayed": 1})
        self.assertEqual(item["state"], "ready")

    def test_read_limit_rejects_before_mutation_and_known_keys_advance_only_on_acceptance(self):
        existing = event("known", source="producer")
        ledger = self.ledger([existing])
        fresh = event("new", source="producer-new")
        reject_first = self.input_file([fresh])
        reject_again = self.input_file([fresh])
        replay = self.input_file([existing])
        selected = self.manifest([
            self.entry("too-large-a", reject_first),
            self.entry("too-large-b", reject_again),
            self.entry("replay", replay),
        ])

        with mock.patch("monitoring_ai.source_readiness.MAX_PREVIEW_EVENTS", 1):
            result = preview_selection(selected, ledger.path)

        self.assertEqual([row["state"] for row in result["inputs"]], ["rejected", "rejected", "ready"])
        self.assertIn("100,000-event preview limit", result["inputs"][0]["error"])
        self.assertEqual(result["inputs"][2]["proposed"], {"added": 0, "replayed": 1})
        self.assertEqual(self.stored_pairs(ledger), {("producer", "known")})

    def test_output_has_no_paths_raw_errors_or_private_source_fields(self):
        secret_path = str(self.root / "very-secret-private-session.jsonl")
        row = event("safe-id")
        row["private_prompt"] = "SHOULD_NOT_ESCAPE"
        source = self.input_file([row], name="very-secret-private-session.jsonl")
        selected = self.manifest([self.entry("alias", source)])
        result = preview_selection(selected)
        encoded = json.dumps(result)

        self.assertEqual(result["inputs"][0]["state"], "rejected")
        self.assertNotIn(secret_path, encoded)
        self.assertNotIn("very-secret-private-session", encoded)
        self.assertNotIn("SHOULD_NOT_ESCAPE", encoded)
        self.assertNotIn("path", encoded.lower())
        self.assertIn("can change", result["note"])
        self.assertIn("different source aliases", result["note"])

    def test_manifest_and_ledger_failures_are_generic_top_level_errors(self):
        bad_manifest = self.root / "private-manifest.json"
        bad_manifest.write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Selected source manifest could not be read or validated") as manifest_error:
            preview_selection(bad_manifest)
        self.assertNotIn(str(self.root), str(manifest_error.exception))

        source = self.input_file([event("one")])
        selected = self.manifest([self.entry("input", source)])
        bad_ledger = self.root / "private-ledger.sqlite"
        bad_ledger.write_text("not a sqlite database", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Selected ledger could not be safely previewed") as ledger_error:
            preview_selection(selected, bad_ledger)
        self.assertNotIn(str(self.root), str(ledger_error.exception))


if __name__ == "__main__":
    unittest.main()

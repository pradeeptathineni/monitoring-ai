import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from monitoring_ai.store import Store

ROOT = Path(__file__).parents[1]
NOW = "2026-11-02T20:00:00Z"


def event(ident, *, source="producer", session=None, entity=None, parent=None,
          entity_type="session", tokens=1):
    session = session or f"session-{ident}"
    entity = entity or session
    return {"schema": "monitoring.event.v1", "id": ident, "source": source,
            "session": session, "entity": entity, "parent": parent,
            "entity_type": entity_type, "timestamp": NOW, "kind": "usage",
            "provider": "test", "model": "test-model", "surface": "test",
            "project": "test-app", "tokens": {"input": tokens}}


def write_source(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


def manifest(path, sources):
    path.write_text(json.dumps({"schema": "monitoring.sources.v1", "sources": sources}))
    return path


def ledger_fingerprint(store):
    revision, rows = store.read()
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return revision, len(rows), hashlib.sha256(payload).hexdigest()


class ReadinessJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_preview(self, manifest_path, db=None):
        command = [sys.executable, "-m", "monitoring_ai", "sources", "preview",
                   "--sources", str(manifest_path), "--json"]
        if db is not None:
            command.extend(["--db", str(db)])
        return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=15)

    def test_empty_base_preview_is_ready_and_does_not_create_persistent_ledger(self):
        source_file = write_source(self.root / "good.jsonl", [event("new")])
        selection = manifest(self.root / "sources.json", [
            {"id": "good-input", "format": "normalized", "path": str(source_file)}])
        result = self.run_preview(selection)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["schema"], "monitoring.source-readiness.v1")
        self.assertTrue(value["ready"])
        self.assertTrue(value["read_only"])
        self.assertEqual(value["base"], {"kind": "empty", "revision": 0, "events": 0})
        self.assertEqual(value["inputs"][0]["state"], "ready")
        self.assertEqual(value["inputs"][0]["proposed"], {"added": 1, "replayed": 0})

    def test_conflicts_and_hierarchy_refusals_are_isolated_and_preview_never_writes_base(self):
        base_path = self.root / "existing.sqlite"
        existing = Store(base_path)
        original_conflict = event("same-id", source="upstream-conflict", session="s-conflict")
        original_hierarchy = event("original", source="upstream-hierarchy", session="s-hierarchy", entity="e-hierarchy")
        existing.ingest([original_conflict, original_hierarchy])
        before = ledger_fingerprint(existing)

        conflict_file = write_source(self.root / "conflict.jsonl", [
            event("would-rollback", source="upstream-conflict"),
            event("same-id", source="upstream-conflict", session="s-conflict", tokens=999),
        ])
        hierarchy_file = write_source(self.root / "hierarchy.jsonl", [
            event("also-rollback", source="upstream-hierarchy"),
            event("changed-parent", source="upstream-hierarchy", session="s-hierarchy",
                  entity="e-hierarchy", parent="new-parent", entity_type="agent"),
        ])
        healthy_file = write_source(self.root / "healthy.jsonl", [event("healthy", source="upstream-healthy")])
        selection = manifest(self.root / "sources.json", [
            {"id": "conflict", "format": "normalized", "path": str(conflict_file)},
            {"id": "hierarchy", "format": "normalized", "path": str(hierarchy_file)},
            {"id": "healthy", "format": "normalized", "path": str(healthy_file)},
        ])

        result = self.run_preview(selection, base_path)
        self.assertEqual(result.returncode, 1, result.stderr)
        value = json.loads(result.stdout)
        self.assertFalse(value["ready"])
        self.assertTrue(value["read_only"])
        self.assertEqual(value["base"], {"kind": "supplied-ledger", "revision": before[0], "events": before[1]})
        rows = {row["id"]: row for row in value["inputs"]}
        self.assertEqual(rows["conflict"]["state"], "rejected")
        self.assertIsNone(rows["conflict"]["proposed"])
        self.assertEqual(rows["hierarchy"]["state"], "rejected")
        self.assertIsNone(rows["hierarchy"]["proposed"])
        self.assertEqual(rows["healthy"]["state"], "ready")
        self.assertEqual(rows["healthy"]["proposed"]["added"], 1)
        self.assertNotIn(str(self.root), result.stdout)
        self.assertEqual(ledger_fingerprint(existing), before)
        stored_ids = {row["id"] for row in existing.read()[1]}
        self.assertNotIn("would-rollback", stored_ids)
        self.assertNotIn("also-rollback", stored_ids)
        self.assertNotIn("healthy", stored_ids)

    def test_invalid_supplied_base_or_manifest_uses_configuration_exit_code(self):
        source_file = write_source(self.root / "good.jsonl", [event("new")])
        selection = manifest(self.root / "sources.json", [
            {"id": "good-input", "format": "normalized", "path": str(source_file)}])
        missing_db = self.root / "missing.sqlite"
        result = self.run_preview(selection, missing_db)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(str(missing_db), result.stderr)
        missing_manifest = self.root / "private-selection-name.json"
        result = self.run_preview(missing_manifest)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(str(missing_manifest), result.stderr)


if __name__ == "__main__":
    unittest.main()

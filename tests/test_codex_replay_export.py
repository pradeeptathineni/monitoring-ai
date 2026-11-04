"""Explicit fictional family export keeps new usage and refuses unsafe snapshots."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.adapters import load
from monitoring_ai import codex_replay_export
from monitoring_ai.codex_lineage import UnsupportedHistoryError, UNQUALIFIED_MESSAGE
from monitoring_ai.codex_replay_export import export_codex_family
from monitoring_ai.source_setup import check_selection, create_selection
from monitoring_ai.store import Store


def meta(session, minute=0, parent=None):
    """Supply declared relations alongside unrelated data that must be dropped."""
    payload = dict(id=session, cwd="/fictional/private", content="discard-transcript")
    if parent is not None:
        payload["forked_from_id"] = parent
    return dict(type="session_meta", timestamp=f"2026-11-04T00:{minute:02d}:00Z", payload=payload)


def usage(minute, incoming, outgoing):
    """Supply an independently checkable cumulative snapshot."""
    return dict(type="event_msg", timestamp=f"2026-11-04T00:{minute:02d}:00Z", payload=dict(
        type="token_count", info=dict(total_token_usage=dict(
            input_tokens=incoming, cached_input_tokens=0, output_tokens=outgoing))))


class ReplayExportTests(unittest.TestCase):
    def setUp(self):
        """Create only fictional selected inputs and new private outputs."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.parent = [meta("raw-private-parent"), usage(1, 100, 10), usage(2, 150, 15)]
        self.child = [meta("raw-private-child", 3, "raw-private-parent"),
                      usage(3, 100, 10), usage(3, 150, 15), usage(4, 175, 18)]
        self.manifest = self.root / "sources.json"
        self.output = self.root / "reconciled.jsonl"
        self.select()

    def select(self):
        """Write the explicitly selected family, including child-first ordering."""
        entries = []
        for alias, rows in (("child", self.child), ("parent", self.parent)):
            path = self.root / (alias + ".jsonl")
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            entries.append(dict(id=alias, format="codex", path=str(path), project="fixture"))
        self.manifest.write_text(json.dumps(dict(schema="monitoring.sources.v1", sources=entries)))

    def test_cli_export_normalized_setup_import_and_replay(self):
        """The guarded native file becomes usable only through proven reconciliation."""
        self.assertFalse(check_selection(self.manifest)["all_readable"])
        before = {path: path.read_bytes() for path in self.root.glob("*.json*")}
        run = subprocess.run([sys.executable, "-m", "monitoring_ai", "export-codex-family",
                              "--sources", str(self.manifest), "--output", str(self.output)],
                             capture_output=True, text=True, check=True)
        receipt = json.loads(run.stdout)
        self.assertEqual(receipt["events"], 3)
        self.assertEqual(receipt["sources"][0]["copied_usage_records"], 2)
        self.assertFalse(receipt["complete_coverage"])
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        normalized = self.root / "normalized.json"
        create_selection(normalized, file=self.output, id="reconciled", format="normalized")
        self.assertTrue(check_selection(normalized)["all_readable"])
        rows = load(self.output)
        self.assertEqual(sum(sum(row["tokens"].values()) for row in rows), 193)
        self.assertEqual(sum(sum(row["tokens"].values()) for row in rows if row["source"] == "child"), 28)
        self.assertTrue(all(row["kind"] == "usage" and row.get("cost") is None for row in rows))
        encoded = self.output.read_text() + json.dumps(receipt)
        for value in (str(self.root), "raw-private-parent", "raw-private-child", "discard-transcript", "/fictional/private"):
            self.assertNotIn(value, encoded)
        store = Store(self.root / "events.sqlite")
        self.assertEqual(store.ingest(rows)["added"], 3)
        self.assertEqual(store.ingest(rows)["added"], 0)
        self.assertEqual(len({row["session"] for row in store.read()[1]}), 2)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        with self.assertRaises(FileExistsError):
            export_codex_family(self.manifest, self.output)
        self.assertEqual(load(self.output), rows)

    def test_unqualified_metadata_refuses_even_with_proven_prefix(self):
        """Known copied counters cannot qualify separately declared history boundaries."""
        for key, value in (("history_base", {"thread_id": "private-rollout", "end_ordinal_exclusive": 2,
                                             "end_byte_offset": 100}),
                           ("parent_thread_id", "raw-private-parent"),
                           ("subagent_history_start_ordinal", 0),
                           ("forked_from_ordinal_exclusive", 0),
                           ("history_mode", "paginated")):
            with self.subTest(key=key):
                for rows in (self.parent, self.child):
                    rows[0]["payload"][key] = value
                    self.select()
                    before = {p: p.read_bytes() for p in self.root.glob("*.json*")}
                    with self.assertRaises(UnsupportedHistoryError) as error:
                        export_codex_family(self.manifest, self.output)
                    self.assertEqual(str(error.exception), UNQUALIFIED_MESSAGE)
                    self.assertFalse(self.output.exists())
                    self.assertFalse(list(self.root.glob(".monitoring-export-*")))
                    self.assertEqual(before, {p: p.read_bytes() for p in before})
                    del rows[0]["payload"][key]
        # Null new fields and the legacy mode keep the existing exact-prefix route.
        for rows in (self.parent, self.child):
            rows[0]["payload"].update(history_base=None, parent_thread_id=None,
                                      subagent_history_start_ordinal=None,
                                      forked_from_ordinal_exclusive=None, history_mode="legacy")
        self.select()
        export_codex_family(self.manifest, self.output)
        self.assertEqual(sum(sum(row["tokens"].values()) for row in load(self.output)), 193)

    def test_parent_later_activity_is_not_subtracted_from_child(self):
        """An equal later parent counter represents distinct post-fork work."""
        self.parent.append(usage(5, 175, 18))
        self.select()
        export_codex_family(self.manifest, self.output)
        rows = load(self.output)
        self.assertEqual(sum(sum(row["tokens"].values()) for row in rows), 221)
        self.assertEqual(sum(sum(row["tokens"].values()) for row in rows if row["source"] == "child"), 28)

    def test_unsafe_family_and_mutating_file_never_publish(self):
        """Missing parents, compaction, partial lines and concurrent writes refuse atomically."""
        original = self.manifest.read_bytes()
        document = json.loads(original)
        document["sources"] = document["sources"][:1]
        self.manifest.write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            export_codex_family(self.manifest, self.output)
        self.manifest.write_bytes(original)
        self.child.append(dict(type="compacted", payload=dict(compaction_response_id="private-response")))
        self.select()
        with self.assertRaisesRegex(ValueError, "Compaction"):
            export_codex_family(self.manifest, self.output)
        self.child.pop()
        self.select()
        path = self.root / "child.jsonl"
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
        with self.assertRaises(ValueError):
            export_codex_family(self.manifest, self.output)
        self.select()
        read = codex_replay_export._read

        def changed(source):
            """Append during a selected-file read to exercise the snapshot check."""
            result = read(source)
            with source.path.open("a") as stream:
                stream.write("\n")
            return result

        with patch.object(codex_replay_export, "_read", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "changed"):
                export_codex_family(self.manifest, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".monitoring-export-*")))

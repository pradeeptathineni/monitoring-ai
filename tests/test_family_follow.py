"""Finite selected-family following retains history across upstream failures."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.adapters import load
from monitoring_ai.family_follow import follow_family
from monitoring_ai.producer import EventWriteError, EventWriter
from test_codex_replay_export import meta, usage


class FamilyFollowTests(unittest.TestCase):
    def setUp(self):
        """Keep all producer files, selections and outputs fictional and disposable."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / "followed.jsonl"
        self.parent = self.root / "parent.jsonl"
        self.child = self.root / "child.jsonl"
        self.parent_rows = [meta("fixture-parent"), usage(1, 100, 10), usage(2, 150, 15)]
        self.write(self.parent, self.parent_rows)
        self.write(self.child, [meta("fixture-child", 3, "fixture-parent"),
                                usage(3, 100, 10), usage(3, 150, 15), usage(4, 175, 18)])
        self.manifest = self.root / "selected.json"
        self.manifest.write_text(json.dumps(dict(schema="monitoring.sources.v1", sources=[
            dict(id=alias, format="codex", path=str(path), project="fixture")
            for alias, path in (("parent", self.parent), ("child", self.child))])))
        self.tick = 0

    def write(self, path, rows):
        """Replace one complete fictional producer generation."""
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def tokens(self):
        """Read only the emitted normalized usage for this trial."""
        return sum(sum(row["tokens"].values()) for row in load(self.output))

    def test_partial_append_parent_generation_and_idle_cache(self):
        """A parent-only append invalidates the child proof without counting copies."""
        pending = (json.dumps(usage(6, 200, 20)) + "\n").encode()

        def advance(seconds):
            """Complete a partial child write, then append separate later parent work."""
            self.tick += seconds
            if self.tick == 1:
                with self.child.open("ab") as stream:
                    stream.write(pending[:25])
            elif self.tick == 2:
                self.assertEqual(self.tokens(), 193)
                with self.child.open("ab") as stream:
                    stream.write(pending[25:])
            elif self.tick == 3:
                self.assertEqual(self.tokens(), 220)
                with self.parent.open("a") as stream:
                    stream.write(json.dumps(usage(5, 175, 18)) + "\n")

        results = list(follow_family(self.manifest, self.output, seconds=4, interval=1,
                                    clock=lambda: self.tick, sleep=advance))
        self.assertEqual([r["state"] for r in results[:-1]],
                         ["following", "unavailable", "following", "following", "following"])
        self.assertEqual(self.tokens(), 248)
        self.assertEqual(results[-1]["full_reads"], 4)
        self.assertEqual(results[-1]["added_total"], 5)
        self.assertFalse(results[-1]["complete_coverage"])
        self.assertNotIn(str(self.root), json.dumps(results))

    def test_rewritten_parent_refuses_then_full_restore_recovers(self):
        """Changing a copied parent prefix cannot silently revise already emitted usage."""
        initial = None

        def advance(seconds):
            """Present a changed parent, then restore the complete accepted generation."""
            nonlocal initial
            self.tick += seconds
            if self.tick == 1:
                initial = self.output.read_bytes()
                self.write(self.parent, [meta("fixture-parent"), usage(1, 101, 10), usage(2, 150, 15)])
            elif self.tick == 2:
                self.assertEqual(self.output.read_bytes(), initial)
                self.write(self.parent, self.parent_rows)

        rows = list(follow_family(self.manifest, self.output, seconds=2, interval=1,
                                 clock=lambda: self.tick, sleep=advance))
        self.assertEqual([row["state"] for row in rows[:-1]], ["following", "unavailable", "following"])
        self.assertEqual(self.output.read_bytes(), initial)
        self.assertEqual(self.tokens(), 193)

    def test_explicit_resume_and_uncertain_output_stop(self):
        """Resume replays nothing; an uncertain sink write stops without automatic retry."""
        collecting = follow_family(self.manifest, self.output, seconds=1, interval=1,
                                   clock=lambda: self.tick, sleep=lambda seconds: setattr(self, "tick", self.tick + seconds))
        next(collecting)
        try:
            with self.assertRaises(ValueError):
                EventWriter(self.output, append=True)
        finally:
            collecting.close()
        before = self.output.read_bytes()
        self.tick = 0
        resumed = list(follow_family(self.manifest, self.output, seconds=1, interval=1, append=True,
                                    clock=lambda: self.tick, sleep=lambda seconds: setattr(self, "tick", self.tick + seconds)))
        self.assertEqual(resumed[-1]["added_total"], 0)
        self.assertEqual(self.output.read_bytes(), before)
        with self.assertRaises(ValueError):
            list(follow_family(self.manifest, self.output, seconds=1))
        self.output.unlink()
        with patch("monitoring_ai.family_follow.EventWriter.write", side_effect=EventWriteError) as write:
            with self.assertRaises(EventWriteError):
                list(follow_family(self.manifest, self.output, seconds=1))
        self.assertEqual(write.call_count, 1)

    def test_finite_cli_stops_and_reports_reconciliation_work(self):
        """An ordinary bounded invocation exits without a daemon or provider call."""
        run = subprocess.run([sys.executable, "-m", "monitoring_ai", "follow-codex-family",
                              "--sources", str(self.manifest), "--output", str(self.output),
                              "--seconds", "1", "--interval", "1"],
                             capture_output=True, text=True, check=True, timeout=5)
        receipt = json.loads(run.stdout.splitlines()[-1])
        self.assertEqual(receipt["state"], "stopped")
        self.assertEqual(receipt["full_reads"], 1)
        self.assertEqual(receipt["added_total"], 3)
        self.assertEqual(self.tokens(), 193)

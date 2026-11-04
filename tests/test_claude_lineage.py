"""Explicit synthetic Claude forks refuse before copied history enters the ledger."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.adapters import claude, claude_row, load
from monitoring_ai.claude_lineage import ClaudeForkHistoryError, MESSAGE
from monitoring_ai.source_observer import SourceObserver
from monitoring_ai.source_readiness import preview_selection
from monitoring_ai.source_setup import check_selection
from monitoring_ai.sources import load_sources
from monitoring_ai.store import Store

SECRET = "fixture-private-parent"
BODY = "fixture-private-body"


def parent_rows():
    """Two fictional per-message observations totaling 165 tokens."""
    return [dict(type="assistant", sessionId=SECRET, uuid=f"parent-row-{i}",
                 timestamp=f"2026-11-04T00:0{i}:00Z", message=dict(
                     role="assistant", id=f"fixture-message-{i}", model="fixture-model",
                     content=[dict(type="text", text=BODY)],
                     usage=dict(input_tokens=inp, output_tokens=out)))
            for i, (inp, out) in enumerate(((100, 10), (50, 5)), 1)]


def new_usage(session=SECRET):
    return dict(type="assistant", sessionId=session, uuid="fixture-new-row",
                timestamp="2026-11-04T00:04:00Z", message=dict(
                    role="assistant", id="fixture-new-message", model="fixture-model",
                    usage=dict(input_tokens=25, output_tokens=3)))


def fork_rows():
    """Represent inspected SDK wire changes without copying its implementation."""
    rows = parent_rows()
    for i, row in enumerate(rows, 1):
        row.update(sessionId="fixture-fork", uuid=f"fork-row-{i}", isSidechain=False,
                   forkedFrom=dict(sessionId=SECRET, messageUuid=f"parent-row-{i}"))
    rows[-1]["timestamp"] = "2026-11-04T00:03:00Z"
    return rows + [new_usage("fixture-fork")]


def write(path, rows, mode="w"):
    with path.open(mode) as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def tokens(store):
    return sum(sum(row["tokens"].values()) for row in store.read()[1])


class ClaudeForkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def selection(self, entries):
        path = self.root / "selected.json"
        path.write_text(json.dumps(dict(schema="monitoring.sources.v1", sources=[
            dict(id=alias, format="claude", path=str(file), project="fixture")
            for alias, file in entries])))
        return path

    def test_projection_and_explicit_marker_boundary(self):
        """Only a boolean survives; null, sidechain and request metadata stay distinct."""
        for value in (dict(sessionId=SECRET, messageUuid=BODY), {}, [], "", False, 0):
            with self.subTest(value=value):
                row = dict(type="user", sessionId="fixture-fork", forkedFrom=value,
                           message=dict(role="user", content=BODY))
                projected = claude_row(row)
                self.assertIs(projected["fork_declared"], True)
                self.assertNotIn("forkedFrom", projected)
                self.assertNotIn(SECRET, json.dumps(projected))
                self.assertNotIn(BODY, json.dumps(projected))
                for declaration in (row, projected):
                    with self.assertRaises(ClaudeForkHistoryError) as error:
                        claude(parent_rows() + [declaration], "selected", "fixture")
                    self.assertEqual(str(error.exception), MESSAGE)
        for extra in ({}, dict(forkedFrom=None), dict(isSidechain=True),
                      dict(requestId="fixture-request"),
                      dict(isSidechain=False, requestId="fixture-request", forkedFrom=None)):
            rows = parent_rows()
            for row in rows:
                row.update(extra)
            self.assertEqual(sum(sum(e["tokens"].values()) for e in claude(
                [claude_row(row) for row in rows], "selected", "fixture")), 165)

    def test_selected_parent_and_fork_check_preview_observer(self):
        """Full selection refuses the copied input while independent usage remains accepted."""
        parent, fork = self.root / "parent.jsonl", self.root / "fork.jsonl"
        write(parent, parent_rows())
        write(fork, fork_rows())
        manifest = self.selection([("parent", parent), ("fork", fork)])
        before = {path: path.read_bytes() for path in (parent, fork, manifest)}
        checks = check_selection(manifest)
        self.assertFalse(checks["all_readable"])
        self.assertEqual([row["state"] for row in checks["checks"]], ["readable", "invalid"])
        self.assertEqual(checks["checks"][1]["error"], MESSAGE)
        preview = preview_selection(manifest)
        self.assertFalse(preview["ready"])
        self.assertEqual(preview["inputs"][1]["error"], MESSAGE)
        store = Store(self.root / "ledger.sqlite")
        observer = SourceObserver(load_sources(manifest))
        first = observer.refresh(store)
        self.assertEqual((first["healthy_inputs"], first["unavailable_inputs"]), (1, 1))
        self.assertEqual(first["inputs"][1]["error"], MESSAGE)
        self.assertEqual((len(store.read()[1]), tokens(store)), (2, 165))
        revision = store.read()[0]
        second = observer.refresh(store)
        self.assertEqual(store.read()[0], revision)
        for result in (checks, preview, first, second, store.read()[1]):
            encoded = json.dumps(result, default=str)
            self.assertNotIn(SECRET, encoded)
            self.assertNotIn(BODY, encoded)
            self.assertNotIn(str(self.root), encoded)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        with self.assertRaises(ClaudeForkHistoryError):
            load(fork, "claude", "fork", "fixture")

    def test_rejected_append_and_replacement_retain_then_recover(self):
        """Unqualified metadata cannot advance accepted cursor, usage or response evidence."""
        for mode in ("append", "replace"):
            with self.subTest(mode=mode):
                path = self.root / (mode + ".jsonl")
                write(path, parent_rows())
                manifest = self.selection([("selected", path)])
                store = Store(self.root / (mode + ".sqlite"))
                observer = SourceObserver(load_sources(manifest))
                observer.refresh(store)
                revision = store.read()[0]
                follower = observer._followers["selected"]
                cursor = (follower._offset, copy.deepcopy(follower._rows))
                if mode == "replace":
                    write(path, fork_rows())
                else:
                    write(path, [dict(type="user", sessionId=SECRET,
                                      forkedFrom=dict(sessionId=BODY)), new_usage()], mode="a")
                for _ in range(2):
                    result = observer.refresh(store)
                    self.assertEqual(result["inputs"][0]["error"], MESSAGE)
                    self.assertEqual(store.read()[0], revision)
                    self.assertEqual(tokens(store), 165)
                    self.assertEqual((follower._offset, follower._rows), cursor)
                    self.assertNotIn("forkedFrom", repr(vars(follower)))
                write(path, parent_rows() + [new_usage()])
                result = observer.refresh(store)
                self.assertIsNone(result["inputs"][0]["error"])
                self.assertEqual(tokens(store), 193)

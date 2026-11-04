"""Regression tests for declared Codex lineage refusal.

Synthetic fixtures only: every file lives in a TemporaryDirectory and only
explicitly selected files are read. No subprocess, server or network use.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from monitoring_ai import adapters
from monitoring_ai.codex_follow import CodexFollower
from monitoring_ai.codex_lineage import (MESSAGE, CodexLineageError,
                                         UnsupportedHistoryError, UNQUALIFIED_MESSAGE)
from monitoring_ai.source_observer import SourceObserver
from monitoring_ai.source_readiness import preview_selection
from monitoring_ai.source_setup import check_selection
from monitoring_ai.sources import Source
from monitoring_ai.store import Store

PARENT_SECRET = "fixture-secret-parent-thread"
CHILD_ID = "fixture-child"
BODY_SECRET = "fixture-private-source-body"


def meta(session_id="fixture-parent", ts="2026-11-04T00:00:00Z", **extra):
    """Build only the session metadata needed by the accounting boundary."""
    payload = {"id": session_id}
    payload.update(extra)
    return {"type": "session_meta", "timestamp": ts, "payload": payload}


def turn(ts="2026-11-04T00:00:01Z"):
    """Supply a fictional model without conversation content."""
    return {"type": "turn_context", "timestamp": ts,
            "payload": {"model": "fixture-model"}}


def usage(inp, out, ts):
    """Supply disjoint-counter inputs at a declared source time."""
    return {"type": "event_msg", "timestamp": ts, "payload": {
        "type": "token_count",
        "info": {"total_token_usage": {
            "input_tokens": inp, "cached_input_tokens": 0,
            "output_tokens": out}}}}


def parent_rows():
    """Two usage points: 110 + 55 = 165 tokens."""
    return [meta(), turn(),
            usage(100, 10, "2026-11-04T00:00:02Z"),
            usage(150, 15, "2026-11-04T00:00:03Z")]


def extension_rows():
    """Third point taking cumulative usage to 175/18 (193 total)."""
    return [usage(175, 18, "2026-11-04T00:00:04Z")]


def declared_child_rows(form="direct", value=PARENT_SECRET, session_id=CHILD_ID):
    """Child copying parent prefix; would give 193 tokens if imported."""
    if form == "direct":
        m = meta(session_id, forked_from_id=value)
    else:
        m = meta(session_id, source={"subagent": {"thread_spawn": {
            "parent_thread_id": value}}}, instructions=BODY_SECRET)
    return [m, turn(),
            usage(100, 10, "2026-11-04T00:00:02Z"),
            usage(150, 15, "2026-11-04T00:00:03Z"),
            usage(175, 18, "2026-11-04T00:00:04Z")]


def write_jsonl(path, rows, mode="w"):
    """Write only the selected fictional records."""
    with open(path, mode, encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def total_tokens(events):
    """Sum input/cache_read/cache_write/output across events."""
    return sum(sum(event["tokens"].values()) for event in events)


def by_id(entries):
    """Find an input by its configured alias."""
    return {entry["id"]: entry for entry in entries}


class LineageFixture(unittest.TestCase):
    def setUp(self):
        """Isolate selected inputs and ledgers in disposable storage."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def assert_safe_error(self, error, *paths):
        """Error is the constant message, without any path, id or body."""
        self.assertEqual(error, MESSAGE)
        text = str(error)
        for leak in (PARENT_SECRET, CHILD_ID, BODY_SECRET, str(self.root),
                     *map(str, paths)):
            self.assertNotIn(leak, text)


class MetadataProjectionTests(LineageFixture):
    """codex_row keeps only id + lineage_declared; codex() refuses declarations."""

    def test_projection_privacy_and_declaration_forms(self):
        """Retain the constraint while discarding its supplied parent identity."""
        self.assertTrue(issubclass(CodexLineageError, ValueError))

        # Ordinary and explicit-null declarations are not lineage.
        ordinary = [meta(),
                    meta(forked_from_id=None),
                    meta(history_base=None, parent_thread_id=None,
                         subagent_history_start_ordinal=None,
                         forked_from_ordinal_exclusive=None, history_mode=None),
                    meta(history_mode="legacy"),
                    meta(source={"subagent": {"thread_spawn":
                                              {"parent_thread_id": None}}})]
        for row in ordinary:
            with self.subTest(ordinary=row["payload"]):
                projected = adapters.codex_row(copy.deepcopy(row))
                self.assertEqual(projected["payload"], {"id": "fixture-parent"})
                self.assertNotIn("source", projected["payload"])
                events = adapters.codex([row] + parent_rows()[1:],
                                        "selected", "fixture")
                self.assertEqual(total_tokens(events), 165)

        # Direct/nested, valid and malformed non-null declarations all refuse.
        declared_values = [PARENT_SECRET, "", 0, False, 42, [], {"x": BODY_SECRET}]
        for form in ("direct", "nested"):
            for value in declared_values:
                with self.subTest(form=form, value=value):
                    rows = declared_child_rows(form, value)
                    projected = adapters.codex_row(copy.deepcopy(rows[0]))
                    self.assertEqual(projected["payload"], {"id": CHILD_ID, "lineage_declared": True})
                    dumped = json.dumps(projected, default=str)
                    self.assertNotIn(PARENT_SECRET, dumped)
                    self.assertNotIn(BODY_SECRET, dumped)
                    self.assertNotIn("thread_spawn", dumped)
                    self.assertNotIn("forked_from_id", dumped)

                    # Unprojected raw rows must still refuse in codex().
                    with self.assertRaises(CodexLineageError) as ctx:
                        adapters.codex(copy.deepcopy(rows), "selected", "fixture")
                    self.assert_safe_error(str(ctx.exception))

                    # Projected rows refuse too.
                    with self.assertRaises(CodexLineageError):
                        adapters.codex([adapters.codex_row(r) for r in rows],
                                       "selected", "fixture")

    def test_load_refuses_declared_file_and_accepts_parent(self):
        """Direct imports obey the same boundary as following."""
        parent = self.root / "parent.jsonl"
        child = self.root / "child.jsonl"
        write_jsonl(parent, parent_rows())
        write_jsonl(child, declared_child_rows("nested"))
        self.assertEqual(total_tokens(adapters.load(parent, "codex", "selected", "fixture")), 165)
        with self.assertRaises(CodexLineageError) as ctx:
            adapters.load(child, "codex", "selected", "fixture")
        self.assert_safe_error(str(ctx.exception), child)


class UnqualifiedHistoryTests(LineageFixture):
    def test_projection_check_and_follower_retention(self):
        """New boundaries refuse before import, retain accepted usage, and leak no values."""
        cases = [(key, value) for key in (
            "history_base", "parent_thread_id", "subagent_history_start_ordinal",
            "forked_from_ordinal_exclusive")
            for value in (PARENT_SECRET, "", 0, False, [], {},
                          {"thread_id": PARENT_SECRET, "end_ordinal_exclusive": 2,
                           "end_byte_offset": 123})]
        cases += [("history_mode", value) for value in ("paginated", "future", "", False, {})]
        for index, (key, value) in enumerate(cases):
            with self.subTest(key=key, value=value):
                path = self.root / f"selected-{index}.jsonl"
                write_jsonl(path, parent_rows())
                follower = CodexFollower(path, "selected", "fixture")
                store = Store(self.root / f"ledger-{index}.sqlite")
                store.ingest(follower())
                follower.commit()
                revision = store.read()[0]
                cursor = (follower._offset, copy.deepcopy(follower._rows))
                row = meta(instructions=BODY_SECRET, **{key: value})
                projected = adapters.codex_row(row)
                self.assertEqual(projected["payload"],
                                 {"id": "fixture-parent", "history_unqualified": True})
                for records in ([row] + parent_rows()[1:],
                                [projected] + [adapters.codex_row(r) for r in parent_rows()[1:]]):
                    with self.assertRaises(UnsupportedHistoryError) as error:
                        adapters.codex(records, "selected", "fixture")
                    self.assertEqual(str(error.exception), UNQUALIFIED_MESSAGE)
                write_jsonl(path, [row] + extension_rows(), mode="a")
                for _ in range(2):
                    with self.assertRaises(UnsupportedHistoryError):
                        follower()
                    follower.discard()
                    self.assertEqual((follower._offset, follower._rows), cursor)
                    self.assertEqual(store.read()[0], revision)
                    self.assertEqual(total_tokens(store.read()[1]), 165)
                manifest = self.root / "selected.json"
                manifest.write_text(json.dumps(dict(schema="monitoring.sources.v1", sources=[
                    dict(id="selected", format="codex", path=str(path), project="fixture")])))
                self.assertEqual(check_selection(manifest)["checks"][0]["error"], UNQUALIFIED_MESSAGE)
                self.assertEqual(preview_selection(manifest)["inputs"][0]["error"], UNQUALIFIED_MESSAGE)
                observed = SourceObserver([Source("selected", "codex", str(path), "fixture")]).refresh(store)
                self.assertEqual(observed["inputs"][0]["error"], UNQUALIFIED_MESSAGE)
                for blob in (json.dumps(observed), repr(vars(follower)), json.dumps(projected)):
                    self.assertNotIn(PARENT_SECRET, blob)
                    self.assertNotIn(BODY_SECRET, blob)
                write_jsonl(path, parent_rows() + extension_rows())
                store.ingest(follower())
                follower.commit()
                self.assertEqual(total_tokens(store.read()[1]), 193)


class SelectionWorkflowTests(LineageFixture):
    """check -> disposable preview -> live observer, child refused, parent kept."""

    def test_check_preview_observe(self):
        """All selected-source entry points refuse the child and retain the parent."""
        parent = self.root / "parent.jsonl"
        child = self.root / "child.jsonl"
        write_jsonl(parent, parent_rows())
        write_jsonl(child, declared_child_rows("direct"))
        manifest = self.root / "sources.json"
        manifest.write_text(json.dumps({
            "schema": "monitoring.sources.v1",
            "sources": [
                {"id": "parent", "format": "codex", "path": str(parent),
                 "project": "fixture"},
                {"id": "child", "format": "codex", "path": str(child),
                 "project": "fixture"}]}), encoding="utf-8")
        originals = {p: p.read_bytes() for p in (parent, child, manifest)}

        # Source check: child flagged with the constant, path-free error.
        checked = check_selection(manifest)
        self.assertFalse(checked["all_readable"])
        checks = by_id(checked["checks"])
        self.assertFalse(checks["parent"]["error"])
        self.assertEqual((checks["parent"]["state"], checks["child"]["state"]),
                         ("readable", "invalid"))
        self.assert_safe_error(checks["child"]["error"], child, parent)

        # Preview: disposable only, no ledger created, inputs untouched.
        ledger = self.root / "ledger.jsonl"
        preview = preview_selection(manifest)
        self.assertFalse(preview["ready"])
        inputs = by_id(preview["inputs"])
        self.assertFalse(inputs["parent"]["error"])
        self.assertEqual(inputs["parent"]["proposed"]["added"], 2)
        self.assert_safe_error(inputs["child"]["error"], child, parent)
        self.assertFalse(ledger.exists())
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertNotIn(PARENT_SECRET, json.dumps(preview, default=str))

        # Live observer: valid parent continues, child refused.
        store = Store(ledger)
        observer = SourceObserver([
            Source("parent", "codex", str(parent), "fixture"),
            Source("child", "codex", str(child), "fixture")])
        first = observer.refresh(store)
        self.assertEqual(first["healthy_inputs"], 1)
        self.assertEqual(first["unavailable_inputs"], 1)
        observed = by_id(first["inputs"])
        self.assert_safe_error(observed["child"]["error"], child, parent)
        self.assertEqual(observed["child"]["added"], 0)
        revision, events = store.read()
        self.assertEqual(total_tokens(events), 165)  # not 358

        # Repeated refresh is idempotent.
        second = observer.refresh(store)
        self.assertEqual(by_id(second["inputs"])["parent"]["added"], 0)
        self.assert_safe_error(by_id(second["inputs"])["child"]["error"])
        revision2, events2 = store.read()
        self.assertEqual(revision2, revision)
        self.assertEqual(total_tokens(events2), 165)

        # Nothing private retained in results or ledger.
        ledger_text = json.dumps(store.read()[1])
        for blob in (json.dumps(first, default=str),
                     json.dumps(second, default=str), ledger_text):
            self.assertNotIn(PARENT_SECRET, blob)
            self.assertNotIn(CHILD_ID, blob)


class FollowerTransitionTests(LineageFixture):
    """Accepted file later declares lineage: atomic refusal, then recovery."""

    def cycle(self, follower, store):
        """Commit the cursor only after the corresponding ledger acceptance."""
        try:
            rows = follower()
            events = rows
        except CodexLineageError:
            follower.discard()
            raise
        store.ingest(events)
        follower.commit()
        return events

    def test_replacement_and_appended_metadata(self):
        """Rejected generations preserve accepted history and recover without double counting."""
        for mode in ("replace", "append"):
            with self.subTest(mode=mode):
                work = self.root / mode
                work.mkdir()
                path = work / "session.jsonl"
                write_jsonl(path, parent_rows())
                store = Store(work / "ledger.jsonl")
                follower = CodexFollower(path, "selected", "fixture")

                self.cycle(follower, store)
                revision, events = store.read()
                self.assertEqual(total_tokens(events), 165)
                cursor = (follower._offset,
                          copy.deepcopy(follower._rows))

                if mode == "replace":
                    write_jsonl(path, declared_child_rows("nested",
                                                         session_id="fixture-parent"))
                else:
                    write_jsonl(path, [meta(ts="2026-11-04T00:00:05Z",
                                            forked_from_id=PARENT_SECRET)]
                                + extension_rows(), mode="a")

                for _ in range(2):  # refusal is stable, not a one-shot
                    with self.assertRaises(CodexLineageError) as ctx:
                        self.cycle(follower, store)
                    self.assert_safe_error(str(ctx.exception), path)
                    self.assertEqual(store.read()[0], revision)
                    self.assertEqual(total_tokens(store.read()[1]), 165)
                    self.assertEqual((follower._offset,
                                      follower._rows), cursor)
                    retained = repr(vars(follower))
                    self.assertNotIn(PARENT_SECRET, retained)
                    self.assertNotIn(BODY_SECRET, retained)

                # Genuine independent file restored and extended.
                write_jsonl(path, parent_rows() + extension_rows())
                self.cycle(follower, store)
                self.assertEqual(total_tokens(store.read()[1]), 193)


class HonestLimitationTests(LineageFixture):
    """No invented fork detection; counter decreases still refuse."""

    def test_counter_decrease_refuses(self):
        """The lineage guard must not hide the existing reset refusal."""
        path = self.root / "decrease.jsonl"
        write_jsonl(path, [meta(), turn(),
                           usage(150, 15, "2026-11-04T00:00:02Z"),
                           usage(100, 10, "2026-11-04T00:00:03Z")])
        with self.assertRaises(ValueError) as ctx:
            adapters.load(path, "codex", "selected", "fixture")
        self.assertNotIsInstance(ctx.exception, CodexLineageError)
        self.assertNotEqual(str(ctx.exception), MESSAGE)

    def test_undeclared_copied_prefix_is_not_detected(self):
        """Without a declaration both files import: 358, not distinct 193."""
        parent = self.root / "parent.jsonl"
        copied = self.root / "copied.jsonl"
        write_jsonl(parent, parent_rows())
        write_jsonl(copied, declared_child_rows("direct", value=None))
        store = Store(self.root / "ledger.jsonl")
        result = SourceObserver([
            Source("parent", "codex", str(parent), "fixture"),
            Source("copied", "codex", str(copied), "fixture")]).refresh(store)
        self.assertEqual(result["healthy_inputs"], 2)
        self.assertEqual(result["unavailable_inputs"], 0)
        self.assertEqual(total_tokens(store.read()[1]), 358)


if __name__ == "__main__":
    unittest.main()

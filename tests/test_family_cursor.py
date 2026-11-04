"""Workflow tests for family_cursor (unittest, stdlib only)."""

import copy
import unittest

from monitoring_ai.family_cursor import ERROR_MESSAGE, MAX_EVENTS, FamilyCursor


def ev(source, id_, ts, inp, out, **extra):
    """Build a fictional normalized usage event."""
    row = {"source": source, "id": id_, "timestamp": ts,
           "tokens": {"input": inp, "output": out}}
    row.update(extra)
    return row


def base():
    """Return the three already-written events (total 193 tokens)."""
    return [ev("parent", "p1", "2026-11-04T00:01:00Z", 100, 10),
            ev("parent", "p2", "2026-11-04T00:02:00Z", 50, 5),
            ev("child", "c1", "2026-11-04T00:03:00Z", 25, 3)]


def new_rows():
    """Return the two events added by the next snapshot."""
    return [ev("parent", "p3", "2026-11-04T00:05:00Z", 25, 3),
            ev("child", "c2", "2026-11-04T00:06:00Z", 25, 2)]


def total(rows):
    """Sum input+output tokens."""
    return sum(r["tokens"]["input"] + r["tokens"]["output"] for r in rows)


class Base(unittest.TestCase):
    """Shared assertion for the constant content-free error."""

    def refuses(self, fn, *args):
        """Assert fn raises ValueError with exactly ERROR_MESSAGE."""
        with self.assertRaises(ValueError) as cm:
            fn(*args)
        self.assertEqual(str(cm.exception), ERROR_MESSAGE)


class StageCommitWorkflow(Base):
    """Normal poll cycle: stage, write, commit, idle, reorder."""

    def test_poll_commit_idle_and_aliases(self):
        """Exercise the complete stagecommit boundary."""
        # Stage commit idle reorder
        cur = FamilyCursor(base())
        self.assertEqual(cur.count, 3)
        self.assertEqual(total(base()), 193)
        snap = base() + new_rows()
        got = cur.stage(snap)
        self.assertEqual([r["id"] for r in got], ["p3", "c2"])
        self.assertEqual(total(got), 55)
        self.assertEqual(cur.count, 3)          # not advanced by stage
        cur.commit()
        self.assertEqual(cur.count, 5)
        self.assertEqual(cur.stage(snap), [])   # idle poll
        cur.commit()
        self.assertEqual(cur.count, 5)
        self.assertEqual(cur.stage(list(reversed(snap))), [])  # reordered

        # New rows keep supplied order
        cur = FamilyCursor(base())
        nr = new_rows()
        got = cur.stage([nr[1]] + base() + [nr[0]])
        self.assertEqual([r["id"] for r in got], ["c2", "p3"])

        # Alias identity distinct
        a = ev("parent", "x", "2026-11-04T00:01:00Z", 1, 1)
        b = ev("child", "x", "2026-11-04T00:01:00Z", 2, 2)
        c = ev("a", "b:c", "2026-11-04T00:01:00Z", 1, 1)
        d = ev("a:b", "c", "2026-11-04T00:01:00Z", 1, 1)
        cur = FamilyCursor([a])
        self.assertEqual(len(cur.stage([a, b, c, d])), 3)
        cur.commit()
        self.assertEqual(cur.count, 4)



class RefusalWorkflow(Base):
    """History changes, malformed input, duplicates and bounds refuse."""

    def test_rejected_snapshots_preserve_history(self):
        """Exercise the complete refusal boundary."""
        # Changed or missing history
        cur = FamilyCursor(base())
        for mutate in (
            lambda s: s[0]["tokens"].update(input=101),
            lambda s: s[0].update(timestamp="2026-11-04T00:01:01Z"),
            lambda s: s[1].update(model="other"),
            lambda s: s[1].update(extra={"k": 1}),
            lambda s: s.pop(2),
        ):
            snap = base() + new_rows()
            mutate(snap)
            self.refuses(cur.stage, snap)
            self.refuses(cur.commit)            # failed stage left no pending
        self.assertEqual(cur.count, 3)

        # Failed stage clears previous pending
        cur = FamilyCursor(base())
        cur.stage(base() + new_rows())
        self.refuses(cur.stage, base()[:2])
        self.refuses(cur.commit)
        self.assertEqual(cur.count, 3)

        # Duplicates
        row = base()[0]
        self.refuses(FamilyCursor, [row, copy.deepcopy(row)])
        cur = FamilyCursor(base())
        self.refuses(cur.stage, base() + [base()[0]])
        n = new_rows()[0]
        self.refuses(cur.stage, base() + [n, copy.deepcopy(n)])

        # Malformed rows
        cur = FamilyCursor()
        good = base()[0]
        bad = [
            "x", None, [],
            {k: v for k, v in good.items() if k != "id"},
            dict(good, source=""), dict(good, id=5),
            dict(good, timestamp=None),
            dict(good, tokens={"input": float("nan")}),
            dict(good, tokens={1: 2}),
            dict(good, extra=object()),
            dict(good, extra=(1, 2)),
        ]
        for row in bad:
            self.refuses(cur.stage, [row])
        self.refuses(FamilyCursor, [bad[3]])

        # Event bound
        def rows(n):
            return [{"source": "s", "id": str(i), "timestamp": "t"}
                    for i in range(n)]
        cur = FamilyCursor()
        self.assertEqual(len(cur.stage(rows(MAX_EVENTS))), MAX_EVENTS)
        self.refuses(cur.stage, rows(MAX_EVENTS + 1))
        self.refuses(FamilyCursor, rows(MAX_EVENTS + 1))



class RecoveryWorkflow(Base):
    """Discard, commit misuse, restart seeding and rotated-file restore."""

    def test_discard_restart_and_rotation(self):
        """Exercise the complete recovery boundary."""
        # Commit without stage and discard
        cur = FamilyCursor(base())
        self.refuses(cur.commit)
        cur.stage(base() + new_rows())
        cur.discard()
        self.refuses(cur.commit)
        self.assertEqual(cur.count, 3)
        got = cur.stage(base() + new_rows())   # recovery: same proposal again
        self.assertEqual(len(got), 2)
        cur.commit()
        self.refuses(cur.commit)               # pending consumed
        self.assertEqual(cur.count, 5)

        # Restart seed and resume
        written = base() + new_rows()[:1]       # partial prior output, validated
        cur = FamilyCursor(written)
        self.assertEqual(cur.count, 4)
        self.refuses(cur.stage, base())         # source lost old row: refuse
        got = cur.stage(base() + new_rows())
        self.assertEqual([r["id"] for r in got], ["c2"])
        cur.commit()
        self.assertEqual(cur.count, 5)

        # Restored rotated file
        cur = FamilyCursor(base())
        cur.stage(base() + new_rows())
        cur.commit()
        self.refuses(cur.stage, new_rows())     # rotated: old rows missing
        self.assertEqual(cur.stage(base() + new_rows()), [])  # restored



class MutationWorkflow(Base):
    """Caller mutations never reach cursor state, and vice versa."""

    def test_snapshots_are_isolated_from_callers(self):
        """Exercise the complete mutation boundary."""
        # Input and output isolation
        seed = base()
        cur = FamilyCursor(seed)
        seed[0]["tokens"]["input"] = 999
        snap = base() + new_rows()
        got = cur.stage(snap)
        snap[3]["tokens"]["output"] = 999       # mutate after stage
        got[0]["tokens"]["input"] = 12345       # mutate returned row
        got[1]["extra"] = "x"
        cur.commit()
        self.assertEqual(cur.stage(base() + new_rows()), [])
        again = FamilyCursor(base())
        first = again.stage(base() + new_rows())
        first[0]["tokens"]["input"] = 0
        second = again.stage(base() + new_rows())
        self.assertEqual(second[0]["tokens"]["input"], 25)

        # Stage does not mutate input
        snap = base() + new_rows()
        before = copy.deepcopy(snap)
        FamilyCursor(base()).stage(snap)
        self.assertEqual(snap, before)



if __name__ == "__main__":
    unittest.main()

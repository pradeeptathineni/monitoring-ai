"""Focused regression tests for codex_replay.prefix_lengths (fictional data only)."""

import copy
import unittest

from monitoring_ai.codex_replay import ERROR_MESSAGE, MAX_SESSIONS, MAX_USAGE_POINTS, prefix_lengths


# ---- Fixture helpers: the fictional family from PROJECT.md ----

def t(minute):
    """Canonical UTC time on the fictional day."""
    return "2026-11-04T00:%02d:00.000Z" % minute


def pt(minute, inp, out, cache=0):
    """One cumulative usage point."""
    return {"at": t(minute), "total": (inp, cache, out)}


def family(parent_later=False):
    """Parent, child forked at 00:03, grandchild forked at 00:06."""
    parent = [pt(1, 100, 10), pt(2, 150, 15)] + ([pt(5, 175, 18)] if parent_later else [])
    child = [pt(3, 100, 10), pt(3, 150, 15), pt(4, 175, 18)]
    grand = copy.deepcopy(child) + [pt(7, 200, 20)]
    return [
        {"session": "h-p", "parent": None, "fork_at": None, "usage": parent},
        {"session": "h-c", "parent": "h-p", "fork_at": t(3), "usage": child},
        {"session": "h-g", "parent": "h-c", "fork_at": t(6), "usage": grand},
    ]


def novel_units(usage, omitted):
    """Input+output growth after the omitted prefix, computed independently."""
    if omitted >= len(usage):
        return 0
    base = usage[omitted - 1]["total"] if omitted else (0, 0, 0)
    last = usage[-1]["total"]
    return (last[0] - base[0]) + (last[2] - base[2])


def family_units(sessions, lengths):
    """Count only distinct growth while keeping every selected branch."""
    return sum(novel_units(s["usage"], lengths[s["session"]]) for s in sessions)


class PrefixLengthsTest(unittest.TestCase):
    # Main case: correct indices, totals counted once, no mutation, order independent.
    def test_family_reconciliation(self):
        sessions = family()
        before = copy.deepcopy(sessions)
        lengths = prefix_lengths(sessions)
        self.assertEqual(lengths, {"h-p": 0, "h-c": 2, "h-g": 3})
        self.assertEqual(sessions, before)
        self.assertEqual(prefix_lengths(list(reversed(sessions))), lengths)
        self.assertEqual(family_units(sessions[:2], lengths), 165 + 28)
        self.assertEqual(family_units(sessions[:2], {"h-p": 0, "h-c": 0}), 358)
        self.assertEqual(family_units(sessions, lengths), 220)

        # The parent's later point equals the child's new counter. The child's point stays new.
        later = family(parent_later=True)
        lengths = prefix_lengths(later)
        self.assertEqual(lengths, {"h-p": 0, "h-c": 2, "h-g": 3})
        self.assertEqual(novel_units(later[1]["usage"], lengths["h-c"]), 28)
        self.assertEqual(family_units(later, lengths), 248)

        # A child with no new usage is valid only when it holds the complete prefix.
        idle = family()[:2]
        idle[1]["usage"] = idle[1]["usage"][:2]
        self.assertEqual(prefix_lengths(idle), {"h-p": 0, "h-c": 2})

    # Limits: exactly at the maximum is accepted.
    def test_limits_accepted(self):
        roots = [{"session": "h-%d" % i, "parent": None, "fork_at": None,
                  "usage": [pt(1, 1, 0)]} for i in range(MAX_SESSIONS - 1)]
        big = [{"at": t(1), "total": (i, 0, 0)}
               for i in range(1, MAX_USAGE_POINTS - len(roots) + 1)]
        roots.append({"session": "h-big", "parent": None, "fork_at": None, "usage": big})
        self.assertEqual(set(prefix_lengths(roots).values()), {0})

    # Refusals: each case changes the valid family once and must raise the constant message.
    def test_refusals(self):
        """Unproven histories refuse without revealing any supplied identity."""
        def by(s, name):
            """Choose one fictional session for a focused mutation."""
            return next(d for d in s if d["session"] == name)

        def usage(s, name):
            """Choose one fictional cumulative stream."""
            return by(s, name)["usage"]

        cases = {
            "missing parent": lambda s: by(s, "h-c").update(parent="h-absent"),
            "duplicate": lambda s: s.append(copy.deepcopy(s[0])),
            "cycle": lambda s: by(s, "h-p").update(parent="h-g", fork_at=t(8)),
            "self parent": lambda s: by(s, "h-c").update(parent="h-c"),
            "exact fork tie": lambda s: usage(s, "h-p").append(pt(3, 160, 16)),
            "incomplete prefix": lambda s: by(s, "h-c").update(usage=[pt(3, 100, 10)]),
            "skipped prefix point": lambda s: usage(s, "h-c").pop(0),
            "mismatch": lambda s: usage(s, "h-c").__setitem__(0, pt(3, 100, 11)),
            "counter decrease": lambda s: usage(s, "h-c").__setitem__(2, pt(4, 140, 18)),
            "repeated vector": lambda s: usage(s, "h-p").append(pt(2, 150, 15)),
            "time decrease": lambda s: usage(s, "h-p").__setitem__(0, pt(9, 100, 10)),
            "null fork": lambda s: by(s, "h-c").update(fork_at=None),
            "root with fork": lambda s: by(s, "h-p").update(fork_at=t(0)),
            "no eligible points": lambda s: by(s, "h-c").update(fork_at=t(0)),
            "novel at fork": lambda s: usage(s, "h-c").__setitem__(2, pt(3, 175, 18)),
            "cache overcount": lambda s: usage(s, "h-p").__setitem__(0, pt(1, 100, 10, cache=101)),
            "noncanonical time": lambda s: by(s, "h-c").update(fork_at="2026-11-04T00:03:00Z"),
            "impossible date": lambda s: by(s, "h-c").update(fork_at="2026-02-30T00:03:00.000Z"),
            "bool counter": lambda s: usage(s, "h-p")[0].update(total=(100, 0, True)),
            "float counter": lambda s: usage(s, "h-p")[0].update(total=(100.0, 0, 10)),
            "negative counter": lambda s: usage(s, "h-p")[0].update(total=(100, 0, -1)),
            "short total": lambda s: usage(s, "h-p")[0].update(total=(100, 0)),
            "extra key": lambda s: s[0].update(model="x"),
            "empty session": lambda s: s[0].update(session=""),
            "too many sessions": lambda s: s.extend(
                {"session": "h-x%d" % i, "parent": None, "fork_at": None, "usage": []}
                for i in range(MAX_SESSIONS)),
            "too many points": lambda s: s[0].update(usage=[
                {"at": t(1), "total": (i, 0, 0)} for i in range(1, MAX_USAGE_POINTS + 2)]),
        }
        for label, mutate in cases.items():
            sessions = family()
            mutate(sessions)
            with self.subTest(label):
                with self.assertRaises(ValueError) as ctx:
                    prefix_lengths(sessions)
                self.assertEqual(str(ctx.exception), ERROR_MESSAGE)
                self.assertIsNone(ctx.exception.__cause__)
                self.assertTrue(ctx.exception.__suppress_context__)

        # Copied original timestamps cannot make a descendant older than its parent
        impossible = family()
        impossible[1]["usage"][0]["at"] = t(1)
        impossible[1]["usage"][1]["at"] = t(2)
        impossible[2]["fork_at"] = "2026-11-04T00:02:30.000Z"
        impossible[2]["usage"] = [pt(1, 100, 10), pt(2, 150, 15), pt(4, 175, 18)]
        with self.assertRaisesRegex(ValueError, ERROR_MESSAGE):
            prefix_lengths(impossible)


if __name__ == "__main__":
    unittest.main()

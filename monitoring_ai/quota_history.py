"""Retain bounded alias-only quota observations independently of the usage ledger."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading

from .events import label, timestamp
from .insights import QUOTA_SCHEMA, validate_quota_book
from .metrics import now_iso, seconds

STORE_SCHEMA = "monitoring.quota-history-store.v1"
FIELDS = {"source", "provider", "account", "provenance", "captured_at", "id", "label", "used_percent", "window_minutes", "resets_at"}


class QuotaHistoryFull(ValueError):
    """Stop recording without deleting earlier observations."""
    pass


def window_key(row):
    """Keep account aliases, named windows and source provenance separate."""
    scope = [row[key] for key in ("source", "provider", "account", "provenance", "id", "window_minutes")]
    return hashlib.sha256(json.dumps(scope, separators=(",", ":")).encode()).hexdigest()[:24]


def samples(book):
    """Project validated snapshots into exact window observations."""
    for snapshot in validate_quota_book(book)["snapshots"]:
        common = {key: value for key, value in snapshot.items() if key != "windows"}
        for window in snapshot["windows"]:
            yield dict(common, **window)


def _sample(payload):
    """Validate stored metadata again before exposing it to a reader."""
    value = json.loads(payload)
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("Unsupported stored quota observation")
    book = {"schema": QUOTA_SCHEMA, "snapshots": [{
        **{key: value[key] for key in ("source", "provider", "account", "provenance", "captured_at")},
        "windows": [{key: value[key] for key in ("id", "label", "used_percent", "window_minutes", "resets_at")}],
    }]}
    return next(samples(book))


class QuotaHistory:
    """Store bounded source observations without modifying usage accounting."""
    def __init__(self, path, *, read_only=False, max_samples=10000, max_series=100):
        """Require an owned private file and refuse incompatible existing stores."""
        if type(max_samples) is not int or not 1 <= max_samples <= 10000 or type(max_series) is not int or not 1 <= max_series <= 100:
            raise ValueError("Quota history capacity must be 1–10000 observations and 1–100 series")
        self.path = Path(path).absolute()
        self.read_only, self.max_samples, self.max_series = read_only, max_samples, max_series
        self.lock, self.seen, self.gaps = threading.Lock(), set(), set()
        created = False
        if not self.path.exists():
            if read_only:
                raise ValueError("Select an existing quota history file")
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd); created = True
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) not in ({0o400, 0o600} if read_only else {0o600}):
            raise ValueError("Quota history needs an owned private regular file")
        with closing(self._connect()) as db:
            if created:
                db.executescript("""
                    CREATE TABLE metadata (schema TEXT NOT NULL);
                    CREATE TABLE samples (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        series TEXT NOT NULL, captured_at TEXT NOT NULL,
                        recorded_at TEXT NOT NULL, owner TEXT NOT NULL,
                        gap INTEGER NOT NULL CHECK(gap IN (0,1)), payload TEXT NOT NULL,
                        UNIQUE(series,captured_at));
                    CREATE INDEX samples_by_source_time ON samples(series,captured_at);
                """)
                db.execute("INSERT INTO metadata VALUES (?)", (STORE_SCHEMA,)); db.commit()
            try:
                if db.execute("SELECT schema FROM metadata").fetchall() != [(STORE_SCHEMA,)]:
                    raise ValueError("Unsupported quota history store")
                db.execute("SELECT sequence,series,captured_at,recorded_at,owner,gap,payload FROM samples LIMIT 0")
            except sqlite3.Error:
                raise ValueError("Select a compatible quota history store") from None

    def _connect(self):
        """Never recreate a removed store during a later read or write."""
        return sqlite3.connect(self.path.as_uri() + ("?mode=ro" if self.read_only else "?mode=rw"), uri=True, timeout=2)

    def mark_gap(self):
        """Break each observed series after a failed collection or history write."""
        with self.lock:
            self.gaps.update(self.seen)

    def record(self, book, owner):
        """Commit a whole validated book, preserving replays and refusing conflicts."""
        if self.read_only:
            raise ValueError("Quota history is read-only")
        owner = label(owner, "collector identity")
        values = list(samples(book))
        with self.lock, closing(self._connect()) as db:
            current_keys = {window_key(row) for row in values}
            self.gaps.update(self.seen - current_keys)
            db.execute("BEGIN IMMEDIATE")
            additions = []
            known = {row[0] for row in db.execute("SELECT DISTINCT series FROM samples")}
            for row in values:
                key = window_key(row)
                payload = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
                previous = db.execute("SELECT payload FROM samples WHERE series=? AND captured_at=?", (key, row["captured_at"])).fetchone()
                if previous is not None:
                    if previous[0] != payload:
                        raise ValueError("Conflicting quota observation at the same source time")
                    continue
                additions.append((key, row["captured_at"], now_iso(), owner, int(key in self.gaps), payload))
            count = db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            if count + len(additions) > self.max_samples or len(known | {row[0] for row in additions}) > self.max_series:
                raise QuotaHistoryFull("Quota history capacity reached. Existing observations are retained")
            db.executemany("INSERT INTO samples(series,captured_at,recorded_at,owner,gap,payload) VALUES(?,?,?,?,?,?)", additions)
            db.commit()
            inserted = {row[0] for row in additions}
            self.seen.update(current_keys)
            self.gaps.difference_update(inserted)
            return len(additions)

    def summary(self):
        """Expose history capacity independently of the event ledger revision."""
        with self.lock, closing(self._connect()) as db:
            count, revision, series = db.execute("SELECT COUNT(*),COALESCE(MAX(sequence),0),COUNT(DISTINCT series) FROM samples").fetchone()
        return dict(schema="monitoring.quota-history-status.v1", observations=count, revision=revision,
                    series_count=series, capacity=self.max_samples, series_capacity=self.max_series, read_only=self.read_only)

    def view(self, series=None, *, limit=200, before=None, expected_revision=None, now=None):
        """Page dated source observations and preserve discontinuities in the chart."""
        if type(limit) is not int or not 1 <= limit <= 200 or (series is not None and not re.fullmatch(r"[0-9a-f]{24}", series)):
            raise ValueError("Select a known quota series and limit 1–200")
        before = timestamp(before) if before is not None else None
        observed = timestamp(now or now_iso())
        with self.lock, closing(self._connect()) as db:
            db.execute("BEGIN")
            revision = db.execute("SELECT COALESCE(MAX(sequence),0) FROM samples").fetchone()[0]
            if expected_revision is not None and expected_revision != revision:
                raise ValueError("Quota history changed. Reload the latest observations")
            grouped = db.execute("SELECT series,COUNT(*),MIN(captured_at),MAX(captured_at) FROM samples GROUP BY series ORDER BY MAX(captured_at) DESC,series").fetchall()
            if len(grouped) > self.max_series:
                raise ValueError("Quota history exceeds series capacity")
            index = []
            for key, count, first, last in grouped:
                row = _sample(db.execute("SELECT payload FROM samples WHERE series=? ORDER BY captured_at DESC LIMIT 1", (key,)).fetchone()[0])
                if window_key(row) != key:
                    raise ValueError("Stored quota scope is inconsistent")
                index.append(dict(key=key, **{name: row[name] for name in ("source", "provider", "account", "provenance", "id", "label", "window_minutes")},
                                  observations=count, first_captured_at=first, last_captured_at=last))
            selected = series or (index[0]["key"] if index else None)
            if series is not None and series not in {row["key"] for row in index}:
                raise ValueError("Quota series is not present in this history")
            suffix, args = (" AND captured_at<?", (selected, before)) if before else ("", (selected,))
            records = db.execute("SELECT payload,recorded_at,owner,gap FROM samples WHERE series=?" + suffix + " ORDER BY captured_at DESC LIMIT ?", (*args, limit + 1)).fetchall() if selected else []
        more, records = len(records) > limit, list(reversed(records[:limit]))
        points, previous, segment = [], None, 0
        for payload, recorded, owner, gap in records:
            row = _sample(payload)
            if window_key(row) != selected:
                raise ValueError("Stored quota scope is inconsistent")
            reasons = []
            if previous is not None:
                old, old_owner = previous
                if gap: reasons.append("collection-gap")
                if owner != old_owner: reasons.append("collector-changed")
                if seconds(row["captured_at"]) - seconds(old["captured_at"]) > 180: reasons.append("observation-gap")
                if row["resets_at"] != old["resets_at"]: reasons.append("reset-time-changed")
                if row["used_percent"] is None or old["used_percent"] is None: reasons.append("percentage-unavailable")
                elif row["used_percent"] < old["used_percent"]: reasons.append("reported-usage-decreased")
            future = row["captured_at"] > observed
            if future: reasons.append("future-source-time")
            if reasons: segment += 1
            points.append(dict(captured_at=row["captured_at"], recorded_at=timestamp(recorded), used_percent=row["used_percent"],
                               remaining_percent=None if row["used_percent"] is None else 100 - row["used_percent"],
                               resets_at=row["resets_at"], segment=segment, break_reasons=reasons, future=future))
            previous = (row, owner)
        return dict(schema="monitoring.quota-history.v1", revision=revision, observed_at=observed,
                    scope="explicit-recorded-quota-observations", complete_coverage=False, series=index, selected=selected,
                    points=points, before=before, next_before=points[0]["captured_at"] if more else None)

"""Keep immutable, source-scoped events with atomic import and replay deduplication."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .events import validate
from .metrics import now_iso
from .source_overlap import NativeOverlapError, MESSAGE


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        except FileExistsError:
            if self.path.is_symlink() or not self.path.is_file():
                raise ValueError("Select a regular ledger file")
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE(source, event_id)
                );
                CREATE INDEX IF NOT EXISTS events_time ON events(timestamp);
                CREATE INDEX IF NOT EXISTS events_identity ON events(event_id);
                CREATE TABLE IF NOT EXISTS native_usage_versions (
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    record_at TEXT NOT NULL,
                    tokens TEXT NOT NULL,
                    PRIMARY KEY(source, event_id, record_at)
                );
                CREATE TABLE IF NOT EXISTS corrections (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    source_record_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    prior_payload TEXT NOT NULL,
                    next_payload TEXT NOT NULL,
                    ledger_revision INTEGER NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _claude_usage(event):
        return (event["kind"] == "usage" and event["provider"] == "anthropic"
                and event["surface"] == "claude-code" and "cost" not in event)

    @staticmethod
    def _without_usage_and_time(event):
        return {key: value for key, value in event.items() if key not in ("tokens", "timestamp")}

    def _reconcile_claude(self, db, event, existing):
        """Return (outcome, current event) under the caller's atomic transaction."""
        source, event_id, record_at = event["source"], event["id"], event["timestamp"]
        tokens = json.dumps(event["tokens"], sort_keys=True, separators=(",", ":"))
        if existing is None:
            payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
            db.execute("INSERT INTO events(source,event_id,timestamp,payload) VALUES(?,?,?,?)",
                       (source, event_id, record_at, payload))
            db.execute("INSERT INTO native_usage_versions(source,event_id,record_at,tokens) VALUES(?,?,?,?)",
                       (source, event_id, record_at, tokens))
            return "added", event

        current = json.loads(existing)
        if not self._claude_usage(current) or self._without_usage_and_time(current) != self._without_usage_and_time(event):
            raise ValueError("An existing Claude message identity changed outside token usage")
        version = db.execute(
            "SELECT tokens FROM native_usage_versions WHERE source=? AND event_id=? AND record_at=?",
            (source, event_id, record_at),
        ).fetchone()
        if version is None:
            # Upgrade an existing v0.8.7 ledger using its first accepted time
            # as the known baseline. Later historical copies are learned on
            # replay, without replacing its original timestamp.
            known = db.execute(
                "SELECT COUNT(*) FROM native_usage_versions WHERE source=? AND event_id=?",
                (source, event_id),
            ).fetchone()[0]
            if not known:
                baseline = json.dumps(current["tokens"], sort_keys=True, separators=(",", ":"))
                db.execute("INSERT INTO native_usage_versions(source,event_id,record_at,tokens) VALUES(?,?,?,?)",
                           (source, event_id, current["timestamp"], baseline))
                if record_at == current["timestamp"]:
                    version = (baseline,)
        if version is not None:
            if version[0] != tokens:
                raise ValueError("A Claude message timestamp has conflicting usage")
            return "replayed", current

        latest = db.execute(
            "SELECT MAX(record_at) FROM native_usage_versions WHERE source=? AND event_id=?",
            (source, event_id),
        ).fetchone()[0]
        if latest is not None and record_at <= latest:
            raise ValueError("An unrecognized older Claude message version cannot replace accepted usage")
        db.execute("INSERT INTO native_usage_versions(source,event_id,record_at,tokens) VALUES(?,?,?,?)",
                   (source, event_id, record_at, tokens))
        if current["tokens"] == event["tokens"]:
            return "replayed", current

        corrected = dict(current, tokens=event["tokens"])
        prior_payload = json.dumps(current, sort_keys=True, separators=(",", ":"))
        next_payload = json.dumps(corrected, sort_keys=True, separators=(",", ":"))
        db.execute("UPDATE events SET payload=? WHERE source=? AND event_id=?",
                   (next_payload, source, event_id))
        revision = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
        revision += db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] + 1
        db.execute("""INSERT INTO corrections(source,event_id,source_record_at,observed_at,
                      prior_payload,next_payload,ledger_revision) VALUES(?,?,?,?,?,?,?)""",
                   (source, event_id, record_at, now_iso(), prior_payload, next_payload, revision))
        return "corrected", corrected

    def ingest(self, rows, *, claude_timestamp_replay=False, claude_corrections=False,
               accepted_rows=False, overlap_sources=()):
        events = [validate(row) for row in rows]
        if len(events) > 50000:
            raise ValueError("Import exceeds 50,000 events")
        added = 0
        corrected = 0
        accepted = {} if accepted_rows else None
        overlap_sources = tuple(sorted(set(overlap_sources)))
        with self.connect() as db:
            for event in events:
                if overlap_sources:
                    # Compare only other explicitly selected sources of the same
                    # native format. A source-scoped ID alone is not enough;
                    # session and kind must agree as well. The whole batch rolls
                    # back if one event overlaps, including earlier additions.
                    placeholders = ",".join("?" for _ in overlap_sources)
                    for (other_payload,) in db.execute(
                        f"SELECT payload FROM events WHERE event_id=? AND source IN ({placeholders})",
                        (event["id"], *overlap_sources),
                    ):
                        other = json.loads(other_payload)
                        if other["session"] == event["session"] and other["kind"] == event["kind"]:
                            raise NativeOverlapError(MESSAGE)
                payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
                existing = db.execute(
                    "SELECT payload FROM events WHERE source=? AND event_id=?",
                    (event["source"], event["id"]),
                ).fetchone()
                if claude_corrections and self._claude_usage(event):
                    outcome, canonical = self._reconcile_claude(db, event, existing[0] if existing else None)
                    added += outcome == "added"
                    corrected += outcome == "corrected"
                    if accepted_rows:
                        accepted[(event["source"], event["id"])] = canonical
                    continue
                if existing:
                    previous = json.loads(existing[0]) if accepted_rows else None
                    if existing[0] != payload:
                        # A rotated Claude file can retain only a later copy of
                        # the same message. Preserve the accepted source time;
                        # never apply this exception to changed usage or other
                        # producer formats.
                        previous = previous or json.loads(existing[0])
                        same_usage = (claude_timestamp_replay and event["kind"] == "usage"
                                      and event["provider"] == "anthropic"
                                      and event["surface"] == "claude-code"
                                      and {k: v for k, v in previous.items() if k != "timestamp"}
                                      == {k: v for k, v in event.items() if k != "timestamp"})
                        if not same_usage:
                            raise ValueError("An existing event identity has different content")
                    if accepted_rows:
                        accepted[(event["source"], event["id"])] = previous
                    continue
                db.execute(
                    "INSERT INTO events(source,event_id,timestamp,payload) VALUES(?,?,?,?)",
                    (event["source"], event["id"], event["timestamp"], payload),
                )
                added += 1
                if accepted_rows:
                    accepted[(event["source"], event["id"])] = event
            self._check_hierarchy(db)
        result = {"added": added, "replayed": len(events) - added}
        if claude_corrections:
            result["corrected"] = corrected
        if accepted_rows:
            result["rows"] = list(accepted.values())
        return result

    def _check_hierarchy(self, db):
        # Out-of-order parents are allowed, but entity identity and ancestry cannot change
        entities = {}
        for (payload,) in db.execute("SELECT payload FROM events"):
            event = json.loads(payload)
            key = (event["source"], event["session"], event["entity"])
            identity = (event["parent"], event["entity_type"], event["project"])
            if key in entities and entities[key] != identity:
                raise ValueError("Entity parent, type or project changed")
            entities[key] = identity
        checked = set()
        for key in entities:
            seen = set()
            cursor = key
            while cursor in entities and cursor not in checked:
                if cursor in seen:
                    raise ValueError("Entity hierarchy contains a cycle")
                seen.add(cursor)
                parent = entities[cursor][0]
                cursor = (key[0], key[1], parent)
            checked.update(seen)

    def read(self):
        with self.connect() as db:
            # Independent input writers may commit while a dashboard is reading.
            # Pin revision and rows to the same SQLite snapshot in WAL mode.
            db.execute("BEGIN")
            revision = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
            revision += db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
            rows = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM events ORDER BY timestamp, source, event_id LIMIT 100001"
            )]
        if len(rows) > 100000:
            raise ValueError("Ledger exceeds this alpha's 100,000-event read limit")
        return revision, rows


def stable_id(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:32]

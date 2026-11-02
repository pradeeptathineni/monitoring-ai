"""Keep immutable, source-scoped events with atomic import and replay deduplication."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .events import validate


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
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, rows):
        events = [validate(row) for row in rows]
        if len(events) > 50000:
            raise ValueError("Import exceeds 50,000 events")
        added = 0
        with self.connect() as db:
            for event in events:
                payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
                existing = db.execute(
                    "SELECT payload FROM events WHERE source=? AND event_id=?",
                    (event["source"], event["id"]),
                ).fetchone()
                if existing:
                    if existing[0] != payload:
                        raise ValueError("An existing event identity has different content")
                    continue
                db.execute(
                    "INSERT INTO events(source,event_id,timestamp,payload) VALUES(?,?,?,?)",
                    (event["source"], event["id"], event["timestamp"], payload),
                )
                added += 1
            self._check_hierarchy(db)
        return {"added": added, "replayed": len(events) - added}

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
            rows = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM events ORDER BY timestamp, source, event_id LIMIT 100001"
            )]
        if len(rows) > 100000:
            raise ValueError("Ledger exceeds this alpha's 100,000-event read limit")
        return revision, rows


def stable_id(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:32]

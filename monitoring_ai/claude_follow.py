"""Incrementally follow one explicitly selected native JSONL file.

Only complete lines are committed. The caller commits this in-memory cursor
after the corresponding ledger batch commits, so rejected batches are retried.
"""

import hashlib
import os
from pathlib import Path
import stat

from .adapters import MAX_BYTES, claude, claude_row, parse_jsonl
from .claude_responses import response_spans


class NativeFollower:
    correct_usage = False
    supports_responses = False

    def __init__(self, path, source, project):
        if not source or not project:
            raise ValueError("Native following requires source and project aliases")
        self.path = Path(path)
        self.source, self.project = source, project
        self._identity = None
        self._offset = 0
        self._modified = None
        self._tail_hash = None
        self._rows = []
        self._events = []
        self._pending = None

    @staticmethod
    def _hash_tail(stream, offset):
        if not offset:
            return None
        stream.seek(max(0, offset - 4096))
        return hashlib.sha256(stream.read(min(offset, 4096))).digest()

    def __call__(self):
        self._pending = None
        selected = self.path.absolute()
        if selected.is_symlink():
            raise ValueError("Select a regular source file, not a symlink")
        target = selected.parent.resolve() / selected.name
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(target, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise ValueError("Source must be a regular file of at most 32 MiB")
            identity = info.st_dev, info.st_ino
            reset = (identity != self._identity or info.st_size < self._offset
                     or (info.st_size == self._offset and info.st_mtime_ns != self._modified))
            if not reset and self._offset and self._hash_tail(stream, self._offset) != self._tail_hash:
                reset = True
            offset = 0 if reset else self._offset
            stream.seek(offset)
            chunk = stream.read(MAX_BYTES + 1)
            if offset + len(chunk) > MAX_BYTES:
                raise ValueError("Source exceeds 32 MiB")
            complete = chunk.rfind(b"\n") + 1
            if not reset and not complete:
                self._pending = (identity, offset, info.st_mtime_ns, self._tail_hash,
                                 self._rows, self._events)
                return self._events
            rows = ([] if reset else self._rows) + parse_jsonl(chunk[:complete], shape=self.shape)
            if len(rows) > 50000:
                raise ValueError("Source exceeds 50,000 records")
            events = self._convert(rows)
            if reset:
                self._validate_generation(events)
            new_offset = offset + complete
            tail_hash = self._hash_tail(stream, new_offset)
            self._pending = (identity, new_offset, info.st_mtime_ns, tail_hash, rows, events)
            return events

    def commit(self):
        if self._pending is None:
            raise RuntimeError("No accepted native source read to commit")
        (self._identity, self._offset, self._modified, self._tail_hash,
         self._rows, self._events) = self._pending
        self._pending = None

    def discard(self):
        self._pending = None

    def _convert(self, rows):
        raise NotImplementedError

    def _validate_generation(self, events):
        pass


class ClaudeFollower(NativeFollower):
    correct_usage = True
    supports_responses = True
    shape = staticmethod(claude_row)

    def _convert(self, rows):
        return claude(rows, self.source, self.project, versions=True)

    def responses(self):
        """Derive optional evidence only from committed, projected source rows."""
        return response_spans(self._rows, self.source, self.project)

"""Inspect one explicitly selected native source's CodeBurn v10 cache member.

The cache is optional compatibility evidence, not a ledger input. Its member
contains conversation material; only allowlisted counters leave this module.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat

from .codeburn_breakdowns import _row_id
from .sources import Source

ERROR = "Selected session cache could not be read or validated"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.json")
COUNTERS = ("inputTokens", "outputTokens", "cacheReadInputTokens",
            "cacheCreationInputTokens", "reasoningTokens")


def _read(path, cap, *, offset=0, length=None):
    """Read one regular file or exact indexed byte span, without final symlinks."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024 * 1024:
            raise ValueError(ERROR)
        size = info.st_size if length is None else length
        if not 0 <= offset <= info.st_size or not 0 <= size <= cap or offset + size > info.st_size:
            raise ValueError(ERROR)
        body = os.pread(fd, size, offset)
        if len(body) != size:
            raise ValueError(ERROR)
        return body, info
    finally:
        os.close(fd)


def _json(body):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(ERROR)
            result[key] = value
        return result
    try:
        return json.loads(body, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError(ERROR)))
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError(ERROR) from None


def _named(cache, name, cap):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(ERROR)
    return _read(cache / name, cap)


def _time(value):
    if not isinstance(value, str):
        raise ValueError(ERROR)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except ValueError:
        raise ValueError(ERROR) from None


def _count(value):
    if type(value) is not int or not 0 <= value <= 10**15:
        raise ValueError(ERROR)
    return value


def _fingerprint(row, info):
    return (type(row[4]) is int and type(row[5]) is int and
            row[4:6] == [info.st_dev, info.st_ino] and
            type(row[6]) in (int, float) and type(row[7]) is int and
            abs(row[6] - info.st_mtime_ns / 1_000_000) < .01 and row[7] == info.st_size)


def inspect_selected_session(cache_dir, source):
    """Return metadata for one directly readable, manifest-selected native file.

    The provider index is searched by exact canonical path; no other member is
    opened. Revisions of the mutable cache replace this view, never ledger rows.
    """
    if not isinstance(source, Source) or source.format not in ("claude", "codex"):
        raise ValueError(ERROR)
    cache = Path(cache_dir)
    try:
        _, native_info = _read(source.path, 0, length=0)
        envelope_bytes, _ = _named(cache, "envelope.json", 64 * 1024)
        envelope = _json(envelope_bytes)
        if not isinstance(envelope, dict) or type(envelope.get("version")) is not int or envelope["version"] != 10 or envelope.get("complete") is not True or not isinstance(envelope.get("providers"), dict):
            raise ValueError(ERROR)
        provider = envelope.get("providers", {}).get(source.format)
        if not isinstance(provider, dict) or provider.get("complete") is not True:
            raise ValueError(ERROR)
        index_bytes, _ = _named(cache, provider.get("index"), 4 * 1024 * 1024)
        index = _json(index_bytes)
        if not isinstance(index, dict) or type(index.get("version")) is not int or index["version"] != 10 or not isinstance(index.get("rows"), list) or len(index["rows"]) > 50000 or not isinstance(index.get("pieces"), dict):
            raise ValueError(ERROR)
        matches = [row for row in index["rows"] if isinstance(row, list) and len(row) == 13 and row[0] == str(source.path)]
        if len(matches) != 1:
            raise ValueError(ERROR)
        row = matches[0]
        if not _fingerprint(row, native_info):
            raise ValueError(ERROR)  # stale cache is unknown, not a fresh session
        piece = index["pieces"].get(row[1])
        if not isinstance(piece, str) or not NAME.fullmatch(piece):
            raise ValueError(ERROR)
        if type(row[11]) is not int or type(row[12]) is not int:
            raise ValueError(ERROR)
        member_bytes, piece_info = _read(cache / piece, 4 * 1024 * 1024, offset=row[11], length=row[12])
        member = _json(b"{" + member_bytes + b"}")
        if not isinstance(member, dict) or set(member) != {str(source.path)}:
            raise ValueError(ERROR)
        value = member[str(source.path)]
        if not isinstance(value, dict) or not isinstance(value.get("turns"), list) or len(value["turns"]) > 1000:
            raise ValueError(ERROR)
        turns = []
        for ordinal, turn in enumerate(value["turns"], 1):
            if not isinstance(turn, dict) or not isinstance(turn.get("calls"), list) or len(turn["calls"]) > 5000:
                raise ValueError(ERROR)
            counts = {key: 0 for key in COUNTERS}
            models = {}
            for call in turn["calls"]:
                if not isinstance(call, dict) or not isinstance(call.get("usage"), dict):
                    raise ValueError(ERROR)
                model = _row_id(call.get("model"), model=True)
                models[model] = models.get(model, 0) + 1
                for key in COUNTERS:
                    counts[key] += _count(call["usage"].get(key))
                    if counts[key] > 10**15:
                        raise ValueError(ERROR)
            turns.append(dict(ordinal=ordinal, at=_time(turn.get("timestamp")),
                              calls=len(turn["calls"]), models=[dict(id=k, calls=v) for k, v in sorted(models.items())],
                              tokens=dict(input=counts["inputTokens"], output=counts["outputTokens"],
                                          cache_read=counts["cacheReadInputTokens"],
                                          cache_write=counts["cacheCreationInputTokens"],
                                          reasoning=counts["reasoningTokens"])))
        # Reject a source or cache generation that changed during projection.
        _, native_after = _read(source.path, 0, length=0)
        envelope_after, _ = _named(cache, "envelope.json", 64 * 1024)
        index_after, _ = _named(cache, provider["index"], 4 * 1024 * 1024)
        _, piece_after = _read(cache / piece, 0, length=0)
        piece_stamp = (piece_info.st_dev, piece_info.st_ino, piece_info.st_size, piece_info.st_mtime_ns)
        after_stamp = (piece_after.st_dev, piece_after.st_ino, piece_after.st_size, piece_after.st_mtime_ns)
        if (not _fingerprint(row, native_after) or envelope_after != envelope_bytes or
                index_after != index_bytes or after_stamp != piece_stamp):
            raise ValueError(ERROR)
        return dict(schema="monitoring.selected-cache-session.v1", source=source.id,
                    provider=source.format, project=source.project, format_version=10,
                    observed_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    cache_modified_at=datetime.fromtimestamp(piece_info.st_mtime, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    native_modified_at=datetime.fromtimestamp(native_info.st_mtime, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    complete=True, total_turns=len(turns), total_calls=sum(item["calls"] for item in turns),
                    turns=turns[-100:], truncated=len(turns) > 100,
                    note="Selected CodeBurn cache metadata only. Calls and token buckets may overlap the native ledger; no usage, billing or lifecycle is imported.")
    except (OSError, KeyError, TypeError, ValueError, OverflowError):
        raise ValueError(ERROR) from None

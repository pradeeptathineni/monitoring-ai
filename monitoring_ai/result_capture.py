"""Extract metadata from an explicitly selected Claude CLI result."""

import json
import os
from pathlib import Path
import tempfile
import fcntl
import stat

from .adapters import MAX_BYTES, parse_jsonl, read_source, reject_constant, unique_keys


class ResultPublicationUncertain(OSError):
    """The target was linked, but directory durability was not confirmed."""


def capture_cli_result(source, output, *, captured_at):
    """Create one private metadata frame without overwriting any output."""
    from .claude_results import capture_result

    try:
        raw = json.loads(read_source(source), object_pairs_hook=unique_keys, parse_constant=reject_constant)
        if not isinstance(raw, dict) or raw.get("type") != "result" or "schema" in raw:
            raise ValueError("Capture requires a native result, not an existing timestamped frame")
        frame = capture_result(raw, captured_at)
    except (ValueError, RecursionError):
        raise ValueError("Selected file is not a supported completed Claude result") from None
    write_result_frame(output, frame)
    return frame


def write_result_frame(output, frame):
    """Publish one sanitized result frame as a new private file."""
    from .claude_results import FRAME_SCHEMA, capture_result

    if not isinstance(frame, dict) or frame.get("schema") != FRAME_SCHEMA:
        raise ValueError("Invalid sanitized result frame")
    frame = capture_result(frame, frame.get("captured_at"))
    publish_private_json(output, frame)


def append_result_frame(output, frame, *, source, project):
    """Append one sanitized cumulative result to an existing private history."""
    from .claude_results import FRAME_SCHEMA, capture_result, claude_results

    if not isinstance(frame, dict) or frame.get("schema") != FRAME_SCHEMA:
        raise ValueError("Invalid sanitized result frame")
    clean = capture_result(frame, frame.get("captured_at"))
    append_private_frame(output, clean, lambda frames: claude_results(frames, source, project))


def append_private_frame(output, frame, validate_history):
    """Validate a private metadata history before append; false means exact replay."""
    payload = (json.dumps(frame, separators=(",", ":"), allow_nan=False) + "\n").encode()
    selected = Path(output).absolute()
    target = selected.parent.resolve(strict=True) / selected.name
    fd = os.open(target, os.O_RDWR | os.O_APPEND | os.O_NONBLOCK |
                 os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid() or info.st_size + len(payload) > MAX_BYTES):
            raise ValueError("Result append needs an owned private file within the size limit")
        os.lseek(fd, 0, os.SEEK_SET)
        parts, remaining = [], info.st_size
        while remaining:
            part = os.read(fd, min(remaining, 1024 * 1024))
            if not part:
                raise ValueError("Result append target changed during inspection")
            parts.append(part)
            remaining -= len(part)
        content = b"".join(parts)
        if content and not content.endswith(b"\n"):
            raise ValueError("Result append target has an incomplete final record")
        frames = parse_jsonl(content, strict=True)
        if len(frames) >= 50_000:
            raise ValueError("Result append record limit reached")
        if validate_history(frames + [frame]) is False:
            return False
        offset = 0
        try:
            while offset < len(payload):
                written = os.write(fd, payload[offset:])
                if written <= 0:
                    raise OSError("short result write")
                offset += written
            os.fsync(fd)
        except OSError:
            raise ResultPublicationUncertain from None
        return True
    finally:
        os.close(fd)


def publish_private_json(output, value):
    """Atomically publish one caller-validated JSON object to a new 0600 file."""
    if not isinstance(value, dict):
        raise ValueError("Private JSON output needs an object")
    selected = Path(output).absolute()
    target = selected.parent.resolve(strict=True) / selected.name
    descriptor, temporary = tempfile.mkstemp(prefix=".monitoring-private-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target, follow_symlinks=False)
        try:
            parent_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except OSError:
            raise ResultPublicationUncertain from None
    finally:
        Path(temporary).unlink(missing_ok=True)


def replace_private_json(output, value):
    """Atomically replace one existing owned 0600 JSON observation file."""
    if not isinstance(value, dict):
        raise ValueError("Private JSON output needs an object")
    selected = Path(output).absolute()
    target = selected.parent.resolve(strict=True) / selected.name
    fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW |
                 getattr(os, "O_CLOEXEC", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise ValueError("Quota replacement needs an owned private file")
    finally:
        os.close(fd)
    descriptor, temporary = tempfile.mkstemp(prefix=".monitoring-private-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        try:
            parent_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except OSError:
            raise ResultPublicationUncertain from None
    finally:
        Path(temporary).unlink(missing_ok=True)

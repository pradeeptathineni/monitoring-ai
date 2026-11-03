"""Extract metadata from an explicitly selected Claude CLI result."""

import json
import os
from pathlib import Path
import tempfile

from .adapters import read_source, reject_constant, unique_keys


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

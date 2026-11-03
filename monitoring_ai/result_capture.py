"""Extract metadata from an explicitly selected Claude CLI result."""

import json
import os
from pathlib import Path
import tempfile

from .adapters import read_source, reject_constant, unique_keys


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
    selected = Path(output).absolute()
    target = selected.parent.resolve(strict=True) / selected.name
    descriptor, temporary = tempfile.mkstemp(prefix=".monitoring-result-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(frame, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target, follow_symlinks=False)
        return frame
    finally:
        Path(temporary).unlink(missing_ok=True)

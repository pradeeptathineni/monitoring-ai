"""Capture one Claude CLI stream as metadata-only invocation lifecycle."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from .adapters import reject_constant, safe_label, unique_keys
from .claude_results import capture_result
from .events import label
from .metrics import now_iso
from .producer import EventWriter, session_alias, status_event
from .result_capture import ResultPublicationUncertain, write_result_frame
from .store import stable_id

MAX_LINE = 4 * 1024 * 1024


def _lines(stream):
    while line := stream.readline(MAX_LINE + 1):
        if len(line) > MAX_LINE or not line.endswith(b"\n"):
            raise ValueError("Claude stream needs complete lines of at most 4 MiB")
        yield line


def _after(previous, current):
    if current > previous:
        return current
    value = datetime.fromisoformat(previous.replace("Z", "+00:00")) + timedelta(milliseconds=1)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def capture_stream(stream, output, *, source, project, clock=now_iso, forward=None,
                   result_output=None):
    """Read one native JSONL invocation and save only observed start/end states.

    The CLI stream's init/result frames describe this invocation, not a native
    conversation close, process PID, accepted task or provider worker roster.
    """
    source, project = label(source, "source"), label(project, "project")
    if result_output is not None:
        try:
            status_target = Path(output).absolute()
            status_target = status_target.parent.resolve(strict=True) / status_target.name
        except (OSError, RuntimeError):
            raise ValueError("Status output needs an existing directory") from None
        try:
            result_target = Path(result_output).absolute()
            result_target = result_target.parent.resolve(strict=True) / result_target.name
        except (OSError, RuntimeError):
            raise ValueError("Result summary needs an existing directory") from None
        if (result_target == status_target or
                (result_target.parent == status_target.parent and
                 result_target.name.casefold() == status_target.name.casefold()) or
                os.path.lexists(result_target)):
            raise ValueError("Result summary needs a separate new file")
    started = ended = None
    result_summary = "unavailable" if result_output is not None else "not-requested"
    with EventWriter(output) as writer:
        try:
            for line in _lines(stream):
                if forward is not None:
                    forward.write(line)
                    forward.flush()
                try:
                    row = json.loads(line, object_pairs_hook=unique_keys,
                                     parse_constant=reject_constant)
                except (ValueError, UnicodeError, RecursionError):
                    raise ValueError("Invalid Claude stream line") from None
                if not isinstance(row, dict):
                    raise ValueError("Invalid Claude stream line")
                if row.get("type") == "system" and row.get("subtype") == "init":
                    native, invocation = row.get("session_id"), row.get("uuid")
                    if not all(isinstance(v, str) and 0 < len(v) <= 1000 for v in (native, invocation)):
                        raise ValueError("Claude stream init has no usable identity")
                    if started is not None:
                        if (native, invocation) != (started["native"], started["invocation"]):
                            raise ValueError("Claude stream contains multiple invocations")
                        continue
                    session = session_alias(native)
                    entity = "cli-" + stable_id(source, native, invocation)
                    model = safe_label(row.get("model"))
                    at = clock()
                    writer.write(status_event(
                        id=entity + "-0", source=source, project=project, session=session,
                        entity=entity, parent=session, entity_type="agent", state="running",
                        timestamp=at, provider="anthropic", model=model,
                        surface="claude-cli-stream"))
                    started = dict(native=native, invocation=invocation, session=session,
                                   entity=entity, model=model, at=at)
                elif row.get("type") == "result":
                    if started is None or ended is not None or row.get("session_id") != started["native"]:
                        raise ValueError("Claude stream result has no matching invocation")
                    is_error = row.get("is_error")
                    if type(is_error) is not bool:
                        raise ValueError("Claude stream result has no qualified outcome")
                    state = ("failed" if is_error else "completed" if row.get("subtype") == "success"
                             else "unknown")
                    at = _after(started["at"], clock())
                    writer.write(status_event(
                        id=started["entity"] + "-1", source=source, project=project,
                        session=started["session"], entity=started["entity"],
                        parent=started["session"], entity_type="agent", state=state,
                        timestamp=at, provider="anthropic", model=started["model"],
                        surface="claude-cli-stream"))
                    ended = state
                    if result_output is not None:
                        try:
                            write_result_frame(result_output, capture_result(row, at))
                            result_summary = "captured"
                        except ResultPublicationUncertain:
                            result_summary = "uncertain"
                        except (ValueError, OSError, RecursionError):
                            result_summary = "failed"
        except (ValueError, OSError):
            if started is not None and ended is None:
                _unknown(writer, source, project, started, clock())
            raise
        if started is not None and ended is None:
            _unknown(writer, source, project, started, clock())
            ended = "unknown"
    receipt = {"observed_invocations": int(started is not None),
               "terminal_state": ended or "unobserved"}
    if result_output is not None:
        receipt["result_summary"] = result_summary
    return receipt


def _unknown(writer, source, project, started, at):
    writer.write(status_event(
        id=started["entity"] + "-1", source=source, project=project,
        session=started["session"], entity=started["entity"],
        parent=started["session"], entity_type="agent", state="unknown",
        timestamp=_after(started["at"], at), provider="anthropic",
        model=started["model"], surface="claude-cli-stream"))

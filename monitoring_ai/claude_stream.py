"""Capture one Claude CLI stream as metadata-only invocation lifecycle."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from .adapters import reject_constant, safe_label, unique_keys
from .claude_results import capture_result
from .claude_rate_limits import claude_rate_limit_book
from .claude_tools import tool_category
from .events import SCHEMA, label, validate
from .insights import validate_quota_book
from .metrics import now_iso
from .producer import EventWriter, session_alias, status_event
from .result_capture import ResultPublicationUncertain, publish_private_json, write_result_frame
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


def _new_output_target(path, name):
    try:
        selected = Path(path).absolute()
        return selected.parent.resolve(strict=True) / selected.name
    except (OSError, RuntimeError):
        raise ValueError(f"{name} needs an existing directory") from None


def capture_stream(stream, output, *, source, project, clock=now_iso, forward=None,
                   result_output=None, quota_output=None, quota_account="local"):
    """Read one native JSONL invocation and save only observed start/end states.

    The CLI stream's init/result frames describe this invocation, not a native
    conversation close, process PID, accepted task or provider worker roster.
    """
    source, project = label(source, "source"), label(project, "project")
    outputs = [("Status output", output)]
    if result_output is not None:
        outputs.append(("Result summary", result_output))
    if quota_output is not None:
        label(quota_account, "account")
        outputs.append(("Quota observation", quota_output))
    if len(outputs) > 1:
        targets = [(name, _new_output_target(path, name)) for name, path in outputs]
        for index, (name, target) in enumerate(targets[1:], 1):
            if os.path.lexists(target) or any(
                target.parent == previous.parent and target.name.casefold() == previous.name.casefold()
                for _, previous in targets[:index]
            ):
                raise ValueError(f"{name} needs a separate new file")
    started = ended = None
    result_summary = "unavailable" if result_output is not None else "not-requested"
    quota_summary = "unavailable" if quota_output is not None else "not-requested"
    quota_windows = {}
    quota_rejected = 0
    tool_uses = {}
    tool_results = {}
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
                elif row.get("type") in ("assistant", "user") and started is not None and ended is None:
                    # CLI frames can repeat assistant messages. Keep only the
                    # metadata needed to pair one request with one result.
                    if row.get("session_id") == started["native"]:
                        _observe_tools(row, started, tool_uses, tool_results,
                                       writer, source, project, clock)
                elif row.get("type") == "result":
                    if started is None or ended is not None or row.get("session_id") != started["native"]:
                        raise ValueError("Claude stream result has no matching invocation")
                    is_error = row.get("is_error")
                    if type(is_error) is not bool:
                        raise ValueError("Claude stream result has no qualified outcome")
                    state = ("failed" if is_error else "completed" if row.get("subtype") == "success"
                             else "unknown")
                    _flush_unknown_tools(tool_uses, tool_results, started, writer, source, project)
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
                        except (ValueError, OSError, TypeError, RecursionError):
                            result_summary = "failed"
                elif row.get("type") == "rate_limit_event" and quota_output is not None:
                    try:
                        if started is None or row.get("session_id") != started["native"]:
                            raise ValueError("Claude rate-limit event does not match this invocation")
                        book = claude_rate_limit_book(
                            row, captured_at=clock(), source=source, account=quota_account)
                        observed = book["snapshots"][0]
                        for window in observed["windows"]:
                            quota_windows[window["id"]] = dict(observed, windows=[window])
                        quota_summary = "observed"
                    except Exception:
                        # An optional, possibly newer CLI payload must not break
                        # the already observed invocation status or valid windows.
                        quota_rejected += 1
                        quota_summary = "partial" if quota_windows else "failed"
        except (ValueError, OSError):
            if started is not None and ended is None:
                _flush_unknown_tools(tool_uses, tool_results, started, writer, source, project)
                _unknown(writer, source, project, started, clock())
            raise
        if started is not None and ended is None:
            _flush_unknown_tools(tool_uses, tool_results, started, writer, source, project)
            _unknown(writer, source, project, started, clock())
            ended = "unknown"
        if quota_windows:
            try:
                quota_book = validate_quota_book({
                    "schema": "monitoring.quotas.v1",
                    "snapshots": [quota_windows[key] for key in sorted(quota_windows)],
                })
                publish_private_json(quota_output, quota_book)
                quota_summary = "partial" if quota_rejected else "captured"
            except ResultPublicationUncertain:
                quota_summary = "uncertain"
            except (ValueError, OSError, TypeError, RecursionError):
                quota_summary = "failed"
    receipt = {"observed_invocations": int(started is not None),
               "terminal_state": ended or "unobserved"}
    if result_output is not None:
        receipt["result_summary"] = result_summary
    if quota_output is not None:
        receipt["quota_observation"] = quota_summary
        receipt["quota_rejected_events"] = quota_rejected
    return receipt


def _unknown(writer, source, project, started, at):
    writer.write(status_event(
        id=started["entity"] + "-1", source=source, project=project,
        session=started["session"], entity=started["entity"],
        parent=started["session"], entity_type="agent", state="unknown",
        timestamp=_after(started["at"], at), provider="anthropic",
        model=started["model"], surface="claude-cli-stream"))


def _observe_tools(row, started, uses, results, writer, source, project, clock):
    message = row.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return
    for block in message["content"]:
        if not isinstance(block, dict):
            continue
        if row["type"] == "assistant" and block.get("type") == "tool_use":
            call, name = block.get("id"), block.get("name")
            if not all(isinstance(value, str) and 0 < len(value) <= 1000
                       for value in (call, name)):
                continue
            model = safe_label(message.get("model"), started["model"])
            prior = uses.get(call)
            if prior is not None:
                if prior[:2] != (name, model):
                    raise ValueError("Conflicting Claude stream tool metadata")
                continue
            if len(uses) >= 50_000:
                raise ValueError("Too many Claude stream tool calls")
            uses[call] = (name, model, _after(started["at"], clock()))
        elif row["type"] == "user" and block.get("type") == "tool_result":
            call = block.get("tool_use_id")
            if not isinstance(call, str) or call not in uses:
                continue
            outcome = ("error" if block.get("is_error") is True else
                       "ok" if block.get("is_error") is False else "unknown")
            if call in results:
                if results[call] != outcome:
                    raise ValueError("Conflicting Claude stream tool outcome")
                continue
            _write_tool(writer, started, source, project, call, uses[call],
                        outcome, _after(uses[call][2], clock()))
            results[call] = outcome


def _flush_unknown_tools(uses, results, started, writer, source, project):
    for call, use in uses.items():
        if call not in results:
            _write_tool(writer, started, source, project, call, use, "unknown", use[2])
            results[call] = "unknown"


def _write_tool(writer, started, source, project, call, use, outcome, at):
    name, model, _ = use
    category, server = tool_category(name)
    event = dict(
        schema=SCHEMA, id="tool-" + stable_id(source, started["native"],
                                               started["invocation"], call),
        source=source, project=project, session=started["session"],
        entity=started["entity"], parent=started["session"], entity_type="agent",
        timestamp=at, kind="tool", provider="anthropic", model=model,
        surface="claude-cli-stream", tool=safe_label(name), tool_category=category,
        outcome=outcome,
    )
    if server is not None:
        event["tool_server"] = safe_label(server)
    writer.write(validate(event))

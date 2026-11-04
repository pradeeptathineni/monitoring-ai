"""Refuse declared parent histories until copied usage can be reconciled."""


MESSAGE = (
    "Declared Codex parent history requires replay reconciliation. "
    "This input was not imported; accepted history is retained."
)


class CodexLineageError(ValueError):
    """Report an unsupported history without retaining its parent identity."""


def declared_parent(payload):
    """Recognize supplied parent declarations without inferring relationships."""
    if payload.get("lineage_declared") is True or payload.get("forked_from_id") is not None:
        return True
    source = payload.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    return isinstance(spawn, dict) and spawn.get("parent_thread_id") is not None


def require_independent(payload):
    """Keep declared forks out of independent cumulative-counter accounting."""
    if declared_parent(payload):
        raise CodexLineageError(MESSAGE)

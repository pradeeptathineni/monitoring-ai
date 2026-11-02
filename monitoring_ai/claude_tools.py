'''Metadata-only Claude Code tool-call events.

Pairs tool_use and tool_result blocks from structured JSONL message content.
Only tool names, call identities, timestamps, models and error flags are read.
Tool inputs, result content, prompts, paths and commands are never touched.
'''

MAX_TOOL_EVENTS = 50000

_CATEGORIES = {
    **dict.fromkeys(('Read', 'Write', 'Edit', 'Glob', 'Grep', 'WebFetch', 'WebSearch'), 'core'),
    **dict.fromkeys(('Bash', 'Shell'), 'shell'),
    **dict.fromkeys(('Agent', 'Task'), 'agent'),
    'Skill': 'skill',
}


def _text(value):
    return isinstance(value, str) and bool(value)


def tool_category(name):
    '''Return (category, native MCP server or None) for a native tool name.'''
    if name in _CATEGORIES:
        return _CATEGORIES[name], None
    if name.startswith('mcp__'):
        parts = name[len('mcp__'):].split('__')
        if len(parts) == 2 and all(parts):
            return 'mcp', parts[0]
    return 'other', None


def _record(store, key, value):
    # Identical repeats are transcript replay; any difference is ambiguous
    if store.setdefault(key, value) != value:
        raise ValueError('Conflicting Claude tool call metadata')


def _collect(rows):
    uses, results = {}, {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        message, session, role = row.get('message'), row.get('sessionId'), row.get('type')
        if not (isinstance(message, dict) and isinstance(message.get('content'), list) and _text(session)):
            continue
        for block in message['content']:
            if not isinstance(block, dict):
                continue
            if role == 'assistant' and block.get('type') == 'tool_use':
                call, name, model = block.get('id'), block.get('name'), message.get('model')
                if _text(call) and _text(name):
                    _record(uses, (session, call), (name, model if _text(model) else 'unknown'))
            elif role == 'user' and block.get('type') == 'tool_result':
                call, at = block.get('tool_use_id'), row.get('timestamp')
                if not (_text(call) and _text(at)):
                    continue
                is_error = block.get('is_error', False)
                if not isinstance(is_error, bool):
                    raise ValueError('Invalid Claude tool result outcome')
                _record(results, (session, call), (at, is_error))
    return uses, results


def claude_tool_events(rows, source, project, *, provider='anthropic', surface='claude-code'):
    '''Return one raw tool event per complete tool_use/tool_result pair.'''
    from .adapters import base, safe_label  # lazy: adapters.claude wires this module in

    uses, results = _collect(rows)
    pairs = [key for key in results if key in uses]
    if len(pairs) > MAX_TOOL_EVENTS:
        raise ValueError('Too many Claude tool events')
    events = []
    for session, call in pairs:
        name, model = uses[session, call]
        at, is_error = results[session, call]
        category, server = tool_category(name)
        event = base(source, session, project, provider, model, surface, at, ('tool', session, call))
        event.update(kind='tool', tool=safe_label(name), tool_category=category,
                     outcome='error' if is_error else 'ok')
        if server is not None:
            event['tool_server'] = safe_label(server)
        events.append(event)
    return events

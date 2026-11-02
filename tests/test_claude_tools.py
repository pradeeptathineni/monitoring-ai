'''Synthetic tests for metadata-only Claude Code tool events.'''
import json
import unittest
from unittest import mock

from monitoring_ai import claude_tools
from monitoring_ai.adapters import base
from monitoring_ai.claude_tools import claude_tool_events

SESSION = 'synthetic-session-7f3a'
PROJECT = 'synthetic-project'
SECRET_INPUT = {'command': 'cat ~/private/ledger.txt', 'file_path': '/synthetic/private/secret-plan.md'}
SECRET_RESULT = 'PRIVATE-RESULT-PAYLOAD-91c2'
SECRET_PROMPT = 'PRIVATE-PROMPT-TEXT'


def use(call, name, model='claude-synthetic-1', at='2026-01-01T00:00:00Z', session=SESSION):
    content = [{'type': 'text', 'text': SECRET_PROMPT},
               {'type': 'tool_use', 'id': call, 'name': name, 'input': SECRET_INPUT}]
    return {'type': 'assistant', 'sessionId': session, 'timestamp': at,
            'message': {'role': 'assistant', 'model': model, 'content': content}}


def result(call, at='2026-01-01T00:00:05Z', session=SESSION, **extra):
    block = {'type': 'tool_result', 'tool_use_id': call, 'content': SECRET_RESULT, **extra}
    return {'type': 'user', 'sessionId': session, 'timestamp': at,
            'message': {'role': 'user', 'content': [block]}}


def pair(call, name, **extra):
    return [use(call, name), result(call, **extra)]


def events(rows):
    return claude_tool_events(rows, 'claude', PROJECT)


class ClaudeToolEventsTest(unittest.TestCase):
    def test_complete_pair_emits_tool_metadata_at_result_time(self):
        [event] = events(pair('toolu_1', 'Read'))
        self.assertEqual(event['kind'], 'tool')
        self.assertEqual(event['tool'], 'Read')
        self.assertEqual(event['tool_category'], 'core')
        self.assertEqual(event['outcome'], 'ok')
        self.assertEqual(event['timestamp'], '2026-01-01T00:00:05Z')
        self.assertEqual(event['model'], 'claude-synthetic-1')
        self.assertNotIn('tool_server', event)

    def test_identical_replay_emits_once(self):
        rows = pair('toolu_1', 'Bash', is_error=False)
        first = events(rows)
        # Omitted is_error equals explicit false, so this is still a replay
        replayed = events(rows + rows + [result('toolu_1')])
        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed, first)

    def test_out_of_order_rows_pair(self):
        [event] = events([result('toolu_2', is_error=True), use('toolu_2', 'Edit')])
        self.assertEqual(event['outcome'], 'error')
        self.assertEqual(event['tool_category'], 'core')
        self.assertEqual(event['model'], 'claude-synthetic-1')

    def test_private_input_and_result_content_discarded(self):
        [event] = events(pair('toolu_3', 'Bash'))
        dumped = json.dumps(event)
        for private in ('ledger.txt', 'secret-plan.md', SECRET_RESULT, SECRET_PROMPT, SESSION, 'toolu_3'):
            self.assertNotIn(private, dumped)
        self.assertTrue({'input', 'content', 'command', 'file_path', 'text'}.isdisjoint(event))

    def test_no_duration_inferred_from_gap(self):
        [event] = events([use('toolu_4', 'Grep', at='2026-01-01T00:00:00Z'),
                          result('toolu_4', at='2026-01-01T01:00:00Z')])
        self.assertEqual([key for key in event if 'duration' in key], [])
        self.assertEqual(event['timestamp'], '2026-01-01T01:00:00Z')

    def test_incomplete_and_unstructured_rows_ignored(self):
        string_content = {'type': 'assistant', 'sessionId': SESSION, 'timestamp': 't',
                          'message': {'model': 'm', 'content': 'tool_use toolu_9 Read'}}
        rows = [use('toolu_5', 'Read'), result('toolu_6'),
                use('toolu_7', 'Read'), result('toolu_7', session='other-session'),
                string_content, {'type': 'user', 'message': None}, 'not-a-row',
                # A tool_result inside an assistant message is not a call result
                {**result('toolu_8'), 'type': 'assistant'}, use('toolu_8', 'Read')]
        self.assertEqual(events(rows), [])

    def test_conflicts_rejected_without_payload_text(self):
        cases = [
            [use('toolu_c', 'Read'), use('toolu_c', 'Write')],
            [use('toolu_c', 'Read', model='m-1'), use('toolu_c', 'Read', model='m-2')],
            [result('toolu_c', at='2026-01-01T00:00:01Z'), result('toolu_c', at='2026-01-01T00:00:02Z')],
            [result('toolu_c', is_error=True), result('toolu_c')],
        ]
        for index, rows in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError) as caught:
                events(rows)
            message = str(caught.exception)
            for private in ('toolu_c', 'Read', 'Write', 'm-1', 'm-2', SECRET_RESULT, SESSION):
                self.assertNotIn(private, message)

    def test_nonboolean_is_error_refused(self):
        for value in ('true', 1, 0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                events(pair('toolu_e', 'Read', is_error=value))

    def test_categories(self):
        expected = {'Read': 'core', 'Write': 'core', 'Edit': 'core', 'Glob': 'core', 'Grep': 'core',
                    'WebFetch': 'core', 'WebSearch': 'core', 'Bash': 'shell', 'Shell': 'shell',
                    'Skill': 'skill', 'Agent': 'agent', 'Task': 'agent', 'read': 'other',
                    'TodoWrite': 'other', 'NotebookEdit': 'other'}
        rows = []
        for index, name in enumerate(expected):
            rows += pair(f'toolu_k{index}', name)
        got = {event['tool']: event['tool_category'] for event in events(rows)}
        self.assertEqual(got, expected)

    def test_mcp_category_and_server_aliases(self):
        names = {
            'mcp__github__create_issue': ('mcp', 'github'),
            'mcp__github__list_pulls': ('mcp', 'github'),
            'mcp__claude_ai_Gmail__search': ('mcp', 'claude_ai_Gmail'),
            'mcp__my server!__fetch': ('mcp', 'my-server-'),
            'mcp____tool': ('other', None),
            'mcp__server__': ('other', None),
            'mcp__a__b__c': ('other', None),
            'mcp_github_create': ('other', None),
            'MCP__github__x': ('other', None),
        }
        for index, (name, (category, server)) in enumerate(names.items()):
            with self.subTest(name=name):
                [event] = events(pair(f'toolu_m{index}', name))
                self.assertEqual(event['tool_category'], category)
                self.assertEqual(event.get('tool_server'), server)
                if server is None:
                    self.assertNotIn('tool_server', event)
        [event] = events(pair('toolu_s', 'mcp__my server!__fetch'))
        self.assertEqual(event['tool'], 'mcp__my-server-__fetch')

    def test_missing_model_is_unknown(self):
        row = use('toolu_u', 'Read')
        del row['message']['model']
        [event] = events([row, result('toolu_u')])
        self.assertEqual(event['model'], 'unknown')

    def test_event_id_uses_tool_identity(self):
        rows = pair('toolu_a', 'Read') + pair('toolu_b', 'Read')
        first, second = events(rows)
        expected = base('claude', SESSION, PROJECT, 'anthropic', 'm', 'claude-code', 't',
                        ('tool', SESSION, 'toolu_a'))
        self.assertEqual(first['id'], expected['id'])
        self.assertEqual(first['session'], expected['session'])
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual({event['id'] for event in events(rows[::-1])}, {first['id'], second['id']})

    def test_emitted_events_bounded(self):
        rows = [row for index in range(3) for row in pair(f'toolu_l{index}', 'Read')]
        with mock.patch.object(claude_tools, 'MAX_TOOL_EVENTS', 2), self.assertRaises(ValueError):
            events(rows)
        self.assertEqual(claude_tools.MAX_TOOL_EVENTS, 50000)


if __name__ == '__main__':
    unittest.main()

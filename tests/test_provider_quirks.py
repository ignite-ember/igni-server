"""What is repaired in a chat payload before it goes upstream, and what is left alone.

Each repair exists because one provider rejects what another accepts,
and the rule is that a repair may fix or drop, never invent. The cases
here are the shapes that reach the proxy: a reasoning model's null
`content`, a tool call cut off at the token limit, and tool results the
CLI left out of their assistant/tool run.
"""

from __future__ import annotations

import json
import logging

from app.services.ai.provider_quirks import (
    normalise_choices,
    repair_json_string,
    sanitise_message_sequence,
    sanitise_tool_call_arguments,
)


class TestContentIsNeverNull:
    def test_a_reasoning_only_message_gets_empty_content_and_keeps_its_reasoning(self):
        data = {'choices': [{'message': {'role': 'assistant', 'content': None, 'reasoning_content': 'thinking'}}]}

        normalise_choices(data)

        assert data['choices'][0]['message'] == {'role': 'assistant', 'content': '', 'reasoning_content': 'thinking'}

    def test_a_stream_chunk_is_repaired_under_delta(self):
        chunk = {'choices': [{'delta': {'content': None}}, {'delta': {'content': 'hi'}}]}

        normalise_choices(chunk, key='delta')

        assert [c['delta']['content'] for c in chunk['choices']] == ['', 'hi']

    def test_a_choice_without_the_part_and_a_response_without_choices_are_left_alone(self):
        data = {'choices': [{'finish_reason': 'stop'}, {'message': 'not a dict'}]}
        normalise_choices(data)
        assert data == {'choices': [{'finish_reason': 'stop'}, {'message': 'not a dict'}]}

        empty: dict = {}
        normalise_choices(empty)
        assert empty == {}


class TestATruncatedToolCallIsClosed:
    def test_arguments_that_parse_are_returned_as_they_are(self):
        assert repair_json_string('{"a": 1}  ') == '{"a": 1}'

    def test_a_missing_quote_and_brace(self):
        repaired = repair_json_string('{"file_path": "x.py", "old_string": "def f(')

        assert json.loads(repaired) == {'file_path': 'x.py', 'old_string': 'def f('}

    def test_cut_mid_key_it_backs_off_to_the_last_complete_value(self):
        """No closer fixes `{"a": "x", "b` — the half key has to go."""
        repaired = repair_json_string('{"a": "x", "b')

        assert json.loads(repaired) == {'a': 'x'}

    def test_beyond_repair_it_is_kept_raw_rather_than_dropped(self):
        repaired = repair_json_string('not json at all')

        assert json.loads(repaired) == {'_raw': 'not json at all'}

    def test_only_unparseable_string_arguments_are_touched(self):
        body = {
            'messages': [
                {'role': 'user', 'content': 'go'},
                {
                    'role': 'assistant',
                    'tool_calls': [
                        {'id': '1', 'function': {'name': 'edit', 'arguments': '{"path": "a'}},
                        {'id': '2', 'function': {'name': 'edit', 'arguments': '{"path": "b"}'}},
                        {'id': '3', 'function': {'name': 'edit', 'arguments': {'already': 'a dict'}}},
                    ],
                },
            ]
        }

        sanitise_tool_call_arguments(body)

        calls = body['messages'][1]['tool_calls']
        assert json.loads(calls[0]['function']['arguments']) == {'path': 'a'}
        assert calls[1]['function']['arguments'] == '{"path": "b"}'
        assert calls[2]['function']['arguments'] == {'already': 'a dict'}


def _call(call_id: str) -> dict:
    return {'id': call_id, 'type': 'function', 'function': {'name': 'bash', 'arguments': '{}'}}


class TestToolResultsStayInTheirRun:
    def test_a_well_formed_conversation_is_untouched(self):
        messages = [
            {'role': 'user', 'content': 'list files'},
            {'role': 'assistant', 'content': None, 'tool_calls': [_call('a'), _call('b')]},
            {'role': 'tool', 'tool_call_id': 'a', 'content': 'x.py'},
            {'role': 'tool', 'tool_call_id': 'b', 'content': 'y.py'},
            {'role': 'assistant', 'content': 'two files'},
        ]
        body = {'messages': list(messages)}

        sanitise_message_sequence(body)

        assert body['messages'] == messages

    def test_a_result_after_an_intervening_message_becomes_a_system_note(self):
        """The CLI put an assistant text between the call and its result:
        Nebius rejects the result there even though its id matches. The
        result was real, so its content is kept — as context, not as a
        tool message."""
        body = {
            'messages': [
                {'role': 'assistant', 'tool_calls': [_call('a')]},
                {'role': 'assistant', 'content': 'Working on it'},
                {'role': 'tool', 'tool_call_id': 'a', 'content': 'x.py'},
            ]
        }

        sanitise_message_sequence(body)

        assert body['messages'][-1] == {'role': 'system', 'content': '[Tool result]: x.py'}
        assert [m['role'] for m in body['messages']] == ['assistant', 'assistant', 'system']

    def test_an_empty_orphan_is_dropped_and_the_drop_is_logged(self, caplog):
        body = {
            'messages': [
                {'role': 'user', 'content': 'hi'},
                {'role': 'tool', 'tool_call_id': 'gone', 'content': ''},
                {'role': 'assistant', 'content': 'hello'},
            ]
        }

        with caplog.at_level(logging.WARNING):
            sanitise_message_sequence(body)

        assert [m['role'] for m in body['messages']] == ['user', 'assistant']
        assert 'Dropped 1 orphaned tool message(s)' in caplog.text

    def test_a_user_message_ends_the_run(self):
        body = {
            'messages': [
                {'role': 'assistant', 'tool_calls': [_call('a')]},
                {'role': 'user', 'content': 'never mind'},
                {'role': 'tool', 'tool_call_id': 'a', 'content': 'late'},
            ]
        }

        sanitise_message_sequence(body)

        assert body['messages'][-1] == {'role': 'system', 'content': '[Tool result]: late'}

    def test_no_messages_is_nothing_to_do(self):
        body: dict = {'model': 'm'}
        sanitise_message_sequence(body)
        assert body == {'model': 'm'}

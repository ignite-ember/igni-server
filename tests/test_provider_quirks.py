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

from app.api.base_v1.schema.chat import ChatMessage
from app.services.ai.provider_quirks import ForwardedChunk, RepliedMessage, TruncatedJson, UpstreamConversation


def _repaired(messages: list[dict]) -> list[dict]:
    conversation = UpstreamConversation(messages=[ChatMessage.model_validate(m) for m in messages])
    return [message.model_dump(exclude_none=True) for message in conversation.repaired()]


class TestContentIsNeverNull:
    def test_a_reasoning_only_message_gets_empty_content_and_keeps_its_reasoning(self):
        message = RepliedMessage.model_validate({'role': 'assistant', 'content': None, 'reasoning_content': 'thinking'})

        assert message.model_dump(exclude_none=True) == {
            'role': 'assistant',
            'content': '',
            'reasoning_content': 'thinking',
        }

    def test_a_stream_chunk_is_repaired_under_delta(self):
        chunk = ForwardedChunk.model_validate({'choices': [{'delta': {'content': None}}, {'delta': {'content': 'hi'}}]})

        assert [c['delta']['content'] for c in chunk.wire()['choices']] == ['', 'hi']

    def test_a_choice_without_a_delta_and_a_chunk_without_choices_are_left_alone(self):
        sent = {'id': 'x', 'choices': [{'finish_reason': 'stop', 'index': 0}], 'usage': {}}
        assert ForwardedChunk.model_validate(sent).wire() == sent

        assert ForwardedChunk.model_validate({}).wire() == {}

    def test_a_delta_without_content_gains_an_empty_one(self):
        chunk = ForwardedChunk.model_validate({'choices': [{'delta': {'role': 'assistant'}}]})

        assert chunk.wire() == {'choices': [{'delta': {'role': 'assistant', 'content': ''}}]}


class TestATruncatedToolCallIsClosed:
    def test_arguments_that_parse_are_returned_as_they_are(self):
        assert TruncatedJson(value='{"a": 1}  ').closed() == '{"a": 1}'

    def test_a_missing_quote_and_brace(self):
        repaired = TruncatedJson(value='{"file_path": "x.py", "old_string": "def f(').closed()

        assert json.loads(repaired) == {'file_path': 'x.py', 'old_string': 'def f('}

    def test_cut_mid_key_it_backs_off_to_the_last_complete_value(self):
        """No closer fixes `{"a": "x", "b` — the half key has to go."""
        repaired = TruncatedJson(value='{"a": "x", "b').closed()

        assert json.loads(repaired) == {'a': 'x'}

    def test_beyond_repair_it_is_kept_raw_rather_than_dropped(self):
        repaired = TruncatedJson(value='not json at all').closed()

        assert json.loads(repaired) == {'_raw': 'not json at all'}

    def test_only_unparseable_string_arguments_are_touched(self):
        messages = _repaired(
            [
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
        )

        calls = messages[1]['tool_calls']
        assert json.loads(calls[0]['function']['arguments']) == {'path': 'a'}
        assert calls[1]['function']['arguments'] == '{"path": "b"}'
        assert calls[2]['function']['arguments'] == {'already': 'a dict'}

    def test_a_message_with_nothing_to_close_is_the_same_object(self):
        message = ChatMessage.model_validate(
            {'role': 'assistant', 'tool_calls': [{'id': '1', 'function': {'arguments': '{}'}}]}
        )

        assert UpstreamConversation(messages=[message]).repaired()[0] is message


def _call(call_id: str) -> dict:
    return {'id': call_id, 'type': 'function', 'function': {'name': 'bash', 'arguments': '{}'}}


class TestToolResultsStayInTheirRun:
    def test_a_well_formed_conversation_is_untouched(self):
        messages = [
            {'role': 'user', 'content': 'list files'},
            {'role': 'assistant', 'tool_calls': [_call('a'), _call('b')]},
            {'role': 'tool', 'tool_call_id': 'a', 'content': 'x.py'},
            {'role': 'tool', 'tool_call_id': 'b', 'content': 'y.py'},
            {'role': 'assistant', 'content': 'two files'},
        ]

        assert _repaired(messages) == messages

    def test_a_result_after_an_intervening_message_becomes_a_system_note(self):
        """The CLI put an assistant text between the call and its result:
        Nebius rejects the result there even though its id matches. The
        result was real, so its content is kept — as context, not as a
        tool message."""
        messages = _repaired(
            [
                {'role': 'assistant', 'tool_calls': [_call('a')]},
                {'role': 'assistant', 'content': 'Working on it'},
                {'role': 'tool', 'tool_call_id': 'a', 'content': 'x.py'},
            ]
        )

        assert messages[-1] == {'role': 'system', 'content': '[Tool result]: x.py'}
        assert [m['role'] for m in messages] == ['assistant', 'assistant', 'system']

    def test_an_empty_orphan_is_dropped_and_the_drop_is_logged(self, caplog):
        with caplog.at_level(logging.WARNING):
            messages = _repaired(
                [
                    {'role': 'user', 'content': 'hi'},
                    {'role': 'tool', 'tool_call_id': 'gone', 'content': ''},
                    {'role': 'assistant', 'content': 'hello'},
                ]
            )

        assert [m['role'] for m in messages] == ['user', 'assistant']
        assert 'Dropped 1 orphaned tool message(s)' in caplog.text

    def test_a_user_message_ends_the_run(self):
        messages = _repaired(
            [
                {'role': 'assistant', 'tool_calls': [_call('a')]},
                {'role': 'user', 'content': 'never mind'},
                {'role': 'tool', 'tool_call_id': 'a', 'content': 'late'},
            ]
        )

        assert messages[-1] == {'role': 'system', 'content': '[Tool result]: late'}

    def test_no_messages_is_nothing_to_do(self):
        assert UpstreamConversation(messages=[]).repaired() == []

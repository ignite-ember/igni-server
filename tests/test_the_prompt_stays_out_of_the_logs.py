"""No logger call anywhere writes a request body. F111.

F83 found that every accepted GitHub webhook logged its full payload —
commit messages and an author email per commit — and replaced two
literal string checks with an AST rule. That rule was correct and it was
pointed at two directories.

`/v1/chat/completions` is three directories away, and it logged
`json.dumps(body)` whenever the upstream answered 400. The body of a chat
completion **is the prompt**: the question, whatever source the CLI
attached as context, the previous turn's tool output. The most sensitive
content this product handles, on the path production actually runs — and
F102 had just made the logs structured and shippable, so it landed in
whatever aggregator the customer points at us.

The log line said `(first 500 chars)`. `json.dumps(body)` does not
truncate. So an operator reading it would believe the exposure was
bounded at 500 characters while a 100 KB prompt went in whole.

This file is the rule with the scope corrected: **every Python file under
`app/`**, not two directories.
"""

from __future__ import annotations

import pathlib

import pytest

from app.services.ai.request_shape import describe
from tests.log_hygiene import CONTENT_FIELDS, offenders_in, RAW_NAMES

_ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestTheRuleCoversTheWholeApplication:
    def test_no_logger_call_writes_a_request_body(self):
        offenders = [
            offender
            for path in sorted((_ROOT / 'app').rglob('*.py'))
            for offender in offenders_in(path.read_text(), str(path.relative_to(_ROOT)))
        ]

        assert not offenders, (
            f'these logger calls write a raw request object: {offenders}. A chat body is the '
            'prompt and a webhook payload is commit messages plus author emails — log the '
            'shape instead (app/services/ai/request_shape.py, '
            'app/services/webhooks/payload_shape.py).'
        )

    def test_the_sweep_reads_the_whole_tree(self):
        """A rule that scans nothing passes forever, which is how the
        webhook-only scope survived: it was passing, on four files."""
        scanned = list((_ROOT / 'app').rglob('*.py'))

        assert len(scanned) > 150, f'only {len(scanned)} files found under app/'
        assert any('chat.py' in str(p) for p in scanned), 'the chat path is not in the sweep'

    @pytest.mark.parametrize(
        'source',
        [
            'logger.info(f"payload: {body.decode()}")',
            'logger.info("payload: %s", json.dumps(payload))',
            'logger.warning(f"{payload}")',
            'logger.error("got %r", body)',
            'logger.debug("x", extra={"p": payload})',
            'logger.error("body %s", json.dumps(body))',
        ],
        ids=['decode', 'dumps-payload', 'fstring', 'repr', 'extra', 'dumps-body'],
    )
    def test_it_still_catches_every_spelling_f83_pinned(self, source):
        """Inherited from F83 verbatim. The detector was refined to stop
        flagging field access, and these are what stop that refinement
        from becoming a hole."""
        assert offenders_in(source, 'probe.py'), source

    @pytest.mark.parametrize(
        'source',
        [
            'logger.info("payload %s", describe_payload(payload))',
            'logger.info("size=%d", len(body))',
            'logger.info("event=%s", x_github_event)',
            'logger.info("repo %s", body.repository_id)',
            "logger.info('App %r registered', payload['slug'])",
            'logger.error("upstream 400 — %s", describe_chat_request(body))',
        ],
        ids=['describe', 'len', 'unrelated', 'attribute', 'subscript', 'chat-shape'],
    )
    def test_it_allows_reaching_in_for_a_scalar(self, source):
        """The refinement that made whole-app scope possible. Six of the
        seven hits across `app/` were field access — and a rule with six
        false positives is a rule somebody deletes."""
        assert not offenders_in(source, 'probe.py'), source

    @pytest.mark.parametrize(
        'source',
        [
            "logger.info('%s', body['messages'])",
            "logger.info('%s', payload['commits'])",
            'logger.info("%s", body.messages)',
            "logger.info('%s', body[key])",
        ],
        ids=['messages', 'commits', 'attribute-content', 'dynamic-key'],
    )
    def test_reaching_in_for_content_is_still_a_leak(self, source):
        """`body['messages']` is the prompt and `payload['commits']` is
        every commit message in a push. Narrowing to a field only helps
        when the field is not itself the content — and a non-literal key
        could be anything."""
        assert offenders_in(source, 'probe.py'), source

    def test_the_vocabulary_is_not_empty(self):
        """Both frozensets are what the rule matches on. Empty ones would
        make every test above pass while checking nothing."""
        assert RAW_NAMES >= {'body', 'payload'}
        assert CONTENT_FIELDS >= {'messages', 'commits'}


class TestTheChatShapeSaysEnoughToDebugA400:
    """An upstream 400 is nearly always a malformed message sequence, a
    tool_call with no reply, or a context length. The shape has to answer
    those without the text, or the next person puts the body back."""

    _BODY = {
        'model': 'qwen-coder',
        'stream': True,
        'max_tokens': 4096,
        'messages': [
            {'role': 'system', 'content': 'You are a coding agent.'},
            {'role': 'user', 'content': 'Refactor this: SECRET_TOKEN=hunter2'},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'c1', 'type': 'function'}]},
            {'role': 'tool', 'content': 'proprietary source code here'},
        ],
        'tools': [{'type': 'function'}, {'type': 'function'}],
    }

    def test_it_names_the_model(self):
        assert "model='qwen-coder'" in describe(self._BODY)

    def test_it_gives_the_role_sequence(self):
        """The thing a 400 is usually about."""
        assert 'roles=system>user>assistant>tool' in describe(self._BODY)

    def test_it_counts_messages_tools_and_calls(self):
        described = describe(self._BODY)

        assert 'messages=4' in described
        assert 'tools=2' in described
        assert 'tool_calls=1' in described

    def test_it_gives_the_size_that_a_context_error_needs(self):
        assert 'content_chars=' in describe(self._BODY)

    def test_it_contains_no_message_content(self):
        """The whole point. Including the parts that look like secrets,
        because a prompt is exactly where those end up."""
        described = describe(self._BODY)

        for content in ('You are a coding agent', 'Refactor this', 'hunter2', 'proprietary source'):
            assert content not in described, f'{content!r} reached the log line'

    def test_it_survives_a_malformed_body(self):
        """It runs on the error path, so raising here would replace an
        upstream 400 with a 500 from the logging."""
        for body in ({}, {'messages': 'not-a-list'}, {'messages': [None, 3, 'x']}, {'model': None}):
            assert isinstance(describe(body), str)

    def test_it_handles_the_multipart_content_form(self):
        """`content` can be a list of parts. Counting their text without
        printing it is the same rule one level down."""
        described = describe(
            {
                'model': 'm',
                'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'secret words'}]}],
            }
        )

        assert 'content_chars=12' in described
        assert 'secret words' not in described

    def test_a_long_conversation_does_not_produce_a_long_line(self):
        """The bug the old line had twice over: unbounded output, and a
        message claiming a bound."""
        described = describe({'model': 'm', 'messages': [{'role': 'user', 'content': 'x' * 50_000}] * 200})

        assert len(described) < 400, f'{len(described)} characters'


class TestTheModelListingClaimsNothingItDoesNotDo:
    def test_it_takes_no_org_argument(self):
        """It took one, ignored it, and was cached on it — so the cache
        held up to 10,000 identical entries keyed on an argument that
        changed nothing, and three separate places read as though model
        access were scoped per organisation."""
        import inspect

        from app.api.base_v1.endpoints.client.chat import _resolve_models_for

        wrapped = getattr(_resolve_models_for, '__wrapped__', _resolve_models_for)
        assert not inspect.signature(
            wrapped
        ).parameters, 'the model lookup takes a parameter again; if it is an org, make sure it actually filters'

    def test_the_endpoint_does_not_advertise_an_org_override(self):
        """Read from the OpenAPI schema, not the source.

        Grepping the file for `org override` matched the comment
        explaining that the claim was removed, and the corrected
        description saying there is no override. Fifth time in this
        review a source sweep has matched prose about the thing instead
        of the thing — and here the published description is what a
        customer actually reads, so it is also the better target.
        """
        import app.main as main

        described = main.app.openapi()['paths']['/v1/chat/models']['get']['description']

        # Only the positive claim is asserted, and the negative one has
        # been abandoned on purpose. `'org override' not in description`
        # failed against the description I wrote to *deny* an org
        # override, because "per-org override" contains it. A negative
        # substring assertion on prose cannot be made to work: any
        # sentence denying a phrase contains the phrase. What can be
        # checked is that the description commits to the true behaviour,
        # and that the function takes no org argument — which is the
        # test above, and is about code rather than words.
        assert (
            'not per-org' in described.lower()
        ), f'the published description does not state that model access is global: {described!r}'

    async def test_it_still_requires_an_active_member(self, client):
        """Removing the org *filter* must not remove the org *check* —
        that is what makes this endpoint require a current member rather
        than anybody holding a token."""
        source = (_ROOT / 'app/api/base_v1/endpoints/client/chat.py').read_text()
        listing = source[source.index('async def list_chat_models') : source.index('async def chat_completions')]

        assert 'resolve_user_org' in listing


pytestmark = pytest.mark.anyio

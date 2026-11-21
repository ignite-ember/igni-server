"""A usage row's output is what the provider bills as output, thinking included, and says how much was thinking.

Vertex AI's Gemini, on its OpenAI-compatible endpoint, reports thinking
outside `completion_tokens` while `total_tokens` counts it, and Google
bills it as output (R529, measured live: `completion_tokens` 1,
`reasoning_tokens` 117, `total_tokens` 125 for a one-word answer). The
row said 1. OpenAI, Azure OpenAI and vLLM count thinking inside
`completion_tokens`, and Anthropic inside `output_tokens`: nothing is
added for them, or their thinking would be billed twice.

The non-streamed answer also lost `completion_tokens_details` on its way
back to the client, which the streamed answer kept.
"""

from __future__ import annotations

import asyncio
import json

from cryptography.fernet import Fernet
import httpx
import pytest
from sqlalchemy import text

from alembic import command

from app.config import settings
from app.db.sql.chat_usage import ChatUsageDB
from app.db.sql.tenant_migrator import TenantMigrator
from app.db.sql.tenant_registry import TenantRegistryDB
from app.services.ai.backend import BackendLease
from app.services.ai.chat.services import chat_services, ChatServices
from app.services.ai.chat.upstream_client import UpstreamChatClient
from app.services.ai.messages.upstream import MessagesUpstream
from app.services.ai.providers.auth import AuthMethod
from app.services.ai.providers.kinds import BackendKind
from tests.chat_proxy import NO_BACKOFF, pools, SpyPool
from tests.cloud_fakes import FakeCloud, lease_of, ScriptedProvider
from tests.conftest import auth_header, create_org, create_user, make_token

#: Vertex AI's answer to "say ready", as R529 recorded it: thinking outside the completion, inside the total.
_GEMINI_USAGE = {
    'prompt_tokens': 7,
    'completion_tokens': 1,
    'total_tokens': 125,
    'completion_tokens_details': {'reasoning_tokens': 117},
    'extra_properties': {'google': {'traffic_type': 'ON_DEMAND'}},
}
#: OpenAI's reasoning models: thinking inside the completion, broken out, more details beside it.
_OPENAI_USAGE = {
    'prompt_tokens': 10,
    'completion_tokens': 50,
    'total_tokens': 60,
    'completion_tokens_details': {'reasoning_tokens': 30, 'accepted_prediction_tokens': 0, 'audio_tokens': 0},
}


def _completion(usage: dict) -> dict:
    return {
        'id': 'chatcmpl-1',
        'object': 'chat.completion',
        'created': 1,
        'model': 'public',
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ready'}, 'finish_reason': 'stop'}],
        'usage': usage,
    }


def _stream(usage: dict) -> str:
    chunks = [
        {'id': 'c', 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'content': 'ready'}}]},
        {'id': 'c', 'object': 'chat.completion.chunk', 'choices': [], 'usage': usage},
    ]
    return ''.join(f'data: {json.dumps(chunk)}\n\n' for chunk in chunks) + 'data: [DONE]\n\n'


def _lease(kind: BackendKind) -> BackendLease:
    if kind is BackendKind.ANTHROPIC:
        return lease_of(kind, AuthMethod.API_KEY, model='m', api_key='sk-ant')
    return lease_of(kind, AuthMethod.NONE if kind is BackendKind.OPENAI else AuthMethod.AMBIENT, model='m')


async def _rows(org_db, org: str) -> list[tuple]:
    async with org_db(org) as session:
        found = await session.execute(
            text('SELECT input_tokens, output_tokens, total_tokens, reasoning_tokens, kind FROM chat_usage')
        )
        return [tuple(row) for row in found.all()]


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


@pytest.fixture
async def person(db) -> tuple[str, str]:
    await create_user(db, user_id='ada', email='ada@test.com')
    org = await create_org(db, owner_user_id='ada', slug='acme', display_name='Acme')
    return make_token('ada', org_id=str(org.id)), str(org.id)


@pytest.fixture
def route(app):
    """The app's chat services over one backend of *kind*, answering *answer*."""

    def build(kind: BackendKind, answer: httpx.Response) -> ScriptedProvider:
        provider = ScriptedProvider(answer)
        credentials = FakeCloud().credentials()
        services = ChatServices(
            pools=pools(SpyPool(acquired=[], on=_lease(kind))),  # type: ignore[arg-type]
            upstream=UpstreamChatClient(credentials=credentials, client_factory=provider, complete_timeout=5),
            messages_upstream=MessagesUpstream(credentials=credentials, client_factory=provider),
            credentials=credentials,
            retry=NO_BACKOFF,
        )
        app.dependency_overrides[chat_services] = lambda: services
        return provider

    yield build
    app.dependency_overrides.pop(chat_services, None)


async def _ask(client, token: str, *, stream: bool = False) -> httpx.Response:
    body = {'model': 'm', 'messages': [{'role': 'user', 'content': 'say ready'}], 'stream': stream}
    resp = await client.post('/chat/completions', headers=auth_header(token), json=body)
    assert resp.status_code == 200, resp.text
    return resp


class TestGeminiOnVertex:
    async def test_the_output_counts_the_thinking_and_the_thinking_is_recorded(self, client, person, route, org_db):
        token, org = person
        route(BackendKind.VERTEX_OPENAI, httpx.Response(200, json=_completion(_GEMINI_USAGE)))

        await _ask(client, token)

        assert await _rows(org_db, org) == [(7, 118, 125, 117, 'vertex_openai')]

    async def test_a_stream_is_counted_the_same(self, client, person, route, org_db):
        token, org = person
        route(BackendKind.VERTEX_OPENAI, httpx.Response(200, text=_stream(_GEMINI_USAGE)))

        await _ask(client, token, stream=True)

        assert await _rows(org_db, org) == [(7, 118, 125, 117, 'vertex_openai')]

    async def test_an_answer_without_thinking_records_none(self, client, person, route, org_db):
        """R529's tool round trip: total is prompt plus completion, and no breakdown."""
        token, org = person
        usage = {'prompt_tokens': 144, 'completion_tokens': 15, 'total_tokens': 159}
        route(BackendKind.VERTEX_OPENAI, httpx.Response(200, json=_completion(usage)))

        await _ask(client, token)

        assert await _rows(org_db, org) == [(144, 15, 159, None, 'vertex_openai')]


class TestThinkingAlreadyInTheCompletion:
    """Each provider kind that counts thinking inside the completion: recorded, never added twice."""

    @pytest.mark.parametrize('kind', [BackendKind.OPENAI, BackendKind.AZURE_OPENAI])
    async def test_is_recorded_and_not_added_again(self, client, person, route, org_db, kind):
        token, org = person
        route(kind, httpx.Response(200, json=_completion(_OPENAI_USAGE)))

        await _ask(client, token)

        assert await _rows(org_db, org) == [(10, 50, 60, 30, kind.value)]

    async def test_a_vllm_node_with_a_reasoning_parser_is_not_added_again(self, client, person, route, org_db):
        token, org = person
        usage = {'prompt_tokens': 10, 'completion_tokens': 40, 'total_tokens': 50}
        usage['completion_tokens_details'] = {'reasoning_tokens': 25}
        route(BackendKind.OPENAI, httpx.Response(200, text=_stream(usage)))

        await _ask(client, token, stream=True)

        assert await _rows(org_db, org) == [(10, 40, 50, 25, 'openai')]

    async def test_a_node_that_reports_no_thinking_records_none(self, client, person, route, org_db):
        token, org = person
        usage = {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 7}
        route(BackendKind.OPENAI, httpx.Response(200, json=_completion(usage)))

        await _ask(client, token)

        assert await _rows(org_db, org) == [(3, 4, 7, None, 'openai')]

    async def test_a_larger_total_without_reported_thinking_is_not_taken_for_thinking(
        self, client, person, route, org_db
    ):
        token, org = person
        usage = {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 20}
        route(BackendKind.OPENAI, httpx.Response(200, json=_completion(usage)))

        await _ask(client, token)

        assert await _rows(org_db, org) == [(3, 4, 20, None, 'openai')]

    async def test_claude_counts_thinking_in_its_output_and_reports_no_breakdown(self, client, person, route, org_db):
        token, org = person
        message = {'type': 'message', 'content': [], 'usage': {'input_tokens': 5, 'output_tokens': 300}}
        route(BackendKind.ANTHROPIC, httpx.Response(200, json=message))
        body = {'model': 'm', 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': 'hi'}]}

        resp = await client.post('/messages', headers=auth_header(token), json=body)

        assert resp.status_code == 200, resp.text
        assert await _rows(org_db, org) == [(5, 300, 305, None, 'anthropic')]


class TestTheNonStreamedAnswer:
    async def test_passes_the_breakdown_back_as_the_provider_sent_it(self, client, person, route):
        token, _org = person
        route(BackendKind.AZURE_OPENAI, httpx.Response(200, json=_completion(_OPENAI_USAGE)))

        resp = await _ask(client, token)

        assert resp.json()['usage'] == _OPENAI_USAGE

    async def test_passes_geminis_thinking_count_back(self, client, person, route):
        token, _org = person
        route(BackendKind.VERTEX_OPENAI, httpx.Response(200, json=_completion(_GEMINI_USAGE)))

        resp = await _ask(client, token)

        assert resp.json()['usage']['completion_tokens_details'] == {'reasoning_tokens': 117}
        # What the client is told is the provider's own count; only the row is normalised.
        assert resp.json()['usage']['completion_tokens'] == 1

    async def test_a_breakdown_it_cannot_read_never_costs_the_answer(self, client, person, route, org_db):
        token, org = person
        usage = {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 7}
        usage['completion_tokens_details'] = {'reasoning_tokens': 'lots'}
        route(BackendKind.OPENAI, httpx.Response(200, json=_completion(usage)))

        resp = await _ask(client, token)

        assert resp.json()['choices'][0]['message']['content'] == 'ready'
        assert await _rows(org_db, org) == [(3, 4, 7, None, 'openai')]


class TestThePortalsUsage:
    async def test_sums_the_thinking_and_says_none_when_nothing_reported_it(self, client, person, org_db):
        token, org = person
        usage = ChatUsageDB()
        common = {'user_id': 'ada', 'org_id': org, 'model': 'm', 'duration': 1.0, 'stream': False}

        empty = (await client.get('/portal/usage', headers=auth_header(token))).json()
        await usage.log(**common, input_tokens=7, output_tokens=118, total_tokens=125, reasoning_tokens=117)
        await usage.log(**common, input_tokens=3, output_tokens=4, total_tokens=7)
        await usage.log(**common, input_tokens=10, output_tokens=50, total_tokens=60, reasoning_tokens=30)
        full = (await client.get('/portal/usage', headers=auth_header(token))).json()

        assert empty['total_reasoning_tokens'] is None
        assert (full['total_output_tokens'], full['total_reasoning_tokens']) == (172, 147)
        assert [(d['output_tokens'], d['reasoning_tokens']) for d in full['data']] == [(172, 147)]


class TestTheMigration:
    """t0034 adds `chat_usage.reasoning_tokens`, null for the rows already there, and takes it away again."""

    async def _columns(self, org_db, org: str) -> dict[str, str]:
        async with org_db(org) as session:
            found = await session.execute(
                text(
                    'SELECT column_name, is_nullable FROM information_schema.columns '
                    "WHERE table_name = 'chat_usage' AND table_schema = current_schema()"
                )
            )
            return {str(name): str(nullable) for name, nullable in found.all()}

    async def test_adds_a_nullable_column_and_leaves_existing_rows_null(self, person, org_db):
        _token, org = person
        await ChatUsageDB().log(
            user_id='ada',
            org_id=org,
            model='m',
            input_tokens=7,
            output_tokens=1,
            total_tokens=125,
            duration=None,
            stream=False,
        )
        config = TenantMigrator().alembic_config(await TenantRegistryDB().database_of(org))

        await asyncio.to_thread(command.downgrade, config, 't0033')
        assert 'reasoning_tokens' not in await self._columns(org_db, org)

        await asyncio.to_thread(command.upgrade, config, 'head')
        assert (await self._columns(org_db, org))['reasoning_tokens'] == 'YES'
        async with org_db(org) as session:
            kept = (await session.execute(text('SELECT output_tokens, reasoning_tokens FROM chat_usage'))).one()
        assert tuple(kept) == (1, None)


class TestTheVertexRowsWrittenBefore:
    """t0035 puts Gemini's thinking back into the rows written before t0034, keeps what they held, and undoes it."""

    #: (id, kind, input, output, total), as rows written before t0034 hold them.
    _ROWS = [
        ('gemini', 'vertex_openai', 7, 1, 125),
        ('gpt', 'openai', 10, 50, 70),
        ('gemini-no-thinking', 'vertex_openai', 144, 15, 159),
        ('gemini-odd', 'vertex_openai', 10, 20, 25),
    ]
    _CORRECTED = {
        'gemini': (118, 117, 1),
        'gpt': (50, None, None),
        'gemini-no-thinking': (15, None, None),
        'gemini-odd': (20, None, None),
    }
    _ORIGINAL = {
        'gemini': (1, None),
        'gpt': (50, None),
        'gemini-no-thinking': (15, None),
        'gemini-odd': (20, None),
    }

    @pytest.fixture
    async def at_t0034(self, person, org_db):
        """An organisation's database at t0034, holding rows as they were written before it."""
        _token, org = person
        async with org_db(org) as session:
            await session.execute(text('SELECT 1'))
        config = TenantMigrator().alembic_config(await TenantRegistryDB().database_of(org))
        await asyncio.to_thread(command.downgrade, config, 't0034')
        async with org_db(org) as session:
            for row_id, kind, used_in, used_out, total in self._ROWS:
                await session.execute(
                    text(
                        'INSERT INTO chat_usage (id, user_id, org_id, model, kind, input_tokens, output_tokens, '
                        'total_tokens, stream) VALUES (:id, :u, :o, :m, :k, :i, :out, :t, 0)'
                    ),
                    {'id': row_id, 'u': 'ada', 'o': org, 'm': 'm', 'k': kind}
                    | {'i': used_in, 'out': used_out, 't': total},
                )
        return org, config

    async def _corrected(self, org_db, org: str) -> dict[str, tuple]:
        async with org_db(org) as session:
            found = await session.execute(
                text('SELECT id, output_tokens, reasoning_tokens, output_tokens_reported FROM chat_usage')
            )
            return {str(row_id): tuple(rest) for row_id, *rest in found.all()}

    async def test_corrects_only_vertex_rows_whose_total_shows_the_thinking(self, at_t0034, org_db):
        org, config = at_t0034

        await asyncio.to_thread(command.upgrade, config, 'head')

        assert await self._corrected(org_db, org) == self._CORRECTED

    async def test_running_it_again_changes_nothing(self, at_t0034, org_db):
        org, config = at_t0034
        await asyncio.to_thread(command.upgrade, config, 'head')

        await asyncio.to_thread(command.stamp, config, 't0034')
        await asyncio.to_thread(command.upgrade, config, 'head')

        assert await self._corrected(org_db, org) == self._CORRECTED

    async def test_the_downgrade_restores_what_the_rows_held(self, at_t0034, org_db):
        org, config = at_t0034
        await asyncio.to_thread(command.upgrade, config, 'head')

        await asyncio.to_thread(command.downgrade, config, 't0034')

        async with org_db(org) as session:
            found = await session.execute(text('SELECT id, output_tokens, reasoning_tokens FROM chat_usage'))
            assert {str(row_id): tuple(rest) for row_id, *rest in found.all()} == self._ORIGINAL

"""An app's and a space's VM call the model with their own token; the usage is their organisation's.

`/v1/chat/completions` takes a person's session, an app's token or a
space's. A producer's usage row names it — `app:<id>` / `space:<id>` —
in its organisation, with the app, so an app's use and its spaces' sum
(`docs/service-identity.md` §11, §17).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import text

from app.api.base_v1.schema.chat import CompletionUsage
from app.config import settings
from app.services.ai.chat.schemas import UpstreamCompletion
from app.services.ai.chat.services import chat_services, ChatServices
from app.services.ai.schemas import BackendLease
from tests.cloud_world import CloudOrg
from tests.conftest import auth_header

pytestmark = pytest.mark.anyio

EXCHANGE = 'urn:ietf:params:oauth:grant-type:token-exchange'
ACCESS = 'urn:ietf:params:oauth:token-type:access_token'


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def node(app):
    """The upstream: answers every completion with 7 tokens of usage."""
    model = FakeModel()
    services = ChatServices(upstream=model)
    app.dependency_overrides[chat_services] = lambda: services
    yield model
    app.dependency_overrides.pop(chat_services, None)


class FakeModel:
    """The pool and the upstream: records who asked, and reports usage as a real answer would."""

    def __init__(self) -> None:
        self.pool = AsyncMock()
        self.pool.acquire = AsyncMock(
            return_value=BackendLease(
                lease_id='l1', backend_id='gpu-01', api_key=None, base_url='http://gpu-01/v1', model='m'
            )
        )

    async def complete(self, *, lease, request) -> UpstreamCompletion:
        return UpstreamCompletion(usage=CompletionUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7))


async def _chat(client, headers: dict[str, str], model: FakeModel):
    with patch('app.services.ai.pool_manager.PoolManager.get_pool', new=lambda _self: model.pool):
        return await client.post(
            '/chat/completions', headers=headers, json={'messages': [{'role': 'user', 'content': 'hi'}]}
        )


async def _usage(org_db, org_id: str) -> list[tuple[str, str | None, int]]:
    async with org_db(org_id) as session:
        rows = await session.execute(text('SELECT user_id, app_id, total_tokens FROM chat_usage ORDER BY user_id'))
        return [(str(u), a, int(t)) for u, a, t in rows.all()]


async def test_an_app_and_its_space_are_served_and_their_usage_is_the_orgs(client, org_db, node):
    acme = await CloudOrg(client).create()
    app = await acme.app()
    app_headers = await acme.app_token(app)
    space = (await client.put(f'/apps/{app.client_id}/spaces/cust-1', headers=app_headers)).json()
    space_token = (
        await client.post(
            '/oauth/token',
            data={
                'grant_type': EXCHANGE,
                'subject_token': app_headers['Authorization'].removeprefix('Bearer '),
                'subject_token_type': ACCESS,
                'space': space['space_id'],
            },
        )
    ).json()['access_token']
    model = node

    by_app = await _chat(client, app_headers, model)
    by_space = await _chat(client, auth_header(space_token), model)
    by_person = await _chat(client, acme.admin, model)

    assert (by_app.status_code, by_space.status_code, by_person.status_code) == (200, 200, 200)
    asked = [call.args[0] for call in model.pool.acquire.call_args_list]
    assert asked == [f'app:{app.client_id}', f'space:{space["space_id"]}', acme.admin_id]
    assert await _usage(org_db, acme.org_id) == sorted(
        [
            (f'app:{app.client_id}', app.client_id, 7),
            (f'space:{space["space_id"]}', app.client_id, 7),
            (acme.admin_id, None, 7),
        ]
    )


async def test_a_deleted_spaces_token_is_refused(client, node):
    acme = await CloudOrg(client).create()
    app = await acme.app()
    app_headers = await acme.app_token(app)
    space = (await client.put(f'/apps/{app.client_id}/spaces/cust-1', headers=app_headers)).json()
    token = (
        await client.post(
            '/oauth/token',
            data={
                'grant_type': EXCHANGE,
                'subject_token': app_headers['Authorization'].removeprefix('Bearer '),
                'subject_token_type': ACCESS,
                'space': space['space_id'],
            },
        )
    ).json()['access_token']
    await client.delete(f'/apps/{app.client_id}/spaces/cust-1', headers=app_headers)

    response = await _chat(client, auth_header(token), node)

    assert response.status_code == 401

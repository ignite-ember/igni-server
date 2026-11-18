"""A Vertex call names the project its quota is charged to.

Vertex refuses a call that names no quota project: "the
aiplatform.googleapis.com API requires a quota project". The project that
serves the model is the one that carries the quota, so it travels with
the call — on the OpenAI wire and on Claude's own wire alike — and no
other provider is asked for one.
"""

from __future__ import annotations

from app.services.ai.backend import BackendLease
from app.services.ai.messages.calls import CallerHeaders, MessagesCalls, MessagesRequest
from app.services.ai.providers.auth import AuthMethod
from app.services.ai.providers.calls import ProviderCalls
from app.services.ai.providers.kinds import BackendKind
from tests.cloud_fakes import backend, FakeCloud

_PROJECT = 'ember-dev-491411'
_VERTEX = {'kind': 'vertex_openai', 'project': _PROJECT, 'region': 'europe-west4'}
_CLAUDE_ON_VERTEX = {'kind': 'vertex_anthropic', 'project': _PROJECT, 'region': 'europe-west4'}
_BODY = {'model': 'claude', 'max_tokens': 64, 'messages': [{'role': 'user', 'content': 'hi'}]}


def _lease(fields: dict[str, object]) -> BackendLease:
    """The lease the pool hands out for a backend: where the project travels."""
    return BackendLease.for_backend(backend(**fields), lease_id='l', model='claude')


class TestTheQuotaProjectTravels:
    async def test_the_lease_carries_the_projects_name(self):
        assert _lease(_VERTEX).project == _PROJECT

    async def test_a_vertex_call_names_the_project_its_quota_is_charged_to(self):
        call = await ProviderCalls(FakeCloud().credentials()).prepare(_lease(_VERTEX), body={})

        assert call.headers['x-goog-user-project'] == _PROJECT

    async def test_claude_on_vertex_names_the_project_too(self):
        call = await MessagesCalls(FakeCloud().credentials()).prepare(
            _lease(_CLAUDE_ON_VERTEX),
            request=MessagesRequest(**_BODY),
            stream=False,
            asked=CallerHeaders(),
        )

        assert call.headers['x-goog-user-project'] == _PROJECT

    async def test_another_provider_is_asked_for_no_project(self):
        lease = BackendLease.for_backend(
            backend(
                id='b',
                models=['claude'],
                base_url='http://node.invalid/v1',
                kind=BackendKind.OPENAI,
                auth=AuthMethod.API_KEY,
                api_key='sk',
            ),
            lease_id='l',
            model='claude',
        )

        call = await ProviderCalls(FakeCloud().credentials()).prepare(lease, body={})

        assert 'x-goog-user-project' not in call.headers

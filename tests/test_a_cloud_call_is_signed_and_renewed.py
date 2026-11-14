"""A call to a cloud backend is signed with its credential, which is renewed once when refused.

`ProviderCalls` decides each call's address, headers and body: a static
key goes in the header its kind reads, a method the server signs for gets
the token its settings and secret obtain, and the model is sent under the
provider's own name. A provider that refuses the token (401, 403) gets one
renewed token on the same backend before the refusal counts; a static key
is never asked again. A cloud backend's health is whether the server can
get its token — the provider itself is not called. No cloud is reached.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.services.ai.chat.upstream_client import UpstreamChatClient
from app.services.ai.health import NodeHealthProbe
from app.services.ai.providers.auth import AuthMethod
from app.services.ai.providers.calls import ProviderCalls, UpstreamCall
from app.services.ai.providers.kinds import BackendKind
from app.services.ai.schemas import HealthState, NodeHealth
from app.services.ai.upstream import UpstreamCredentialsUnavailable, UpstreamError
from tests.chat_proxy import drain, request
from tests.cloud_fakes import AZURE, backend, FakeCloud, lease_of, SERVICE_ACCOUNT, VERTEX


class TestTheCallIsAddressedAndSigned:
    async def test_an_openai_backend_is_called_exactly_as_before(self):
        call = await ProviderCalls(FakeCloud().credentials()).prepare(
            lease_of(auth=AuthMethod.API_KEY, api_key='sk'), body={'model': 'public', 'x': 1}
        )
        assert call == UpstreamCall(
            url='http://node.invalid/v1/chat/completions',
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer sk'},
            body={'model': 'public', 'x': 1},
        )

    async def test_the_provider_is_sent_its_own_name_for_the_model(self):
        lease = lease_of(model_ids={'public': 'my-azure-deployment'})
        call = await ProviderCalls(FakeCloud().credentials()).prepare(lease, body={'model': 'public'})
        assert call.body == {'model': 'my-azure-deployment'}

    async def test_an_azure_resource_key_goes_in_azures_own_header_and_no_token_is_fetched(self):
        cloud = FakeCloud()
        lease = lease_of(BackendKind.AZURE_OPENAI, AuthMethod.API_KEY, api_key='az-key')
        call = await ProviderCalls(cloud.credentials()).prepare(lease, body={})
        assert call.headers == {'Content-Type': 'application/json', 'api-key': 'az-key'}
        assert cloud.fetches == []

    @pytest.mark.parametrize(
        ('kind', 'auth', 'settings', 'secret'),
        [
            (BackendKind.AZURE_OPENAI, AuthMethod.AMBIENT, {}, None),
            (BackendKind.AZURE_OPENAI, AuthMethod.MANAGED_IDENTITY, {'client_id': 'c'}, None),
            (BackendKind.AZURE_OPENAI, AuthMethod.CLIENT_SECRET, {'tenant_id': 't', 'client_id': 'c'}, 'shh'),
            (BackendKind.VERTEX_OPENAI, AuthMethod.AMBIENT, {}, None),
            (BackendKind.VERTEX_OPENAI, AuthMethod.IMPERSONATE, {'service_account': 'sa@p.iam'}, None),
            (BackendKind.VERTEX_OPENAI, AuthMethod.SERVICE_ACCOUNT_KEY, {}, SERVICE_ACCOUNT),
        ],
    )
    async def test_a_token_method_is_signed_with_what_its_settings_and_secret_obtain(
        self, kind, auth, settings, secret
    ):
        cloud = FakeCloud()
        lease = lease_of(kind, auth, auth_settings=settings, credential=secret)
        call = await ProviderCalls(cloud.credentials()).prepare(lease, body={})
        assert call.headers == {'Content-Type': 'application/json', 'Authorization': 'Bearer tok-1'}
        assert cloud.fetches == [(kind, auth, settings, secret)]

    async def test_no_token_is_a_503_naming_no_secret(self):
        lease = lease_of(BackendKind.VERTEX_OPENAI, AuthMethod.SERVICE_ACCOUNT_KEY, credential=SERVICE_ACCOUNT)
        with pytest.raises(UpstreamCredentialsUnavailable) as raised:
            await ProviderCalls(FakeCloud(fail=True).credentials()).prepare(lease, body={})
        assert (raised.value.answer_status, raised.value.worth_another_node) == (503, True)
        assert raised.value.sentence == 'The server could not obtain credentials for the model provider.'
        assert str(raised.value) == 'No credentials for the vertex_openai backend'


class _Provider:
    """A provider that refuses the first *refusals* calls with *status*, then answers."""

    def __init__(self, *, refusals: int, status: int = 401, stream: bool = False) -> None:
        self.refusals = refusals
        self.status = status
        self.stream = stream
        self.seen: list[str | None] = []

    def __call__(self, **kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer), **kwargs)

    def _answer(self, sent: httpx.Request) -> httpx.Response:
        self.seen.append(sent.headers.get('authorization'))
        if len(self.seen) <= self.refusals:
            return httpx.Response(self.status, text='refused')
        if self.stream:
            return httpx.Response(200, text='data: {"choices": []}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={'id': 'x', 'choices': [], 'usage': {}})


_VERTEX_LEASE = lease_of(BackendKind.VERTEX_OPENAI, AuthMethod.AMBIENT)
_AZURE_LEASE = lease_of(BackendKind.AZURE_OPENAI, AuthMethod.MANAGED_IDENTITY, auth_settings={'client_id': 'c'})


class TestARefusedTokenIsRenewedOnce:
    @pytest.mark.parametrize('status', [401, 403])
    async def test_a_completion_is_retried_once_with_a_new_token(self, status):
        provider = _Provider(refusals=1, status=status)
        client = UpstreamChatClient(credentials=FakeCloud().credentials(), client_factory=provider)

        completion = await client.complete(lease=_VERTEX_LEASE, request=request())

        assert provider.seen == ['Bearer tok-1', 'Bearer tok-2']
        assert completion.id == 'x'

    async def test_a_second_refusal_counts_as_the_refusal_it_is(self):
        provider = _Provider(refusals=2)
        client = UpstreamChatClient(credentials=FakeCloud().credentials(), client_factory=provider)

        with pytest.raises(UpstreamError) as raised:
            await client.complete(lease=_VERTEX_LEASE, request=request())

        assert raised.value.status_code == 401
        assert provider.seen == ['Bearer tok-1', 'Bearer tok-2']

    async def test_a_static_key_refused_is_not_asked_again(self):
        provider = _Provider(refusals=1)
        client = UpstreamChatClient(credentials=FakeCloud().credentials(), client_factory=provider)

        with pytest.raises(UpstreamError) as raised:
            await client.complete(lease=lease_of(auth=AuthMethod.API_KEY, api_key='sk'), request=request())

        assert raised.value.status_code == 401
        assert provider.seen == ['Bearer sk']

    async def test_a_stream_is_retried_once_with_a_new_token(self):
        provider = _Provider(refusals=1, stream=True)
        client = UpstreamChatClient(credentials=FakeCloud().credentials(), client_factory=provider)

        lines = await drain(client.stream(lease=_AZURE_LEASE, request=request(stream=True)))

        assert provider.seen == ['Bearer tok-1', 'Bearer tok-2']
        assert [line.frame for line in lines] == ['data: {"choices": []}\n\n', 'data: [DONE]\n\n']

    async def test_a_stream_refused_twice_raises_the_401(self):
        provider = _Provider(refusals=2, stream=True)
        client = UpstreamChatClient(credentials=FakeCloud().credentials(), client_factory=provider)

        with pytest.raises(UpstreamError) as raised:
            await drain(client.stream(lease=_AZURE_LEASE, request=request(stream=True)))

        assert raised.value.status_code == 401
        assert provider.seen == ['Bearer tok-1', 'Bearer tok-2']


def _unique(fields: dict) -> dict:
    """*fields* with an id no other test's cached health shares."""
    return {**fields, 'id': f'cloud-{uuid.uuid4().hex[:8]}'}


class TestACloudBackendsHealthIsItsToken:
    async def test_a_token_obtained_is_healthy_and_the_provider_is_not_called(self):
        cloud = FakeCloud()
        probed = backend(**_unique(VERTEX))

        health = await NodeHealthProbe(credentials=cloud.credentials()).check_all([probed])

        assert health[probed.id].state is HealthState.HEALTHY
        assert health[probed.id].detail == 'The server holds a token for this provider'
        assert len(cloud.fetches) == 1

    @pytest.mark.parametrize(
        ('fields', 'detail'),
        [
            ({}, "Could not get a token with the server's own cloud identity"),
            ({'credential': SERVICE_ACCOUNT}, 'Could not get a token with the stored service-account key'),
            (
                {'auth': 'impersonate', 'auth_settings': {'service_account': 'sa@p.iam'}},
                'Could not get a token with the impersonated service account',
            ),
        ],
    )
    async def test_no_token_is_degraded_and_names_the_method(self, fields, detail):
        probed = backend(**_unique({**VERTEX, **fields}))

        health = await NodeHealthProbe(credentials=FakeCloud(fail=True).credentials()).check_all([probed])

        assert health[probed.id] == NodeHealth(state=HealthState.DEGRADED, detail=detail)

    async def test_a_resource_key_is_not_probed_for_a_token(self):
        cloud = FakeCloud()
        probed = backend(**_unique({**AZURE, 'api_key': 'k'}))

        health = await NodeHealthProbe(credentials=cloud.credentials()).check_all([probed])

        assert health[probed.id] == NodeHealth(
            state=HealthState.HEALTHY, detail='Uses a stored key; checked on its first request'
        )
        assert cloud.fetches == []

    async def test_a_cloud_backend_is_not_scraped_for_vllm_metrics(self, monkeypatch):
        scraped: list[str] = []

        async def fetch(*, base_url, api_key, timeout):
            scraped.append(base_url)
            raise RuntimeError('not reached in this test')

        monkeypatch.setattr('app.services.ai.metrics.fetch', fetch)
        gpu = backend(id='gpu', base_url='http://gpu.invalid/v1')

        await NodeHealthProbe(credentials=FakeCloud().credentials()).metrics_for([gpu, backend(**VERTEX)])

        assert scraped == ['http://gpu.invalid/v1']

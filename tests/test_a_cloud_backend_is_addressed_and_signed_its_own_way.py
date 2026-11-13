"""A backend's kind decides where a request goes and how it is signed.

`openai` is every backend there was before kinds, and must behave exactly
as before. The cloud kinds are signed with a token the server gets from
the cloud's own identity — or from a stored credential — and that token
is cached, renewed before it runs out, and renewed once more when the
provider refuses it. No test here reaches a cloud: the token fetches are
fakes, and the egress guard would refuse anything else.
"""

from __future__ import annotations

import json

import httpx
from pydantic import ValidationError
import pytest

from app.services.ai.chat.upstream_client import UpstreamChatClient
from app.services.ai.health import NodeHealthProbe
from app.services.ai.providers.calls import prepare, renews
from app.services.ai.providers.credentials import (
    AccessToken,
    CachedToken,
    CloudCredentials,
    CredentialUnavailable,
    REFRESH_MARGIN_SECONDS,
)
from app.services.ai.providers.kinds import BackendKind, Wire
from app.services.ai.schemas import BackendLease, HealthState, InferenceBackend
from app.services.ai.upstream import UpstreamCredentialsUnavailable, UpstreamError
from tests.chat_proxy import drain, request

_SERVICE_ACCOUNT = json.dumps({'type': 'service_account', 'private_key': 'PRIVATE-KEY-MATERIAL', 'client_email': 'x'})
_AZURE_APP = json.dumps({'tenant_id': 't', 'client_id': 'c', 'client_secret': 'CLIENT-SECRET-MATERIAL'})


def _vertex(**fields) -> InferenceBackend:
    return InferenceBackend.model_validate(
        {'id': 'vx', 'kind': 'vertex_openai', 'project': 'p1', 'region': 'europe-west4', 'models': ['gemini'], **fields}
    )


class _Fetches:
    """Token fetches that count themselves and hand out tok-1, tok-2, ..."""

    def __init__(self, *, fail: bool = False, lifetime: float = 3600.0, clock=lambda: 1000.0) -> None:
        self.calls: list[tuple[BackendKind, dict | None]] = []
        self.fail = fail
        self.lifetime = lifetime
        self.clock = clock

    def factory(self, kind: BackendKind):
        def build(stored):
            async def fetch() -> AccessToken:
                self.calls.append((kind, stored))
                if self.fail:
                    raise RuntimeError('no identity here')
                return AccessToken(value=f'tok-{len(self.calls)}', expires_at=self.clock() + self.lifetime)

            return fetch

        return build

    def credentials(self) -> CloudCredentials:
        return CloudCredentials(
            fetches={kind: self.factory(kind) for kind in (BackendKind.VERTEX_OPENAI, BackendKind.AZURE_OPENAI)},
            clock=self.clock,
        )


def _lease(kind: BackendKind = BackendKind.OPENAI, **fields) -> BackendLease:
    return BackendLease(
        lease_id='l', backend_id='b', base_url='http://node.invalid/v1', model='public', kind=kind, **fields
    )


class TestABackendSaysWhatItIs:
    def test_a_backend_written_before_kinds_is_an_openai_one(self):
        backend = InferenceBackend(id='gpu', base_url='http://gpu:8000/v1', models=['m'])
        assert (backend.kind, backend.wire) == (BackendKind.OPENAI, Wire.OPENAI)

    def test_every_kind_speaks_one_wire(self):
        assert {kind: kind.wire for kind in BackendKind} == {
            BackendKind.OPENAI: Wire.OPENAI,
            BackendKind.AZURE_OPENAI: Wire.OPENAI,
            BackendKind.VERTEX_OPENAI: Wire.OPENAI,
        }

    def test_a_vertex_backend_is_addressed_by_its_project_and_region(self):
        assert _vertex().base_url == (
            'https://europe-west4-aiplatform.googleapis.com/v1/projects/p1/locations/europe-west4/endpoints/openapi'
        )

    def test_the_global_region_has_no_regional_host(self):
        assert _vertex(region='global').base_url == (
            'https://aiplatform.googleapis.com/v1/projects/p1/locations/global/endpoints/openapi'
        )

    def test_a_base_url_given_for_vertex_wins(self):
        assert _vertex(base_url='https://private.example/v1').base_url == 'https://private.example/v1'

    @pytest.mark.parametrize('missing', ['project', 'region'])
    def test_a_vertex_backend_without_its_project_or_region_is_refused(self, missing):
        fields = {'id': 'vx', 'kind': 'vertex_openai', 'project': 'p1', 'region': 'r', 'models': ['m']}
        del fields[missing]
        with pytest.raises(ValidationError):
            InferenceBackend.model_validate(fields)

    @pytest.mark.parametrize(
        ('fields', 'says'),
        [
            ({'kind': 'openai', 'base_url': 'http://g/v1', 'region': 'r'}, 'Vertex backend only'),
            ({'kind': 'vertex_openai', 'project': 'p', 'region': 'r', 'api_key': 'k'}, 'not an api_key'),
            ({'kind': 'openai', 'base_url': 'http://g/v1', 'credential': _AZURE_APP}, 'give an api_key instead'),
            (
                {'kind': 'azure_openai', 'base_url': 'https://a/openai/v1', 'api_key': 'k', 'credential': _AZURE_APP},
                'not both',
            ),
            ({'kind': 'azure_openai', 'base_url': 'https://a/openai/v1', 'model_ids': {'other': 'x'}}, 'other'),
        ],
    )
    def test_a_field_the_kind_would_ignore_is_refused(self, fields, says):
        with pytest.raises(ValidationError, match=says):
            InferenceBackend.model_validate({'id': 'b', 'models': ['m'], **fields})

    @pytest.mark.parametrize(
        ('kind', 'credential', 'says'),
        [
            ('vertex_openai', 'not json PRIVATE-KEY-MATERIAL', 'not JSON'),
            ('vertex_openai', json.dumps(['PRIVATE-KEY-MATERIAL']), 'not a JSON object'),
            (
                'vertex_openai',
                json.dumps({'type': 'authorized_user', 'secret': 'PRIVATE-KEY-MATERIAL'}),
                'service-account',
            ),
            ('azure_openai', json.dumps({'tenant_id': 't', 'client_secret': 'CLIENT-SECRET-MATERIAL'}), 'client_id'),
        ],
    )
    def test_a_stored_credential_must_be_what_its_cloud_takes_and_is_never_echoed(self, kind, credential, says):
        place = {'project': 'p', 'region': 'r'} if kind == 'vertex_openai' else {'base_url': 'https://a/openai/v1'}
        with pytest.raises(ValidationError, match=says) as raised:
            InferenceBackend.model_validate(
                {'id': 'b', 'kind': kind, 'models': ['m'], 'credential': credential, **place}
            )
        assert 'MATERIAL' not in str(raised.value)

    def test_the_stored_credential_is_not_in_the_repr(self):
        assert 'PRIVATE-KEY-MATERIAL' not in repr(_vertex(credential=_SERVICE_ACCOUNT))
        assert 'CLIENT-SECRET' not in repr(_lease(BackendKind.AZURE_OPENAI, credential=_AZURE_APP))


class TestTheCallIsAddressedAndSigned:
    async def test_an_openai_backend_is_called_exactly_as_before(self):
        call = await prepare(_lease(api_key='sk'), {'model': 'public', 'x': 1}, _Fetches().credentials())
        assert (call.url, call.headers, call.body) == (
            'http://node.invalid/v1/chat/completions',
            {'Content-Type': 'application/json', 'Authorization': 'Bearer sk'},
            {'model': 'public', 'x': 1},
        )

    async def test_the_provider_is_sent_its_own_name_for_the_model(self):
        lease = _lease(model_ids={'public': 'my-azure-deployment'})
        call = await prepare(lease, {'model': 'public'}, _Fetches().credentials())
        assert call.body == {'model': 'my-azure-deployment'}

    async def test_an_azure_resource_key_goes_in_azures_own_header(self):
        fetches = _Fetches()
        call = await prepare(_lease(BackendKind.AZURE_OPENAI, api_key='az-key'), {}, fetches.credentials())
        assert call.headers == {'Content-Type': 'application/json', 'api-key': 'az-key'}
        assert fetches.calls == []

    @pytest.mark.parametrize('kind', [BackendKind.AZURE_OPENAI, BackendKind.VERTEX_OPENAI])
    async def test_a_cloud_backend_without_a_key_is_signed_with_the_clouds_token(self, kind):
        fetches = _Fetches()
        call = await prepare(_lease(kind), {}, fetches.credentials())
        assert call.headers == {'Content-Type': 'application/json', 'Authorization': 'Bearer tok-1'}
        assert fetches.calls == [(kind, None)]

    async def test_a_stored_credential_is_handed_to_the_clouds_sdk(self):
        fetches = _Fetches()
        await prepare(_lease(BackendKind.VERTEX_OPENAI, credential=_SERVICE_ACCOUNT), {}, fetches.credentials())
        assert fetches.calls == [(BackendKind.VERTEX_OPENAI, json.loads(_SERVICE_ACCOUNT))]

    async def test_no_token_is_a_503_naming_no_secret(self):
        with pytest.raises(UpstreamCredentialsUnavailable) as raised:
            await prepare(
                _lease(BackendKind.VERTEX_OPENAI, credential=_SERVICE_ACCOUNT), {}, _Fetches(fail=True).credentials()
            )
        assert raised.value.answer_status == 503
        assert raised.value.worth_another_node
        assert 'MATERIAL' not in str(raised.value) + raised.value.sentence

    @pytest.mark.parametrize(
        ('lease', 'renewable'),
        [
            (_lease(api_key='sk'), False),
            (_lease(BackendKind.AZURE_OPENAI, api_key='az-key'), False),
            (_lease(BackendKind.AZURE_OPENAI), True),
            (_lease(BackendKind.VERTEX_OPENAI), True),
        ],
    )
    def test_only_a_token_the_server_got_is_worth_renewing(self, lease, renewable):
        assert renews(lease) is renewable


class TestATokenIsCachedAndRenewed:
    async def test_a_token_is_reused_until_the_margin_before_it_expires(self):
        now = [1000.0]
        fetches = _Fetches(lifetime=3600.0, clock=lambda: now[0])
        cached = CachedToken(fetches.factory(BackendKind.VERTEX_OPENAI)(None), clock=lambda: now[0])

        assert await cached.value() == 'tok-1'
        now[0] += 3600.0 - REFRESH_MARGIN_SECONDS - 1
        assert await cached.value() == 'tok-1'
        now[0] += 1
        assert await cached.value() == 'tok-2'

    async def test_invalidating_fetches_a_new_one(self):
        fetches = _Fetches()
        cached = CachedToken(fetches.factory(BackendKind.VERTEX_OPENAI)(None), clock=lambda: 1000.0)
        await cached.value()
        cached.invalidate()
        assert await cached.value() == 'tok-2'

    async def test_a_replaced_credential_gets_a_token_of_its_own_at_once(self):
        fetches = _Fetches()
        credentials = fetches.credentials()
        first = await credentials.bearer(backend_id='b', kind=BackendKind.VERTEX_OPENAI, credential=_SERVICE_ACCOUNT)
        replaced = json.dumps({'type': 'service_account', 'private_key': 'another'})
        second = await credentials.bearer(backend_id='b', kind=BackendKind.VERTEX_OPENAI, credential=replaced)
        assert (first, second) == ('tok-1', 'tok-2')

    async def test_an_sdk_failure_is_one_named_error(self):
        with pytest.raises(CredentialUnavailable, match='vertex_openai'):
            await (
                _Fetches(fail=True)
                .credentials()
                .bearer(backend_id='b', kind=BackendKind.VERTEX_OPENAI, credential=None)
            )


class _Upstream:
    """A provider that refuses the first *refusals* tokens, then answers."""

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


class TestARefusedTokenIsRenewedOnce:
    @pytest.mark.parametrize('status', [401, 403])
    async def test_a_completion_is_retried_once_with_a_new_token(self, status):
        upstream, fetches = _Upstream(refusals=1, status=status), _Fetches()
        client = UpstreamChatClient(client_factory=upstream, credentials=fetches.credentials())

        await client.complete(lease=_lease(BackendKind.VERTEX_OPENAI), request=request())

        assert upstream.seen == ['Bearer tok-1', 'Bearer tok-2']

    async def test_a_second_refusal_counts(self):
        upstream = _Upstream(refusals=2)
        client = UpstreamChatClient(client_factory=upstream, credentials=_Fetches().credentials())

        with pytest.raises(UpstreamError) as raised:
            await client.complete(lease=_lease(BackendKind.VERTEX_OPENAI), request=request())

        assert raised.value.status_code == 401
        assert len(upstream.seen) == 2

    async def test_a_static_key_refused_is_not_asked_again(self):
        upstream = _Upstream(refusals=1)
        client = UpstreamChatClient(client_factory=upstream, credentials=_Fetches().credentials())

        with pytest.raises(UpstreamError):
            await client.complete(lease=_lease(api_key='sk'), request=request())

        assert upstream.seen == ['Bearer sk']

    async def test_a_stream_is_retried_once_with_a_new_token(self):
        upstream = _Upstream(refusals=1, stream=True)
        client = UpstreamChatClient(client_factory=upstream, credentials=_Fetches().credentials())

        lines = await drain(client.stream(lease=_lease(BackendKind.AZURE_OPENAI), request=request(stream=True)))

        assert upstream.seen == ['Bearer tok-1', 'Bearer tok-2']
        assert lines

    async def test_a_stream_refused_twice_raises_the_refusal(self):
        upstream = _Upstream(refusals=2, stream=True)
        client = UpstreamChatClient(client_factory=upstream, credentials=_Fetches().credentials())

        with pytest.raises(UpstreamError):
            await drain(client.stream(lease=_lease(BackendKind.AZURE_OPENAI), request=request(stream=True)))
        assert len(upstream.seen) == 2


class TestACloudBackendsHealthIsItsToken:
    async def test_a_token_obtained_is_healthy_and_the_provider_is_not_called(self):
        fetches = _Fetches()
        health = await NodeHealthProbe(credentials=fetches.credentials())._probe(_vertex())
        assert health.state is HealthState.HEALTHY
        assert len(fetches.calls) == 1

    async def test_no_token_is_degraded_and_says_whose_identity(self):
        probe = NodeHealthProbe(credentials=_Fetches(fail=True).credentials())
        ambient = await probe._probe(_vertex())
        stored = await probe._probe(_vertex(credential=_SERVICE_ACCOUNT))
        assert ambient.state is stored.state is HealthState.DEGRADED
        assert "server's own cloud identity" in ambient.detail
        assert 'stored credential' in stored.detail

    async def test_a_cloud_backend_is_not_scraped_for_vllm_metrics(self, monkeypatch):
        scraped: list[str] = []

        async def fetch(*, base_url, api_key, timeout):
            scraped.append(base_url)
            raise RuntimeError('not reached in this test')

        monkeypatch.setattr('app.services.ai.metrics.fetch', fetch)
        gpu = InferenceBackend(id='gpu', base_url='http://gpu.invalid/v1', models=['m'])
        await NodeHealthProbe(credentials=_Fetches().credentials()).metrics_for([gpu, _vertex()])
        assert scraped == ['http://gpu.invalid/v1']

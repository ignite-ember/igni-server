"""A backend's kind decides where a request goes; its auth method, how it is signed.

`openai` is every backend there was before kinds, and must behave exactly
as before. Each kind offers named auth methods (`providers/auth.py`):
a static key, nothing, or a token the server gets from the cloud — from
its own identity, a user-assigned managed identity, an app's client
secret, an impersonated service account or a service-account key. A
token is cached, renewed before it runs out, and renewed once more when
the provider refuses it. No test here reaches a cloud: the token fetches
and the SDK classes are fakes, and the egress guard would refuse anything else.
"""

from __future__ import annotations

import json
import sys
import types

import httpx
from pydantic import ValidationError
import pytest

from app.services.ai.chat.upstream_client import UpstreamChatClient
from app.services.ai.health import NodeHealthProbe
from app.services.ai.providers.auth import AuthMethod, METHODS
from app.services.ai.providers.calls import prepare, renews
from app.services.ai.providers.credentials import (
    AccessToken,
    azure_fetch,
    CachedToken,
    CloudCredentials,
    CredentialUnavailable,
    google_fetch,
    REFRESH_MARGIN_SECONDS,
    Signer,
)
from app.services.ai.providers.kinds import BackendKind, Wire
from app.services.ai.schemas import BackendLease, HealthState, InferenceBackend
from app.services.ai.upstream import UpstreamCredentialsUnavailable, UpstreamError
from tests.chat_proxy import drain, request

_SERVICE_ACCOUNT = json.dumps({'type': 'service_account', 'private_key': 'PRIVATE-KEY-MATERIAL', 'client_email': 'x'})
_AZURE = {'kind': 'azure_openai', 'base_url': 'https://res.openai.azure.com/openai/v1'}
_VERTEX = {'kind': 'vertex_openai', 'project': 'p1', 'region': 'europe-west4'}


def _backend(**fields) -> InferenceBackend:
    return InferenceBackend.model_validate({'id': 'b', 'models': ['m'], **fields})


def _vertex(**fields) -> InferenceBackend:
    return _backend(**{**_VERTEX, **fields})


class _Fetches:
    """Token fetches that record what they were built from and hand out tok-1, tok-2, ..."""

    def __init__(self, *, fail: bool = False, lifetime: float = 3600.0, clock=lambda: 1000.0) -> None:
        self.calls: list[tuple[BackendKind, AuthMethod, dict, str | None]] = []
        self.fail = fail
        self.lifetime = lifetime
        self.clock = clock

    def factory(self, kind: BackendKind, method: AuthMethod):
        def build(settings, secret):
            async def fetch() -> AccessToken:
                self.calls.append((kind, method, dict(settings), secret))
                if self.fail:
                    raise RuntimeError('no identity here')
                return AccessToken(value=f'tok-{len(self.calls)}', expires_at=self.clock() + self.lifetime)

            return fetch

        return build

    def credentials(self) -> CloudCredentials:
        return CloudCredentials(
            fetches={
                (kind, method): self.factory(kind, method)
                for kind, methods in METHODS.items()
                for method in methods
                if method.is_token
            },
            clock=self.clock,
        )


def _lease(kind: BackendKind = BackendKind.OPENAI, auth: AuthMethod = AuthMethod.NONE, **fields) -> BackendLease:
    return BackendLease(
        lease_id='l', backend_id='b', base_url='http://node.invalid/v1', model='public', kind=kind, auth=auth, **fields
    )


def _signer(auth=AuthMethod.AMBIENT, settings=None, secret=None) -> Signer:
    return Signer(backend_id='b', kind=BackendKind.VERTEX_OPENAI, auth=auth, settings=settings or {}, secret=secret)


class TestABackendSaysWhatItIs:
    def test_a_backend_written_before_kinds_is_an_openai_one(self):
        backend = InferenceBackend(id='gpu', base_url='http://gpu:8000/v1', models=['m'])
        assert (backend.kind, backend.wire) == (BackendKind.OPENAI, Wire.OPENAI)

    def test_every_kind_speaks_one_wire(self):
        assert {kind: kind.wire for kind in BackendKind} == {
            BackendKind.OPENAI: Wire.OPENAI,
            BackendKind.AZURE_OPENAI: Wire.OPENAI,
            BackendKind.VERTEX_OPENAI: Wire.OPENAI,
            BackendKind.ANTHROPIC: Wire.ANTHROPIC,
            BackendKind.BEDROCK: Wire.ANTHROPIC,
            BackendKind.VERTEX_ANTHROPIC: Wire.ANTHROPIC,
            BackendKind.AZURE_ANTHROPIC: Wire.ANTHROPIC,
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
        fields = {'id': 'vx', 'models': ['m'], **_VERTEX}
        del fields[missing]
        with pytest.raises(ValidationError, match='project and region'):
            InferenceBackend.model_validate(fields)

    def test_a_region_on_a_backend_that_is_not_vertex_is_refused(self):
        with pytest.raises(ValidationError, match='Vertex or Bedrock backend only'):
            _backend(base_url='http://g/v1', region='r')

    def test_a_model_map_naming_a_model_not_served_is_refused(self):
        with pytest.raises(ValidationError, match='other'):
            _backend(**_AZURE, model_ids={'other': 'x'})


class TestEachKindOffersItsMethods:
    def test_the_portal_and_the_router_share_one_table(self):
        assert {kind: list(methods) for kind, methods in METHODS.items()} == {
            BackendKind.OPENAI: [AuthMethod.NONE, AuthMethod.API_KEY],
            BackendKind.AZURE_OPENAI: [
                AuthMethod.AMBIENT,
                AuthMethod.MANAGED_IDENTITY,
                AuthMethod.CLIENT_SECRET,
                AuthMethod.API_KEY,
            ],
            BackendKind.VERTEX_OPENAI: [AuthMethod.AMBIENT, AuthMethod.IMPERSONATE, AuthMethod.SERVICE_ACCOUNT_KEY],
            BackendKind.ANTHROPIC: [AuthMethod.API_KEY],
            BackendKind.BEDROCK: [AuthMethod.AMBIENT, AuthMethod.ASSUME_ROLE, AuthMethod.ACCESS_KEYS],
            BackendKind.VERTEX_ANTHROPIC: [
                AuthMethod.AMBIENT,
                AuthMethod.IMPERSONATE,
                AuthMethod.SERVICE_ACCOUNT_KEY,
            ],
            BackendKind.AZURE_ANTHROPIC: [
                AuthMethod.AMBIENT,
                AuthMethod.MANAGED_IDENTITY,
                AuthMethod.CLIENT_SECRET,
                AuthMethod.API_KEY,
            ],
        }

    @pytest.mark.parametrize(
        ('fields', 'method'),
        [
            ({'base_url': 'http://g/v1'}, AuthMethod.NONE),
            ({'base_url': 'http://g/v1', 'api_key': 'k'}, AuthMethod.API_KEY),
            (_AZURE, AuthMethod.AMBIENT),
            ({**_AZURE, 'api_key': 'k'}, AuthMethod.API_KEY),
            (_VERTEX, AuthMethod.AMBIENT),
            ({**_VERTEX, 'credential': _SERVICE_ACCOUNT}, AuthMethod.SERVICE_ACCOUNT_KEY),
        ],
    )
    def test_a_backend_that_names_no_method_means_what_it_carries(self, fields, method):
        assert _backend(**fields).auth is method

    @pytest.mark.parametrize(
        'fields',
        [
            {**_AZURE, 'auth': 'managed_identity', 'auth_settings': {'client_id': 'c'}},
            {
                **_AZURE,
                'auth': 'client_secret',
                'auth_settings': {'tenant_id': 't', 'client_id': 'c'},
                'credential': 's',
            },
            {**_VERTEX, 'auth': 'impersonate', 'auth_settings': {'service_account': 'sa@p1.iam.gserviceaccount.com'}},
        ],
    )
    def test_a_method_with_its_settings_and_secret_is_accepted(self, fields):
        assert _backend(**fields).auth is AuthMethod(fields['auth'])

    @pytest.mark.parametrize(
        ('fields', 'says'),
        [
            ({**_VERTEX, 'auth': 'api_key', 'api_key': 'k'}, 'not offered for this kind'),
            ({'base_url': 'http://g/v1', 'auth': 'ambient'}, 'not offered for this kind'),
            ({**_AZURE, 'auth': 'managed_identity'}, 'needs client_id'),
            ({**_AZURE, 'auth': 'client_secret', 'auth_settings': {'client_id': 'c'}, 'credential': 's'}, 'tenant_id'),
            ({**_AZURE, 'auth': 'client_secret', 'auth_settings': {'tenant_id': 't', 'client_id': 'c'}}, 'credential'),
            ({**_AZURE, 'auth': 'ambient', 'auth_settings': {'client_id': 'c'}}, 'does not take client_id'),
            ({**_AZURE, 'auth': 'ambient', 'credential': 's'}, 'takes no stored credential'),
            ({**_AZURE, 'auth': 'managed_identity', 'auth_settings': {'client_id': 'c'}, 'api_key': 'k'}, 'api_key'),
            ({'base_url': 'http://g/v1', 'auth': 'api_key'}, 'needs its key'),
            ({'base_url': 'http://g/v1', 'credential': 's'}, 'takes no stored credential'),
            ({**_VERTEX, 'auth': 'impersonate'}, 'service_account'),
        ],
    )
    def test_a_method_missing_what_it_needs_or_given_what_it_ignores_is_refused(self, fields, says):
        with pytest.raises(ValidationError, match=says):
            _backend(**fields)

    @pytest.mark.parametrize(
        'credential',
        [
            'not json PRIVATE-KEY-MATERIAL',
            json.dumps(['PRIVATE-KEY-MATERIAL']),
            json.dumps({'type': 'authorized_user', 'secret': 'PRIVATE-KEY-MATERIAL'}),
        ],
    )
    def test_a_service_account_key_must_be_one_and_is_never_echoed(self, credential):
        with pytest.raises(ValidationError) as raised:
            _vertex(auth='service_account_key', credential=credential)
        assert 'MATERIAL' not in str(raised.value)

    def test_the_secret_is_not_in_the_repr(self):
        assert 'PRIVATE-KEY-MATERIAL' not in repr(_vertex(credential=_SERVICE_ACCOUNT))
        assert 'CLIENT-SECRET' not in repr(_lease(credential='CLIENT-SECRET-MATERIAL'))


class _Recorded:
    """Stands in for one SDK credential class: records how it was built, hands out a token."""

    built: list[tuple[str, dict]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, *args, **kwargs):
        _Recorded.built.append((self.name, kwargs))
        return self

    async def get_token(self, scope):
        return types.SimpleNamespace(token=f'{self.name}-token', expires_on=9_999_999_999)


class TestEachMethodBuildsTheRightSdkCredential:
    """The real fetches, over fake SDK classes: which one each method builds, and from what."""

    @pytest.fixture(autouse=True)
    def fake_azure(self, monkeypatch):
        _Recorded.built = []
        aio = types.ModuleType('azure.identity.aio')
        for name in ('DefaultAzureCredential', 'ManagedIdentityCredential', 'ClientSecretCredential'):
            setattr(aio, name, _Recorded(name))
        monkeypatch.setitem(sys.modules, 'azure.identity.aio', aio)

    @pytest.mark.parametrize(
        ('method', 'settings', 'secret', 'built'),
        [
            (AuthMethod.AMBIENT, {}, None, ('DefaultAzureCredential', {})),
            (
                AuthMethod.MANAGED_IDENTITY,
                {'client_id': 'uami'},
                None,
                ('ManagedIdentityCredential', {'client_id': 'uami'}),
            ),
            (
                AuthMethod.CLIENT_SECRET,
                {'tenant_id': 't', 'client_id': 'c'},
                'shh',
                ('ClientSecretCredential', {'tenant_id': 't', 'client_id': 'c', 'client_secret': 'shh'}),
            ),
        ],
    )
    async def test_azure(self, method, settings, secret, built):
        token = await azure_fetch(method)(settings, secret)()
        assert _Recorded.built == [built]
        assert token.value == f'{built[0]}-token'

    @pytest.fixture
    def fake_google(self, monkeypatch):
        """google.auth.default, impersonation and service-account keys, recorded."""
        made: list[tuple[str, dict]] = []

        class Creds:
            def __init__(self, name, **kwargs):
                made.append((name, kwargs))
                self.token, self.expiry = f'{name}-token', None

            def refresh(self, _request):
                pass

        import google.auth
        from google.auth import impersonated_credentials
        from google.oauth2 import service_account

        monkeypatch.setattr(google.auth, 'default', lambda scopes: (Creds('default', scopes=scopes), 'p'))
        monkeypatch.setattr(
            impersonated_credentials, 'Credentials', lambda **kw: Creds('impersonated', target=kw['target_principal'])
        )
        monkeypatch.setattr(
            service_account.Credentials,
            'from_service_account_info',
            staticmethod(lambda info, scopes: Creds('key', email=info['client_email'])),
        )
        return made

    async def test_google_ambient_uses_the_default_chain(self, fake_google):
        token = await google_fetch(AuthMethod.AMBIENT)({}, None)()
        assert [name for name, _ in fake_google] == ['default']
        assert token.value == 'default-token'

    async def test_google_impersonation_acts_as_the_named_account(self, fake_google):
        token = await google_fetch(AuthMethod.IMPERSONATE)({'service_account': 'sa@p.iam'}, None)()
        assert fake_google[-1] == ('impersonated', {'target': 'sa@p.iam'})
        assert token.value == 'impersonated-token'

    async def test_google_key_is_read_from_the_stored_secret(self, fake_google):
        token = await google_fetch(AuthMethod.SERVICE_ACCOUNT_KEY)({}, _SERVICE_ACCOUNT)()
        assert fake_google == [('key', {'email': 'x'})]
        assert token.value == 'key-token'


class TestTheCallIsAddressedAndSigned:
    async def test_an_openai_backend_is_called_exactly_as_before(self):
        call = await prepare(
            _lease(auth=AuthMethod.API_KEY, api_key='sk'), {'model': 'public', 'x': 1}, _Fetches().credentials()
        )
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
        lease = _lease(BackendKind.AZURE_OPENAI, AuthMethod.API_KEY, api_key='az-key')
        call = await prepare(lease, {}, fetches.credentials())
        assert call.headers == {'Content-Type': 'application/json', 'api-key': 'az-key'}
        assert fetches.calls == []

    @pytest.mark.parametrize(
        ('kind', 'auth', 'settings', 'secret'),
        [
            (BackendKind.AZURE_OPENAI, AuthMethod.AMBIENT, {}, None),
            (BackendKind.AZURE_OPENAI, AuthMethod.MANAGED_IDENTITY, {'client_id': 'c'}, None),
            (BackendKind.AZURE_OPENAI, AuthMethod.CLIENT_SECRET, {'tenant_id': 't', 'client_id': 'c'}, 'shh'),
            (BackendKind.VERTEX_OPENAI, AuthMethod.AMBIENT, {}, None),
            (BackendKind.VERTEX_OPENAI, AuthMethod.IMPERSONATE, {'service_account': 'sa@p.iam'}, None),
            (BackendKind.VERTEX_OPENAI, AuthMethod.SERVICE_ACCOUNT_KEY, {}, _SERVICE_ACCOUNT),
        ],
    )
    async def test_a_token_method_is_signed_with_the_token_its_settings_and_secret_get(
        self, kind, auth, settings, secret
    ):
        fetches = _Fetches()
        lease = _lease(kind, auth, auth_settings=settings, credential=secret)
        call = await prepare(lease, {}, fetches.credentials())
        assert call.headers == {'Content-Type': 'application/json', 'Authorization': 'Bearer tok-1'}
        assert fetches.calls == [(kind, auth, settings, secret)]

    async def test_no_token_is_a_503_naming_no_secret(self):
        lease = _lease(BackendKind.VERTEX_OPENAI, AuthMethod.SERVICE_ACCOUNT_KEY, credential=_SERVICE_ACCOUNT)
        with pytest.raises(UpstreamCredentialsUnavailable) as raised:
            await prepare(lease, {}, _Fetches(fail=True).credentials())
        assert raised.value.answer_status == 503
        assert raised.value.worth_another_node
        assert 'MATERIAL' not in str(raised.value) + raised.value.sentence

    @pytest.mark.parametrize(
        ('auth', 'renewable'),
        [(AuthMethod.NONE, False), (AuthMethod.API_KEY, False), (AuthMethod.ACCESS_KEYS, False)]
        + [
            (method, True)
            for method in AuthMethod
            if method not in (AuthMethod.NONE, AuthMethod.API_KEY, AuthMethod.ACCESS_KEYS)
        ],
    )
    def test_only_a_token_the_server_got_is_worth_renewing(self, auth, renewable):
        assert renews(_lease(BackendKind.AZURE_OPENAI, auth)) is renewable


class TestATokenIsCachedAndRenewed:
    async def test_a_token_is_reused_until_the_margin_before_it_expires(self):
        now = [1000.0]
        fetches = _Fetches(lifetime=3600.0, clock=lambda: now[0])
        fetch = fetches.factory(BackendKind.VERTEX_OPENAI, AuthMethod.AMBIENT)({}, None)
        cached = CachedToken(fetch, clock=lambda: now[0])

        assert await cached.value() == 'tok-1'
        now[0] += 3600.0 - REFRESH_MARGIN_SECONDS - 1
        assert await cached.value() == 'tok-1'
        now[0] += 1
        assert await cached.value() == 'tok-2'

    async def test_invalidating_fetches_a_new_one(self):
        fetch = _Fetches().factory(BackendKind.VERTEX_OPENAI, AuthMethod.AMBIENT)({}, None)
        cached = CachedToken(fetch, clock=lambda: 1000.0)
        await cached.value()
        cached.invalidate()
        assert await cached.value() == 'tok-2'

    @pytest.mark.parametrize(
        ('first', 'second'),
        [
            (
                _signer(AuthMethod.SERVICE_ACCOUNT_KEY, secret=_SERVICE_ACCOUNT),
                _signer(AuthMethod.SERVICE_ACCOUNT_KEY, secret=json.dumps({'type': 'service_account', 'k': 2})),
            ),
            (
                _signer(AuthMethod.IMPERSONATE, settings={'service_account': 'a@p.iam'}),
                _signer(AuthMethod.IMPERSONATE, settings={'service_account': 'b@p.iam'}),
            ),
            (_signer(AuthMethod.AMBIENT), _signer(AuthMethod.IMPERSONATE, settings={'service_account': 'a@p.iam'})),
        ],
    )
    async def test_a_changed_method_setting_or_secret_gets_a_token_of_its_own_at_once(self, first, second):
        credentials = _Fetches().credentials()
        assert (await credentials.bearer(first), await credentials.bearer(second)) == ('tok-1', 'tok-2')

    async def test_the_same_signer_reuses_its_token(self):
        credentials = _Fetches().credentials()
        assert (await credentials.bearer(_signer()), await credentials.bearer(_signer())) == ('tok-1', 'tok-1')

    async def test_an_sdk_failure_is_one_named_error(self):
        with pytest.raises(CredentialUnavailable, match='vertex_openai'):
            await _Fetches(fail=True).credentials().bearer(_signer())


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


_VERTEX_LEASE = _lease(BackendKind.VERTEX_OPENAI, AuthMethod.AMBIENT)
_AZURE_LEASE = _lease(BackendKind.AZURE_OPENAI, AuthMethod.MANAGED_IDENTITY, auth_settings={'client_id': 'c'})


class TestARefusedTokenIsRenewedOnce:
    @pytest.mark.parametrize('status', [401, 403])
    async def test_a_completion_is_retried_once_with_a_new_token(self, status):
        upstream, fetches = _Upstream(refusals=1, status=status), _Fetches()
        client = UpstreamChatClient(client_factory=upstream, credentials=fetches.credentials())

        await client.complete(lease=_VERTEX_LEASE, request=request())

        assert upstream.seen == ['Bearer tok-1', 'Bearer tok-2']

    async def test_a_second_refusal_counts(self):
        upstream = _Upstream(refusals=2)
        client = UpstreamChatClient(client_factory=upstream, credentials=_Fetches().credentials())

        with pytest.raises(UpstreamError) as raised:
            await client.complete(lease=_VERTEX_LEASE, request=request())

        assert raised.value.status_code == 401
        assert len(upstream.seen) == 2

    async def test_a_static_key_refused_is_not_asked_again(self):
        upstream = _Upstream(refusals=1)
        client = UpstreamChatClient(client_factory=upstream, credentials=_Fetches().credentials())

        with pytest.raises(UpstreamError):
            await client.complete(lease=_lease(auth=AuthMethod.API_KEY, api_key='sk'), request=request())

        assert upstream.seen == ['Bearer sk']

    async def test_a_stream_is_retried_once_with_a_new_token(self):
        upstream = _Upstream(refusals=1, stream=True)
        client = UpstreamChatClient(client_factory=upstream, credentials=_Fetches().credentials())

        lines = await drain(client.stream(lease=_AZURE_LEASE, request=request(stream=True)))

        assert upstream.seen == ['Bearer tok-1', 'Bearer tok-2']
        assert lines

    async def test_a_stream_refused_twice_raises_the_refusal(self):
        upstream = _Upstream(refusals=2, stream=True)
        client = UpstreamChatClient(client_factory=upstream, credentials=_Fetches().credentials())

        with pytest.raises(UpstreamError):
            await drain(client.stream(lease=_AZURE_LEASE, request=request(stream=True)))
        assert len(upstream.seen) == 2


class TestACloudBackendsHealthIsItsToken:
    async def test_a_token_obtained_is_healthy_and_the_provider_is_not_called(self):
        fetches = _Fetches()
        health = await NodeHealthProbe(credentials=fetches.credentials())._probe(_vertex())
        assert health.state is HealthState.HEALTHY
        assert len(fetches.calls) == 1

    @pytest.mark.parametrize(
        ('fields', 'names'),
        [
            ({}, "server's own cloud identity"),
            ({'credential': _SERVICE_ACCOUNT}, 'stored service-account key'),
            ({'auth': 'impersonate', 'auth_settings': {'service_account': 'sa@p.iam'}}, 'impersonated service account'),
        ],
    )
    async def test_no_token_is_degraded_and_names_the_method(self, fields, names):
        probe = NodeHealthProbe(credentials=_Fetches(fail=True).credentials())
        health = await probe._probe(_vertex(**fields))
        assert health.state is HealthState.DEGRADED
        assert names in (health.detail or '')

    async def test_a_resource_key_is_not_probed_for_a_token(self):
        fetches = _Fetches()
        health = await NodeHealthProbe(credentials=fetches.credentials())._probe(_backend(**_AZURE, api_key='k'))
        assert health.state is HealthState.HEALTHY
        assert fetches.calls == []

    async def test_a_cloud_backend_is_not_scraped_for_vllm_metrics(self, monkeypatch):
        scraped: list[str] = []

        async def fetch(*, base_url, api_key, timeout):
            scraped.append(base_url)
            raise RuntimeError('not reached in this test')

        monkeypatch.setattr('app.services.ai.metrics.fetch', fetch)
        gpu = InferenceBackend(id='gpu', base_url='http://gpu.invalid/v1', models=['m'])
        await NodeHealthProbe(credentials=_Fetches().credentials()).metrics_for([gpu, _vertex()])
        assert scraped == ['http://gpu.invalid/v1']
